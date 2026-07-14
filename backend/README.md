# Harbor Market backend

The backend is a FastAPI application with SQLAlchemy 2, PostgreSQL, and Alembic. Runtime
configuration is read from environment variables; `.env` is supported for local development and
is ignored by Git and Docker.

Required settings:

- `DATABASE_URL`: SQLAlchemy PostgreSQL URL, using the `postgresql+psycopg://` driver.
- `AUTH_SECRET` (or `AUTH_SECRET_KEY`): random signing key of at least 32 characters. Generate one
  with `openssl rand -hex 32` and keep it outside source control.

Object storage defaults to `STORAGE_BACKEND=disabled`, which keeps authentication-only local and
test setups independent of MinIO. A MinIO deployment sets:

- `STORAGE_BACKEND=minio`
- `STORAGE_ENDPOINT=minio:9000` (host and port only; do not include `http://`)
- `STORAGE_ACCESS_KEY` and `STORAGE_SECRET_KEY`
- `STORAGE_BUCKET=harbor-market-products`
- `STORAGE_SECURE=false` for the private Compose network

Compose provisions `STORAGE_ACCESS_KEY` as a separate application user with access only to the
configured bucket. `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` are restricted to the MinIO server,
the one-shot initializer, and explicit backup/restore tooling; they are not passed to the backend.

`UPLOAD_MAX_BYTES` defaults to and is capped at 5 MiB. Product-image roles enforce one cover,
up to eight gallery images, and up to twenty detail images. The Nginx request limit is 12
MiB so multipart overhead fits above the application file limit. Product media
keys may use directory-style prefixes such as `products/<product-id>/<image-id>.webp`; absolute
paths, traversal segments, control characters, and backslashes are rejected.

Staging keys use `products/staged/<product-code>/...`, expire after seven days, and are capped at
100 per product code / 5,000 globally. Direct uploads use `products/<product-id>/<role>/...`; Excel
promotions use `products/catalog/<product-code>/<role>/...`. Compose's `cleanup-worker` drains the
durable outbox every `OBJECT_CLEANUP_INTERVAL_SECONDS` (10–3600, default 60).

Compose may set `AUTH_TOKEN_TTL_MINUTES`; the native seconds setting is
`AUTH_SESSION_TTL_SECONDS` and takes precedence when both are present. `ALLOWED_HOSTS` is a
comma-separated list and must include the public hostname in production.

For local HTTP development, also set `ENVIRONMENT=development` and
`AUTH_COOKIE_SECURE=false`. Production defaults to a secure cookie and rejects an explicit
insecure-cookie configuration.

```bash
uv sync --locked
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

After registering an operator, grant admin access with
`uv run python -m app.cli promote-admin USERNAME`. Failed object deletions are persisted in
`object_cleanup_jobs`; retry them with `uv run python -m app.cli retry-object-cleanup --limit 100`.

Formal Excel imports should include a stable `X-Idempotency-Key` (8–128 safe ASCII characters).
The key is bound to the file SHA-256 and dry-run mode. Inspect the current administrator's recent
jobs at `GET /api/v1/admin/import-jobs`; interrupted three-hour import leases are marked failed by
the cleanup worker, while durable promotion intents recover unreferenced copied objects.

The API is served at `http://127.0.0.1:8000`, OpenAPI at `/openapi.json`, and Swagger UI at
`/docs`. Run checks with:

```bash
uv run ruff check .
uv run pytest
```

Request tests use an in-memory database. To additionally verify the Alembic migration and
case-insensitive uniqueness against PostgreSQL, point `TEST_DATABASE_URL` at a disposable database
whose name contains `test`, then run `uv run pytest -m postgres`.

Rate limits use the ASGI client address by default. The bundled Compose deployment sets
`TRUST_PROXY_HEADERS=true` because Uvicorn is reachable only from the private Nginx service; Nginx
sets a single `X-Real-IP`, and the API validates it as one IPv4/IPv6 address before using it. Leave
this setting false whenever the backend can be reached directly or through an untrusted proxy.
