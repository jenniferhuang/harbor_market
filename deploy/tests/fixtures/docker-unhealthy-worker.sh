#!/usr/bin/env bash
set -euo pipefail

last_argument=""
for argument in "$@"; do
  last_argument="$argument"
done

if [[ "${1:-}" == inspect ]]; then
  case "$last_argument" in
    minio-id)
      printf 'healthy\n'
      ;;
    minio-init-id)
      printf 'exited:0\n'
      ;;
    cleanup-worker-id)
      printf 'running:unhealthy\n'
      ;;
    *)
      printf 'Unexpected fake inspect target: %s\n' "$last_argument" >&2
      exit 2
      ;;
  esac
  exit 0
fi

if [[ "${1:-}" != compose ]]; then
  printf 'Unexpected fake Docker command: %s\n' "$*" >&2
  exit 2
fi

case "$*" in
  *' ps -aq minio-init')
    printf 'minio-init-id\n'
    ;;
  *' ps -q cleanup-worker')
    printf 'cleanup-worker-id\n'
    ;;
  *' ps -q minio')
    printf 'minio-id\n'
    ;;
  *' logs --tail=200 cleanup-worker')
    printf 'simulated unhealthy cleanup worker\n'
    ;;
  *' ps')
    printf 'simulated compose status\n'
    ;;
  *)
    printf 'Unexpected fake Compose command: %s\n' "$*" >&2
    exit 2
    ;;
esac
