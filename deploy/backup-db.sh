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

backup_dir="${BACKUP_DIR:-$HOME/HarborMarketBackups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${DB_BACKUP_DESTINATION:-$backup_dir/xiangyue_xiamen-$timestamp.sql.gz}"
destination_dir="$(dirname "$destination")"
destination_name="$(basename "$destination")"
partial="$destination.partial.$$"
partial_checksum="$destination.sha256.partial.$$"
partial_metadata="$destination.metadata.partial.$$"

umask 077
if [[ ! -d "$destination_dir" ]]; then
  mkdir -p "$destination_dir"
  chmod 700 "$destination_dir"
fi
if [[ -e "$destination" || -e "$destination.sha256" || -e "$destination.metadata" ]]; then
  printf 'Refusing to overwrite an existing database backup: %s\n' "$destination" >&2
  exit 2
fi

cleanup_partial() {
  rm -f "$partial" "$partial_checksum" "$partial_metadata"
}
trap cleanup_partial EXIT

configured_db="$(
  compose "$docker_bin" --env-file "$env_file" config --format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["db"]["environment"]["POSTGRES_DB"])'
)"

# The temporary file and completion metadata are kept on the destination
# filesystem. A failed pg_dump or gzip can therefore never look complete.
compose "$docker_bin" --env-file "$env_file" exec -T db \
  sh -ec 'exec pg_dump --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | gzip -9 >"$partial"
gzip -t "$partial"

checksum="$(shasum -a 256 "$partial" | awk '{print $1}')"
printf '%s  %s\n' "$checksum" "$destination_name" >"$partial_checksum"
printf 'format=postgres-plain-gzip-v2\ndatabase=%s\nfile=%s\nsha256=%s\ncompleted_at=%s\n' \
  "$configured_db" "$destination_name" "$checksum" "$timestamp" >"$partial_metadata"

chmod 600 "$partial" "$partial_checksum" "$partial_metadata"
mv "$partial" "$destination"
mv "$partial_checksum" "$destination.sha256"
mv "$partial_metadata" "$destination.metadata"
trap - EXIT

if [[ -z "${DB_BACKUP_DESTINATION:-}" ]]; then
  find "$backup_dir" -type f -name 'xiangyue_xiamen-*.sql.gz*' -mtime +14 -delete
fi

printf '%s\n' "$destination"
