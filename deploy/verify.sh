#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
env_file="$project_dir/.env"

compose "$docker_bin" --env-file "$env_file" ps

for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error http://127.0.0.1:8080/api/v1/health >/tmp/harbor-market-health.json; then
    break
  fi
  if [[ "$attempt" -eq 60 ]]; then
    compose "$docker_bin" --env-file "$env_file" logs --tail=200
    exit 1
  fi
  sleep 2
done

python3 -m json.tool /tmp/harbor-market-health.json
curl --fail --silent --show-error http://127.0.0.1:8080/register >/dev/null
printf 'Harbor Market local verification passed.\n'
