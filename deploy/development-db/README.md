# Persistent development database

This deployment is set up on `aqa01-i01-ocr01.int.rclabenv.com`. It provides a persistent
development database with PostgreSQL 16.13, matching the locally verified PostgreSQL 16.13 runtime.
Database `xiangyue_xiamen` and its owner/application role `harbor_market` are provisioned separately
from the Compose initializer's `harbor_market_operator` maintenance role. Password login and
public-schema permissions are verified. The application role has no superuser, database-creation,
or role-creation privileges. Migrations are at `0004_track_promoted_staging_keys`, with 12 public
tables. Both existing preview accounts were copied transactionally into the previously empty
schema, including one administrator. Existing MinIO media remains local.

This database is intended to retain development data, including catalog data prepared for later
import or migration into production. Keep that data and its associated media across development
sessions. PostgreSQL tests that reset data belong in a different database.

The manifest is `deploy/development-db/compose.yaml`; its VM directory is
`/opt/harbor-market-development-db`. PostgreSQL publishes on the VM's private interface
`10.74.0.205:55432` for direct internal-network connections and on `127.0.0.1:55432` for an optional
SSH tunnel. TCP connections require the application's password using SCRAM authentication. The named
volume `harbor-market-development-db-data` retains database contents across container restarts.
Never remove that volume when restarting or updating the service.

## VM operations

Keep the VM's `.env` private (`chmod 600`); it supplies `DATABASE_OPERATOR_PASSWORD` and can set
`REMOTE_DATABASE_PORT=55432` and `REMOTE_DATABASE_BIND_ADDRESS=10.74.0.205` (the VM's private
interface address). Application credentials belong in the local project's ignored
`.env`. Do not commit either file.

On the VM, manage and check the service with:

```bash
cd /opt/harbor-market-development-db
docker compose --env-file .env -f compose.yaml up -d
docker inspect --format '{{.State.Health.Status}}' harbor-market-development-db
docker logs --tail 100 harbor-market-development-db
```

For a service restart:

```bash
docker restart harbor-market-development-db
```

Before schema changes, make a restricted backup on the VM:

```bash
umask 077
mkdir -p /opt/harbor-market-development-db/backups
docker exec harbor-market-development-db pg_dump -U harbor_market_operator \
  -d xiangyue_xiamen --no-owner --no-privileges \
  > /opt/harbor-market-development-db/backups/xiangyue_xiamen-$(date -u +%Y%m%dT%H%M%SZ).sql
```

Check the command succeeded before using the backup. Restore only after stopping application
writes and reviewing the destination database.

The base `deploy/backup-db.sh` and paired backup/restore helpers target the local Compose `db`
service. They do not back up or restore this VM database, even when the local application uses the
development override. Use the VM `pg_dump` above for its PostgreSQL data.

## Local application

With Docker running, start from the project root:

```bash
bash deploy/start-development.sh
```

The script verifies the database identity and PostgreSQL version from Docker, starts existing
images without rebuilding, and checks application health. Both native and Docker URLs connect
directly to `aqa01-i01-ocr01.int.rclabenv.com:55432`. Database clients can use that hostname and
port, database `xiangyue_xiamen`, user `harbor_market`, and the real `REMOTE_DATABASE_PASSWORD`
stored in the ignored root `.env`. Mac port `55432` belongs to another project and is untouched.

Root `.env` selects `compose.yaml:compose.development-db.yaml` with `COMPOSE_FILE`.
It explicitly records the real VM host, database name, application user, and password as
`REMOTE_DATABASE_HOST`, `REMOTE_DATABASE_NAME`, `REMOTE_DATABASE_USER`, and
`REMOTE_DATABASE_PASSWORD`. `POSTGRES_*` retains the stopped local database's fallback settings.
`COMPOSE_DATABASE_URL` connects both backend and cleanup worker directly to the VM; native tools
use `DATABASE_URL` with the same VM endpoint. Optional tunnel settings remain recorded as
`REMOTE_DATABASE_SSH_HOST`, `REMOTE_DATABASE_PORT`, and `LOCAL_DATABASE_PORT`.
MinIO continues to use the existing local storage. The local Compose
database is optional under the `local-database` profile; its existing volume can be retained.

For an optional manual tunnel when local port `55433` is free:

```bash
ssh -N -L 127.0.0.1:55433:127.0.0.1:55432 root@aqa01-i01-ocr01.int.rclabenv.com
```

To use the tunnel, set native `DATABASE_URL` to `127.0.0.1:55433` and Docker
`COMPOSE_DATABASE_URL` to `host.docker.internal:55433`, retaining the application credentials.
The startup script establishes or reuses the project tunnel when these endpoints are configured.

For later schema updates, back up development data before applying forward migrations. Native
backend work uses the root environment file; from the project root:

```bash
cd backend
uv run --env-file ../.env alembic upgrade head
uv run --env-file ../.env uvicorn app.main:app --reload
```

The base production `compose.yaml`, used alone, still constructs `DATABASE_URL` from `POSTGRES_*`
and its private `db` service. The development override is an explicit local opt-in.
A native backend also needs a reachable object-storage endpoint for media operations, since
`minio:9000` is a Compose-private hostname.

Never point PostgreSQL migration/concurrency tests here: they truncate application tables and
downgrade migrations. A later test setup must use a separate disposable database with
`TEST_DATABASE_URL`.

## Promote development data to production

Keep development and production on compatible application revisions and Alembic schema versions.
For catalog promotion into an existing production installation, use the Excel export/import flow
with a reviewed dry-run and a production backup first. Product and SKU codes identify updates;
submitted values can overwrite production prices, stock, and publication state.

Prepare matching category codes in production before importing: the workbook's Dictionary sheet
references categories but does not create them. Product images require a separate transfer to
production MinIO. Excel exports contain image keys and metadata, not image bytes; upload/stage the
images in production and update the workbook keys before validation and import.

A whole-dataset migration must include a VM `pg_dump` and matching local MinIO objects. Pause
application writes, including the cleanup worker, while capturing that matching database/media
pair. The local paired helpers cannot capture the VM database. The paired restore described in
`deploy/BACKUP_RESTORE.md` replaces destination stores rather than merging and also carries users,
mock payments, and operational records; review how the VM dump and media backup will be supplied
before using that destination restore workflow. Choose and review the appropriate migration path
when production promotion is requested.
