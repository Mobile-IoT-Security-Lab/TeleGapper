import random
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy

from scripts.Util import DeviceWedgedError, Util


class CanvasAnalysis:
    """Telegram mini app canvas dynamic analysis workflow."""

    @staticmethod
    def get_canvas_screen_info(driver: webdriver.Remote) -> dict:
        """
        Return the canvas size in CSS pixels, the devicePixelRatio
        and the viewport size. All in a single execute_script.
        """
        result = driver.execute_script(
            """
            const canvas = document.querySelector("canvas");
            if (!canvas) return null;

            const r = canvas.getBoundingClientRect();
            return {
                left:            r.left,
                top:             r.top,
                width:           r.width,
                height:          r.height,
                viewportWidth:   window.innerWidth,
                viewportHeight:  window.innerHeight,
                dpr:             window.devicePixelRatio || 1
            };
        """
        )
        if not result:
            raise RuntimeError("Canvas not found")
        return result

    @staticmethod
    def get_canvas_rect(driver: webdriver.Remote) -> dict:

        rect = driver.execute_script(
            """
            const canvas = document.querySelector("canvas");
            if (!canvas) return null;

            const r = canvas.getBoundingClientRect();

            return {
                left: r.left,
                top: r.top,
                width: r.width,
                height: r.height,
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight
            };
        """
        )
        if not rect:
            raise RuntimeError("Canvas not found")
        return rect

    @staticmethod
    def get_webview_screen_rect(driver: webdriver.Remote) -> dict:
        """
        Find the native WebView container and read its on-screen rect.
        """
        original_context = driver.current_context

        try:
            if original_context != "NATIVE_APP":
                driver.switch_to.context("NATIVE_APP")

            candidates = driver.find_elements(
                AppiumBy.CLASS_NAME, "android.webkit.WebView"
            )
            if not candidates:
                candidates = driver.find_elements(
                    AppiumBy.XPATH, "//*[contains(@class, 'WebView')]"
                )

            if not candidates:
                raise RuntimeError("Native WebView not found")

            webview = candidates[0]
            rect = webview.rect

            return {
                "left": rect["x"],
                "top": rect["y"],
                "width": rect["width"],
                "height": rect["height"],
            }
        finally:
            if driver.current_context != original_context:
                try:
                    driver.switch_to.context(original_context)
                except Exception:
                    pass

    @staticmethod
    def get_valid_sectors(
        rows: int,
        cols: int,
        exclude_border: bool = True,
    ) -> list[tuple[int, int]]:
        sectors = []

        for row in range(rows):
            for col in range(cols):
                if exclude_border and rows > 2 and cols > 2:
                    if row == 0 or row == rows - 1 or col == 0 or col == cols - 1:
                        continue
                sectors.append((row, col))

        if not sectors:
            sectors = [(row, col) for row in range(rows) for col in range(cols)]

        return sectors

    @staticmethod
    def get_random_point_in_sector(
        canvas_rect: dict,
        rows: int,
        cols: int,
        row: int,
        col: int,
        padding: int = 10,
    ) -> tuple[int, int]:
        sector_width = canvas_rect["width"] / cols
        sector_height = canvas_rect["height"] / rows

        x1 = canvas_rect["left"] + col * sector_width
        y1 = canvas_rect["top"] + row * sector_height
        x2 = x1 + sector_width
        y2 = y1 + sector_height

        min_x = int(x1) + padding
        max_x = int(x2) - 1 - padding
        min_y = int(y1) + padding
        max_y = int(y2) - 1 - padding

        # fallback if the padding is too aggressive
        if min_x > max_x:
            min_x = int(x1)
            max_x = int(x2) - 1
        if min_y > max_y:
            min_y = int(y1)
            max_y = int(y2) - 1

        if min_x > max_x or min_y > max_y:
            raise RuntimeError("Sector too small")

        x = random.randint(min_x, max_x)
        y = random.randint(min_y, max_y)

        return x, y

    @staticmethod
    def get_device_screen_size(device_id: str | None = None) -> tuple[int, int]:
        """Read the real screen size via ADB."""
        cmd = ["adb"]
        if device_id:
            cmd += ["-s", device_id]
        cmd += ["shell", "wm", "size"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        # Output: "Physical size: 1080x2400"
        for line in result.stdout.splitlines():
            if "Physical size" in line or "Override size" in line:
                parts = line.split(":")[-1].strip().split("x")
                if len(parts) == 2:
                    return int(parts[0]), int(parts[1])
        return 1080, 2400  # fallback

    @staticmethod
    def web_to_screen_point(
        canvas_info: dict,
        web_x: int,
        web_y: int,
    ) -> tuple[int, int]:
        """
        Convert WebView CSS coordinates into native screen coordinates
        using the devicePixelRatio and the bounds computed in the browser.
        """
        dpr = canvas_info.get("dpr", 2.625)

        # web_x and web_y are relative to the canvas.
        # Referencing them to the viewport (adding the canvas left and top)
        # and multiplying by the dpr gives the physical pixels on screen.
        screen_x = int((canvas_info["left"] + web_x) * dpr)
        screen_y = int((canvas_info["top"] + web_y) * dpr)

        return screen_x, screen_y

    @staticmethod
    def adb_tap(x: int, y: int, device_id: str | None = None) -> None:
        result = Util.adb(
            ["shell", "input", "swipe", str(x), str(y), str(x), str(y), "50"],
            device_id=device_id,
            timeout=10,
        )
        if result is None:
            # adb shell timed out: the device is likely wedged. Abort the burst
            # so the run unwinds to the device reboot instead of hammering a dead
            # bridge.
            raise DeviceWedgedError("adb tap timed out (device wedged?)")

        print("ADB returncode:", result.returncode)
        if result.returncode != 0:
            raise RuntimeError(f"ADB tap failed: {result.stderr.strip()}")

    @classmethod
    def click_random_canvas_point(
        cls,
        driver: webdriver.Remote,
        rows: int = 8,
        cols: int = 4,
        forbidden_y: int | None = None,
        forbidden_margin: int = 0,
        device_id: str | None = None,
    ) -> tuple[int, int, int, int]:
        print("START click_random_canvas_point")

        if not Util.isValidCanvasPresent(driver):
            raise RuntimeError("Canvas not valid")

        if not hasattr(cls, "_cached_webview_rect") or cls._cached_webview_rect is None:
            cls._cached_webview_rect = cls.get_webview_screen_rect(driver)
        
        rect = cls._cached_webview_rect
        
        safe_top = rect["top"] + 50
        safe_bottom = rect["top"] + rect["height"] - 50
        safe_height = safe_bottom - safe_top
        
        if safe_height <= 0:
            raise RuntimeError("WebView rect too small for safe clicking")

        valid_sectors = cls.get_valid_sectors(
            rows=rows, cols=cols, exclude_border=False
        )

        if (
            not hasattr(cls, "_unvisited_native_sectors")
            or not cls._unvisited_native_sectors
        ):
            cls._unvisited_native_sectors = list(valid_sectors)
            random.shuffle(cls._unvisited_native_sectors)

        row, col = cls._unvisited_native_sectors.pop()

        sector_w = rect["width"] / cols
        sector_h = safe_height / rows

        x1 = rect["left"] + col * sector_w
        y1 = safe_top + row * sector_h
        x2 = x1 + sector_w
        y2 = y1 + sector_h

        padding = 10
        min_x_sec = int(x1) + padding
        max_x_sec = int(x2) - 1 - padding
        min_y_sec = int(y1) + padding
        max_y_sec = int(y2) - 1 - padding

        if min_x_sec > max_x_sec:
            min_x_sec = int(x1)
            max_x_sec = int(x2) - 1
        if min_y_sec > max_y_sec:
            min_y_sec = int(y1)
            max_y_sec = int(y2) - 1

        screen_x = random.randint(min_x_sec, max_x_sec)
        screen_y = random.randint(min_y_sec, max_y_sec)

        print(f"TRY native sector=({row},{col}) " f"screen=({screen_x},{screen_y})")

        cls.adb_tap(screen_x, screen_y, device_id=device_id)
        print("ADB TAP SENT")

        return row, col, screen_x, screen_y

    @classmethod
    def canvas_analysis(
        cls,
        driver: webdriver.Remote,
        click_index: int,
        host_name: str,
        click_log_path: Path,
        device_id: str | None = None,
        burst_size: int = 2,
        tap_delay: float = 1.0,
    ) -> int:
        pass

        # Watchdog: a heavy mini app (e.g. a miner) can wedge the device adbd,
        # making every adb shell / Appium contexts call hang forever. Abort the
        # whole bot so the run unwinds to the device reboot, instead of looping
        # on a dead bridge.
        if not Util.adb_healthcheck(device_id=device_id, timeout=5):
            raise DeviceWedgedError(
                "adb bridge unresponsive during canvas analysis"
            )

        if not Util.verify_webview(driver):
            print("OVERLAY WEBVIEW FOUND")
            Util.recover_mini_app_with_go_back(driver, host_name, 3)
        if Util.isMiniAppMinimize(driver):
            print("MINI APP MINIMIZED")
            if not Util.recover_mini_app_minimized(driver, host_name, 3):
                print(
                    "⚠️ Recovery failed: no live WebView context, skipping canvas burst."
                )
                return click_index

        # Guard: make sure a WebView context is actually alive before running any
        # JS. Without this, click_random_canvas_point -> isValidCanvasPresent calls
        # execute_script on a dead/empty context and hangs until the script timeout.
        if not Util._wait_for_webview_context(driver, timeout=3):
            print("⚠️ No WebView context available, skipping canvas burst.")
            return click_index

        for _ in range(burst_size):
            click_index += 1
            click_record = {
                "click_index": click_index,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "host_name": host_name,
                "button": {
                    "tag": "canvas",
                    "text": "",
                    "id": "",
                    "className": "",
                    "role": "",
                    "type": "",
                    "name": "",
                    "href": "",
                    "ariaLabel": "",
                    "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
                },
                "interactable_check": True,
                "click_success": False,
                "error": "",
            }

            try:
                row, col, x, y = cls.click_random_canvas_point(
                    driver=driver,
                    rows=8,
                    cols=4,
                    forbidden_y=300,
                    forbidden_margin=0,
                    device_id=device_id,
                )

                click_record["button"] = {
                    "tag": "canvas",
                    "text": f"canvas-sector-{row}-{col}",
                    "id": "",
                    "className": "",
                    "role": "canvas",
                    "type": "",
                    "name": "",
                    "href": "",
                    "ariaLabel": "",
                    "rect": {"x": x, "y": y, "width": 0, "height": 0},
                }
                click_record["click_success"] = True

                # Throttle: heavy mini apps (miners/games) saturate the device
                # and wedge adbd if we hammer taps back-to-back. Pace them.
                time.sleep(tap_delay)

                print(
                    f"🖼️ Tapped canvas #{click_index}: sector ({row},{col}) point ({x},{y})"
                )

            except DeviceWedgedError:
                # Don't swallow a wedged-device signal: let it propagate so the
                # run unwinds to the device reboot. The finally still logs it.
                click_record["error"] = "device wedged"
                raise
            except Exception as click_error:
                click_record["error"] = str(click_error)
                print("Canvas analysis error:", click_error)
            finally:
                Util.append_click_log(click_log_path, click_record)
        return click_index


__all__ = ["CanvasAnalysis"]
