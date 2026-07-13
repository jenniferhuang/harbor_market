# Harbor Market frontend

Vue 3 single-page application for registration, login, cookie-backed session restoration, and the protected Home route.

## Local development

Requires Node.js 20.19 through 22.

```bash
npm ci
npm run dev
```

Vite serves the app at `http://localhost:5173` and proxies same-origin `/api` requests to `http://127.0.0.1:8000`.

## Quality checks

```bash
npm run lint
npm run test
npm run build
```

## Container

The multi-stage `Dockerfile` builds the static app and serves it with nginx. nginx falls unknown application routes back to `index.html`, exposes `/healthz`, and proxies `/api/*` to the Compose service `backend:8000`.
