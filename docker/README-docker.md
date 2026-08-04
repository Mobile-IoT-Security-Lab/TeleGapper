# Running TeleGapper in Docker

## Why the split

TeleGapper drives a **physical Android device over USB**, and Docker Desktop on
macOS has no USB passthrough. So the container cannot own the device.

What runs where:

| Component | Where | Why |
|---|---|---|
| `Automator.py`, `scripts/*` | container | pure Python, no device ownership needed |
| `ScraperMiniApp/scraper.py` | container | only needs Chromium |
| adb **server** | host | holds the USB transport |
| Appium | host | needs local `adb forward` ports and the chromedriver binaries |
| Burp Suite | host | intercepting proxy the device points at |

The container's adb *client* talks to the host adb *server* over TCP
(`ADB_SERVER_SOCKET`), which is enough for everything the Python code does:
`shell`, `screencap`, `pull`, `tap`, `reboot`. Appium stays on the host because
its `adb forward` ports would otherwise be opened on the host and be unreachable
from inside the container.

## One-time setup

1. `cp .env.example .env` and fill in the device serial (`adb devices`) and the
   chromedriver paths. Those two chromedriver paths are **host** paths — Appium
   resolves them, and Appium runs on the host.

2. Make sure `burp_log.txt` exists at the path in `BURP_LOG_HOST_PATH`; the
   container truncates and reads it in place through a bind mount.

3. Build:

   ```bash
   docker compose build
   ```

## Every run

On the **host**, before starting a container:

```bash
# adb server reachable from Docker (the default binds 127.0.0.1 only)
adb kill-server
adb -a -P 5037 nodaemon server &

# Appium listening on all interfaces
appium --address 0.0.0.0 --allow-insecure uiautomator2:chromedriver_autodownload
```

> `adb -a` exposes the adb server on every interface, which means any host on
> your LAN can drive the attached device. Keep it behind a firewall, and stop it
> (`adb kill-server`) when you are done.

Then:

```bash
# batch over ScraperMiniApp/rerun.txt
docker compose run --rm pipeline

# single bot
docker compose run --rm pipeline python Automator.py @DetectivePuzzlesBot

# tapps.center catalog scraper (no device needed) -> ./out/tapps_apps_live.csv
docker compose run --rm scraper
```

The entrypoint runs a preflight and fails fast with the exact remedy if the adb
server is unreachable, no device is online, or Appium is not answering. Set
`SKIP_PREFLIGHT=true` to bypass it.

## Outputs

Everything lands on the host through bind mounts:

- `./Analysis results/` — xml, html, screenshots, traffic, reports, privacy policies
- `./out/tapps_apps_live.csv` — scraper output

## Running on the host instead

The code still works outside Docker. Set `APPIUM_AUTOSTART=true` and
`ADB_MANAGE_SERVER=true` (the defaults when unset), and point `APPS_DIR` /
`BURP_LOG_FILE` at host paths. Those two flags exist so the containerized run
never spawns an Appium it cannot see, and never issues `adb kill-server` against
the host's server — which would take Appium down with it.
