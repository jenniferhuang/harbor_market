#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$project_dir/.env"
control_socket="$project_dir/.data/db-ssh.sock"

# Read only connection metadata. Never execute the environment file or print
# database credentials, including when a malformed value causes validation to fail.
metadata="$(python3 - "$env_file" "$control_socket" <<'PY'
import pathlib
import re
import shlex
import sys
from urllib.parse import unquote, urlsplit

path = pathlib.Path(sys.argv[1])
keys = {
    "ENVIRONMENT", "LOCAL_DATABASE_PORT", "REMOTE_DATABASE_PORT",
    "REMOTE_DATABASE_HOST", "REMOTE_DATABASE_SSH_HOST", "APP_PORT", "DATABASE_URL",
    "COMPOSE_DATABASE_URL",
}
values = {}
try:
    lines = path.read_text(encoding="utf-8").splitlines()
except OSError:
    raise SystemExit("Cannot read the project .env file.") from None
for line in lines:
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].lstrip()
    key, separator, raw = line.partition("=")
    key = key.strip()
    if not separator or key not in keys:
        continue
    try:
        parts = shlex.split(raw, comments=True, posix=True)
    except ValueError:
        raise SystemExit(f"Invalid .env value for {key}.") from None
    if len(parts) > 1:
        raise SystemExit(f"Invalid .env value for {key}.")
    values[key] = parts[0] if parts else ""

if values.get("ENVIRONMENT") != "development":
    raise SystemExit("start-development.sh requires ENVIRONMENT=development.")

def port(key, default):
    value = values.get(key) or str(default)
    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise SystemExit(f"{key} must be a TCP port between 1 and 65535.")
    return str(int(value))

local_port = port("LOCAL_DATABASE_PORT", 55433)
remote_port = port("REMOTE_DATABASE_PORT", 55432)
app_port = port("APP_PORT", 8080)
ssh_host = values.get("REMOTE_DATABASE_SSH_HOST") or "root@aqa01-i01-ocr01.int.rclabenv.com"
remote_host = values.get("REMOTE_DATABASE_HOST") or "aqa01-i01-ocr01.int.rclabenv.com"
try:
    url = urlsplit(values.get("DATABASE_URL", ""))
    database = unquote(url.path.removeprefix("/"))
    username = unquote(url.username or "")
    if url.hostname in {"127.0.0.1", "localhost"} and url.port == int(local_port):
        connection_mode = "tunnel"
    elif url.hostname == remote_host and url.port == int(remote_port):
        connection_mode = "direct"
    else:
        connection_mode = ""
    valid_url = (
        url.scheme in {"postgresql", "postgresql+psycopg"}
        and bool(connection_mode)
        and bool(database) and bool(username)
        and not url.query and not url.fragment
    )
except ValueError:
    valid_url = False
if not valid_url:
    raise SystemExit("DATABASE_URL must target the configured VM host/port or local SSH tunnel and application database.")
if any("\n" in value or "\r" in value for value in (database, username)):
    raise SystemExit("DATABASE_URL contains invalid connection metadata.")

# Docker must use the same database and credentials through the endpoint selected
# above. Reject mismatched native/Docker settings before contacting any service.
try:
    compose_url = urlsplit(values.get("COMPOSE_DATABASE_URL", ""))
    expected_host = remote_host if connection_mode == "direct" else "host.docker.internal"
    expected_port = remote_port if connection_mode == "direct" else local_port
    valid_compose_url = (
        compose_url.scheme in {"postgresql", "postgresql+psycopg"}
        and compose_url.hostname == expected_host
        and compose_url.port == int(expected_port)
        and unquote(compose_url.path.removeprefix("/")) == database
        and unquote(compose_url.username or "") == username
        and unquote(compose_url.password or "") == unquote(url.password or "")
        and not compose_url.query and not compose_url.fragment
    )
except ValueError:
    valid_compose_url = False
if not valid_compose_url:
    raise SystemExit("COMPOSE_DATABASE_URL must match DATABASE_URL credentials, database, and configured connection mode.")

if connection_mode == "tunnel":
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", ssh_host):
        raise SystemExit("REMOTE_DATABASE_SSH_HOST must be an SSH hostname, optionally with a user.")
    if len(sys.argv[2].encode()) > 100:
        raise SystemExit("The project path is too long for the development SSH control socket.")
print("\n".join((local_port, remote_port, ssh_host, app_port, database, username, connection_mode)))
PY
)"
metadata_values=()
while IFS= read -r value; do
  metadata_values+=("$value")
done <<<"$metadata"
local_port="${metadata_values[0]}"
remote_port="${metadata_values[1]}"
ssh_host="${metadata_values[2]}"
app_port="${metadata_values[3]}"
expected_database="${metadata_values[4]}"
expected_username="${metadata_values[5]}"
connection_mode="${metadata_values[6]}"

docker_bin="${DOCKER_BIN:-$(command -v docker || true)}"
docker_context="${DOCKER_CONTEXT:-colima}"
if [[ -z "$docker_bin" || ! -x "$docker_bin" ]]; then
  printf 'Docker CLI is unavailable; set DOCKER_BIN to its executable path.\n' >&2
  exit 1
fi
docker_command=("$docker_bin" --context "$docker_context")
compose_command=("${docker_command[@]}" compose
  --project-directory "$project_dir" --env-file "$env_file"
  -f "$project_dir/compose.yaml" -f "$project_dir/compose.development-db.yaml")
"${docker_command[@]}" info >/dev/null
"${compose_command[@]}" config --quiet

if [[ "$connection_mode" == "tunnel" ]]; then
  umask 077
  mkdir -p "$project_dir/.data"
  if ssh -o BatchMode=yes -o ConnectTimeout=12 -S "$control_socket" \
      -O check "$ssh_host" >/dev/null 2>&1; then
    printf 'Reusing the project development database tunnel.\n'
  else
    if [[ -e "$control_socket" || -L "$control_socket" ]]; then
      if [[ ! -S "$control_socket" || -L "$control_socket" ]]; then
        printf 'The development SSH control socket path contains an unexpected file.\n' >&2
        exit 1
      fi
      rm -f -- "$control_socket"
    fi
    ssh -fNT -M -S "$control_socket" \
      -o BatchMode=yes -o ConnectTimeout=12 -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -L "127.0.0.1:$local_port:127.0.0.1:$remote_port" "$ssh_host"
    printf 'Started the project development database tunnel.\n'
  fi
else
  printf 'Using the direct persistent development database connection.\n'
fi

# This bypasses the image's normal Alembic startup command. Verify the same URL
# the backend and cleanup worker will use before either process can migrate/write.
"${compose_command[@]}" run --rm --no-deps -T --entrypoint python backend -c '
import os
import sys
from sqlalchemy import create_engine, text

engine = None
try:
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"connect_timeout": 5})
    with engine.connect() as connection:
        database, username, version = connection.execute(text(
            "SELECT current_database(), current_user, current_setting('\''server_version_num'\'')"
        )).one()
    if database != sys.argv[1] or username != sys.argv[2] or int(version) // 10000 != 16:
        raise RuntimeError("Unexpected database identity or PostgreSQL major version")
except Exception:
    raise SystemExit("Development database verification failed; the application was not switched.") from None
finally:
    if engine is not None:
        engine.dispose()
print("Verified the persistent application database and PostgreSQL 16.")
' "$expected_database" "$expected_username"

"${compose_command[@]}" up --no-build -d frontend backend cleanup-worker minio minio-init

health_url="http://127.0.0.1:$app_port/api/v1/health"
deadline=$((SECONDS + 45))
while (( SECONDS < deadline )); do
  remaining=$((deadline - SECONDS))
  request_timeout=2
  if (( remaining < request_timeout )); then
    request_timeout="$remaining"
  fi
  if curl --fail --silent --max-time "$request_timeout" "$health_url" \
      | python3 -c 'import json,sys; d=json.load(sys.stdin).get("data",{}); sys.exit(0 if d.get("status")=="ok" and d.get("database")=="ok" and d.get("storage")=="ok" else 1)' \
      >/dev/null 2>&1; then
    printf 'Harbor Market is healthy with the persistent development database.\n'
    printf 'Open http://127.0.0.1:%s/admin/products\n' "$app_port"
    exit 0
  fi
  if (( SECONDS < deadline )); then
    sleep 1
  fi
done
printf 'Application health did not become ready within 45 seconds; inspect the development Compose services.\n' >&2
exit 1
