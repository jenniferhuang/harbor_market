#!/usr/bin/env bash
set -euo pipefail

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

release_root="${RELEASE_BACKUP_DIR:-$HOME/HarborMarketBackups/releases}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${RELEASE_BACKUP_DESTINATION:-$release_root/release-$timestamp}"
destination_parent="$(dirname "$destination")"
partial="$destination.partial.$$"

backend_was_running=false
worker_was_running=false
backend_container_id=""
worker_container_id=""
backup_completed=false

finish() {
  local status="$?"
  local restart_failed=false
  trap - EXIT INT TERM
  if [[ "$backup_completed" != true && -e "$partial" ]]; then
    rm -rf -- "$partial"
  fi
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

  if [[ "$restart_failed" == true ]]; then
    # The watchdog must not race in and create replacement writers while the
    # operator determines why the exact prior containers could not restart.
    operation_lock_preserve "backup could not restore the prior writer state" || true
  elif ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}

operation_lock_acquire backup-release
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

umask 077
if [[ ! -d "$destination_parent" ]]; then
  mkdir -p "$destination_parent"
  chmod 700 "$destination_parent"
fi
if [[ -e "$destination" ]]; then
  printf 'Refusing to overwrite an existing release backup: %s\n' "$destination" >&2
  exit 2
fi
mkdir -p "$partial"

service_is_running() {
  local service="$1"
  local container_id
  container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q "$service")" || return 1
  [[ -n "$container_id" ]] || return 1
  [[ "$("$docker_bin" inspect --format '{{.State.Running}}' "$container_id")" == true ]]
}

backend_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q backend)"
worker_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q cleanup-worker)"
if service_is_running backend; then
  backend_was_running=true
fi
if service_is_running cleanup-worker; then
  worker_was_running=true
fi

# Stop both writers unconditionally. This also catches a container that happens
# to be between restart attempts while its initial state is sampled above.
compose "$docker_bin" --env-file "$env_file" stop --timeout "$stop_timeout" \
  cleanup-worker backend

DB_BACKUP_DESTINATION="$partial/database.sql.gz" ENV_FILE="$env_file" \
  "$deploy_dir/backup-db.sh" >/dev/null
OBJECT_BACKUP_DESTINATION="$partial/object-store" ENV_FILE="$env_file" \
  "$deploy_dir/backup-objects.sh" >/dev/null

database="$(awk -F= '$1 == "database" {print $2}' "$partial/database.sql.gz.metadata")"
database_sha256="$(awk -F= '$1 == "sha256" {print $2}' "$partial/database.sql.gz.metadata")"
bucket="$(awk -F= '$1 == "bucket" {print $2}' "$partial/object-store/.complete")"
manifest_sha256="$(
  awk -F= '$1 == "manifest_sha256" {print $2}' "$partial/object-store/.complete"
)"
printf 'format=harbor-market-release-v1\ndatabase=%s\ndatabase_file=database.sql.gz\ndatabase_sha256=%s\nbucket=%s\nobject_directory=object-store\nobject_manifest_sha256=%s\ncompleted_at=%s\n' \
  "$database" "$database_sha256" "$bucket" "$manifest_sha256" "$timestamp" \
  >"$partial/.complete"
chmod -R u+rwX,go-rwx "$partial"

mv "$partial" "$destination"
backup_completed=true
printf '%s\n' "$destination"
