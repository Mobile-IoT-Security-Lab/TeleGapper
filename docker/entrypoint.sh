#!/usr/bin/env bash
# Preflight for the containerized pipeline: everything it drives (adb server,
# Appium, Burp log) lives outside the container, so fail loudly and early
# instead of halfway through a bot run.
set -euo pipefail

log() { printf '%s\n' "$*"; }

if [ "${SKIP_PREFLIGHT:-false}" = "true" ]; then
    exec "$@"
fi

log "🐳 TeleGapper container preflight"
log "   adb server : ${ADB_SERVER_SOCKET:-<unset>}"
log "   appium     : ${APPIUM_SERVER_URL:-<unset>}"

# --- adb server on the host -------------------------------------------------
# The host must expose it on all interfaces: `adb -a -P 5037 nodaemon server`.
# A local `adb start-server` binds 127.0.0.1 only and is unreachable from here.
if ! devices="$(adb devices 2>&1)"; then
    log "❌ Cannot reach the adb server at ${ADB_SERVER_SOCKET:-<unset>}"
    log "   On the host run: adb kill-server && adb -a -P 5037 nodaemon server"
    exit 1
fi

if ! printf '%s' "$devices" | grep -qE '\sdevice$'; then
    log "❌ adb server reachable but no device is online:"
    printf '%s\n' "$devices"
    log "   Check the USB cable, 'Allow USB debugging' and that the screen is unlocked."
    exit 1
fi
log "✅ adb device online:"
printf '%s\n' "$devices" | sed '/^$/d;1d;s/^/   /'

# --- Appium on the host -----------------------------------------------------
appium_url="${APPIUM_SERVER_URL:-http://host.docker.internal:4723}"
if ! curl -fsS --max-time 5 "${appium_url}/status" >/dev/null 2>&1; then
    log "❌ Appium is not answering at ${appium_url}/status"
    log "   On the host run: appium --address 0.0.0.0 --allow-insecure uiautomator2:chromedriver_autodownload"
    exit 1
fi
log "✅ Appium reachable at ${appium_url}"

# --- Burp log ---------------------------------------------------------------
# Not fatal: traffic analysis degrades gracefully, the rest of the run does not.
burp_log="${BURP_LOG_FILE:-}"
if [ -n "$burp_log" ] && [ ! -f "$burp_log" ]; then
    log "⚠️  Burp log not mounted at ${burp_log} — traffic capture will be empty."
    log "   Create the file on the host and bind-mount it (see docker-compose.yml)."
fi

exec "$@"
