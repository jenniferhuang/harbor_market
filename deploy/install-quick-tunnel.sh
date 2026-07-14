#!/usr/bin/env bash
set -euo pipefail

project_dir="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
brew_bin="${BREW_BIN:-/opt/homebrew/bin/brew}"
label="com.harbor-market.quick-tunnel"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$HOME/Library/Logs/HarborMarket"

if [[ ! -x /opt/homebrew/bin/cloudflared ]]; then
  if [[ ! -x "$brew_bin" ]]; then
    printf 'Native Homebrew is required at %s\n' "$brew_bin" >&2
    exit 1
  fi
  "$brew_bin" install cloudflared
fi

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"
error_log="$log_dir/quick-tunnel.err.log"
if [[ -f "$error_log" ]]; then
  first_new_line=$(( $(wc -l < "$error_log") + 1 ))
else
  first_new_line=1
fi
cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$project_dir/deploy/quick-tunnel.sh</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>StandardOutPath</key>
  <string>$log_dir/quick-tunnel.out.log</string>
  <key>StandardErrorPath</key>
  <string>$log_dir/quick-tunnel.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$plist"
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl enable "gui/$(id -u)/$label"
launchctl kickstart -k "gui/$(id -u)/$label"

for attempt in $(seq 1 45); do
  recent_log="$(tail -n +"$first_new_line" "$error_log" 2>/dev/null || true)"
  public_url="$(printf '%s\n' "$recent_log" | grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' | tail -1 || true)"
  if [[ -n "$public_url" && "$recent_log" == *"Registered tunnel connection"* ]]; then
    printf '%s\n' "$public_url"
    exit 0
  fi
  sleep 2
done

tail -n +"$first_new_line" "$error_log" 2>/dev/null | tail -80 >&2 || true
printf 'Quick Tunnel did not register a public edge connection.\n' >&2
exit 1
