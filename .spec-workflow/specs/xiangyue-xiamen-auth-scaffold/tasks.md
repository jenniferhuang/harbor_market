# Tasks Document

- [x] 1. Implement the FastAPI authentication backend
  - Files: `backend/**`
  - Create settings, database session, user model, schemas, auth service, API routes, health endpoint, and exception mapping.
  - Add Argon2 hashing, expiring signed HttpOnly cookie authentication, username normalization, and rate limiting.
  - _Requirements: 2, 3, 4, 6, 7_
  - _Success: Backend tests cover registration, duplicate username, login success/failure, current user, token failure, and logout._

- [x] 2. Add PostgreSQL migrations and backend container
  - Files: `backend/alembic/**`, `backend/Dockerfile`, backend dependency files
  - Create the initial users migration and a production container that migrates before serving.
  - _Requirements: 7, 9, 12_
  - _Success: A clean PostgreSQL database migrates successfully and retains users across backend restarts._

- [x] 3. Implement the Vue authentication frontend
  - Files: `frontend/**`
  - Create typed API access, auth state restoration, route guards, registration, login, protected home, and logout.
  - Add accessible inline validation, password visibility controls, loading states, and responsive mobile/desktop styles.
  - _Requirements: 2, 3, 4, 5, 8_
  - _Success: Frontend tests pass and no authentication credential is stored in local storage._

- [x] 4. Add frontend production serving and API proxy
  - Files: `frontend/Dockerfile`, `frontend/nginx.conf`
  - Build static Vue assets, provide SPA fallback, and proxy same-origin `/api` requests to the backend service.
  - _Requirements: 6.7, 8.8, 9_
  - _Success: Direct navigation and API calls work through one frontend origin._

- [x] 5. Assemble the Docker Compose runtime
  - Files: `compose.yaml`, `.env.example`, `.gitignore`, deployment documentation
  - Connect frontend, backend, and PostgreSQL with health checks, restart policies, secrets from an untracked `.env`, private database networking, and persistent volume storage.
  - _Requirements: 1, 7, 9_
  - _Success: `docker compose config` validates and all services become healthy on the remote Mac._

- [x] 6. Add operations, backup, and public connector assets
  - Files: `deploy/**`
  - Add idempotent deployment/check scripts, database backup/restore scripts, launchd supervision, and a public connector configuration that preserves Hermes, MySQL, and Xray.
  - _Requirements: 9, 10, 11_
  - _Success: Reboot/restart behavior is documented, backups restore, and only the intended HTTPS hostname is public._

- [x] 7. Add end-to-end and deployment verification
  - Files: `tests/e2e/**`, verification scripts
  - Test registration, login, protected refresh, logout, DNS, TLS, API health, Compose health, and persistence.
  - _Requirements: 5, 10, 11, 12_
  - _Success: The flow passes against the public site at desktop and iPhone viewports._

- [x] 8. Deploy and publish Harbor Market
  - Pull the approved Git revision on `192.168.1.33`, install/start the Docker-compatible engine, generate production secrets, build images, run migrations, and start Compose.
  - Configure DNS and the supervised outbound connector, then execute the complete verification checklist.
  - _Requirements: All_
  - _Success: A user can register and log in through the public HTTPS hostname, data survives a service restart, and existing Hermes/Xray services remain healthy._

## Completion Evidence

- Completed on 2026-07-14 against remote Mac `192.168.1.33` using native arm64 Colima, Docker, and Docker Compose.
- Backend: Ruff passed; Pytest passed 21 tests with the optional local PostgreSQL integration test skipped when `TEST_DATABASE_URL` was unavailable.
- Frontend: ESLint completed without errors; Vitest passed 13 tests; the production build completed; production dependency audit reported zero vulnerabilities.
- Deployment: PostgreSQL, FastAPI, and Nginx health checks passed; database `xiangyue_xiamen` retained users across a backend restart.
- Recovery: a protected compressed backup restored into a clean temporary PostgreSQL database with the expected user rows.
- Public verification: the supervised Quick Tunnel served the registration UI over trusted HTTPS; registration, login, authenticated current-user lookup, logout, and post-logout rejection passed through the public URL.
- Visual verification: desktop and iPhone layouts rendered without overlap; public DOM verification confirmed the full Create account form. The screenshot transport timed out independently of the rendered application.
- Compatibility: existing Lightsail/Xray behavior remained unchanged. A named `app.hermes-node.com` tunnel remains a later stability upgrade when Cloudflare DNS-write credentials are available; the approved initial Quick Tunnel publication is complete.
