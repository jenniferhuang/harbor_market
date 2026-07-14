#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)" || {
  printf 'Docker CLI was not found.\n' >&2
  exit 1
}

stop_deploy_writers() {
  local service
  local container_ids
  local project_container_ids
  local working_dir_container_ids
  local project_name="${COMPOSE_PROJECT_NAME:-}"
  local fallback_failed=false

  if compose "$docker_bin" --env-file "$project_dir/.env" stop --timeout 60 \
    cleanup-worker backend >/dev/null 2>&1; then
    return 0
  fi

  if [[ -z "$project_name" && -r "$project_dir/.env" ]]; then
    project_name="$(
      awk -F= '$1 == "COMPOSE_PROJECT_NAME" { value = substr($0, index($0, "=") + 1) }
        END { print value }' "$project_dir/.env"
    )"
  fi
  project_name="${project_name:-harbor-market}"

  # A broken or incompatible target Compose file must not prevent shutdown.
  # Locate Compose-owned writer containers by immutable Docker labels instead.
  for service in cleanup-worker backend; do
    working_dir_container_ids=""
    project_container_ids=""
    if ! working_dir_container_ids="$(
      "$docker_bin" ps -q \
        --filter "label=com.docker.compose.project.working_dir=$project_dir" \
        --filter "label=com.docker.compose.service=$service"
    )"; then
      fallback_failed=true
    fi
    if ! project_container_ids="$(
      "$docker_bin" ps -q \
        --filter "label=com.docker.compose.project=$project_name" \
        --filter "label=com.docker.compose.service=$service"
    )"; then
      fallback_failed=true
    fi
    container_ids="$(
      printf '%s\n%s\n' "$working_dir_container_ids" "$project_container_ids" \
        | awk 'NF' | sort -u
    )"
    if [[ -n "$container_ids" ]] \
      && ! "$docker_bin" stop --timeout 60 $container_ids >/dev/null; then
      fallback_failed=true
    fi
  done

  [[ "$fallback_failed" == false ]]
}

deploy_verified=false
deploy_finish() {
  local status="$?"
  trap - EXIT INT TERM
  if [[ "$deploy_verified" != true ]]; then
    # The active checkout and/or Compose state may already have changed. Keep
    # automatic reconciliation out until the orchestrator restores the reviewed
    # revision and matching data pair.
    if ! stop_deploy_writers; then
      printf 'CRITICAL: deployment writers could not be stopped.\n' >&2
    fi
    operation_lock_preserve "deployment did not pass verification; rollback is required" \
      || true
    status=1
  elif ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}

operation_lock_acquire mac-deploy
trap deploy_finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_docker_engine "$docker_bin"

env_file="$project_dir/.env"
if [[ ! -f "$env_file" ]]; then
  umask 077
  postgres_password="$(openssl rand -hex 24)"
  auth_secret="$(openssl rand -hex 48)"
  minio_root_user="harbor-market-$(openssl rand -hex 8)"
  minio_root_password="$(openssl rand -hex 32)"
  storage_access_key="harbor-market-app-$(openssl rand -hex 8)"
  storage_secret_key="$(openssl rand -hex 32)"
  cat >"$env_file" <<ENV
COMPOSE_PROJECT_NAME=harbor-market
ENVIRONMENT=production
APP_PORT=8080
POSTGRES_DB=xiangyue_xiamen
POSTGRES_USER=harbor_market
POSTGRES_PASSWORD=$postgres_password
AUTH_SECRET_KEY=$auth_secret
AUTH_COOKIE_NAME=harbor_market_session
AUTH_COOKIE_PATH=/api/v1
AUTH_COOKIE_SECURE=true
AUTH_SESSION_TTL_SECONDS=28800
MINIO_ROOT_USER=$minio_root_user
MINIO_ROOT_PASSWORD=$minio_root_password
MINIO_CONSOLE_PORT=9001
MINIO_DATA_DIR=./.data/minio
STORAGE_BACKEND=minio
STORAGE_ACCESS_KEY=$storage_access_key
STORAGE_SECRET_KEY=$storage_secret_key
STORAGE_BUCKET=harbor-market-products
UPLOAD_MAX_BYTES=5242880
OBJECT_CLEANUP_INTERVAL_SECONDS=60
ALLOWED_HOSTS=localhost,127.0.0.1,app.hermes-node.com,*.trycloudflare.com
TRUST_PROXY_HEADERS=true
ENV
fi
chmod 600 "$env_file"

ensure_env_value() {
  local key="$1"
  local value="$2"
  if ! grep -q "^${key}=" "$env_file"; then
    printf '%s=%s\n' "$key" "$value" >>"$env_file"
  fi
}

migrate_legacy_cookie_path() {
  if ! grep -qx 'AUTH_COOKIE_PATH=/api/v1/auth' "$env_file"; then
    return
  fi
  local backup_path="$env_file.before-cookie-path-$(date -u +%Y%m%dT%H%M%SZ)"
  cp -p "$env_file" "$backup_path"
  local temporary
  temporary="$(mktemp "$env_file.XXXXXX")"
  awk '
    $0 == "AUTH_COOKIE_PATH=/api/v1/auth" { print "AUTH_COOKIE_PATH=/api/v1"; next }
    { print }
  ' "$env_file" >"$temporary"
  chmod 600 "$temporary"
  mv "$temporary" "$env_file"
  printf 'Migrated legacy cookie path; environment backup: %s\n' "$backup_path"
}

# Existing installations predate object storage. Add only missing values and never
# overwrite operator-managed settings or credentials.
ensure_env_value MINIO_ROOT_USER "harbor-market-$(openssl rand -hex 8)"
ensure_env_value MINIO_ROOT_PASSWORD "$(openssl rand -hex 32)"
migrate_legacy_cookie_path
ensure_env_value AUTH_COOKIE_PATH /api/v1
ensure_env_value ENVIRONMENT production
ensure_env_value MINIO_CONSOLE_PORT 9001
ensure_env_value MINIO_DATA_DIR ./.data/minio
ensure_env_value STORAGE_BACKEND minio
ensure_env_value STORAGE_ACCESS_KEY "harbor-market-app-$(openssl rand -hex 8)"
ensure_env_value STORAGE_SECRET_KEY "$(openssl rand -hex 32)"
ensure_env_value STORAGE_BUCKET harbor-market-products
ensure_env_value UPLOAD_MAX_BYTES 5242880
ensure_env_value OBJECT_CLEANUP_INTERVAL_SECONDS 60
ensure_env_value TRUST_PROXY_HEADERS true

compose "$docker_bin" --env-file "$env_file" config --quiet
validate_production_env <(
  compose "$docker_bin" --env-file "$env_file" config --format json
)
compose "$docker_bin" --env-file "$env_file" build backend
compose "$docker_bin" --env-file "$env_file" build frontend
compose "$docker_bin" --env-file "$env_file" up --detach --no-build --remove-orphans

/bin/bash "$deploy_dir/verify.sh"
deploy_verified=true
