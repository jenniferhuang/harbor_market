FROM node:22.14.0-alpine3.21 AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN --mount=type=cache,id=harbor-market-npm,target=/root/.npm,sharing=locked \
    for attempt in 1 2 3; do \
        npm ci --no-audit --no-fund && exit 0; \
        if [ "$attempt" -eq 3 ]; then exit 1; fi; \
        rm -rf node_modules; \
        sleep 5; \
    done

COPY . .
RUN npm run build

FROM nginx:1.27.4-alpine3.21 AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
