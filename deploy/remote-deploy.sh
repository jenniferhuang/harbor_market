#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-jennifer.huang@192.168.1.33}"
SSH_KEY="${SSH_KEY:-/Users/jennifer.huang/.ssh/id_rsa}"
REMOTE_DIR="${REMOTE_DIR:-/Users/jennifer.huang/jennifer/harbor_market}"
REPOSITORY="${REPOSITORY:-git@github.com:jenniferhuang/harbor_market.git}"
BRANCH="${BRANCH:-main}"

ssh_args=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=15)

ssh "${ssh_args[@]}" "$REMOTE_HOST" /bin/bash -s -- "$REMOTE_DIR" "$REPOSITORY" "$BRANCH" <<'REMOTE'
set -euo pipefail
remote_dir="$1"
repository="$2"
branch="$3"

if [[ -d "$remote_dir/.git" ]]; then
  git -C "$remote_dir" fetch --prune origin
  git -C "$remote_dir" checkout "$branch"
  git -C "$remote_dir" pull --ff-only origin "$branch"
else
  mkdir -p "$(dirname "$remote_dir")"
  git clone --branch "$branch" "$repository" "$remote_dir"
fi

exec /bin/bash "$remote_dir/deploy/mac-deploy.sh"
REMOTE
