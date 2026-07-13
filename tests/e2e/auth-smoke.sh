#!/usr/bin/env bash
set -euo pipefail

base_url="${1:-${HARBOR_MARKET_BASE_URL:-}}"
if [[ -z "$base_url" ]]; then
  printf 'Usage: %s <https://public-host>\n' "$0" >&2
  exit 2
fi
base_url="${base_url%/}"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/harbor-market-e2e.XXXXXX")"
cookies="$work_dir/cookies.txt"
response="$work_dir/response.json"
trap 'rm -rf "$work_dir"' EXIT

timestamp="$(date +%s)"
username="smoke_${timestamp}_$$"
password="HarborSmoke-${timestamp}-Check!"
payload="$(printf '{\"username\":\"%s\",\"password\":\"%s\"}' \
  "$username" "$password")"

request() {
  /usr/bin/curl --silent --show-error --max-time 30 \
    --output "$response" --write-out '%{http_code}' "$@"
}

expect_code() {
  local expected="$1"
  local actual="$2"
  local checkpoint="$3"
  if [[ "$actual" != "$expected" ]]; then
    printf '%s: expected HTTP %s, received %s\n' \
      "$checkpoint" "$expected" "$actual" >&2
    sed -n '1,20p' "$response" >&2
    exit 1
  fi
  printf '%s=HTTP_%s\n' "$checkpoint" "$actual"
}

code="$(request "$base_url/api/v1/health")"
expect_code 200 "$code" health
grep -Fq '"database":"ok"' "$response"

code="$(request "$base_url/register")"
expect_code 200 "$code" registration_ui
grep -Fq 'Harbor Market' "$response"

code="$(request -H 'Content-Type: application/json' --data-binary "$payload" \
  "$base_url/api/v1/auth/register")"
expect_code 201 "$code" registration

code="$(request --cookie-jar "$cookies" -H 'Content-Type: application/json' \
  --data-binary "$payload" "$base_url/api/v1/auth/login")"
expect_code 200 "$code" login

code="$(request --cookie "$cookies" "$base_url/api/v1/auth/me")"
expect_code 200 "$code" authenticated_me
grep -Fq "\"username\":\"$username\"" "$response"

code="$(request --cookie "$cookies" --cookie-jar "$cookies" --request POST \
  "$base_url/api/v1/auth/logout")"
expect_code 200 "$code" logout

code="$(request --cookie "$cookies" "$base_url/api/v1/auth/me")"
expect_code 401 "$code" post_logout_me

printf 'Harbor Market end-to-end auth smoke test passed.\n'
