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

Payment integration defaults to `PAYMENT_MODE=disabled`. A local-only mock can be enabled with:

- `ENVIRONMENT=development` (or `test`)
- `PAYMENT_MODE=mock`
- `PAYMENT_MOCK_CONTROLS_ENABLED=true`
- `PAYMENT_MOCK_SIGNING_SECRET`: an uncommitted random value of at least 32 characters

Settings reject mock mode in production. The mock admin API accepts a server/operator supplied
amount only to exercise the boundary before the order module exists; no customer-facing
payment-creation route accepts an amount. `POST /api/v1/admin/payments` requires an
`X-Idempotency-Key`. Provider state can be queried, closed, or simulated through guarded admin
routes. The scenario control accepts only `NOTPAY`, `SUCCESS`, and `CLOSED`; `PAYERROR` is not an
injectable mock state.

Mock-provider records are stored in PostgreSQL rather than process memory, so prepay and trade
state survive application restarts and are consistent across workers. A partial unique index allows
only one active (`created` or `pending`) attempt per order reference. There is intentionally no
single-success constraint: all provider-confirmed successes are retained, and multiple successes
set operational-review flags on the related attempts.

`/api/v1/payments/providers/wechat-pay/notify` is unauthenticated by design and accepts only a valid,
recent provider signature. Its callback body limit is 1.25 MiB
(`PAYMENT_WEBHOOK_MAX_BYTES=1310720`), leaving bounded envelope room around WeChat's maximum 1 MiB
ciphertext field.
Each valid decoded callback, including an unmatched merchant order, is retained in the provider
inbox as a normalized envelope: provider AppID and merchant ID, merchant order and provider state,
transaction ID and success time, and amount/currency. The inbox stores the raw-body SHA-256 rather
than raw plaintext, making later replay and investigation possible without retaining the callback
body. The mock uses HMAC solely for tests; real WeChat Pay will use RSA response/callback
verification and AES-GCM notification decryption behind the same gateway interface.

Before a live adapter can be enabled, payment orchestration must follow a claim/network/re-lock
sequence: persist and commit a short operation claim, perform the WeChat network request with no
payment or order row locks held, then re-lock, revalidate current state, and apply the response.
Holding DB locks across live provider calls is explicitly prohibited.

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

The persistent development database set up on
`aqa01-i01-ocr01.int.rclabenv.com` uses PostgreSQL 16.13, database `xiangyue_xiamen`, and
application role `harbor_market`, matching the locally verified PostgreSQL 16.13 runtime.
Migrations are at `0004_track_promoted_staging_keys`, with 12 public tables. Both preview accounts
were preserved, including one admin. Never use this database for destructive migration/concurrency
tests or set it as `TEST_DATABASE_URL`.

Start the configured Docker application from the project root:

```bash
bash deploy/start-development.sh
```

The script verifies database access from Docker, starts the application without rebuilding, and
checks health. Both backend and cleanup worker connect directly to
`aqa01-i01-ocr01.int.rclabenv.com:55432` via the root `.env`
`COMPOSE_DATABASE_URL` and development Compose override. MinIO remains local. Base production
Compose used alone retains its `POSTGRES_*` connection.

An SSH tunnel is optional. For a manual tunnel when local port `55433` is free:

```bash
ssh -N -L 127.0.0.1:55433:127.0.0.1:55432 root@aqa01-i01-ocr01.int.rclabenv.com
```

To use the tunnel, set native `DATABASE_URL` to `127.0.0.1:55433` and Docker
`COMPOSE_DATABASE_URL` to `host.docker.internal:55433`, retaining the application credentials.
The startup script recognizes and manages this connection mode.

For native backend work, root `.env` `DATABASE_URL` uses the direct VM endpoint. From the project root:

```bash
cd backend
uv run --env-file ../.env uvicorn app.main:app --reload
```

The explicit `--env-file` loads the root file when running from `backend`. A native backend also
needs a reachable object-storage endpoint for media operations; Compose's `minio:9000` hostname
is private to its network. Existing local database backup helpers do not back up the VM database.
See [the runbook](../deploy/development-db/README.md) for VM backups and production promotion.

Rate limits use the ASGI client address by default. The bundled Compose deployment sets
`TRUST_PROXY_HEADERS=true` because Uvicorn is reachable only from the private Nginx service; Nginx
sets a single `X-Real-IP`, and the API validates it as one IPv4/IPv6 address before using it. Leave
this setting false whenever the backend can be reached directly or through an untrusted proxy.

## Native Mini Program WeChat login

This feature adds migration `0005_add_mini_customer_sessions`, following `0004`. It creates
independent `mini_customers` and `mini_sessions` tables; it does not convert browser users or grant
administrator access. Apply it through the normal migration process after reviewing a backup of
the target database. The implementation worktree does not migrate the persistent development DB.

Login defaults to `WECHAT_MINIPROGRAM_AUTH_MODE=disabled`. Enable the real provider with `live`,
`WECHAT_MINIPROGRAM_APP_ID` and `WECHAT_MINIPROGRAM_APP_SECRET` in the backend environment or ignored
root `.env`. Compose forwards these settings to the backend. The AppSecret must never be placed in
Mini Program code, frontend build variables, source control, or a client response. Incomplete or
invalid live configuration returns a Chinese 503; disabled mode never manufactures a successful
identity. Real WeChat login requires a registered Mini Program and its matching credentials.

The backend exchanges the one-time `wx.login()` code using Tencent's
[code2Session endpoint](https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html).
Its fixed HTTPS target, timeout (1–15 seconds, default 5), bounded response and safe error messages
keep upstream secrets private. Only the server-derived AppID/OpenID pair identifies a customer;
Tencent's `session_key` is discarded. OpenID and AppID are not returned in the customer object.

All success responses use `{ "data": ... }`; errors use `{ "error": { "code", "message" } }` with
Chinese messages. Login and profile reject additional JSON fields, including client-supplied OpenID,
roles and avatar URLs:

| Method and path under `/api/v1/mini/auth` | Request | Response `data` |
| --- | --- | --- |
| `POST /login` | `{ "code": "wx.login code" }` | `access_token`, `token_type: "Bearer"`, UTC ISO8601 `expires_at`, `customer` |
| `GET /me` | Bearer header | Customer |
| `PATCH /profile` | Bearer header and `{ "nickname": "昵称" }` | Customer |
| `POST /avatar` | Bearer header and multipart `file` | Customer |
| `GET /avatar` | Bearer header | Private JPEG bytes |
| `POST /logout` | Bearer header | `{ "logged_out": true }` |

A customer contains only `id`, `nickname`, `avatar_url`. The initial nickname is `微信用户` and
avatar URL is null. Nicknames must contain 1–64 Unicode code points after trimming, without control
characters. The avatar URL becomes `/api/v1/mini/auth/avatar`; download it with `wx.downloadFile`
and an `Authorization: Bearer <access_token>` header. Do not append tokens to URLs. Browser cookies
are not accepted on these routes, and Mini Program tokens cannot authenticate browser/admin APIs.
Guest public catalog and locally held cart behavior are unchanged.

Sessions use cryptographically random opaque tokens. Only SHA-256 token/code hashes are stored;
plaintext tokens, login codes and Tencent session keys are not retained. `MINI_SESSION_TTL_SECONDS`
defaults to seven days and is capped at thirty days. Authentication checks expiry, revocation and
active customer state. Logout revokes the current session without revoking other device sessions.
Used-code hashes prevent replay for each AppID. This feature does not automatically purge expired
session records; their retention and any future cleanup policy must preserve the code replay guard.

Every login attempt consumes the client-address rate limit before calling Tencent. Defaults are
10 attempts per 60 seconds (`MINI_LOGIN_RATE_LIMIT`, `MINI_LOGIN_RATE_WINDOW_SECONDS`), and 429
responses carry `Retry-After`. The bounded limiter is per process; scale-out deployments need
a shared limiter or an equivalent gateway policy. Keep proxy trust limited to the bundled proxy.

Avatar files are capped at 2 MiB (`MINI_AVATAR_UPLOAD_MAX_BYTES`, optionally lower). Only static
JPEG/PNG/WebP images of at most 16 million pixels are accepted. The backend verifies and reencodes
them as metadata-free JPEGs within 512×512, stores them privately in existing MinIO, and serves
only the authenticated customer's avatar with `Cache-Control: private, no-store`. Content hashes
and lengths detect a corrupt stored object. Durable cleanup intents recover failed uploads and
replaced avatars; the cleanup worker protects any avatar key still referenced by a customer.
Existing bucket backups must include the `customers/` prefix when promoting customer data.

Isolated SQLite and injected-provider tests exercise login, replay, profile, avatar, expiry,
revocation and permission separation without contacting Tencent or the persistent database.
Passing these tests does not claim live login verification. When credentials and deployment are
ready, verify `wx.login` → backend login → `/me` → avatar upload/download → logout on a real
Mini Program; expired/reused codes should produce a Chinese prompt to obtain a fresh code.

## Shop homepage and customer records

Migration `0006_add_shop_homepage_commerce` adds the minimal homepage data: one store profile,
carousel/category/announcement media, fixed threshold coupons and customer claims, favorites,
and administrator-recorded completed historical orders with immutable product/price snapshots.
`products.search_hit_count` is the only search counter; no event analytics or reporting tables
are introduced. The exact client contract is in [HOMEPAGE_API.md](HOMEPAGE_API.md).

Homepage responses expose only published products in active categories. Positive search counts
rank hot searches. Only explicit `POST /api/v1/shop/search` increments every matched public product
once; subsequent catalog GET pagination does not count. Requests reuse the existing fuzzy name/code
filter and IP/proxy trust policy. The per-process bounded limiter defaults to 30 requests per
60 seconds, including invalid JSON, configurable with `SHOP_SEARCH_RATE_LIMIT` and
`SHOP_SEARCH_RATE_WINDOW_SECONDS`.

Sales and repeat purchases are aggregated directly from completed historical order lines; voided
orders are excluded. Anonymous records contribute quantity/order count but do not invent repeat
customers. Without public sales history, the source is honestly `featured` or `newest` and all
sale counts are zero. Historical order entry is browser-admin only, uses unique external references,
server-calculated totals, and saved product names/codes. It neither creates a WeChat payment nor
changes stock. Coupon functionality is display/claim only; checkout and redemption are outside this
homepage baseline. Existing customer claims remain idempotent after campaign expiry/deactivation.

The default unconfigured store name is `港湾集市`. Administrators can bind an active Mini Program
customer as the owner. That customer may edit store fields and upload announcement images using
their existing Bearer session; it grants no browser administrator role. Customer selections expose
only IDs and nicknames. Store contacts, coordinates and public media are intended to be public.

`SHOP_IMAGE_UPLOAD_MAX_BYTES` defaults to 5 MiB, and `SHOP_VIDEO_UPLOAD_MAX_BYTES` to 10 MiB, within
the existing proxy's 12 MiB request limit. Images are verified static JPEG/PNG/WebP and reencoded
without metadata within 2048×2048. MP4 videos receive bounded structural, track-duration and codec
checks (H.264/H.265, at most 60 seconds), without external decoding/transcoding. Public video reads
support a single byte range; all reads verify size/hash and stay within bounded object sizes.
Admin media lists default to 20 rows, maximum 100; use paging for larger lists. Replacements and
category deletions reuse durable object cleanup, and current shop-media references are protected.

New migration and API checks run against isolated SQLite, with PostgreSQL SQL compiled offline.
Deployment, migrations against the persistent database, and live customer/media data population
are separate integration steps. Include the new tables and `shop/` object prefix in coordinated
database/MinIO backups and restoration.
