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

Run the commands below from the repository root. No `.env.example`, Burp log or
batch input file is bundled.

1. Create `.env` with your device serial (`adb devices`), Android version and
   chromedriver paths. For example, adapting the values to your machine:

   ```env
   ANDROID_DEVICE_NAME="<adb devices serial>"
   ANDROID_PLATFORM_VERSION="16"
   CHROMEDRIVER_DIR="/absolute/host/path/to/chromedrivers"
   CHROMEDRIVER_MAPPING_FILE="/absolute/host/path/to/chromedriver_mapping.json"
   BURP_LOG_HOST_PATH="./burp_log.txt"
   ```

   The two chromedriver paths are **host** paths: Appium resolves them on the
   host. Leave `ANDROID_DEVICE_NAME` empty to let Appium pick the only attached
   device. Compose supplies the container's `APPS_DIR` and `BURP_LOG_FILE`;
   `BURP_LOG_HOST_PATH` selects the host file mounted as that log.

2. Create the local files used by the pipeline's bind mounts:

   ```bash
   touch burp_log.txt
   touch ScraperMiniApp/rerun.txt
   ```

   If `BURP_LOG_HOST_PATH` points elsewhere, create that file instead. Configure
   Burp to continuously log Proxy requests/responses there, and configure the
   device to use Burp. The pipeline truncates the source log between phases.
   For batch mode, edit `ScraperMiniApp/rerun.txt` to contain one bot username
   per line, including `@`, with no blank lines. Both files must exist even for
   a single-bot invocation because Compose always mounts them.

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

The scraper service can also be used on its own: it needs neither the pipeline
input files nor the host adb/Appium/Burp services. The scraper targets the
historical site layout; see [its README](../ScraperMiniApp/README.md) for the
compatibility limitation.

The entrypoint runs a preflight and fails fast with the exact remedy if the adb
server is unreachable, no device is online, or Appium is not answering. Set
`SKIP_PREFLIGHT=true` to bypass it.

## Outputs

Everything lands on the host through bind mounts:

- `./Analysis results/` — HTML, initial/interaction traffic (`trafficStart/`, `trafficEnd/`), reports (`resStart/`, `resEnd/`), click logs (`dynamicAnalysis/`) and retrieved privacy policies (`PrivacyPolicies/`)
- `./out/tapps_apps_live.csv` — scraper output

The pipeline creates its output directories automatically. XML dumping and
screenshot capture are currently disabled in `scripts/DynamicAnalysis.py`.
See the [main README](../README.md#output) for file naming.

## Running on the host instead

Follow the [main README setup](../README.md#setup) to install dependencies and
configure host paths. Set `APPIUM_AUTOSTART=true` and
`ADB_MANAGE_SERVER=true` (the defaults when unset), and point `APPS_DIR` /
`BURP_LOG_FILE` at host paths. Those two flags exist so the containerized run
never spawns an Appium it cannot see, and never issues `adb kill-server` against
the host's server — which would take Appium down with it.
