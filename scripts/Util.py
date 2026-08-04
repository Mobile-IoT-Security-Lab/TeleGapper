#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import json
import tempfile
# from playwright.sync_api import sync_playwright
from weasyprint import HTML as WeasyHTML
import requests
from appium import webdriver
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langdetect import detect
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy


load_dotenv()


# Repository root, derived from this file's location so it never depends on the
# machine it runs on or on the directory the process was started from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def absolute_path(value: str | None) -> str | None:
    """Turn a configured path into an absolute one anchored at PROJECT_ROOT.

    Paths come from .env or from the defaults below and may be relative; they
    are resolved against the repository root instead of the current working
    directory, so a run started from anywhere reads and writes the same files.
    An already absolute value (the container paths in docker-compose.yml, for
    instance) is kept as is. Returns None for an unset value, so callers can
    still tell "not configured" apart from a real path.
    """
    if not value:
        return None
    return str((PROJECT_ROOT / value).resolve())


class DeviceWedgedError(RuntimeError):
    """Raised when the adb bridge to the device is dead (adbd wedged).

    Used to abort the current round and bubble control back up to the device
    reboot, instead of hanging forever on adb calls that will never return.
    """


class Util:
    """Shared project utilities."""

    CLICKABLE_SELECTOR = (
        "a[href],button,input[type='button'],input[type='submit'],"
        "[role='button'],[role='tab'],[role='link'],"
        "[onclick],[tabindex]:not([tabindex='-1']),"
        "[class*='btn'],[class*='button'],[class*='tab'],[class*='nav'],"
        "[class*='card'],[class*='close'],[class*='item'],[class*='clickable']"
    )

    APPS_DIR = absolute_path(os.getenv("APPS_DIR") or "Analysis results")
    APPIUM_SERVER_URL = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")
    APPIUM_ALLOW_INSECURE = os.getenv(
        "APPIUM_ALLOW_INSECURE",
        "uiautomator2:chromedriver_autodownload",
    )
    APPIUM_LOG_FILE = absolute_path(
        os.getenv("APPIUM_LOG_FILE") or "appium_server.log"
    )
    BURP_LOG_FILE = absolute_path(os.getenv("BURP_LOG_FILE"))

    # When the pipeline runs in a container, Appium and the adb server live on
    # the host: spawning or killing them from here is either impossible (no
    # `appium` binary) or actively harmful (`adb kill-server` would take down
    # the host server every other component is attached to).
    APPIUM_AUTOSTART = os.getenv("APPIUM_AUTOSTART", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    ADB_MANAGE_SERVER = os.getenv("ADB_MANAGE_SERVER", "true").lower() not in {
        "0",
        "false",
        "no",
    }

    @classmethod
    def ensure_apps_dir(cls) -> Path:
        """Create the analysis results directory if it does not exist."""
        path = Path(cls.APPS_DIR)
        path.mkdir(parents=True, exist_ok=True)
        for directory in (
            "xml",
            "html",
            "screenshotStart",
            "screenshotEnd",
            "dynamicAnalysis",
            "trafficStart",
            "trafficEnd",
            "resStart",
            "resEnd",
        ):
            (path / directory).mkdir(parents=True, exist_ok=True)
        print(f"📁 Directory '{path}' ready.")
        return path

    @classmethod
    def _appium_connection_info(cls) -> tuple[str, str, int, str]:
        parsed = urlparse(cls.APPIUM_SERVER_URL)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4723
        base_path = parsed.path.rstrip("/")
        return scheme, host, port, base_path

    @classmethod
    def _appium_status_url(cls) -> str:
        scheme, host, port, base_path = cls._appium_connection_info()
        return f"{scheme}://{host}:{port}{base_path}/status"

    @classmethod
    def is_appium_running(cls, timeout: float = 1.5) -> bool:
        """Return True if Appium responds on /status."""
        try:
            req = Request(
                cls._appium_status_url(),
                headers={"Accept": "application/json"},
            )
            with urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except URLError:
            return False
        except Exception:
            return False

    @staticmethod
    def _find_pids_on_port(port: int) -> list[str]:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    @classmethod
    def stop_appium_server(cls) -> None:
        """Stop any Appium processes listening on the configured port."""
        if not cls.APPIUM_AUTOSTART:
            print("↩️ APPIUM_AUTOSTART=false: leaving the external Appium alone.")
            return

        _, _, port, _ = cls._appium_connection_info()
        pids = cls._find_pids_on_port(port)
        appium_pids: list[str] = []

        for pid in pids:
            proc = subprocess.run(
                ["ps", "-p", pid, "-o", "command="],
                check=False,
                capture_output=True,
                text=True,
            )
            if "appium" in proc.stdout.lower():
                appium_pids.append(pid)

        if not appium_pids:
            return

        print(f"🛑 Stopping Appium on port {port}...")
        for pid in appium_pids:
            subprocess.run(["kill", "-TERM", pid], check=False, capture_output=True)

        deadline = time.time() + 10
        while time.time() < deadline:
            if not cls.is_appium_running():
                print("✅ Appium server stopped.")
                return
            time.sleep(0.5)

        for pid in appium_pids:
            subprocess.run(["kill", "-KILL", pid], check=False, capture_output=True)
        time.sleep(1)
        if cls.is_appium_running():
            raise RuntimeError("Appium is still running after stop attempt.")
        print("✅ Appium server stopped.")

    @classmethod
    def start_appium_server(cls, restart: bool = False, timeout: int = 40) -> None:
        """Start Appium in the background and wait until it is reachable."""
        if restart:
            cls.stop_appium_server()

        if cls.is_appium_running():
            print(f"✅ Appium already running at {cls.APPIUM_SERVER_URL}")
            return

        # Externally managed Appium (e.g. on the host, with this pipeline in a
        # container): wait for it to come back instead of spawning our own.
        if not cls.APPIUM_AUTOSTART:
            print(f"⏳ Waiting for external Appium at {cls.APPIUM_SERVER_URL}...")
            deadline = time.time() + timeout
            while time.time() < deadline:
                if cls.is_appium_running():
                    print(f"✅ Appium server is ready at {cls.APPIUM_SERVER_URL}")
                    return
                time.sleep(1)
            raise RuntimeError(
                f"External Appium is not reachable at {cls.APPIUM_SERVER_URL}. "
                "Start it on the host with: appium --address 0.0.0.0 "
                "--allow-insecure uiautomator2:chromedriver_autodownload"
            )

        scheme, host, port, base_path = cls._appium_connection_info()
        if scheme not in {"http", "https"}:
            raise ValueError(f"Unsupported Appium URL scheme: {scheme}")

        cmd = ["appium", "--address", host, "--port", str(port)]
        if base_path:
            cmd.extend(["--base-path", base_path])
        if cls.APPIUM_ALLOW_INSECURE:
            cmd.extend(["--allow-insecure", cls.APPIUM_ALLOW_INSECURE])

        log_path = Path(cls.APPIUM_LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        print(f"🚀 Starting Appium ({host}:{port})...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cls.is_appium_running():
                print(f"✅ Appium server is ready at {cls.APPIUM_SERVER_URL}")
                return
            if process.poll() is not None:
                break
            time.sleep(1)

        raise RuntimeError(
            f"Failed to start Appium at {cls.APPIUM_SERVER_URL}. Check log: {log_path}"
        )

    @staticmethod
    def adb(
        args: list[str],
        device_id: str | None = None,
        timeout: float = 15.0,
        text: bool = True,
    ) -> "subprocess.CompletedProcess | None":
        """Run an adb command with a timeout that is ALWAYS set.

        Returns the CompletedProcess, or None if the command times out
        (device wedged) — the caller decides what to do with None. It does not
        raise on timeout. This is the single choke point for every adb call,
        so no command can block the run forever.
        """
        cmd = ["adb"] + (["-s", device_id] if device_id else []) + args
        try:
            return subprocess.run(
                cmd, check=False, capture_output=True, text=text, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            print(f"⏱ adb timeout ({timeout}s): {' '.join(args)}")
            return None

    @staticmethod
    def wait_for_device_ready(timeout: int = 240, device_id: str | None = None) -> None:
        """Wait until the Android device is online and finished booting."""
        print("⏳ Waiting for Android device...")
        deadline = time.time() + timeout

        # wait-for-device with a bounded timeout: otherwise on an offline device
        # it would block forever and the loop deadline would never be checked.
        while time.time() < deadline:
            if Util.adb(["wait-for-device"], device_id=device_id, timeout=10) is not None:
                break

        while time.time() < deadline:
            res = Util.adb(
                ["shell", "getprop", "sys.boot_completed"],
                device_id=device_id,
                timeout=8,
            )
            completed = res.stdout.strip() if res else ""
            if completed == "1":
                Util.adb(
                    ["shell", "input", "keyevent", "82"],
                    device_id=device_id,
                    timeout=8,
                )
                print("✅ Device is online and boot completed.")
                return
            time.sleep(2)

        raise TimeoutError("Android device did not finish booting in time.")

    @staticmethod
    def enable_webview_debug() -> None:
        """Enable WebView debugging on the device."""
        print("🔧 Enabling WebView debugging...")
        result = Util.adb(
            ["shell", "setprop", "debug.chromium.webview_debug", "true"], timeout=10
        )
        if result is None:
            print("⚠️ Unable to enable WebView debugging: adb timed out.")
            return
        if result.returncode == 0:
            print("✅ WebView debugging enabled.")
            return
        print(f"⚠️ Unable to enable WebView debugging: {result.stderr.strip()}")

    @staticmethod
    def restart_adb_server() -> None:
        """Restart the adb server (kill-server + start-server)."""
        if not Util.ADB_MANAGE_SERVER:
            # With ADB_SERVER_SOCKET pointing at a remote server, kill-server
            # would kill *that* one — the host's, taking Appium down with it.
            print("↩️ ADB_MANAGE_SERVER=false: skipping adb server restart.")
            return

        print("🔄 Restarting adb server...")
        Util.adb(["kill-server"], timeout=10)
        result = Util.adb(["start-server"], timeout=15)
        if result is not None and result.returncode == 0:
            print("✅ adb server restarted.")
            return
        detail = result.stderr.strip() if result is not None else "adb timed out"
        print(f"⚠️ Unable to restart adb server: {detail}")

    @staticmethod
    def adb_healthcheck(
        device_id: str | None = None,
        timeout: float = 5.0,
        attempt_recover: bool = True,
    ) -> bool:
        """Check that the adb shell channel to the device responds.

        A heavy mini app (e.g. a miner) can saturate the device and wedge
        adbd: `adb shell` hangs forever and every following Appium/contexts
        call comes back empty. Here we send a short-timeout `echo`; if it does
        not respond we try an `adb reconnect` and test once more.

        Returns True if the shell responds, False if the device is wedged.
        """

        def _ping() -> bool:
            result = Util.adb(
                ["shell", "echo", "ping"], device_id=device_id, timeout=timeout
            )
            return (
                result is not None
                and result.returncode == 0
                and "ping" in (result.stdout or "")
            )

        if _ping():
            return True

        print("⚠️ adb shell not responding (device wedged?).")
        if not attempt_recover:
            return False

        print("   ↻ Trying adb reconnect...")
        Util.adb(["reconnect"], device_id=device_id, timeout=timeout)
        time.sleep(2)

        if _ping():
            print("✅ adb shell recovered after reconnect.")
            return True

        print("❌ adb shell still not responding: needs USB replug / device reboot.")
        return False

    @staticmethod
    def adb_tap(driver: webdriver.Remote, x: int, y: int) -> None:
        device_id = (
            driver.capabilities.get("udid")
            or driver.capabilities.get("deviceName")
            or driver.capabilities.get("appium:deviceName")
        )
        Util.adb(["shell", "input", "tap", str(x), str(y)], device_id=device_id, timeout=10)

    @staticmethod
    def bot_slug(bot: str) -> str:
        """Generate a clean file name from the bot."""
        return bot.replace("@", "").replace("bot", "")

    @classmethod
    def get_file_paths(cls, apps_dir: Path, bot: str) -> tuple[Path, Path]:
        """Return the paths for XML and HTML."""
        slug = cls.bot_slug(bot)
        return (
            apps_dir / f"xml/{slug}_ui_dump.xml",
            apps_dir / f"html/{slug}_mini_app.html",
        )

    @classmethod
    def _capture_screenshot(
        cls,
        bot: str,
        suffix: str,
        subdir: str,
        driver: webdriver.Remote | None = None,
    ) -> None:
        screenshot_name = f"{bot}_{suffix}.png"
        local_dir = Path(cls.APPS_DIR) / subdir
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / screenshot_name
        print("📸 Bot screenshot: " + bot)

        # Use ADB screencap instead of Appium screenshots.
        device_path = f"/sdcard/{screenshot_name}"
        screencap = Util.adb(["shell", "screencap", "-p", device_path], timeout=20)
        if screencap is None:
            print("⚠️ ADB screencap timed out (device wedged?).")
            return
        if screencap.returncode != 0:
            print(f"⚠️ ADB screencap failed: {screencap.stderr.strip()}")
            return

        pull = Util.adb(["pull", device_path, str(local_dir)], timeout=30)
        cleanup = Util.adb(["shell", "rm", device_path], timeout=10)
        if pull is None:
            print("⚠️ ADB pull timed out (device wedged?).")
            return
        if cleanup is None:
            print("⚠️ ADB screenshot cleanup timed out.")
        if pull.returncode == 0:
            print(f"✅ Screenshot saved via ADB: {local_path.name}")
        else:
            print(f"⚠️ ADB pull failed: {pull.stderr.strip()}")
        if cleanup is not None and cleanup.returncode != 0:
            print(f"⚠️ ADB screenshot cleanup failed: {cleanup.stderr.strip()}")

    @classmethod
    def home_screenshot(cls, bot: str, driver: webdriver.Remote | None = None) -> None:
        cls._capture_screenshot(bot, "homeActivity", "screenshotStart", driver=driver)

    @classmethod
    def end_screenshot(cls, bot: str, driver: webdriver.Remote | None = None) -> None:
        cls._capture_screenshot(bot, "EndActivity", "screenshotEnd", driver=driver)

    @staticmethod
    def reboot_device(device_id: str | None = None, boot_timeout: int = 240) -> None:
        print("🔄 Rebooting device...")

        # If the device went 'offline' (adbd wedged) `adb reboot` won't be picked
        # up, so try a reconnect first to get the transport back to 'device'.
        result = Util.adb(["reboot"], device_id=device_id, timeout=20)
        if result is None or result.returncode != 0:
            print("   ↻ reboot not accepted, trying adb reconnect...")
            Util.adb(["reconnect"], device_id=device_id, timeout=10)
            time.sleep(3)
            Util.adb(["reboot"], device_id=device_id, timeout=20)

        # Wait for the device to come back, capped so we never hang forever.
        if Util.adb(["wait-for-device"], device_id=device_id, timeout=boot_timeout) is None:
            print(
                "⚠️ Device did not come back after reboot within timeout: "
                "physical intervention may be needed (replug/power)."
            )
            return

        deadline = time.time() + boot_timeout
        while time.time() < deadline:
            booted = Util.adb(
                ["shell", "getprop", "sys.boot_completed"],
                device_id=device_id,
                timeout=10,
            )
            if booted is not None and (booted.stdout or "").strip() == "1":
                print("✅ Device rebooted and booted.")
                break
            time.sleep(3)
        else:
            print("⚠️ Boot not completed within timeout.")
            return

        Util.start_appium_server(restart=True)
        print("✅ Post-reboot Appium restart completed.")

    @classmethod
    def reset_burp_log(cls) -> None:
        """Empty the log file before starting the new bot."""
        print("🧹 Cleaning Burp log...")
        if not cls.BURP_LOG_FILE:
            raise RuntimeError("BURP_LOG_FILE is not configured.")

        try:
            log_path = Path(cls.BURP_LOG_FILE)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as file_handle:
                file_handle.write("")
            print(
                f"✅ Burp log cleared ({cls.BURP_LOG_FILE}). Ready to intercept."
            )
        except Exception as exc:
            print(f"❌ Error while clearing the log: {exc}")

    @classmethod
    def save_bot_trafficStart(cls, apps_dir: Path, bot: str) -> None:
        """Save the Burp log isolated for this bot."""
        if not cls.BURP_LOG_FILE:
            raise RuntimeError("BURP_LOG_FILE is not configured.")

        traffic_dir = apps_dir / "trafficStart"
        traffic_dir.mkdir(exist_ok=True)
        dest_file = traffic_dir / f"{bot}_traffic.txt"
        try:
            shutil.copy2(cls.BURP_LOG_FILE, dest_file)
            print(f"📦 Bot traffic saved to: {dest_file.name}")
        except FileNotFoundError:
            print(f"❌ Could not find file {cls.BURP_LOG_FILE}")
        except Exception as exc:
            print(f"❌ Error while saving traffic: {exc}")

    @classmethod
    def save_bot_trafficEnd(cls, apps_dir: Path, bot: str) -> None:
        """Save the Burp log isolated for this bot."""
        if not cls.BURP_LOG_FILE:
            raise RuntimeError("BURP_LOG_FILE is not configured.")

        traffic_dir = apps_dir / "trafficEnd"
        traffic_dir.mkdir(exist_ok=True)
        dest_file = traffic_dir / f"{bot}_traffic.txt"
        try:
            shutil.copy2(cls.BURP_LOG_FILE, dest_file)
            print(f"📦 Bot traffic saved to: {dest_file.name}")
        except FileNotFoundError:
            print(f"❌ Could not find file {cls.BURP_LOG_FILE}")
        except Exception as exc:
            print(f"❌ Error while saving traffic: {exc}")

    @staticmethod
    def get_base_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def verify_webview(driver: webdriver.Remote) -> bool:
        try:
            if len(driver.window_handles) > 1:
                print("⚠️ Nuova window/webview rilevata")
                time.sleep(2)
                return False
            return True
        except Exception:
            return True

    @classmethod
    def recover_mini_app_with_go_back(
        cls,
        driver: webdriver.Remote,
        host_name: str,
        timeout: float = 4.0,
    ) -> bool:
        time.sleep(1)
        device_id = driver.capabilities.get("udid")

        def adb_tap(x: int, y: int):
            Util.adb(
                ["shell", "input", "tap", str(x), str(y)],
                device_id=device_id,
                timeout=10,
            )

        try:
            adb_tap(66, 248)
            print(
                "❎ ADB tap on Telegram close button at (66, 248) to close webview that is blocking the mini app..."
            )
            time.sleep(1)
            try:
                adb_tap(889, 1431)
                print(
                    "❎ ADB tap on Telegram close anyway button at (889, 1431) to close webview that is blocking the mini app..."
                )
                time.sleep(1)
            except Exception as exc:
                print(f"⚠️ Could not tap Telegram close anyway button: {exc}")
                return False

        except Exception:
            return False

        if not cls._wait_for_webview_context(driver, timeout=timeout):
            return False

        recovered_domain = cls._safe_get_domain(driver)
        return bool(recovered_domain and recovered_domain == host_name)

    @classmethod
    def recover_mini_app_minimized(
        cls,
        driver: webdriver.Remote,
        host_name: str,
        timeout: float = 4.0,
    ) -> bool:
        """Go back and check whether the mini app was recovered."""
        device_id = driver.capabilities.get("udid")

        def adb_tap(x: int, y: int):
            Util.adb(
                ["shell", "input", "tap", str(x), str(y)],
                device_id=device_id,
                timeout=10,
            )

        # The mini app reappears almost instantly after the tap, so a single
        # contexts query right after can come back transiently empty (the bridge
        # hasn't re-enumerated the WebView yet). We re-tap + re-poll a couple of
        # times instead of giving up on the first empty read.
        for attempt in range(3):
            try:
                if attempt < 2:
                    adb_tap(1005, 2284)
                    print(
                        "❎ ADB tap on Telegram to reopen mini app minimized "
                        f"(1005, 2284) [attempt {attempt + 1}]..."
                    )
                else:
                    print("❎ Minimization tap failed. Assuming it's a native overlay. Sending ADB Back button...")
                    Util.adb(["shell", "input", "keyevent", "4"], device_id=device_id, timeout=10)
                time.sleep(1.0)
            except Exception:
                return False

            if cls._wait_for_webview_context(driver, timeout=timeout):
                recovered_domain = cls._safe_get_domain(driver)
                if recovered_domain and recovered_domain == host_name:
                    # Check if it actually un-minimized (i.e. the overlay is gone)
                    if not cls.isMiniAppMinimize(driver, timeout=2):
                        return True

            print("   ↻ Contexts still empty or app still hidden, retrying recovery...")

        return False

    @classmethod
    def recover_mini_app_context(
        cls,
        driver: webdriver.Remote,
        host_name: str,
        timeout: float = 8.0,
    ) -> bool:
        """Bring the mini app WebView back after the page navigated away.

        Clicking an external link (e.g. a Privacy Policy) can move the WebView
        off the mini app domain or open a new window/Custom Tab. Here we try to
        return to the mini app and re-switch to its context, so the run resumes
        on the mini app instead of getting stuck on the external page.

        Returns True if we end up back on the original host.
        """

        def _on_host() -> bool:
            dom = cls._safe_get_domain(driver)
            return bool(dom and dom == host_name)

        # 1) A new window/tab may have opened (the external page). Look across all
        #    handles for the one still on the mini app host and switch to it,
        #    closing the stray windows.
        try:
            handles = driver.window_handles
        except Exception:
            handles = []
        if len(handles) > 1:
            print(f"ℹ️ {len(handles)} windows open, looking for the mini app one...")
            for h in list(handles):
                try:
                    driver.switch_to.window(h)
                    if _on_host():
                        print("✅ Mini app window found, switched back.")
                        return True
                except Exception:
                    continue
            # None is on host: close the extra windows, keep the first.
            for h in handles[1:]:
                try:
                    driver.switch_to.window(h)
                    driver.close()
                except Exception:
                    pass
            try:
                driver.switch_to.window(driver.window_handles[0])
            except Exception:
                pass

        # 2) Same-window navigation: walk back until we land on the host again.
        for _ in range(3):
            if _on_host():
                return True
            try:
                driver.back()
            except Exception:
                pass
            time.sleep(1)
            cls._wait_for_webview_context(driver, timeout=timeout)
            if _on_host():
                print("✅ Returned to mini app via back navigation.")
                return True

        # 3) Last resort: the native Telegram WebView overlay close buttons.
        if cls.recover_mini_app_with_go_back(driver, host_name, timeout):
            print("✅ Returned to mini app via native close buttons.")
            return True

        return False

    @staticmethod
    def isMiniAppMinimize(
        driver: webdriver.Remote,
        timeout: int = 5,
    ) -> bool:
        try:
            # Advanced JS check to see if the app is closed, invisible or minimized
            is_minimized = driver.execute_script(
                """
                return document.hidden || 
                       document.visibilityState === 'hidden' || 
                       window.innerHeight === 0 || 
                       window.innerHeight < window.screen.height * 0.4;
                """
            )
            return bool(is_minimized)
        except Exception:
            # If JS fails, the WebView context is dead (app closed or minimized)
            # So we RETURN TRUE to trigger the recovery!
            return True

    @staticmethod
    def dump_html(html_path: Path, html_content: str) -> None:
        """Save the mini app HTML."""
        with open(html_path, "w", encoding="utf-8") as file_handle:
            file_handle.write(html_content)
        print(f"✅ HTML saved: {html_path.name} ({len(html_content)} chars)")

        if len(html_content) > 1000:
            soup = BeautifulSoup(html_content, "html.parser")
            title = soup.title.get_text(strip=True) if soup.title else "No title"
            scripts = len(soup.find_all("script"))
            print(f"📊 Parsed: Title='{title[:50]}...', Scripts={scripts}")

    @staticmethod
    def dump_ui(driver: webdriver.Remote, xml_path: Path) -> None:
        """Salva XML dump dell'interfaccia."""
        time.sleep(10)
        original_context = getattr(driver, "current_context", None)
        try:
            if original_context != "NATIVE_APP":
                driver.switch_to.context("NATIVE_APP")

            page_source = driver.page_source

            with open(xml_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(page_source)
            print(f"✅ XML saved: {xml_path.name}")
        except Exception as e:
            print(f"⚠️ Error saving XML: {e}")
        finally:
            if original_context and original_context != "NATIVE_APP":
                try:
                    driver.switch_to.context(original_context)
                except Exception:
                    pass

    @classmethod
    def get_mini_app_html(
        cls,
        driver: webdriver.Remote,
        wait: WebDriverWait | None = None,
    ) -> str:
        """Extract the mini app HTML from the WebView."""
        print("🔄 Waiting for WebView context...")
        webview_ctx = cls.wait_for_mini_app_context(driver, timeout=45)
        if not webview_ctx:
            raise TimeoutException(
                "No WEBVIEW context available for mini app HTML dump."
            )

        # The WebView may expose several pages (an about:blank + the mini app).
        # Select the handle whose URL is a real http(s) page so the dump captures
        # the mini app and not a blank page.
        app_url = cls.switch_to_app_window(driver)
        if not app_url:
            # Fallback: keep the last handle if no real URL was found.
            try:
                handles = driver.window_handles
                if handles:
                    driver.switch_to.window(handles[-1])
            except Exception:
                pass

        print(f"✅ Switched to: {driver.current_context} — url: {app_url or '?'}")
        time.sleep(1)
        try:
            driver.set_script_timeout(15)
            html = driver.execute_script("return document.documentElement.outerHTML;") or ""
        except Exception as exc:
            print(f"⚠️ execute_script failed/timed out ({exc}), falling back to page_source")
            html = ""
        if len(html) < 1000:
            try:
                html = driver.page_source or ""
            except Exception as exc:
                print(f"⚠️ page_source failed ({exc})")
                html = ""
        return html

    # Typical markers of SPA pages not yet rendered by JS.
    _JS_REQUIRED_MARKERS = (
        "This site requires JavaScript",
        "enable JavaScript",
        "Please enable JavaScript",
        "You need to enable JavaScript",
    )

    @classmethod
    def capture_rendered_html(
        cls, driver: webdriver.Remote, timeout: float = 15.0
    ) -> str:
        """Capture the HTML rendered by the WebView, waiting for JS to populate
        the page.

        Many Privacy Policies are Single Page Apps: captured too early they
        return only the 'This site requires JavaScript' shell. We poll the
        outerHTML until the content is real (no noscript markers and a body
        with meaningful text).
        """
        deadline = time.time() + timeout
        best = ""
        while time.time() < deadline:
            try:
                html = driver.execute_script(
                    "return document.documentElement.outerHTML;"
                ) or ""
                body_text = driver.execute_script(
                    "return (document.body && document.body.innerText) || '';"
                ) or ""
            except Exception:
                html, body_text = "", ""

            if len(html) > len(best):
                best = html

            has_marker = any(m.lower() in html.lower() for m in cls._JS_REQUIRED_MARKERS)
            if html and not has_marker and len(body_text.strip()) > 200:
                return html
            time.sleep(0.5)
        print("⚠️ PP: rendered HTML not confirmed within timeout, using best-effort.")
        return best

    @classmethod
    def get_domain(cls, driver: webdriver.Remote) -> str:
        if "WEBVIEW" not in (driver.current_context or ""):
            webview_ctx = cls._wait_for_webview_context(driver, timeout=3)
            if not webview_ctx:
                raise RuntimeError("No WEBVIEW context available to read current_url.")
        # Read the URL via execute_script (bounded by the global script timeout)
        # instead of driver.current_url, which is not capped and hangs forever if
        # a click navigated the WebView to a stuck/external page.
        current_url = driver.execute_script("return document.location.href;") or ""
        parsed = urlparse(current_url)
        return parsed.netloc

    @staticmethod
    def get_current_url(driver: webdriver.Remote) -> str:
        """Read the current URL via execute_script (bounded by the script
        timeout), so it cannot hang like driver.current_url when a click
        navigated the WebView to a stuck/external page."""
        try:
            return driver.execute_script("return document.location.href;") or ""
        except Exception:
            return ""

    @staticmethod
    def _is_real_app_url(url: str) -> bool:
        """True if the URL is a real http(s) page (not about:blank/data/chrome)."""
        if not url:
            return False
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)

    @classmethod
    def switch_to_app_window(cls, driver: webdriver.Remote) -> str | None:
        """Iterate the WebView window handles and switch to the mini app one
        (real http/https URL), skipping about:blank/data pages.

        Returns the mini app URL, or None if no valid handle is found.
        """
        try:
            handles = driver.window_handles
        except Exception:
            return None
        # Iterate from the last: the mini app is usually the most recent page.
        for h in reversed(handles):
            try:
                driver.switch_to.window(h)
                url = cls.get_current_url(driver)
                if cls._is_real_app_url(url):
                    return url
            except Exception:
                continue
        return None

    @classmethod
    def resolve_host(cls, driver: webdriver.Remote, timeout: float = 15.0) -> str | None:
        """Resolve the mini app host with polling.

        With pageLoadStrategy 'none' the navigation may not be committed yet
        right after the context switch, and the WebView may expose several
        pages (an about:blank + the mini app). We poll across all handles for
        the one with a real http(s) URL.
        """
        deadline = time.time() + timeout
        last_seen = ""
        while time.time() < deadline:
            try:
                if "WEBVIEW" not in (driver.current_context or ""):
                    cls._wait_for_webview_context(driver, timeout=3)
                url = cls.switch_to_app_window(driver)
                if url:
                    return urlparse(url).netloc
                last_seen = cls.get_current_url(driver)
            except Exception as exc:
                last_seen = f"<error: {exc}>"
            time.sleep(0.5)
        print(f"⏱ resolve_host timed out after {timeout}s (last url seen: {last_seen!r})")
        return None

    @classmethod
    def is_really_interactable(cls, driver: webdriver.Remote, element) -> bool:
        try:
            return driver.execute_script(
                """
const el = arguments[0];
if (!el || !el.isConnected) return false;

el.scrollIntoView({block:'center', inline:'center'});

const s = getComputedStyle(el);
if (s.display === 'none' || s.visibility !== 'visible' || s.pointerEvents === 'none') return false;
if (parseFloat(s.opacity || '1') < 0.1) return false;
if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;

const r = el.getBoundingClientRect();
if (r.width < 2 || r.height < 2) return false;

const cx = r.left + r.width / 2;
const cy = r.top + r.height / 2;
if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) return false;

const top = document.elementFromPoint(cx, cy);
return !!top && (top === el || el.contains(top));
""",
                element,
            )
        except Exception:
            return False

    @classmethod
    def get_interactable_elements(cls, driver: webdriver.Remote) -> list:
        try:
            return driver.execute_script(
                f"""
                const selector = "{cls.CLICKABLE_SELECTOR}";
                const elements = Array.from(document.querySelectorAll(selector));
                
                // Also look for any generic element that has cursor: pointer
                const allDivs = document.querySelectorAll("div, span, img, p, li");
                for (const el of allDivs) {{
                    if (window.getComputedStyle(el).cursor === 'pointer') {{
                        elements.push(el);
                    }}
                }}
                
                const interactable = [];
                for (const el of elements) {{
                    if (!el || !el.isConnected) continue;
                    const s = getComputedStyle(el);
                    if (s.display === 'none' || s.visibility !== 'visible' || s.pointerEvents === 'none') continue;
                    if (parseFloat(s.opacity || '1') < 0.1) continue;
                    
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
                    if (el.classList.contains('disabled') || el.classList.contains('is-disabled')) continue;
                    
                    const r = el.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) continue;
                    
                    // Exclude elements clearly off-screen
                    if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) continue;
                    
                    interactable.push(el);
                }}
                return Array.from(new Set(interactable)); // Remove any duplicates caused by multiple selectors
            """
            )
        except Exception:
            return []

    @classmethod
    def _build_click_log_path(cls, bot: str) -> Path:
        """Build the click log path: <apps_dir>/dynamicAnalysis/<botname>/clicks.json"""
        base_dir = Path(cls.APPS_DIR) / "dynamicAnalysis" / cls.bot_slug(bot)
        base_dir.mkdir(parents=True, exist_ok=True)
        return base_dir / "clicks.json"

    @staticmethod
    def _extract_element_metadata(driver: webdriver.Remote, element) -> dict:
        """Extract useful element metadata before the click."""
        return driver.execute_script(
            """
const el = arguments[0];
if (!el) return {};
const r = el.getBoundingClientRect();
return {
  tag: (el.tagName || '').toLowerCase(),
  text: (el.innerText || el.textContent || '').trim().slice(0, 300),
  id: el.id || '',
  className: el.className || '',
  role: el.getAttribute('role') || '',
  type: el.getAttribute('type') || '',
  name: el.getAttribute('name') || '',
  href: el.getAttribute('href') || '',
  ariaLabel: el.getAttribute('aria-label') || '',
  rect: {x: r.x, y: r.y, width: r.width, height: r.height},
};
""",
            element,
        )

    @staticmethod
    def append_click_log(log_path: Path, record: dict) -> None:
        """Appende un record in JSON list su disco."""
        records: list[dict] = []
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as file_handle:
                    loaded = json.load(file_handle)
                if isinstance(loaded, list):
                    records = loaded
            except (json.JSONDecodeError, OSError):
                records = []
        records.append(record)
        with open(log_path, "w", encoding="utf-8") as file_handle:
            json.dump(records, file_handle, indent=2, ensure_ascii=False)

    @staticmethod
    def _find_webview_context(
        driver: webdriver.Remote, exclude_chrome: bool = False
    ) -> str | None:
        """Find a WEBVIEW context, preferring the Telegram one.

        With exclude_chrome=True, WEBVIEW_chrome is discarded: that is the
        Chrome Custom Tab (e.g. Privacy Policy), NOT the Telegram mini app,
        which runs in WEBVIEW_org.telegram.messenger.
        """
        contexts = driver.contexts or []
        preferred = next(
            (ctx for ctx in contexts if "WEBVIEW" in ctx and "org.telegram" in ctx),
            None,
        )
        if preferred:
            return preferred
        webviews = [ctx for ctx in contexts if "WEBVIEW" in ctx]
        if exclude_chrome:
            webviews = [ctx for ctx in webviews if "chrome" not in ctx.lower()]
        return webviews[0] if webviews else None

    @classmethod
    def _wait_for_webview_context(
        cls,
        driver: webdriver.Remote,
        timeout: float = 30.0,
        exclude_chrome: bool = False,
    ) -> str | None:
        """Wait for a WEBVIEW context to become available and switch to it."""
        deadline = time.time() + timeout
        last_switch_error: str | None = None
        while time.time() < deadline:
            try:
                target = cls._find_webview_context(driver, exclude_chrome=exclude_chrome)
                if target:
                    if driver.current_context != target:
                        driver.switch_to.context(target)
                    return target
            except Exception as exc:
                last_switch_error = str(exc)
            time.sleep(0.5)
        # Diagnostic: show what contexts were available at timeout
        try:
            available = driver.contexts or []
        except Exception as exc:
            print(f"⚠️ Error getting contexts at timeout: {exc}")
            available = []
        print(f"⏱ _wait_for_webview_context timed out after {timeout}s")
        print(f"   Available contexts: {available}")
        if last_switch_error:
            print(f"   Last switch error: {last_switch_error}")
        return None

    @classmethod
    def wait_for_mini_app_context(
        cls, driver: webdriver.Remote, timeout: float = 45.0
    ) -> str | None:
        """Wait for the mini app context, robust to the hosting model.

        Phase 1: prefer a non-chrome WebView (Telegram in-app mini app).
        Phase 2: if it does not appear, also accept WEBVIEW_chrome (Chrome
        Custom Tab), because some mini apps run there.
        """
        deadline = time.time() + timeout
        phase1 = min(timeout * 0.6, timeout - 5) if timeout > 8 else timeout
        ctx = cls._wait_for_webview_context(
            driver, timeout=phase1, exclude_chrome=True
        )
        if ctx:
            return ctx
        remaining = max(2.0, deadline - time.time())
        print("ℹ️ No non-chrome WebView found, accepting Chrome as fallback.")
        return cls._wait_for_webview_context(
            driver, timeout=remaining, exclude_chrome=False
        )

    @classmethod
    def _safe_get_domain(cls, driver: webdriver.Remote) -> str | None:
        """Return the current domain if available, otherwise None."""
        try:
            return cls.get_domain(driver)
        except Exception:
            return None

    @staticmethod
    def search_keywords_in_file_html(
        file_path: Path,
        keywords: list[str],
        label: str,
        json_output_path: Path | None = None,
    ) -> None:
        """Search keywords in the HTML file and update the output JSON."""
        found_any = False
        detected_language = "unknown"

        try:
            with open(file_path, encoding="utf-8") as file_handle:
                content = file_handle.read()

            try:
                text_content = BeautifulSoup(content, "html.parser").get_text(
                    separator=" ", strip=True
                )
                if text_content:
                    detected_language = detect(text_content)
            except Exception as exc:
                print(f"⚠️ Error while detecting language in {label}: {exc}")

            lines = content.splitlines()
            for line_num, line in enumerate(lines, 1):
                for keyword in keywords:
                    if keyword in line:
                        print(f"✅ Found '{keyword}' in {label}")
                        found_any = True

            if not found_any:
                print(f"❌ No keywords found in {label}")

        except FileNotFoundError:
            print(f"❌ File not found: {file_path}")
            return

        if json_output_path is not None:
            accessible = "yes" if found_any else "no"
            try:
                with open(json_output_path, "r", encoding="utf-8") as json_file:
                    data = json.load(json_file)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}

            data["privacy_policy_accessible(HTML)"] = accessible
            if found_any or detected_language != "unknown":
                data["app_language"] = detected_language

            with open(json_output_path, "w", encoding="utf-8") as json_file:
                json.dump(data, json_file, indent=2, ensure_ascii=False)

    @staticmethod
    def search_keywords_in_file_xml(
        file_path: Path,
        keywords: list[str],
        label: str,
        json_output_path: Path | None = None,
    ) -> None:
        """Search keywords in the XML file and update the output JSON."""
        found_any = False
        try:
            with open(file_path, encoding="utf-8") as file_handle:
                for line_num, line in enumerate(file_handle, 1):
                    for keyword in keywords:
                        if keyword in line:
                            print(
                                f"✅ Found '{keyword}' in {label} (line {line_num}): {line.strip()}"
                            )
                            found_any = True
            if not found_any:
                print(f"❌ No keywords found in {label}")
        except FileNotFoundError:
            print(f"❌ File not found: {file_path}")

        if json_output_path is not None:
            accessible = "yes" if found_any else "no"
            try:
                with open(json_output_path, "r", encoding="utf-8") as json_file:
                    data = json.load(json_file)
            except (FileNotFoundError, json.JSONDecodeError):
                data = {}

            data["privacy_policy_accessible(XML)"] = accessible

            with open(json_output_path, "w", encoding="utf-8") as json_file:
                json.dump(data, json_file, indent=2, ensure_ascii=False)

    @staticmethod
    def isValidCanvasPresent(driver: webdriver.Remote) -> bool:
        # Keep the script timeout short so a dead/empty WebView context fails fast
        # instead of hanging until the default (~30s) chromedriver script timeout.
        try:
            driver.set_script_timeout(3)
        except Exception:
            pass
        try:
            result = driver.execute_script(
                """
                const canvas = document.querySelector("canvas");
                if (!canvas) return { exists: false };

                const rect = canvas.getBoundingClientRect();
                const style = window.getComputedStyle(canvas);

                return {
                    exists: true,
                    width: rect.width,
                    height: rect.height,
                    pointerEvents: style.pointerEvents,
                    display: style.display,
                    visibility: style.visibility
                };
            """
            )
        except Exception as exc:
            print(f"⚠️ isValidCanvasPresent: execute_script failed ({exc})")
            return False
        if not isinstance(result, dict) or not result.get("exists"):
            return False

        if result["width"] <= 0 or result["height"] <= 0:
            return False

        if result["pointerEvents"] == "none":
            return False

        if result["display"] == "none":
            return False

        if result["visibility"] == "hidden":
            return False

        return True
    
    @staticmethod
    def save_privacy_policy_pdf(url: str, bot: str, rendered_html: str | None = None):
        out = Path(Util.APPS_DIR) / "PrivacyPolicies"
        out.mkdir(parents=True, exist_ok=True)

        pdf_path = out / f"{bot}_privacy_policy.pdf"
        html_path = out / f"{bot}_privacy_policy.html"

        # Prefer JS-rendered HTML from the live WebView (handles JS-heavy pages)
        if rendered_html and len(rendered_html) > 500:
            html_path.write_text(rendered_html, encoding="utf-8")
            print(f"✅ HTML saved (JS-rendered): {html_path}")
            try:
                WeasyHTML(string=rendered_html, base_url=url).write_pdf(pdf_path)
                print(f"✅ PDF generated from JS-rendered HTML: {pdf_path}")
                return str(pdf_path)
            except Exception as e:
                raise RuntimeError(f"HTML saved but PDF conversion failed: {e}")

        # Fallback: plain HTTP fetch (no JS — works for static pages and direct PDFs)
        headers = {
            "User-Agent": "Mozilla/5.0 (Android 13; Mobile) AppleWebKit/537.36 Chrome/120.0.0.0"
        }
        response = requests.get(url, timeout=30, headers=headers, allow_redirects=True)
        content_type = (response.headers.get("Content-Type") or "").lower()
        content = response.content

        print(f"🌐 Final URL: {response.url}")
        print(f"📦 Content-Type: {content_type}")
        print(f"📦 Size: {len(content)} bytes")

        if content.startswith(b"%PDF"):
            pdf_path.write_bytes(content)
            print(f"✅ Real PDF saved: {pdf_path}")
            return str(pdf_path)

        if "text/html" in content_type or b"<html" in content[:500].lower():
            html = response.text
            html_path.write_text(html, encoding="utf-8")
            print(f"✅ HTML saved: {html_path}")
            try:
                WeasyHTML(string=html, base_url=response.url).write_pdf(pdf_path)
                print(f"✅ PDF generated from HTML: {pdf_path}")
                return str(pdf_path)
            except Exception as e:
                raise RuntimeError(f"HTML saved but PDF conversion failed: {e}")

        raise RuntimeError(
            f"Unsupported content. Content-Type={content_type}, "
            f"magic={content[:10]!r}"
        )
__all__ = ["Util"]
