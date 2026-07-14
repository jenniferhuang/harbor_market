# Harbor Market

Harbor Market is a Mac-hosted Python/Vue application scaffold with real local user registration and login. FastAPI serves the API, Vue provides the browser experience, PostgreSQL stores application data in the `xiangyue_xiamen` database, and a private MinIO bucket stores product media.

## Runtime

- Python 3.12 + FastAPI
- Vue 3 + TypeScript + Vite
- PostgreSQL 16
- MinIO object storage (private S3 API)
- Nginx same-origin frontend/API proxy
- Docker Compose v2

## Quick Start

Create a local environment file and replace every placeholder password/key:

```bash
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8080/api/v1/health
```

Open `http://127.0.0.1:8080/register` to create the first user.

Promote the operator account, then open `/admin/products`:

```bash
docker compose exec backend python -m app.cli promote-admin YOUR_USERNAME
```

## Product center

The admin workspace manages stable category/product/SKU codes, prices in integer cents, stock,
Luckin-style selling points and structured customization specifications, and three image roles:
one cover, up to eight gallery images, and up to twenty detail images. Public mini-program/H5 APIs
are under `/api/v1/catalog`; private admin APIs are under `/api/v1/admin`.

Excel supports template download, export, dry-run validation, and atomic import. For images on a
new product, first use **Excel → 为新商品暂存图片**, copy the generated
`products/staged/{PRODUCT_CODE}/...` key into the Images sheet, then dry-run. A formal import
validates the real image bytes and conditionally promotes it to a server-generated canonical key.

Object deletions use a database outbox. A failed MinIO cleanup returns `cleanup_pending` while the
database change remains committed; retry an individual job from the admin API or process pending
jobs operationally. Compose also runs a bounded `cleanup-worker` every
`OBJECT_CLEANUP_INTERVAL_SECONDS` (default 60 seconds), which expires staging objects after seven
days and recovers interrupted cleanup/import leases:

```bash
docker compose exec backend python -m app.cli retry-object-cleanup --limit 100
```

Staging is capped at 100 active objects per product code and 5,000 globally. The Excel UI can
cancel a staged image. Formal imports send a retained `X-Idempotency-Key`; recent jobs are visible
in the UI and at `GET /api/v1/admin/import-jobs`, so a lost HTTP response can be checked instead of
re-importing blindly.

The MinIO S3 API (`minio:9000`) is reachable only inside the Compose network. Its administrative console is bound to `http://127.0.0.1:9001` by default and must not be sent through the public application tunnel. A one-shot initializer uses the root identity to create the bucket, remove any inherited anonymous policy, and create a separate application user limited to list/get/put/delete operations on that bucket; the backend receives only that least-privilege identity.

Production deployment binds the application to Mac loopback only. Public HTTPS is provided by a supervised outbound connector; PostgreSQL and the backend are never published directly.

Until custom DNS write access is available, `deploy/install-quick-tunnel.sh` publishes a temporary `https://*.trycloudflare.com` URL through an outbound-only, launchd-supervised connector. It does not use or modify the existing Lightsail Xray ports, hostname, or configuration.

The current temporary URL is recorded in the tunnel log:

```bash
grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' \
  ~/Library/Logs/HarborMarket/quick-tunnel.err.log | tail -1
```

## Operations

```bash
# Follow application logs
docker compose logs --tail=200 -f

# Stop application containers without deleting users
docker compose down

# Rebuild after pulling a new revision
docker compose up --build -d

# Back up PostgreSQL and MinIO as one quiesced, checksummed release
deploy/backup-release.sh

# Restore the matching database/object pair; creates another paired rollback first
deploy/restore-release.sh --confirm-replace ~/HarborMarketBackups/releases/release-TIMESTAMP

# Verify the public registration and authentication flow
tests/e2e/auth-smoke.sh https://your-public-hostname
```

Remote deployment requires `REVISION=<reviewed-40-character-commit>`; mutable branch deployment is
intentionally rejected. Existing `AUTH_COOKIE_PATH=/api/v1/auth` installations are backed up and
migrated to `/api/v1` by `deploy/mac-deploy.sh` so the admin API receives the login cookie.

`deploy/install-launch-agent.sh` installs a launchd monitor that starts native
Colima and reconciles the Compose stack at login. It also checks application
health every 60 seconds and recovers the engine or web services when needed.
Compose restart policies supervise the individual containers, while the
Cloudflare connector has its own KeepAlive LaunchAgent.

The Mac must remain powered, awake, network-connected, and lid-open unless it
is operating in supported closed-display mode.

MinIO data is a host bind mount at `${MINIO_DATA_DIR:-./.data/minio}`. Paired backups are written
under `~/HarborMarketBackups/releases` by default. The object phase verifies that its destination is
actually shared into Docker, hashes every object, and publishes the completion marker last. Restore
rejects incomplete/mismatched pairs, takes a pre-restore paired rollback, and keeps writers stopped
after any partial or uncertain failure. See `deploy/BACKUP_RESTORE.md` for the exact runbook.

Do not delete the PostgreSQL volume or the MinIO data directory on a deployment containing data. `docker compose down -v` deletes the PostgreSQL volume, while removing `MINIO_DATA_DIR` deletes product media. Back up both stores before deployment or rollback.

## Specification

Product-center requirements, design, and implementation tasks are in
`.spec-workflow/specs/harbor-product-management/`; the original authentication scaffold remains in
`.spec-workflow/specs/xiangyue-xiamen-auth-scaffold/`.
