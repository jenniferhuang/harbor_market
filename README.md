# Harbor Market

Harbor Market is a Mac-hosted Python/Vue application scaffold with real local user registration and login. FastAPI serves the authentication API, Vue provides the browser experience, and PostgreSQL stores users in the `xiangyue_xiamen` database.

## Runtime

- Python 3.12 + FastAPI
- Vue 3 + TypeScript + Vite
- PostgreSQL 16
- Nginx same-origin frontend/API proxy
- Docker Compose v2

## Quick Start

Create a local environment file and replace both placeholder secrets:

```bash
cp .env.example .env
docker compose config
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:8080/api/v1/health
```

Open `http://127.0.0.1:8080/register` to create the first user.

Production deployment binds the application to Mac loopback only. Public HTTPS is provided by a supervised outbound connector; PostgreSQL and the backend are never published directly.

Until custom DNS write access is available, `deploy/install-quick-tunnel.sh` publishes a temporary `https://*.trycloudflare.com` URL through an outbound-only, launchd-supervised connector. It does not use or modify the existing Lightsail Xray ports, hostname, or configuration.

## Operations

```bash
# Follow application logs
docker compose logs --tail=200 -f

# Stop application containers without deleting users
docker compose down

# Rebuild after pulling a new revision
docker compose up --build -d
```

Do not run `docker compose down -v` on a deployment containing data. The `-v` option deletes the PostgreSQL volume.

## Specification

The approved requirements, design, and implementation tasks are in `.spec-workflow/specs/xiangyue-xiamen-auth-scaffold/`.
