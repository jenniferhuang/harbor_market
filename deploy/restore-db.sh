#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 || "$1" != "--confirm-replace" || ! -f "$2" ]]; then
  printf 'Usage: %s --confirm-replace <backup.sql.gz>\n' "$0" >&2
  exit 2
fi

source_backup="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
source_checksum="$source_backup.sha256"
source_metadata="$source_backup.metadata"
if [[ ! -f "$source_checksum" || ! -f "$source_metadata" ]]; then
  printf 'Backup is incomplete: matching .sha256 and .metadata files are required.\n' >&2
  exit 2
fi

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

operation_lock_acquire restore-db
trap restore_preflight_finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

configured_db="$(
  compose "$docker_bin" --env-file "$env_file" config --format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["db"]["environment"]["POSTGRES_DB"])'
)"
case "$configured_db" in
  postgres|template0|template1)
    printf 'Refusing to replace protected database: %s\n' "$configured_db" >&2
    exit 2
    ;;
esac

# Validate all completion metadata and the compressed stream before stopping
# services or mutating the target database.
python3 - "$source_backup" "$source_checksum" "$source_metadata" "$configured_db" <<'PY'
from __future__ import annotations

import hashlib
import pathlib
import sys

backup = pathlib.Path(sys.argv[1])
checksum_file = pathlib.Path(sys.argv[2])
metadata_file = pathlib.Path(sys.argv[3])
expected_database = sys.argv[4]


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


metadata = read_metadata(metadata_file)
if metadata.get("format") != "postgres-plain-gzip-v2":
    raise SystemExit("Unsupported database backup format")
if metadata.get("database") != expected_database:
    raise SystemExit(
        f"Backup database {metadata.get('database')!r} does not match configured "
        f"database {expected_database!r}"
    )
if metadata.get("file") != backup.name:
    raise SystemExit("Backup metadata filename does not match the selected file")

sidecar_parts = checksum_file.read_text(encoding="utf-8").strip().split(maxsplit=1)
if len(sidecar_parts) != 2 or sidecar_parts[1] != backup.name:
    raise SystemExit("Malformed database checksum sidecar")

digest = hashlib.sha256()
with backup.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
actual = digest.hexdigest()
if actual != sidecar_parts[0] or actual != metadata.get("sha256"):
    raise SystemExit("Database backup SHA-256 verification failed")
PY
gzip -t "$source_backup"

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
    operation_lock_preserve "database restore requires manual recovery" || true
  elif [[ "$restart_failed" == true ]]; then
    operation_lock_preserve "database restore could not restore the prior writer state" || true
  elif ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}
trap restore_service_state EXIT

# Stop both writers unconditionally, including a service in a restart loop.
compose "$docker_bin" --env-file "$env_file" stop --timeout "$stop_timeout" \
  cleanup-worker backend

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="${BACKUP_DIR:-$HOME/HarborMarketBackups}"
rollback_backup="${DB_ROLLBACK_DESTINATION:-$backup_dir/pre-restore-db-$timestamp-$$.sql.gz}"
DB_BACKUP_DESTINATION="$rollback_backup" ENV_FILE="$env_file" \
  "$deploy_dir/backup-db.sh" >/dev/null
printf 'Automatic database rollback backup: %s\n' "$rollback_backup"

reset_database() {
  compose "$docker_bin" --env-file "$env_file" exec -T db sh -ec '
    case "$POSTGRES_DB" in
      postgres|template0|template1)
        printf "Refusing to replace protected database: %s\n" "$POSTGRES_DB" >&2
        exit 2
        ;;
    esac
    dropdb --if-exists --force --username="$POSTGRES_USER" \
      --maintenance-db=postgres -- "$POSTGRES_DB"
    createdb --username="$POSTGRES_USER" --owner="$POSTGRES_USER" -- "$POSTGRES_DB"
  '
}

restore_database() {
  local backup="$1"
  reset_database || return 1
  if ! gzip -dc "$backup" | compose "$docker_bin" --env-file "$env_file" exec -T db \
    sh -ec 'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'; then
    return 1
  fi
  compose "$docker_bin" --env-file "$env_file" exec -T db \
    sh -ec 'test "$(psql -X -Atq -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1")" = 1'
}

restart_allowed=false
if ! restore_database "$source_backup"; then
  printf 'Selected database restore failed; restoring the automatic rollback backup.\n' >&2
  if restore_database "$rollback_backup"; then
    restart_allowed=true
    printf 'Original database restored from %s.\n' "$rollback_backup" >&2
    exit 1
  fi
  restart_allowed=false
  printf 'CRITICAL: automatic database rollback also failed.\n' >&2
  exit 1
fi

restart_allowed=true
printf 'Database restore completed from %s.\n' "$source_backup"
