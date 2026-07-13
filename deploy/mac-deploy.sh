#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)" || {
  printf 'Docker CLI was not found.\n' >&2
  exit 1
}
start_docker_engine "$docker_bin"

env_file="$project_dir/.env"
if [[ ! -f "$env_file" ]]; then
  umask 077
  postgres_password="$(openssl rand -hex 24)"
  auth_secret="$(openssl rand -hex 48)"
  cat >"$env_file" <<ENV
COMPOSE_PROJECT_NAME=harbor-market
APP_PORT=8080
POSTGRES_DB=xiangyue_xiamen
POSTGRES_USER=harbor_market
POSTGRES_PASSWORD=$postgres_password
AUTH_SECRET_KEY=$auth_secret
AUTH_COOKIE_NAME=harbor_market_session
AUTH_COOKIE_SECURE=true
AUTH_SESSION_TTL_SECONDS=28800
ALLOWED_HOSTS=localhost,127.0.0.1,app.hermes-node.com,*.trycloudflare.com
ENV
fi
chmod 600 "$env_file"

compose "$docker_bin" --env-file "$env_file" config --quiet
compose "$docker_bin" --env-file "$env_file" up --build --detach --remove-orphans

exec /bin/bash "$deploy_dir/verify.sh"
