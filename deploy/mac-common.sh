#!/usr/bin/env bash

project_dir="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# Host-side operations share one atomic lock.  The owner token is exported so
# child scripts (for example restore-release -> restore-objects) join the
# operation without releasing their parent's lock.  A preserved lock is a
# deliberate fail-closed maintenance state and is never considered stale
# automatically.
operation_lock_dir="${HARBOR_MARKET_OPERATION_LOCK_DIR:-$project_dir/.data/operation.lock}"
operation_lock_token="${HARBOR_MARKET_OPERATION_LOCK_TOKEN:-}"
operation_lock_acquired_here=false
operation_lock_joined=false

operation_lock_describe() {
  local operation="unknown"
  local pid="unknown"
  local started_at="unknown"
  if [[ -r "$operation_lock_dir/operation" ]]; then
    operation="$(sed -n '1p' "$operation_lock_dir/operation")"
  fi
  if [[ -r "$operation_lock_dir/pid" ]]; then
    pid="$(sed -n '1p' "$operation_lock_dir/pid")"
  fi
  if [[ -r "$operation_lock_dir/started_at" ]]; then
    started_at="$(sed -n '1p' "$operation_lock_dir/started_at")"
  fi
  printf 'operation=%s pid=%s started_at=%s path=%s\n' \
    "$operation" "$pid" "$started_at" "$operation_lock_dir"
}

_operation_lock_acquire() {
  local operation="$1"
  local quiet="${2:-false}"
  local current_owner=""
  local lock_parent
  local host_name
  local new_token

  case "$operation" in
    ''|*[!A-Za-z0-9._-]*)
      [[ "$quiet" == true ]] || printf 'Invalid operation lock name: %s\n' "$operation" >&2
      return 2
      ;;
  esac

  if [[ -r "$operation_lock_dir/owner" ]]; then
    current_owner="$(sed -n '1p' "$operation_lock_dir/owner")"
  fi
  if [[ -n "$operation_lock_token" && "$current_owner" == "$operation_lock_token" ]]; then
    operation_lock_joined=true
    export HARBOR_MARKET_OPERATION_LOCK_DIR="$operation_lock_dir"
    export HARBOR_MARKET_OPERATION_LOCK_TOKEN="$operation_lock_token"
    return 0
  fi

  lock_parent="$(dirname "$operation_lock_dir")"
  if ! (umask 077 && mkdir -p "$lock_parent"); then
    [[ "$quiet" == true ]] \
      || printf 'Could not create the operation-lock parent: %s\n' "$lock_parent" >&2
    return 1
  fi
  if ! (umask 077 && mkdir "$operation_lock_dir") 2>/dev/null; then
    if [[ "$quiet" != true ]]; then
      printf 'Another Harbor Market operation owns the maintenance lock: ' >&2
      operation_lock_describe >&2
    fi
    return 1
  fi

  host_name="$(hostname 2>/dev/null || printf 'unknown-host')"
  new_token="$host_name-$$-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
  if ! (
    set -e
    umask 077
    printf '%s\n' "$new_token" >"$operation_lock_dir/owner"
    printf '%s\n' "$operation" >"$operation_lock_dir/operation"
    printf '%s\n' "$$" >"$operation_lock_dir/pid"
    printf '%s\n' "$(date -u +%FT%TZ)" >"$operation_lock_dir/started_at"
    printf '%s\n' "$host_name" >"$operation_lock_dir/host"
  ); then
    rm -f "$operation_lock_dir/owner" "$operation_lock_dir/operation" \
      "$operation_lock_dir/pid" "$operation_lock_dir/started_at" \
      "$operation_lock_dir/host"
    rmdir "$operation_lock_dir" 2>/dev/null || true
    [[ "$quiet" == true ]] || printf 'Could not initialize operation lock metadata.\n' >&2
    return 1
  fi

  operation_lock_token="$new_token"
  operation_lock_acquired_here=true
  operation_lock_joined=false
  export HARBOR_MARKET_OPERATION_LOCK_DIR="$operation_lock_dir"
  export HARBOR_MARKET_OPERATION_LOCK_TOKEN="$operation_lock_token"
}

operation_lock_acquire() {
  _operation_lock_acquire "$1" false
}

operation_lock_try_acquire() {
  _operation_lock_acquire "$1" true
}

operation_lock_preserve() {
  local reason="${1:-manual recovery is required}"
  local current_owner=""
  if [[ -r "$operation_lock_dir/owner" ]]; then
    current_owner="$(sed -n '1p' "$operation_lock_dir/owner")"
  fi
  if [[ -z "$operation_lock_token" || "$current_owner" != "$operation_lock_token" ]]; then
    printf 'Cannot preserve an operation lock not owned by this operation.\n' >&2
    return 1
  fi
  if [[ -e "$operation_lock_dir/preserve" ]]; then
    printf 'Maintenance lock was already preserved: %s\n' \
      "$operation_lock_dir" >&2
    return 0
  fi
  if ! (
    set -e
    umask 077
    printf 'reason=%s\npreserved_at=%s\n' "$reason" "$(date -u +%FT%TZ)" \
      >"$operation_lock_dir/preserve"
  ); then
    printf 'Could not write the maintenance preservation marker: %s\n' \
      "$operation_lock_dir/preserve" >&2
    return 1
  fi
  printf 'Maintenance lock preserved for manual recovery: %s\n' \
    "$operation_lock_dir" >&2
}

operation_lock_release() {
  local current_owner=""
  if [[ "$operation_lock_acquired_here" != true ]]; then
    return 0
  fi
  if [[ -r "$operation_lock_dir/owner" ]]; then
    current_owner="$(sed -n '1p' "$operation_lock_dir/owner")"
  fi
  if [[ -z "$operation_lock_token" || "$current_owner" != "$operation_lock_token" ]]; then
    printf 'Refusing to release an operation lock owned by another process.\n' >&2
    return 1
  fi
  if [[ -e "$operation_lock_dir/preserve" ]]; then
    printf 'Maintenance lock remains preserved: %s\n' "$operation_lock_dir" >&2
    return 0
  fi

  rm -f "$operation_lock_dir/owner" "$operation_lock_dir/operation" \
    "$operation_lock_dir/pid" "$operation_lock_dir/started_at" \
    "$operation_lock_dir/host"
  if ! rmdir "$operation_lock_dir"; then
    printf 'Operation lock contains unexpected state and was not released: %s\n' \
      "$operation_lock_dir" >&2
    return 1
  fi
  operation_lock_acquired_here=false
  operation_lock_token=""
  unset HARBOR_MARKET_OPERATION_LOCK_TOKEN
}

validate_production_env() {
  local compose_config="$1"
  python3 - "$compose_config" <<'PY'
from __future__ import annotations

import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"Could not read rendered Compose configuration: {exc}") from exc

services = config.get("services")
if not isinstance(services, dict):
    raise SystemExit("Rendered Compose configuration has no services map")


def environment_value(service_name: str, key: str):
    service = services.get(service_name)
    if not isinstance(service, dict):
        return None
    environment = service.get("environment")
    if not isinstance(environment, dict):
        return None
    value = environment.get(key)
    return value if isinstance(value, str) else None


environment = environment_value("backend", "ENVIRONMENT")
if environment != "production":
    raise SystemExit("mac-deploy requires rendered ENVIRONMENT=production")

storage_backend = environment_value("backend", "STORAGE_BACKEND")
if storage_backend != "minio":
    raise SystemExit(
        "mac-deploy requires rendered STORAGE_BACKEND=minio for production"
    )

locations = {
    "POSTGRES_PASSWORD": ("db", "POSTGRES_PASSWORD"),
    "AUTH_SECRET_KEY": ("backend", "AUTH_SECRET_KEY"),
    "MINIO_ROOT_USER": ("minio", "MINIO_ROOT_USER"),
    "MINIO_ROOT_PASSWORD": ("minio", "MINIO_ROOT_PASSWORD"),
    "STORAGE_ACCESS_KEY": ("backend", "STORAGE_ACCESS_KEY"),
    "STORAGE_SECRET_KEY": ("backend", "STORAGE_SECRET_KEY"),
}
values = {}
failed = False
for name, (service_name, key) in locations.items():
    value = environment_value(service_name, key)
    if not value:
        print(f"Rendered production configuration is missing {name}.", file=sys.stderr)
        failed = True
        continue
    normalized = value.lower()
    if normalized.startswith(
        (
            "replace",
            "changeme",
            "change-me",
            "change_me",
            "placeholder",
            "example",
            "test-only",
            "your-",
        )
    ):
        print(
            f"Rendered production configuration contains a placeholder for {name}.",
            file=sys.stderr,
        )
        failed = True
    if any(marker in normalized for marker in ("${", "$(", "<replace", "<change")):
        print(
            f"Rendered production configuration contains an unresolved value for {name}.",
            file=sys.stderr,
        )
        failed = True
    values[name] = value

if values.get("MINIO_ROOT_USER") and values.get("MINIO_ROOT_USER") == values.get(
    "STORAGE_ACCESS_KEY"
):
    print(
        "MINIO_ROOT_USER and STORAGE_ACCESS_KEY must be different identities.",
        file=sys.stderr,
    )
    failed = True
if values.get("MINIO_ROOT_PASSWORD") and values.get(
    "MINIO_ROOT_PASSWORD"
) == values.get("STORAGE_SECRET_KEY"):
    print(
        "MINIO_ROOT_PASSWORD and STORAGE_SECRET_KEY must be different secrets.",
        file=sys.stderr,
    )
    failed = True

if failed:
    raise SystemExit(1)
PY
}

find_docker() {
  local candidate
  if [[ -n "${DOCKER_BIN:-}" ]]; then
    if [[ ! -x "$DOCKER_BIN" ]]; then
      printf 'DOCKER_BIN is not executable: %s\n' "$DOCKER_BIN" >&2
      return 1
    fi
    printf '%s\n' "$DOCKER_BIN"
    return 0
  fi
  for candidate in /opt/homebrew/bin/docker "$(command -v docker 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      if [[ "$(uname -m)" == "arm64" && "$candidate" == /usr/local/* ]]; then
        continue
      fi
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

find_colima() {
  local candidate
  for candidate in /opt/homebrew/bin/colima "$(command -v colima 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      if [[ "$(uname -m)" == "arm64" && "$candidate" == /usr/local/* ]]; then
        continue
      fi
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

wait_for_docker() {
  local docker_bin="$1"
  local attempts="${2:-90}"
  local count
  for ((count = 1; count <= attempts; count++)); do
    if "$docker_bin" info >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

start_docker_engine() {
  local docker_bin="$1"

  if "$docker_bin" info >/dev/null 2>&1; then
    return 0
  fi

  local colima_bin
  if colima_bin="$(find_colima)"; then
    if [[ -f "$HOME/.colima/default/colima.yaml" ]]; then
      "$colima_bin" start
    else
      "$colima_bin" start \
        --cpu 4 \
        --memory 6 \
        --disk 60 \
        --vm-type vz \
        --mount-type virtiofs
    fi
    wait_for_docker "$docker_bin" 60
    return
  fi

  printf 'Native arm64 Colima is required at /opt/homebrew/bin/colima.\n' >&2
  return 1
}

compose() {
  local docker_bin="$1"
  shift
  "$docker_bin" compose --project-directory "$project_dir" "$@"
}
