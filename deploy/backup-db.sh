#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
env_file="$project_dir/.env"
backup_dir="${BACKUP_DIR:-$HOME/HarborMarketBackups}"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$backup_dir/xiangyue_xiamen-$timestamp.sql.gz"
compose "$docker_bin" --env-file "$env_file" exec -T db \
  sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip -9 >"$destination"
chmod 600 "$destination"
find "$backup_dir" -type f -name 'xiangyue_xiamen-*.sql.gz' -mtime +14 -delete
printf '%s\n' "$destination"
