#!/usr/bin/env bash
set -u

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
env_file="$project_dir/.env"
check_interval="${HARBOR_MARKET_CHECK_INTERVAL_SECONDS:-60}"
app_port="$(sed -n 's/^APP_PORT=//p' "$env_file" | tail -1)"
app_port="${app_port:-8080}"
health_url="http://127.0.0.1:$app_port/api/v1/health"

if [[ ! "$check_interval" =~ ^[1-9][0-9]*$ || ! "$app_port" =~ ^[0-9]+$ ]]; then
  printf 'Invalid monitor interval or APP_PORT.\n' >&2
  exit 2
fi

start_stack() {
  if ! start_docker_engine "$docker_bin"; then
    printf '%s Docker engine recovery failed; retrying.\n' "$(date -u +%FT%TZ)" >&2
    return 1
  fi

  compose "$docker_bin" --env-file "$env_file" \
    up --detach --remove-orphans --no-build >/dev/null
}

while true; do
  if ! "$docker_bin" info >/dev/null 2>&1; then
    printf '%s Docker engine unavailable; starting Colima and the stack.\n' \
      "$(date -u +%FT%TZ)"
    start_stack || true
  elif ! /usr/bin/curl --fail --silent --show-error --max-time 10 \
    "$health_url" >/dev/null 2>&1; then
    printf '%s Application health check failed; reconciling Compose.\n' \
      "$(date -u +%FT%TZ)"
    if start_stack; then
      sleep 15
      if ! /usr/bin/curl --fail --silent --show-error --max-time 10 \
        "$health_url" >/dev/null 2>&1; then
        printf '%s Restarting the frontend and backend services.\n' \
          "$(date -u +%FT%TZ)"
        compose "$docker_bin" --env-file "$env_file" \
          restart backend frontend >/dev/null || true
      fi
    fi
  fi

  sleep "$check_interval"
done
