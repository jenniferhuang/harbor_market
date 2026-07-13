#!/usr/bin/env bash

project_dir="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

find_docker() {
  local candidate
  for candidate in /opt/homebrew/bin/docker "$(command -v docker 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      if [[ "$(uname -m)" == "arm64" && "$candidate" == /usr/local/* ]]; then
        continue
      fi
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

find_colima() {
  local candidate
  for candidate in /opt/homebrew/bin/colima "$(command -v colima 2>/dev/null || true)"; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      if [[ "$(uname -m)" == "arm64" && "$candidate" == /usr/local/* ]]; then
        continue
      fi
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

wait_for_docker() {
  local docker_bin="$1"
  local attempts="${2:-90}"
  local count
  for ((count = 1; count <= attempts; count++)); do
    if "$docker_bin" info >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

start_docker_engine() {
  local docker_bin="$1"

  if "$docker_bin" info >/dev/null 2>&1; then
    return 0
  fi

  local colima_bin
  if colima_bin="$(find_colima)"; then
    "$colima_bin" start --cpu 4 --memory 6 --disk 40
    wait_for_docker "$docker_bin" 60
    return
  fi

  printf 'Native arm64 Colima is required at /opt/homebrew/bin/colima.\n' >&2
  return 1
}

compose() {
  local docker_bin="$1"
  shift
  "$docker_bin" compose --project-directory "$project_dir" "$@"
}
