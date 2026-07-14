#!/usr/bin/env bash
set -euo pipefail

cloudflared_bin="${CLOUDFLARED_BIN:-/opt/homebrew/bin/cloudflared}"
if [[ ! -x "$cloudflared_bin" ]]; then
  printf 'cloudflared is required at %s\n' "$cloudflared_bin" >&2
  exit 1
fi

# Clash can advertise unreachable IPv6 edge addresses and disrupt QUIC.
export TUNNEL_TRANSPORT_PROTOCOL="${TUNNEL_TRANSPORT_PROTOCOL:-http2}"
export TUNNEL_EDGE_IP_VERSION="${TUNNEL_EDGE_IP_VERSION:-4}"

exec "$cloudflared_bin" tunnel \
  --no-autoupdate \
  --url "${HARBOR_MARKET_ORIGIN:-http://127.0.0.1:8080}"
