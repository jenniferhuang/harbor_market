#!/usr/bin/env bash
set -euo pipefail

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)"
env_file="${ENV_FILE:-$project_dir/.env}"
if [[ ! -r "$env_file" ]]; then
  printf 'Missing deployment environment file: %s\n' "$env_file" >&2
  exit 1
fi
app_port="$(sed -n 's/^APP_PORT=//p' "$env_file" | tail -1)"
app_port="${app_port:-8080}"
if [[ ! "$app_port" =~ ^[0-9]+$ ]]; then
  printf 'APP_PORT must be numeric.\n' >&2
  exit 2
fi
health_url="http://127.0.0.1:$app_port/api/v1/health"

compose "$docker_bin" --env-file "$env_file" ps

minio_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q minio)"
if [[ -z "$minio_container_id" ]]; then
  printf 'MinIO container is missing.\n' >&2
  exit 1
fi
minio_health=""
for attempt in $(seq 1 30); do
  minio_health="$(
    "$docker_bin" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$minio_container_id"
  )"
  if [[ "$minio_health" == healthy ]]; then
    break
  fi
  sleep 2
done
if [[ "$minio_health" != healthy ]]; then
  compose "$docker_bin" --env-file "$env_file" logs --tail=200 minio
  exit 1
fi
minio_init_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -aq minio-init)"
if [[ -z "$minio_init_container_id" ]] \
  || [[ "$("$docker_bin" inspect --format '{{.State.Status}}:{{.State.ExitCode}}' \
    "$minio_init_container_id")" != "exited:0" ]]; then
  compose "$docker_bin" --env-file "$env_file" logs --tail=200 minio-init
  exit 1
fi

cleanup_worker_container_id="$(
  compose "$docker_bin" --env-file "$env_file" ps -q cleanup-worker
)"
if [[ -z "$cleanup_worker_container_id" ]]; then
  printf 'Object cleanup worker container is missing.\n' >&2
  exit 1
fi
cleanup_worker_state=""
for attempt in $(seq 1 60); do
  cleanup_worker_state="$(
    "$docker_bin" inspect \
      --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
      "$cleanup_worker_container_id"
  )"
  if [[ "$cleanup_worker_state" == "running:healthy" ]]; then
    break
  fi
  if [[ "$cleanup_worker_state" == *:unhealthy \
    || "$cleanup_worker_state" == exited:* \
    || "$cleanup_worker_state" == dead:* ]]; then
    break
  fi
  sleep 2
done
if [[ "$cleanup_worker_state" != "running:healthy" ]]; then
  compose "$docker_bin" --env-file "$env_file" logs --tail=200 cleanup-worker
  printf 'Object cleanup worker is not healthy: %s\n' \
    "${cleanup_worker_state:-unknown}" >&2
  exit 1
fi

for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error "$health_url" >/tmp/harbor-market-health.json; then
    break
  fi
  if [[ "$attempt" -eq 60 ]]; then
    compose "$docker_bin" --env-file "$env_file" logs --tail=200
    exit 1
  fi
  sleep 2
done

python3 -m json.tool /tmp/harbor-market-health.json
curl --fail --silent --show-error "http://127.0.0.1:$app_port/register" >/dev/null

storage_backend="$(
  compose "$docker_bin" --env-file "$env_file" config --format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["backend"]["environment"]["STORAGE_BACKEND"])'
)"
if [[ "$storage_backend" != minio ]]; then
  printf 'Production verification requires STORAGE_BACKEND=minio; got %s.\n' \
    "${storage_backend:-missing}" >&2
  exit 1
fi

privacy_status="$(
  compose "$docker_bin" --env-file "$env_file" run --rm -T \
    --no-deps \
    --entrypoint /bin/sh minio-client -ec '
      mc alias set harbor http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
      mc anonymous get "harbor/$STORAGE_BUCKET"
    '
)"
if [[ "$privacy_status" != *'is `private`'* ]]; then
  printf 'MinIO bucket privacy verification failed: %s\n' "$privacy_status" >&2
  exit 1
fi

compose "$docker_bin" --env-file "$env_file" exec -T backend python - <<'PY'
from uuid import uuid4

from app.main import app

storage = app.state.object_storage
object_name = f"verification/roundtrip/{uuid4().hex}.txt"
payload = b"harbor-market-storage-verification"
try:
    created = storage.put(object_name, payload, content_type="text/plain")
    if created.size != len(payload):
        raise RuntimeError("MinIO round-trip stat size mismatch")
    if storage.get(object_name, max_bytes=len(payload)) != payload:
        raise RuntimeError("MinIO round-trip payload mismatch")
finally:
    storage.delete(object_name)
PY
printf 'Harbor Market local verification passed.\n'
