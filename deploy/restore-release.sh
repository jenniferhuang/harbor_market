#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 || "$1" != "--confirm-replace" || ! -d "$2" ]]; then
  printf 'Usage: %s --confirm-replace <release-backup-directory>\n' "$0" >&2
  exit 2
fi

source_dir="$(cd "$2" && pwd)"
if [[ ! -f "$source_dir/.complete" \
  || ! -f "$source_dir/database.sql.gz" \
  || ! -f "$source_dir/database.sql.gz.sha256" \
  || ! -f "$source_dir/database.sql.gz.metadata" \
  || ! -d "$source_dir/object-store/objects" \
  || ! -f "$source_dir/object-store/manifest.tsv" \
  || ! -f "$source_dir/object-store/.complete" ]]; then
  printf 'Release backup is incomplete: %s\n' "$source_dir" >&2
  exit 2
fi

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=mac-common.sh
source "$deploy_dir/mac-common.sh"

docker_bin="$(find_docker)" || {
  printf 'Docker CLI was not found.\n' >&2
  exit 1
}
env_file="${ENV_FILE:-$project_dir/.env}"
if [[ ! -r "$env_file" ]]; then
  printf 'Missing deployment environment file: %s\n' "$env_file" >&2
  exit 1
fi
stop_timeout="${BACKUP_STOP_TIMEOUT_SECONDS:-60}"
if [[ ! "$stop_timeout" =~ ^[0-9]+$ ]] \
  || (( 10#$stop_timeout < 10 || 10#$stop_timeout > 300 )); then
  printf 'BACKUP_STOP_TIMEOUT_SECONDS must be between 10 and 300.\n' >&2
  exit 2
fi

restore_preflight_finish() {
  local status="$?"
  trap - EXIT INT TERM
  if ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}

operation_lock_acquire restore-release
trap restore_preflight_finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

read -r configured_db configured_bucket < <(
  compose "$docker_bin" --env-file "$env_file" config --format json \
    | python3 -c 'import json, sys; config=json.load(sys.stdin); print(config["services"]["db"]["environment"]["POSTGRES_DB"], config["services"]["backend"]["environment"]["STORAGE_BUCKET"])'
)
case "$configured_db" in
  postgres|template0|template1)
    printf 'Refusing to replace protected database: %s\n' "$configured_db" >&2
    exit 2
    ;;
esac

python3 - "$source_dir" "$configured_db" "$configured_bucket" <<'PY'
from __future__ import annotations

import hashlib
import pathlib
import sys

release = pathlib.Path(sys.argv[1])
expected_database = sys.argv[2]
expected_bucket = sys.argv[3]


def read_metadata(path: pathlib.Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw_line:
            raise SystemExit(f"Malformed metadata line in {path}: {raw_line!r}")
        key, value = raw_line.split("=", 1)
        if not key or key in result:
            raise SystemExit(f"Invalid or duplicate metadata key in {path}: {key!r}")
        result[key] = value
    return result


metadata = read_metadata(release / ".complete")
database_metadata = read_metadata(release / "database.sql.gz.metadata")
object_metadata = read_metadata(release / "object-store" / ".complete")

if metadata.get("format") != "harbor-market-release-v1":
    raise SystemExit("Unsupported release backup format")
if metadata.get("database_file") != "database.sql.gz":
    raise SystemExit("Unexpected release database filename")
if metadata.get("object_directory") != "object-store":
    raise SystemExit("Unexpected release object directory")
if metadata.get("database") != expected_database:
    raise SystemExit("Release database does not match the configured database")
if metadata.get("bucket") != expected_bucket:
    raise SystemExit("Release bucket does not match the configured bucket")
if database_metadata.get("format") != "postgres-plain-gzip-v2":
    raise SystemExit("Unsupported release database component format")
if database_metadata.get("database") != expected_database:
    raise SystemExit("Release database component does not match configuration")
if database_metadata.get("file") != "database.sql.gz":
    raise SystemExit("Unexpected release database component filename")
if object_metadata.get("format") != "minio-mirror-sha256-v2":
    raise SystemExit("Unsupported release object component format")
if object_metadata.get("bucket") != expected_bucket:
    raise SystemExit("Release object component does not match configuration")
if metadata.get("database_sha256") != database_metadata.get("sha256"):
    raise SystemExit("Release database checksum does not match component metadata")
if metadata.get("object_manifest_sha256") != object_metadata.get("manifest_sha256"):
    raise SystemExit("Release object checksum does not match component metadata")

database_backup = release / "database.sql.gz"
if database_backup.is_symlink():
    raise SystemExit("Refusing a symlinked release database backup")
database_digest = hashlib.sha256()
with database_backup.open("rb") as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        database_digest.update(chunk)
if database_digest.hexdigest() != metadata.get("database_sha256"):
    raise SystemExit("Release database content failed SHA-256 verification")
sidecar_parts = (release / "database.sql.gz.sha256").read_text(
    encoding="utf-8"
).strip().split(maxsplit=1)
if (
    len(sidecar_parts) != 2
    or sidecar_parts[0] != database_digest.hexdigest()
    or sidecar_parts[1] != "database.sql.gz"
):
    raise SystemExit("Release database checksum sidecar is invalid")

objects = release / "object-store" / "objects"
entries: list[tuple[str, int, str]] = []
total_bytes = 0
for path in sorted(objects.rglob("*"), key=lambda item: item.relative_to(objects).as_posix()):
    if path.is_symlink():
        raise SystemExit(f"Refusing a symlinked release object: {path}")
    if not path.is_file():
        continue
    relative = path.relative_to(objects).as_posix()
    if "\t" in relative or "\n" in relative or "\r" in relative:
        raise SystemExit(f"Unsupported control character in object key: {relative!r}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    entries.append((digest.hexdigest(), size, relative))
    total_bytes += size

expected_manifest = "".join(
    f"{digest}\t{size}\t{relative}\n" for digest, size, relative in entries
).encode()
actual_manifest = (release / "object-store" / "manifest.tsv").read_bytes()
if actual_manifest != expected_manifest:
    raise SystemExit("Release object content does not match manifest.tsv")
if hashlib.sha256(actual_manifest).hexdigest() != metadata.get("object_manifest_sha256"):
    raise SystemExit("Release object manifest failed SHA-256 verification")
if object_metadata.get("object_count") != str(len(entries)):
    raise SystemExit("Release object count does not match component metadata")
if object_metadata.get("total_bytes") != str(total_bytes):
    raise SystemExit("Release object bytes do not match component metadata")
PY
gzip -t "$source_dir/database.sql.gz"

expected_manifest_sha256="$(
  awk -F= '$1 == "manifest_sha256" {print $2}' \
    "$source_dir/object-store/.complete"
)"
export OBJECT_TRANSFER_DIR="$source_dir/object-store"
compose "$docker_bin" --env-file "$env_file" run --rm -T --no-deps \
  --env EXPECTED_MANIFEST_SHA256="$expected_manifest_sha256" \
  --entrypoint /bin/sh minio-client -ec '
    if [ ! -d /transfer/objects ] || [ ! -f /transfer/manifest.tsv ] \
      || [ ! -f /transfer/.complete ]; then
      printf "Release object backup is not mounted inside Docker.\n" >&2
      exit 1
    fi
    actual_manifest_sha256="$(sha256sum /transfer/manifest.tsv)"
    actual_manifest_sha256="${actual_manifest_sha256%% *}"
    if [ "$actual_manifest_sha256" != "$EXPECTED_MANIFEST_SHA256" ]; then
      printf "Docker-mounted release manifest does not match the selected backup.\n" >&2
      exit 1
    fi
  '

service_is_running() {
  local service="$1"
  local container_id
  container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q "$service")" || return 1
  [[ -n "$container_id" ]] || return 1
  [[ "$("$docker_bin" inspect --format '{{.State.Running}}' "$container_id")" == true ]]
}

backend_was_running=false
worker_was_running=false
backend_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q backend)"
worker_container_id="$(compose "$docker_bin" --env-file "$env_file" ps -q cleanup-worker)"
if service_is_running backend; then
  backend_was_running=true
fi
if service_is_running cleanup-worker; then
  worker_was_running=true
fi

restart_allowed=true
restore_service_state() {
  local status="$?"
  local restart_failed=false
  trap - EXIT INT TERM
  if [[ "$restart_allowed" == true ]]; then
    if [[ "$backend_was_running" == true ]]; then
      if ! "$docker_bin" start "$backend_container_id" >/dev/null; then
        restart_failed=true
        status=1
      fi
    fi
    if [[ "$worker_was_running" == true ]]; then
      if ! "$docker_bin" start "$worker_container_id" >/dev/null; then
        restart_failed=true
        status=1
      fi
    fi
  else
    printf 'Release restore did not complete; backend and cleanup worker remain stopped.\n' >&2
    status=1
  fi

  if [[ "$restart_allowed" != true ]]; then
    operation_lock_preserve "paired release restore requires manual recovery" || true
  elif [[ "$restart_failed" == true ]]; then
    operation_lock_preserve "paired release restore could not restore the prior writer state" || true
  elif ! operation_lock_release; then
    status=1
  fi
  exit "$status"
}
trap restore_service_state EXIT

# Stop both writers unconditionally, including a service in a restart loop.
compose "$docker_bin" --env-file "$env_file" stop --timeout "$stop_timeout" \
  cleanup-worker backend

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_root="${RELEASE_ROLLBACK_DESTINATION:-$HOME/HarborMarketBackups/releases/pre-restore-release-$timestamp-$$}"
if [[ -e "$rollback_root" ]]; then
  printf 'Refusing to overwrite an existing release rollback path: %s\n' \
    "$rollback_root" >&2
  exit 2
fi
rollback_parent="$(dirname "$rollback_root")"
if [[ ! -d "$rollback_parent" ]]; then
  mkdir -p "$rollback_parent"
  chmod 700 "$rollback_parent"
fi
mkdir "$rollback_root"
chmod 700 "$rollback_root"

restart_allowed=false
if ! OBJECT_ROLLBACK_DESTINATION="$rollback_root/object-store" ENV_FILE="$env_file" \
  "$deploy_dir/restore-objects.sh" --confirm-replace "$source_dir/object-store"; then
  restart_allowed=false
  printf 'Release restore stopped during the object phase.\n' >&2
  exit 1
fi

if ! DB_ROLLBACK_DESTINATION="$rollback_root/database.sql.gz" ENV_FILE="$env_file" \
  "$deploy_dir/restore-db.sh" --confirm-replace "$source_dir/database.sql.gz"; then
  printf 'Database phase failed; restoring the pre-release object bucket.\n' >&2
  if ! ENV_FILE="$env_file" "$deploy_dir/restore-objects.sh" --confirm-replace \
    "$rollback_root/object-store"; then
    printf 'CRITICAL: paired object rollback failed.\n' >&2
  fi
  restart_allowed=false
  exit 1
fi

database_sha256="$(awk -F= '$1 == "sha256" {print $2}' "$rollback_root/database.sql.gz.metadata")"
manifest_sha256="$(
  awk -F= '$1 == "manifest_sha256" {print $2}' "$rollback_root/object-store/.complete"
)"
printf 'format=harbor-market-release-v1\ndatabase=%s\ndatabase_file=database.sql.gz\ndatabase_sha256=%s\nbucket=%s\nobject_directory=object-store\nobject_manifest_sha256=%s\ncompleted_at=%s\n' \
  "$configured_db" "$database_sha256" "$configured_bucket" "$manifest_sha256" "$timestamp" \
  >"$rollback_root/.complete"
chmod -R u+rwX,go-rwx "$rollback_root"

restart_allowed=true
printf 'Release restore completed from %s.\n' "$source_dir"
printf 'Paired pre-restore rollback release: %s\n' "$rollback_root"
