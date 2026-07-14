#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fake_docker="$deploy_dir/tests/fixtures/docker-unhealthy-worker.sh"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/harbor-market-verify-test.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT

printf 'APP_PORT=8080\n' >"$test_root/.env"

if PROJECT_DIR="$test_root" ENV_FILE="$test_root/.env" DOCKER_BIN="$fake_docker" \
  /bin/bash "$deploy_dir/verify.sh" >"$test_root/stdout" 2>"$test_root/stderr"; then
  printf 'FAIL: verification accepted an unhealthy cleanup worker.\n' >&2
  exit 1
fi

if ! grep -q 'Object cleanup worker is not healthy: running:unhealthy' \
  "$test_root/stderr"; then
  printf 'FAIL: verification did not report the unhealthy cleanup worker.\n' >&2
  sed -n '1,120p' "$test_root/stderr" >&2
  exit 1
fi

printf 'Cleanup-worker health verification test passed.\n'
