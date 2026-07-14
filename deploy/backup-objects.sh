#!/usr/bin/env bash
set -euo pipefail

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

backup_root="${OBJECT_BACKUP_DIR:-$HOME/HarborMarketBackups/objects}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${OBJECT_BACKUP_DESTINATION:-$backup_root/objects-$timestamp}"
destination_parent="$(dirname "$destination")"
partial="$destination.partial.$$"

umask 077
if [[ ! -d "$destination_parent" ]]; then
  mkdir -p "$destination_parent"
  chmod 700 "$destination_parent"
fi
if [[ -e "$destination" ]]; then
  printf 'Refusing to overwrite an existing object backup: %s\n' "$destination" >&2
  exit 2
fi
mkdir -p "$partial/objects"
mount_probe="harbor-market-object-backup-$timestamp-$$-$RANDOM"
printf '%s\n' "$mount_probe" >"$partial/.mount-probe"
chmod 600 "$partial/.mount-probe"

cleanup_partial() {
  rm -rf -- "$partial"
}
trap cleanup_partial EXIT

configured_bucket="$(
  compose "$docker_bin" --env-file "$env_file" config --format json \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["services"]["backend"]["environment"]["STORAGE_BUCKET"])'
)"

export OBJECT_TRANSFER_DIR="$partial"
compose "$docker_bin" --env-file "$env_file" run --rm -T \
  --no-deps --env OBJECT_TRANSFER_PROBE="$mount_probe" \
  --entrypoint /bin/sh minio-client -ec '
    if [ ! -f /transfer/.mount-probe ] \
      || [ "$(cat /transfer/.mount-probe)" != "$OBJECT_TRANSFER_PROBE" ]; then
      printf "Object backup directory is not shared with the Docker engine.\n" >&2
      exit 1
    fi
    rm /transfer/.mount-probe
    mc alias set harbor http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
    mc mirror --overwrite --preserve "harbor/$STORAGE_BUCKET" /transfer/objects
  '
if [[ -e "$partial/.mount-probe" ]]; then
  printf 'Object backup directory failed the Docker write-back probe.\n' >&2
  exit 1
fi

# Record a deterministic content manifest. The completion marker is written
# only after every mirrored object has been hashed successfully.
python3 - "$partial" <<'PY'
from __future__ import annotations

import hashlib
import pathlib
import sys

backup = pathlib.Path(sys.argv[1])
objects = backup / "objects"
entries: list[tuple[str, int, str]] = []
total_bytes = 0

for path in sorted(objects.rglob("*"), key=lambda item: item.relative_to(objects).as_posix()):
    if path.is_symlink():
        raise SystemExit(f"Refusing to back up symlink: {path}")
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

manifest = backup / "manifest.tsv"
with manifest.open("w", encoding="utf-8", newline="\n") as stream:
    for digest, size, relative in entries:
        stream.write(f"{digest}\t{size}\t{relative}\n")

manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
(backup / "manifest.summary").write_text(
    f"manifest_sha256={manifest_digest}\n"
    f"object_count={len(entries)}\n"
    f"total_bytes={total_bytes}\n",
    encoding="utf-8",
)
PY

manifest_sha256="$(awk -F= '$1 == "manifest_sha256" {print $2}' "$partial/manifest.summary")"
object_count="$(awk -F= '$1 == "object_count" {print $2}' "$partial/manifest.summary")"
total_bytes="$(awk -F= '$1 == "total_bytes" {print $2}' "$partial/manifest.summary")"
printf 'format=minio-mirror-sha256-v2\nbucket=%s\nmanifest=manifest.tsv\nmanifest_sha256=%s\nobject_count=%s\ntotal_bytes=%s\ncompleted_at=%s\n' \
  "$configured_bucket" "$manifest_sha256" "$object_count" "$total_bytes" "$timestamp" \
  >"$partial/.complete"
rm "$partial/manifest.summary"
chmod -R u+rwX,go-rwx "$partial"

mv "$partial" "$destination"
trap - EXIT
printf '%s\n' "$destination"
