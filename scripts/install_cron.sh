#!/usr/bin/env bash
# Install a launchd job that runs `jd run` every morning at 07:00 SGT.
#
# On macOS, launchd uses LOCAL time. If your machine is in +07 (Thailand)
# and you want 07:00 SGT (+08), the local trigger should be 06:00 local.
# Adjust LOCAL_HOUR below if your timezone differs.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_LABEL="com.jdsearch.daily"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

# Default: run at 06:00 local (Bangkok) = 07:00 SGT
LOCAL_HOUR="${LOCAL_HOUR:-6}"
LOCAL_MINUTE="${LOCAL_MINUTE:-0}"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${PLIST_LABEL}</string>

  <key>WorkingDirectory</key>
  <string>${PROJECT_DIR}</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-lc</string>
    <string>cd "${PROJECT_DIR}" && /opt/homebrew/bin/uv run jd run 2>&amp;1</string>
  </array>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${LOCAL_HOUR}</integer>
    <key>Minute</key>
    <integer>${LOCAL_MINUTE}</integer>
  </dict>

  <key>StandardOutPath</key>
  <string>${PROJECT_DIR}/data/cron.log</string>

  <key>StandardErrorPath</key>
  <string>${PROJECT_DIR}/data/cron.err.log</string>

  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo "installed launchd job at ${PLIST_PATH}"
echo "will run daily at ${LOCAL_HOUR}:$(printf '%02d' "$LOCAL_MINUTE") local time"
echo "logs: ${PROJECT_DIR}/data/cron.log"
echo ""
echo "to uninstall:"
echo "  launchctl unload ${PLIST_PATH} && rm ${PLIST_PATH}"
