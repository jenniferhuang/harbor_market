#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 1 || ! -f "$1" ]]; then
  printf 'Usage: %s <backup.sql.gz>\n' "$0" >&2
  exit 2
fi

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
env_file="$project_dir/.env"

printf 'Restoring %s into xiangyue_xiamen. Existing rows may conflict.\n' "$1"
gzip -dc "$1" | compose "$docker_bin" --env-file "$env_file" exec -T db \
  sh -c 'exec psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
