# Harbor Market backend

The backend is a FastAPI application with SQLAlchemy 2, PostgreSQL, and Alembic. Runtime
configuration is read from environment variables; `.env` is supported for local development and
is ignored by Git and Docker.

Required settings:

- `DATABASE_URL`: SQLAlchemy PostgreSQL URL, using the `postgresql+psycopg://` driver.
- `AUTH_SECRET` (or `AUTH_SECRET_KEY`): random signing key of at least 32 characters. Generate one
  with `openssl rand -hex 32` and keep it outside source control.

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

The API is served at `http://127.0.0.1:8000`, OpenAPI at `/openapi.json`, and Swagger UI at
`/docs`. Run checks with:

```bash
uv run ruff check .
uv run pytest
```

Request tests use an in-memory database. To additionally verify the Alembic migration and
case-insensitive uniqueness against PostgreSQL, point `TEST_DATABASE_URL` at a disposable database
whose name contains `test`, then run `uv run pytest -m postgres`.

Rate limits use the ASGI client address. When Uvicorn is behind the private Nginx service, set
`FORWARDED_ALLOW_IPS` to the trusted proxy address or network so forwarded client addresses are
accepted; do not trust forwarded headers when the backend is directly reachable.
