# Requirements Document

## Document Status

- **Spec name:** `xiangyue-xiamen-auth-scaffold`
- **Phase:** Requirements
- **Status:** Draft for approval
- **Deployment target:** `jennifer.huang@192.168.1.33`
- **Reference implementation:** [rooms-sdet-tools/heimdall](https://git.ringcentral.com/rooms-sdet-tools/heimdall)
- **Database name:** `xiangyue_xiamen`

## Introduction

This specification defines version 1 of a real, runnable full-stack application scaffold. The frontend shall use Vue, the backend shall use Python, and the application shall provide local username/password registration, login, authenticated-session verification, and logout backed by a persistent PostgreSQL database.

The complete application stack shall run on Jennifer's remote Mac at `192.168.1.33` through Docker and Docker Compose. The frontend, backend, database, and persistent application data shall not be hosted on AWS. A public gateway may relay HTTPS traffic to the Mac, but the Mac remains the application origin and system of record.

The Heimdall project is a structural reference for frontend/backend separation, Vue routing, typed API access, Python service organization, and same-origin `/api` routing. Its LDAP authentication, hard-coded JWT secret, non-expiring-token behavior, and user response shape are explicitly not requirements for this project.

## Alignment with Product Vision

No steering documents exist yet for `harbor_market`. This version aligns with the stated product goal: establish a small but genuine full-stack foundation that can be opened from a browser anywhere, creates users in a real database, and proves registration and login end to end before domain-specific Harbor Market features are added.

## Scope Decisions

1. **Simple local identity:** Version 1 uses username and password only. It does not use LDAP, Okta, Google login, email verification, MFA, or enterprise SSO.
2. **PostgreSQL selected:** PostgreSQL shall run as a Compose service. The remote Mac already runs native MySQL on `127.0.0.1:3306`; PostgreSQL avoids coupling to or modifying that existing service.
3. **Mac-hosted application:** Frontend, backend, PostgreSQL, database volume, logs, and backups shall reside on the remote Mac.
4. **Public browser access:** A public DNS hostname and HTTPS entry point shall forward requests to the Mac-hosted application without exposing PostgreSQL or the raw backend port.
5. **Strict workflow:** Design shall begin only after this requirements document is approved through spec-workflow.

## Requirements

### Requirement 1: Project Scaffold

**User Story:** As a developer, I want separate Python backend and Vue frontend workspaces, so that the application has clear ownership boundaries and can grow beyond the authentication demo.

#### Acceptance Criteria

1. WHEN the project is initialized THEN the system SHALL contain separate `backend` and `frontend` source directories.
2. WHEN local or remote development begins THEN the system SHALL provide documented commands for starting the frontend, backend, and database.
3. WHEN the production-like stack is started THEN one Docker Compose project SHALL orchestrate the frontend, backend, and PostgreSQL services.
4. WHEN dependency versions are declared THEN the system SHALL pin compatible Python, Node.js, Vue, PostgreSQL, and container image versions.
5. WHEN the project structure is reviewed THEN each module SHALL have a single, documented responsibility and SHALL avoid copying unrelated Heimdall modules.

### Requirement 2: User Registration

**User Story:** As a new user, I want to register with a username and password, so that I can create an account without an administrator or external identity provider.

#### Acceptance Criteria

1. WHEN an unauthenticated visitor opens the login page THEN the system SHALL display a clear `Create account` entry that navigates to the registration page.
2. WHEN the registration page opens THEN the system SHALL display username, password, and confirm-password fields.
3. WHEN the visitor submits valid matching values THEN the backend SHALL create exactly one active user in the `xiangyue_xiamen` database.
4. WHEN a username already exists under case-insensitive comparison THEN the backend SHALL reject registration with HTTP `409` and the frontend SHALL display an inline, non-technical message.
5. WHEN the password and confirm-password values differ THEN the frontend SHALL prevent submission and display the validation error.
6. WHEN registration succeeds THEN the frontend SHALL redirect the user to the login page and SHALL display a registration-success message.
7. WHEN registration input is invalid THEN the backend SHALL return field-level validation errors without echoing the submitted password.
8. WHEN repeated registration attempts exceed the configured rate limit THEN the backend SHALL temporarily reject additional attempts with HTTP `429`.

### Requirement 3: Username and Password Login

**User Story:** As a registered user, I want to log in with my username and password, so that I can access protected application content.

#### Acceptance Criteria

1. WHEN the login page opens THEN the system SHALL display username and password fields and a primary login action.
2. WHEN valid credentials are submitted THEN the backend SHALL verify the password hash, establish an authenticated session, and update the user's `last_login_at` timestamp.
3. WHEN the username is unknown or the password is incorrect THEN the backend SHALL return the same generic authentication failure response and SHALL not reveal which field was wrong.
4. WHEN a disabled user attempts login THEN the backend SHALL deny access without revealing the account state.
5. WHEN repeated failed login attempts exceed the configured rate limit THEN the backend SHALL temporarily reject additional attempts with HTTP `429`.
6. WHEN login succeeds THEN the frontend SHALL navigate to the protected demo page.
7. WHEN login fails THEN the password field SHALL be cleared while the username remains available for correction.

### Requirement 4: Authentication Session and Logout

**User Story:** As a logged-in user, I want my authenticated state to persist safely and to be removable through logout, so that protected routes behave predictably.

#### Acceptance Criteria

1. WHEN authentication succeeds THEN the backend SHALL issue a time-limited authentication credential through a `Secure`, `HttpOnly`, and appropriate `SameSite` cookie in the public HTTPS environment.
2. WHEN the browser requests the current-user endpoint with a valid credential THEN the backend SHALL return only safe user fields.
3. WHEN the credential is missing, invalid, or expired THEN protected API endpoints SHALL return HTTP `401`.
4. WHEN an unauthenticated visitor navigates to a protected frontend route THEN the router SHALL redirect to the login page and preserve the intended destination.
5. WHEN the user logs out THEN the backend SHALL invalidate or expire the browser credential and the frontend SHALL return to the login page.
6. WHEN the page is refreshed during a valid session THEN the frontend SHALL restore the current user by calling the backend rather than trusting browser-local user data.
7. WHEN any user API response is serialized THEN it SHALL exclude `password`, `password_hash`, session secrets, and internal authentication data.

### Requirement 5: Protected Demonstration Page

**User Story:** As a stakeholder, I want a visible authenticated page, so that I can verify this scaffold performs real registration and login rather than presenting static forms.

#### Acceptance Criteria

1. WHEN a user logs in successfully THEN the system SHALL display a protected page containing the authenticated username.
2. WHEN the protected page is displayed THEN it SHALL provide a logout action.
3. WHEN an unauthenticated request is made directly to the protected route THEN access SHALL be denied and the user SHALL be redirected to login.
4. WHEN a valid user refreshes the protected page THEN the page SHALL remain accessible after backend session verification.
5. WHEN version 1 is reviewed THEN registration, login, current-user verification, protected-route access, and logout SHALL all operate against the real backend and PostgreSQL database.

### Requirement 6: Authentication API Contract

**User Story:** As a frontend developer, I want a small, stable authentication API, so that the Vue client can integrate without depending on backend internals.

#### Acceptance Criteria

1. WHEN the backend is running THEN it SHALL expose `POST /api/v1/auth/register`.
2. WHEN the backend is running THEN it SHALL expose `POST /api/v1/auth/login`.
3. WHEN the backend is running THEN it SHALL expose `POST /api/v1/auth/logout`.
4. WHEN the backend is running THEN it SHALL expose `GET /api/v1/auth/me`.
5. WHEN service health is checked THEN the backend SHALL expose `GET /api/v1/health` without requiring authentication.
6. WHEN an API response is returned THEN it SHALL use a documented JSON success or error shape with appropriate HTTP status codes.
7. WHEN the frontend calls the API in the deployed environment THEN it SHALL use same-origin `/api` paths rather than a hard-coded private IP address.
8. WHEN API documentation is generated THEN request and response schemas SHALL be available through an OpenAPI endpoint.

### Requirement 7: Persistent User Database

**User Story:** As an operator, I want users stored in a persistent database, so that accounts survive container restarts and application upgrades.

#### Acceptance Criteria

1. WHEN PostgreSQL initializes for the first time THEN it SHALL create or use the database named exactly `xiangyue_xiamen`.
2. WHEN the application schema is migrated THEN it SHALL contain a `users` table with, at minimum, `id`, `username`, `password_hash`, `is_active`, `created_at`, `updated_at`, and `last_login_at`.
3. WHEN a password is registered or changed THEN the backend SHALL store only a modern adaptive password hash and SHALL never store plaintext or reversible password data.
4. WHEN two users attempt to register the same normalized username THEN the database SHALL enforce uniqueness in addition to application validation.
5. WHEN the database container restarts or is recreated THEN user rows SHALL remain available through a named volume or explicit Mac host volume.
6. WHEN schema changes are introduced THEN they SHALL be applied through versioned migrations rather than manual database edits.
7. WHEN containers communicate with PostgreSQL THEN they SHALL use a private Compose network and SHALL not publish the database port to the public network.
8. WHEN the backend starts before PostgreSQL is ready THEN it SHALL wait for database health or fail clearly without corrupting state.

### Requirement 8: Vue Registration and Login Experience

**User Story:** As a user, I want a clear and responsive registration/login experience, so that account access is easy on desktop and iPhone browsers.

#### Acceptance Criteria

1. WHEN the login or registration view is rendered THEN the layout SHALL remain usable at mobile and desktop viewport sizes.
2. WHEN a form submission is active THEN the submit action SHALL show progress and SHALL prevent duplicate submissions.
3. WHEN validation fails THEN errors SHALL appear next to the relevant field and SHALL not shift or overlap surrounding controls.
4. WHEN a backend error occurs THEN the frontend SHALL show an actionable message without exposing stack traces or internal exception text.
5. WHEN a password field is displayed THEN it SHALL provide an accessible show/hide control.
6. WHEN keyboard-only navigation is used THEN all form fields, links, and actions SHALL have a visible focus state and logical order.
7. WHEN the authentication pages are implemented THEN they SHALL use Vue Router guards and a dedicated API client rather than direct requests embedded throughout components.
8. WHEN the frontend is built for deployment THEN static assets SHALL be served by the frontend container and unknown application routes SHALL fall back to the Vue entry document.

### Requirement 9: Docker Compose Deployment on the Remote Mac

**User Story:** As an operator, I want the complete application to run through Docker Compose on the remote Mac, so that deployment is repeatable and does not require paid application hosting.

#### Acceptance Criteria

1. WHEN `docker compose up -d` is executed on `192.168.1.33` THEN the frontend, backend, and PostgreSQL services SHALL start successfully.
2. WHEN a service process exits unexpectedly THEN the Compose restart policy SHALL restart it automatically.
3. WHEN service readiness is evaluated THEN each long-running service SHALL define a useful health check.
4. WHEN Compose configuration is committed THEN it SHALL contain no plaintext production password, signing secret, or tunnel credential.
5. WHEN runtime secrets are configured THEN they SHALL be loaded from an untracked environment file or protected secret file on the remote Mac.
6. WHEN the deployment is inspected THEN all application compute and persistent application storage SHALL be on the remote Mac, not on AWS.
7. WHEN the Mac reboots THEN the chosen Docker-compatible runtime and Compose project SHALL have a documented automatic-start strategy.
8. WHEN the Mac display locks or turns off THEN the containers SHALL remain running, provided the Mac itself does not sleep.
9. IF the Mac lid is closed without supported closed-display mode THEN the deployment SHALL be considered unavailable rather than claiming continuous service.
10. WHEN implementation begins THEN the operator SHALL first verify a working Docker Engine and Compose v2 runtime because Docker Desktop is installed but its daemon is not currently running, and Colima is installed but stopped.

### Requirement 10: Public Domain and HTTPS Access

**User Story:** As a remote user, I want to open the application through a domain name from another network, so that I do not need to know the Mac's private IP or join its home Wi-Fi.

#### Acceptance Criteria

1. WHEN public deployment is complete THEN the application SHALL be reachable through an approved HTTPS hostname under a user-controlled domain.
2. WHEN a visitor uses the public hostname THEN DNS and the public relay SHALL route the request to the Mac-hosted frontend without requiring the visitor to install Clash, Shadowrocket, Tailscale, or another VPN client.
3. WHEN public traffic reaches the application THEN TLS SHALL terminate at a trusted public endpoint and HTTP traffic SHALL redirect to HTTPS where applicable.
4. WHEN public ingress is configured THEN PostgreSQL, the raw backend port, Docker control sockets, and Mac administration ports SHALL remain unexposed.
5. WHEN an ingress approach is selected during design THEN it SHALL preserve the existing `v2.hermes-node.com` Xray VPN behavior and SHALL include a rollback procedure.
6. IF the existing Xray service cannot safely provide reverse ingress by itself THEN the design SHALL add a reverse-tunnel or reverse-proxy component while continuing to keep all application compute and data on the Mac.
7. WHEN the Mac, home network, or reverse path is unavailable THEN the public endpoint SHALL fail closed and SHALL not route to a stale or alternate copy of application data.
8. WHEN public registration and login are enabled THEN basic request throttling SHALL be active before the hostname is announced for use.

### Requirement 11: Local Persistence, Backup, and Recovery

**User Story:** As an operator, I want a simple local backup and restore procedure, so that accidental container or database failure does not destroy registered accounts.

#### Acceptance Criteria

1. WHEN a database backup is created THEN it SHALL be written to a protected directory on the remote Mac outside the live PostgreSQL container filesystem.
2. WHEN automated backups are enabled THEN the system SHALL retain a documented finite number of backups and remove older files safely.
3. WHEN a restore test is performed THEN the documented procedure SHALL restore the schema and users into a clean `xiangyue_xiamen` database.
4. WHEN backup files are stored THEN their permissions SHALL prevent access by unrelated local users.
5. WHEN version 1 is delivered THEN at least one successful backup-and-restore smoke test SHALL be recorded.

### Requirement 12: Verification and Quality Gates

**User Story:** As a developer, I want automated verification of the authentication flow and deployment, so that the scaffold is trustworthy before feature work begins.

#### Acceptance Criteria

1. WHEN backend tests run THEN they SHALL cover registration success, duplicate username, invalid input, login success, login failure, current-user access, expired/invalid authentication, and logout.
2. WHEN database integration tests run THEN they SHALL execute against PostgreSQL and SHALL verify migration and uniqueness behavior.
3. WHEN frontend tests run THEN they SHALL cover form validation, API success/error handling, router protection, and logout behavior.
4. WHEN end-to-end tests run THEN they SHALL register a new user, log in, open the protected page, refresh it, and log out using the composed application.
5. WHEN the production Compose stack is started THEN a smoke test SHALL verify frontend, API health, database health, and public HTTPS access independently.
6. WHEN source is committed THEN linting, type checking, backend tests, frontend tests, and Compose configuration validation SHALL pass.

## Non-Functional Requirements

### Code Architecture and Modularity

- **Single Responsibility Principle:** Authentication routes, business logic, persistence, schema definitions, Vue views, API client code, and route guards shall remain separate.
- **Modular Design:** Registration and login components shall be reusable without embedding deployment or database logic.
- **Dependency Management:** Backend and frontend dependencies shall be minimal for version 1 and locked reproducibly.
- **Clear Interfaces:** The OpenAPI contract and database migration history shall define stable boundaries.

### Performance

- Under normal personal/demo load, API requests excluding network transit should target a 95th-percentile response time below 500 ms on the M1 Mac.
- Authentication pages should become interactive within 3 seconds on a typical mobile connection after uncached navigation.
- Version 1 does not require horizontal scaling or high-volume load guarantees.

### Security

- Passwords shall be hashed with an established adaptive algorithm provided by a maintained library.
- Authentication signing material shall be random, environment-provided, and rotatable.
- Authentication credentials shall not be stored in browser `localStorage`.
- API responses and logs shall not contain passwords, password hashes, cookie values, or signing secrets.
- Backend input shall be schema-validated and database access shall be parameterized through the selected data layer.
- Production CORS shall be same-origin or explicitly allow only the approved public hostname.
- Public endpoints shall use HTTPS, security headers, and basic registration/login throttling.

### Reliability

- Compose services shall use restart policies and health checks.
- PostgreSQL data shall survive service and container recreation.
- The public site is dependent on the Mac remaining powered, awake, connected, and lid-open unless supported closed-display mode is configured.
- Deployment shall not modify or interrupt the existing Hermes gateways or Xray VPN without an explicit, reversible design step.

### Usability

- Registration and login shall be understandable without visible implementation instructions.
- Forms shall work with desktop and iPhone Safari/Chrome-class browsers.
- Error messages shall identify corrective action without revealing security-sensitive details.

### Cost and Data Residency

- Version 1 shall add no paid application compute, managed database, or object-storage service.
- Frontend, backend, database, logs, and backups shall reside on the remote Mac.
- Reuse of the already-running Lightsail/Xray gateway is permitted only as a traffic relay and shall not move application execution or persistent data to AWS.

## Explicitly Out of Scope for Version 1

- LDAP, Okta, Google, Apple, GitHub, or other federated login
- Email addresses, email verification, password reset, and notification delivery
- Multi-factor authentication and passkeys
- Role-based access control, administrator UI, and user-management UI
- User profile editing and password change
- Social features, Harbor Market business modules, payment, or marketplace workflows
- Redis, Celery, background workers, queues, and scheduled jobs
- Multi-region deployment, high availability, and horizontal scaling
- AWS-hosted frontend, backend, database, or application storage
- Public database access or direct public access to the Mac's Docker/API ports

## Verified Environment Notes

- Remote host: `JenniferMac.local`, macOS `14.3.1`, Apple M1, 8 cores, 16 GB RAM.
- Available data-disk capacity at assessment: approximately 162 GiB.
- Native MySQL `8.3.0` is already running at `127.0.0.1:3306`; it shall remain untouched.
- Docker Desktop and Docker/Compose binaries are installed, but no Docker daemon is currently available.
- Colima `0.9.1` is installed but not running.
- The public-ingress design must account for Clash's active local proxy and verify that the selected reverse connector remains stable through network changes.

## Open Decisions for the Design Phase

1. Select the Python web framework and ORM/migration stack while keeping the authentication contract above unchanged.
2. Select Docker Desktop or Colima as the persistent Docker Engine on the remote Mac.
3. Confirm the public hostname; `app.hermes-node.com` is the working proposal.
4. Select and validate the reverse-ingress mechanism: Xray reverse configuration, Lightsail reverse SSH plus proxy, or Cloudflare Tunnel.
5. Select the authentication-cookie lifetime and exact password/username validation limits within the security requirements above.

