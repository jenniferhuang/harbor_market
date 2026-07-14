#!/usr/bin/env bash
set -u

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The watchdog must always contend as an independent lock owner. In
# particular, do not inherit a deploy/restore token if it was started from an
# operator shell that currently owns maintenance mode.
unset HARBOR_MARKET_OPERATION_LOCK_TOKEN
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
env_file="${ENV_FILE:-$project_dir/.env}"
if [[ ! -r "$env_file" ]]; then
  printf 'Missing deployment environment file: %s\n' "$env_file" >&2
  exit 1
fi
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

watcher_finish() {
  local status="$?"
  trap - EXIT INT TERM
  if ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}
trap watcher_finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while true; do
  if ! operation_lock_try_acquire watchdog-reconcile; then
    printf '%s Maintenance operation active; skipping reconciliation (%s).\n' \
      "$(date -u +%FT%TZ)" "$(operation_lock_describe)"
    sleep "$check_interval"
    continue
  fi

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

  if ! operation_lock_release; then
    printf '%s Watchdog could not safely release its operation lock.\n' \
      "$(date -u +%FT%TZ)" >&2
    exit 1
  fi

  sleep "$check_interval"
done
