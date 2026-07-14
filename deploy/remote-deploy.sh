#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-jennifer.huang@192.168.1.33}"
SSH_KEY="${SSH_KEY:-/Users/jennifer.huang/.ssh/id_rsa}"
REMOTE_DIR="${REMOTE_DIR:-/Users/jennifer.huang/jennifer/harbor_market}"
REPOSITORY="${REPOSITORY:-git@github.com:jenniferhuang/harbor_market.git}"
REVISION="${REVISION:-}"

if [[ ! "$REVISION" =~ ^[0-9a-fA-F]{40}$ ]]; then
  printf 'REVISION must be the reviewed 40-character Git commit SHA.\n' >&2
  exit 2
fi
REVISION="$(printf '%s' "$REVISION" | tr '[:upper:]' '[:lower:]')"

ssh_args=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15)

ssh "${ssh_args[@]}" "$REMOTE_HOST" /bin/bash -s -- "$REMOTE_DIR" "$REPOSITORY" "$REVISION" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
repository="$2"
revision="$3"

assert_clean_worktree() {
  local dirty
  dirty="$(git -C "$remote_dir" status --porcelain --untracked-files=all)"
  if [[ -n "$dirty" ]]; then
    printf 'Refusing deployment because the remote worktree has local changes:\n%s\n' \
      "$dirty" >&2
    return 1
  fi
}

stop_remote_writers() {
  local docker_bin="$1"
  local service
  local container_ids
  local project_container_ids
  local working_dir_container_ids
  local project_name="${COMPOSE_PROJECT_NAME:-}"
  local fallback_failed=false

  if compose "$docker_bin" --env-file "$remote_dir/.env" stop --timeout 60 \
    cleanup-worker backend >/dev/null 2>&1; then
    return 0
  fi
  if [[ -z "$project_name" && -r "$remote_dir/.env" ]]; then
    project_name="$(
      awk -F= '$1 == "COMPOSE_PROJECT_NAME" { value = substr($0, index($0, "=") + 1) }
        END { print value }' "$remote_dir/.env"
    )"
  fi
  project_name="${project_name:-harbor-market}"

  for service in cleanup-worker backend; do
    working_dir_container_ids=""
    project_container_ids=""
    if ! working_dir_container_ids="$(
      "$docker_bin" ps -q \
        --filter "label=com.docker.compose.project.working_dir=$remote_dir" \
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

remote_lock_owned=false
remote_checkout_started=false
remote_deploy_verified=false
remote_finish() {
  local status="$?"
  local docker_bin=""
  trap - EXIT INT TERM
  if [[ "$remote_lock_owned" == true \
    && "$remote_checkout_started" == true \
    && "$remote_deploy_verified" != true ]]; then
    if docker_bin="$(find_docker 2>/dev/null)"; then
      if ! stop_remote_writers "$docker_bin"; then
        printf 'CRITICAL: remote deployment writers could not be stopped.\n' >&2
      fi
    else
      printf 'CRITICAL: Docker CLI unavailable; writers could not be stopped.\n' >&2
    fi
    operation_lock_preserve \
      "remote deployment changed the checkout but did not pass verification" || true
    status=1
  elif [[ "$remote_lock_owned" == true ]] && ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}

export PROJECT_DIR="$remote_dir"
export HARBOR_MARKET_OPERATION_LOCK_DIR="$remote_dir/.data/operation.lock"
unset HARBOR_MARKET_OPERATION_LOCK_TOKEN

if [[ -d "$remote_dir/.git" ]]; then
  assert_clean_worktree
  if [[ ! -r "$remote_dir/deploy/mac-common.sh" ]]; then
    printf 'Existing deployment has no readable operation-lock helper.\n' >&2
    exit 1
  fi
  # Source the currently installed helper before checkout. This keeps backup,
  # restore, watchdog, checkout, build, startup, and verification on one lock.
  # shellcheck source=/dev/null
  source "$remote_dir/deploy/mac-common.sh"
  if ! declare -F operation_lock_acquire >/dev/null \
    || ! declare -F operation_lock_release >/dev/null; then
    printf '%s\n' \
      'Installed revision predates the maintenance lock; stop the legacy watchdog and bootstrap the lock-aware revision manually.' >&2
    exit 1
  fi
  operation_lock_acquire remote-deploy
  remote_lock_owned=true
  trap remote_finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  git -C "$remote_dir" fetch --prune origin
  remote_checkout_started=true
  git -C "$remote_dir" checkout --detach "$revision"
else
  mkdir -p "$(dirname "$remote_dir")"
  git clone --no-checkout "$repository" "$remote_dir"
  git -C "$remote_dir" checkout --detach "$revision"
fi
assert_clean_worktree

actual_revision="$(git -C "$remote_dir" rev-parse HEAD)"
if [[ "$actual_revision" != "$revision" ]]; then
  printf 'Checked out %s instead of requested %s.\n' "$actual_revision" "$revision" >&2
  exit 1
fi
printf 'Deploying reviewed revision %s\n' "$actual_revision"

/bin/bash "$remote_dir/deploy/mac-deploy.sh"
remote_deploy_verified=true
REMOTE
