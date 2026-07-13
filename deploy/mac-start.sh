#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
start_docker_engine "$docker_bin"
compose "$docker_bin" --env-file "$project_dir/.env" up --detach --remove-orphans
