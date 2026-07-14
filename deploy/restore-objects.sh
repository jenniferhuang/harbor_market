#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 || "$1" != "--confirm-replace" || ! -d "$2/objects" ]]; then
  printf 'Usage: %s --confirm-replace <object-backup-directory>\n' "$0" >&2
  exit 2
fi
if [[ ! -f "$2/.complete" || ! -f "$2/manifest.tsv" ]]; then
  printf 'Backup is incomplete or was not created by backup-objects.sh: %s\n' "$2" >&2
  exit 2
fi

source_dir="$(cd "$2" && pwd)"
deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)" || {
  printf 'Docker CLI was not found.\n' >&2
  exit 1
}
env_file="${ENV_FILE:-$project_dir/.env}"
if [[ ! -r "$env_file" ]]; then
  printf 'Missing deployment environment file: %s\n' "$env_file" >&2
  exit 1
fi
stop_timeout="${BACKUP_STOP_TIMEOUT_SECONDS:-60}"
if [[ ! "$stop_timeout" =~ ^[0-9]+$ ]] \
  || (( 10#$stop_timeout < 10 || 10#$stop_timeout > 300 )); then
  printf 'BACKUP_STOP_TIMEOUT_SECONDS must be between 10 and 300.\n' >&2
  exit 2
fi

restore_preflight_finish() {
  local status="$?"
  trap - EXIT INT TERM
  if ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}

operation_lock_acquire restore-objects
trap restore_preflight_finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

configured_bucket="$(
  compose "$docker_bin" --env-file "$env_file" config --format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["backend"]["environment"]["STORAGE_BUCKET"])'
)"

# Rehash every file and compare the deterministic manifest, totals, format, and
# bucket before MinIO is changed.
python3 - "$source_dir" "$configured_bucket" <<'PY'
from __future__ import annotations

import hashlib
import pathlib
import sys

backup = pathlib.Path(sys.argv[1])
expected_bucket = sys.argv[2]


def read_metadata(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            raise SystemExit(f"Malformed metadata line in {path}: {raw_line!r}")
        key, value = raw_line.split("=", 1)
        if not key or key in result:
            raise SystemExit(f"Invalid or duplicate metadata key in {path}: {key!r}")
        result[key] = value
    return result


metadata = read_metadata(backup / ".complete")
if metadata.get("format") != "minio-mirror-sha256-v2":
    raise SystemExit("Unsupported object backup format")
if metadata.get("bucket") != expected_bucket:
    raise SystemExit(
        f"Backup bucket {metadata.get('bucket')!r} does not match configured "
        f"bucket {expected_bucket!r}"
    )
if metadata.get("manifest") != "manifest.tsv":
    raise SystemExit("Unexpected object backup manifest name")

objects = backup / "objects"
entries: list[tuple[str, int, str]] = []
total_bytes = 0
for path in sorted(objects.rglob("*"), key=lambda item: item.relative_to(objects).as_posix()):
    if path.is_symlink():
        raise SystemExit(f"Refusing to restore symlink: {path}")
    if not path.is_file():
        continue
    relative = path.relative_to(objects).as_posix()
    if "\t" in relative or "\n" in relative or "\r" in relative:
        raise SystemExit(f"Unsupported control character in object key: {relative!r}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    entries.append((digest.hexdigest(), size, relative))
    total_bytes += size

expected_manifest = "".join(
    f"{digest}\t{size}\t{relative}\n" for digest, size, relative in entries
).encode()
actual_manifest = (backup / "manifest.tsv").read_bytes()
if actual_manifest != expected_manifest:
    raise SystemExit("Object backup content does not match manifest.tsv")
manifest_digest = hashlib.sha256(actual_manifest).hexdigest()
if metadata.get("manifest_sha256") != manifest_digest:
    raise SystemExit("Object manifest SHA-256 verification failed")
if metadata.get("object_count") != str(len(entries)):
    raise SystemExit("Object backup count does not match completion metadata")
if metadata.get("total_bytes") != str(total_bytes):
    raise SystemExit("Object backup size does not match completion metadata")
PY

expected_manifest_sha256="$(
  awk -F= '$1 == "manifest_sha256" {print $2}' "$source_dir/.complete"
)"
export OBJECT_TRANSFER_DIR="$source_dir"
compose "$docker_bin" --env-file "$env_file" run --rm -T --no-deps \
  --env EXPECTED_MANIFEST_SHA256="$expected_manifest_sha256" \
  --entrypoint /bin/sh minio-client -ec '
    if [ ! -d /transfer/objects ] || [ ! -f /transfer/manifest.tsv ] \
      || [ ! -f /transfer/.complete ]; then
      printf "Selected object backup is not mounted inside Docker.\n" >&2
      exit 1
    fi
    actual_manifest_sha256="$(sha256sum /transfer/manifest.tsv)"
    actual_manifest_sha256="${actual_manifest_sha256%% *}"
    if [ "$actual_manifest_sha256" != "$EXPECTED_MANIFEST_SHA256" ]; then
      printf "Docker-mounted object manifest does not match the selected backup.\n" >&2
      exit 1
    fi
  '

service_is_running() {
  local service="$1"
  local container_id
  container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q "$service")" || return 1
  [[ -n "$container_id" ]] || return 1
  [[ "$("$docker_bin" inspect --format '{{.State.Running}}' "$container_id")" == true ]]
}

backend_was_running=false
worker_was_running=false
backend_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q backend)"
worker_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q cleanup-worker)"
if service_is_running backend; then
  backend_was_running=true
fi
if service_is_running cleanup-worker; then
  worker_was_running=true
fi

restart_allowed=true
restore_service_state() {
  local status="$?"
  local restart_failed=false
  trap - EXIT INT TERM
  if [[ "$restart_allowed" == true ]]; then
    if [[ "$backend_was_running" == true ]]; then
      if ! "$docker_bin" start "$backend_container_id" >/dev/null; then
        restart_failed=true
        status=1
      fi
    fi
    if [[ "$worker_was_running" == true ]]; then
      if ! "$docker_bin" start "$worker_container_id" >/dev/null; then
        restart_failed=true
        status=1
      fi
    fi
  else
    printf 'Backend and cleanup worker remain stopped for manual recovery.\n' >&2
    status=1
  fi

  if [[ "$restart_allowed" != true ]]; then
    operation_lock_preserve "object restore requires manual recovery" || true
  elif [[ "$restart_failed" == true ]]; then
    operation_lock_preserve "object restore could not restore the prior writer state" || true
  elif ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}
trap restore_service_state EXIT

# Stop both writers unconditionally, including a service in a restart loop.
compose "$docker_bin" --env-file "$env_file" stop --timeout "$stop_timeout" \
  cleanup-worker backend

backup_root="${OBJECT_BACKUP_DIR:-$HOME/HarborMarketBackups/objects}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_dir="${OBJECT_ROLLBACK_DESTINATION:-$backup_root/pre-restore-$timestamp-$$}"
OBJECT_BACKUP_DESTINATION="$rollback_dir" ENV_FILE="$env_file" \
  "$deploy_dir/backup-objects.sh" >/dev/null
printf 'Automatic object rollback backup: %s\n' "$rollback_dir"

restore_bucket() {
  local backup="$1"
  local manifest_sha256
  manifest_sha256="$(awk -F= '$1 == "manifest_sha256" {print $2}' "$backup/.complete")"
  export OBJECT_TRANSFER_DIR="$backup"
  compose "$docker_bin" --env-file "$env_file" run --rm -T \
    --no-deps --env EXPECTED_MANIFEST_SHA256="$manifest_sha256" \
    --entrypoint /bin/sh minio-client -ec '
      if [ ! -d /transfer/objects ] || [ ! -f /transfer/manifest.tsv ] \
        || [ ! -f /transfer/.complete ]; then
        printf "Object restore source is not mounted inside Docker.\n" >&2
        exit 1
      fi
      actual_manifest_sha256="$(sha256sum /transfer/manifest.tsv)"
      actual_manifest_sha256="${actual_manifest_sha256%% *}"
      if [ "$actual_manifest_sha256" != "$EXPECTED_MANIFEST_SHA256" ]; then
        printf "Docker-mounted object manifest changed before restore.\n" >&2
        exit 1
      fi
      mc alias set harbor http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
      mc mirror --overwrite --remove --preserve /transfer/objects "harbor/$STORAGE_BUCKET"
      mc ls --recursive --json "harbor/$STORAGE_BUCKET" >/tmp/harbor-market-object-list.json
      expected_count="$(wc -l </transfer/manifest.tsv)"
      actual_count="$(wc -l </tmp/harbor-market-object-list.json)"
      if [ "$actual_count" -ne "$expected_count" ]; then
        printf "Restored bucket object count differs: expected %s, got %s\n" \
          "$expected_count" "$actual_count" >&2
        exit 1
      fi
      tab="$(printf "\t")"
      while IFS="$tab" read -r expected_hash expected_size object_key; do
        [ -n "$object_key" ] || continue
        mc cat "harbor/$STORAGE_BUCKET/$object_key" >/tmp/harbor-market-verify-object
        actual_hash="$(sha256sum /tmp/harbor-market-verify-object)"
        actual_hash="${actual_hash%% *}"
        actual_size="$(wc -c </tmp/harbor-market-verify-object)"
        if [ "$actual_hash" != "$expected_hash" ] \
          || [ "$actual_size" -ne "$expected_size" ]; then
          printf "Restored object failed verification: %s\n" "$object_key" >&2
          exit 1
        fi
      done </transfer/manifest.tsv
      rm -f /tmp/harbor-market-verify-object
    '
}

restart_allowed=false
if ! restore_bucket "$source_dir"; then
  printf 'Selected object restore failed; restoring the automatic rollback backup.\n' >&2
  if restore_bucket "$rollback_dir"; then
    restart_allowed=true
    printf 'Original object bucket restored from %s.\n' "$rollback_dir" >&2
    exit 1
  fi
  restart_allowed=false
  printf 'CRITICAL: automatic object rollback also failed.\n' >&2
  exit 1
fi

restart_allowed=true
printf 'Object restore completed from %s.\n' "$source_dir"
printf 'Keep the rollback backup until database/media consistency is verified: %s\n' \
  "$rollback_dir"
