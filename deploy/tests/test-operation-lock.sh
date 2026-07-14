#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
common_script="$deploy_dir/mac-common.sh"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/harbor-market-deploy-test.XXXXXX")"
trap 'rm -rf -- "$test_root"' EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

write_rendered_config() {
  local destination="$1"
  local environment="$2"
  local postgres_password="$3"
  local auth_secret="$4"
  local root_user="$5"
  local root_password="$6"
  local app_user="$7"
  local app_password="$8"
  local storage_backend="${9:-minio}"
  printf '{"services":{"backend":{"environment":{"ENVIRONMENT":"%s","AUTH_SECRET_KEY":"%s","STORAGE_BACKEND":"%s","STORAGE_ACCESS_KEY":"%s","STORAGE_SECRET_KEY":"%s"}},"db":{"environment":{"POSTGRES_PASSWORD":"%s"}},"minio":{"environment":{"MINIO_ROOT_USER":"%s","MINIO_ROOT_PASSWORD":"%s"}}}}\n' \
    "$environment" "$auth_secret" "$storage_backend" "$app_user" "$app_password" \
    "$postgres_password" "$root_user" "$root_password" >"$destination"
}

export PROJECT_DIR="$test_root/project"
mkdir -p "$PROJECT_DIR"
# shellcheck source=../mac-common.sh
source "$common_script"

valid_config="$test_root/valid-config.json"
write_rendered_config "$valid_config" production pg-random-value auth-random-value \
  minio-root root-random-value minio-app app-random-value
validate_production_env "$valid_config" \
  || fail 'valid rendered production environment was rejected'

resolved_override_config="$test_root/resolved-override-config.json"
# This is the value Compose renders after dotenv quote/comment handling and
# shell-variable precedence, regardless of whether the raw .env looked safe.
write_rendered_config "$resolved_override_config" production \
  replace-with-a-generated-password auth-random-value \
  minio-root root-random-value minio-app app-random-value
if validate_production_env "$resolved_override_config" >/dev/null 2>&1; then
  fail 'a rendered placeholder override was accepted'
fi

same_user_config="$test_root/same-user-config.json"
write_rendered_config "$same_user_config" production pg-random-value auth-random-value \
  shared-user root-random-value shared-user app-random-value
if validate_production_env "$same_user_config" >/dev/null 2>&1; then
  fail 'rendered identical MinIO root and application users were accepted'
fi

same_secret_config="$test_root/same-secret-config.json"
write_rendered_config "$same_secret_config" production pg-random-value auth-random-value \
  minio-root shared-secret minio-app shared-secret
if validate_production_env "$same_secret_config" >/dev/null 2>&1; then
  fail 'rendered identical MinIO root and application secrets were accepted'
fi

nonproduction_config="$test_root/nonproduction-config.json"
write_rendered_config "$nonproduction_config" development \
  pg-random-value auth-random-value \
  minio-root root-random-value minio-app app-random-value
if validate_production_env "$nonproduction_config" >/dev/null 2>&1; then
  fail 'mac deployment accepted a rendered non-production environment'
fi

disabled_storage_config="$test_root/disabled-storage-config.json"
write_rendered_config "$disabled_storage_config" production \
  pg-random-value auth-random-value \
  minio-root root-random-value minio-app app-random-value disabled
if validate_production_env "$disabled_storage_config" >/dev/null 2>&1; then
  fail 'mac deployment accepted a non-MinIO production storage backend'
fi

operation_lock_acquire test-parent
[[ -d "$operation_lock_dir" ]] || fail 'operation lock directory was not created'

if (
  unset HARBOR_MARKET_OPERATION_LOCK_TOKEN
  # shellcheck source=../mac-common.sh
  source "$common_script"
  operation_lock_try_acquire competing-operation
); then
  fail 'an independent operation acquired an owned lock'
fi

(
  # The exported owner token makes a nested restore-style process join.
  # shellcheck source=../mac-common.sh
  source "$common_script"
  operation_lock_acquire nested-operation
  [[ "$operation_lock_joined" == true ]] || fail 'nested operation did not join'
  operation_lock_release
)
[[ -d "$operation_lock_dir" ]] || fail 'nested release removed the parent lock'

operation_lock_release
[[ ! -e "$operation_lock_dir" ]] || fail 'owner release did not remove the lock'

operation_lock_acquire preserve-parent
(
  # shellcheck source=../mac-common.sh
  source "$common_script"
  operation_lock_acquire preserve-child
  operation_lock_preserve 'deterministic fail-closed test' 2>/dev/null
  operation_lock_release
)
operation_lock_release 2>/dev/null
[[ -f "$operation_lock_dir/preserve" ]] \
  || fail 'nested fail-closed operation did not preserve the parent lock'

printf 'Deploy operation-lock and production-environment tests passed.\n'
