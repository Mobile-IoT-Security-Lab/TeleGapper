import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from dotenv import load_dotenv
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from scripts.TrafficAnalysis import TrafficDumpAnalyzer
from scripts.Util import DeviceWedgedError, Util
from scripts.CanvasAnalysis import CanvasAnalysis

load_dotenv()


class DynamicAnalysis:
    """Telegram mini app dynamic analysis workflow."""

    CLICKABLE_SELECTOR = (
        "a[href],button,input[type='button'],input[type='submit'],"
        "[role='button'],[onclick],[tabindex]:not([tabindex='-1'])"
    )

    KEYWORDS = [
        "Privacy",
        "Privacy Policy",
        "Terms and Conditions",
        "Terms of Service",
        "Accept",
        "Accept All",
        "Agree",
    ]

    # Chromedriver paths are resolved by the Appium server, so they are host
    # paths even when this code runs inside a container.
    _CHROMEDRIVER_DIR = os.getenv("CHROMEDRIVER_DIR", "")
    _CHROMEDRIVER_MAPPING_FILE = os.getenv("CHROMEDRIVER_MAPPING_FILE", "")
    _DEVICE_NAME = os.getenv("ANDROID_DEVICE_NAME", "")

    DEFAULT_DEVICE_CAPS = {
        "platformName": "Android",
        # 'none' = ChromeDriver does not wait for page load to complete before
        # returning from navigation commands (getUrl, etc). Mining/game mini apps
        # keep persistent connections (websocket/long-poll) so the page never
        # reaches readyState 'complete' under the default 'normal' strategy,
        # which makes driver.current_url / execute_script hang forever.
        "pageLoadStrategy": "none",
        "appium:automationName": "UiAutomator2",
        "appium:platformVersion": os.getenv("ANDROID_PLATFORM_VERSION", "16"),
        "appium:appPackage": os.getenv("TELEGRAM_APP_PACKAGE", "org.telegram.messenger"),
        "appium:appActivity": os.getenv(
            "TELEGRAM_APP_ACTIVITY", "org.telegram.ui.LaunchActivity"
        ),
        "appium:noReset": True,
        "appium:forceAppLaunch": True,
        "appium:autoWebview": False,
        "appium:ensureWebviewsHavePages": True,
        "appium:enableWebviewDetail": True,
        "appium:webviewDevtoolsPort": int(os.getenv("WEBVIEW_DEVTOOLS_PORT", "9222")),
        "appium:uiautomator2:skipServerInstallation": True,
        # --- adb stress reduction (heavy/miner mini apps wedge adbd) ---
        # Don't fail/hang on the `settings ... hidden_api_policy` commands Appium
        # runs on init/quit (that's the command timing out at session teardown).
        "appium:ignoreHiddenApiPolicyError": True,
        # Skip the extra adb settings/push commands Appium runs on session start;
        # safe with noReset since Telegram is already installed and configured.
        "appium:skipDeviceInitialization": True,
        # Never let Appium kill/restart the adb server mid-run.
        "appium:suppressKillServer": True,
        # Fail an individual adb exec in 10s instead of 20s, so a wedged device
        # is detected and recovered sooner.
        "appium:adbExecTimeout": 10000,
    }

    # Only send these when configured: an empty deviceName makes Appium reject
    # the session, while omitting it lets Appium pick the single attached device.
    if _DEVICE_NAME:
        DEFAULT_DEVICE_CAPS["appium:deviceName"] = _DEVICE_NAME
    if _CHROMEDRIVER_DIR:
        DEFAULT_DEVICE_CAPS["appium:chromedriverExecutableDir"] = _CHROMEDRIVER_DIR
    if _CHROMEDRIVER_MAPPING_FILE:
        DEFAULT_DEVICE_CAPS["appium:chromedriverChromeMappingFile"] = (
            _CHROMEDRIVER_MAPPING_FILE
        )

    APPIUM_SERVER_URL = os.getenv("APPIUM_SERVER_URL", "http://127.0.0.1:4723")

    @classmethod
    def launch_telegram(cls) -> webdriver.Remote:
        """Launch Telegram with the capabilities."""
        options = UiAutomator2Options().load_capabilities(cls.DEFAULT_DEVICE_CAPS)
        driver = webdriver.Remote(cls.APPIUM_SERVER_URL, options=options)
        # Safety net: even with pageLoadStrategy 'none', cap any page-load wait so
        # navigation commands raise a TimeoutException instead of hanging forever.
        try:
            driver.set_page_load_timeout(20)
        except Exception:
            pass
        # Cap every execute_script too: if a click navigates the WebView to a
        # stuck/external page, JS-based reads (domain, interactable elements)
        # raise a TimeoutException instead of hanging the whole run.
        try:
            driver.set_script_timeout(10)
        except Exception:
            pass
        print(f"🚀 Telegram launched! Session: {driver.session_id}")
        return driver

    @classmethod
    def navigate_to_bot(
        cls, driver: webdriver.Remote, wait: WebDriverWait, bot: str
    ) -> None:
        """Navigate to the bot chat."""
        print("🔍 Navigating to bot...")
        search_button = wait.until(
            EC.element_to_be_clickable(
                (
                    AppiumBy.XPATH,
                    "//android.widget.FrameLayout[5]/android.widget.ImageView",
                )
            )
        )
        search_button.click()

        search_input = wait.until(
            EC.presence_of_element_located(
                (
                    AppiumBy.XPATH,
                    "//android.widget.EditText[@content-desc='Search Chats']",
                )
            )
        )
        search_input.send_keys(bot)

        time.sleep(1)
        index = 1
        found = False
        while not found:
            xpath = f"(//android.view.ViewGroup[@text])[{index}]"
            try:
                element = wait.until(
                    EC.element_to_be_clickable((AppiumBy.XPATH, xpath))
                )
                print(element.text)
                for text in element.text.strip().lower().split(","):
                    candidate = text.strip()
                    print(candidate)
                    if bot.lower().strip() == candidate:
                        element.click()
                        found = True
                        print(f"✅ Found and clicked: {candidate}")
                        break
                else:
                    print(f"⏭️  Skipped: {candidate}")
                    index += 1
            except TimeoutException:
                element = wait.until(
                    EC.element_to_be_clickable(
                        (AppiumBy.XPATH, "(//android.view.ViewGroup[@text])[1]")
                    )
                )
                element.click()
                found = True
                print("Bot Found by fallback and clicked.")

    @classmethod
    def open_bot_chat(
        cls, driver: webdriver.Remote, wait: WebDriverWait, bot: str
    ) -> None:
        """Open the bot chat if needed."""
        bot_buttons = driver.find_elements(
            AppiumBy.XPATH, "//android.widget.Button[@clickable='true']"
        )
        if not bot_buttons:
            cls.navigate_to_bot(driver, wait, bot)

        time.sleep(3)

    @classmethod
    def scrape_privacypolicy(
        cls, driver: webdriver.Remote, wait: WebDriverWait, bot: str
    ) -> None:
        print("🔍 Looking for Privacy Policy...")
        
        short_wait = WebDriverWait(driver, 5)

        try:
            try:
                bot_profile = short_wait.until(
                    EC.presence_of_all_elements_located(
                        (AppiumBy.XPATH, '//android.view.View[@content-desc="Profile photo"]')
                    )
                )
            except TimeoutException:
                print("⚠️ Profile photo not found, skipping Privacy Policy")
                return
            bot_profile[0].click()

            try:
                burger_button = short_wait.until(
                    EC.presence_of_all_elements_located(
                        (AppiumBy.XPATH, '//android.widget.ImageButton[@content-desc="More options"]')
                    )
                )
            except TimeoutException:
                print("⚠️ More options not found, skipping Privacy Policy")
                driver.back()
                return
            burger_button[0].click()

            try:
                privacy_policy_btn = short_wait.until(
                    EC.presence_of_all_elements_located(
                        (AppiumBy.XPATH, '//android.widget.LinearLayout/android.widget.FrameLayout[5]')
                    )
                )
            except TimeoutException:
                print("⚠️ Privacy Policy button not found, continuing analysis")
                driver.back()
                driver.back()
                return

            privacy_policy_btn[0].click()

            try:
                Util._wait_for_webview_context(driver, timeout=20)
            except Exception:
                print("⚠️ Privacy Policy did not appear within 20 seconds, continuing analysis")
                try:
                    driver.switch_to.context("NATIVE_APP")
                except Exception:
                    pass
                try:
                    driver.back()
                except Exception:
                    pass
                try:
                    driver.back()
                except Exception:
                    pass
                return

            try:
                pdf_url = Util.get_current_url(driver)
                print(f"🔗 Privacy Policy URL: {pdf_url}")
                rendered_html = Util.capture_rendered_html(driver, timeout=15)
                Util.save_privacy_policy_pdf(pdf_url, bot, rendered_html=rendered_html or None)
            except Exception as e:
                print(f"⚠️ Error while saving Privacy Policy for {bot}: {e}")

            try:
                driver.switch_to.context("NATIVE_APP")
            except Exception:
                pass

            Util.adb_tap(driver, 66, 248)
            time.sleep(1.5)

        except (TimeoutException, WebDriverException, Exception) as e:
            print(f"⚠️ Could not process Privacy Policy for {bot}: {e}")

            try:
                driver.switch_to.context("NATIVE_APP")
            except Exception:
                pass
            return
    @classmethod
    def start_mini_app(
        cls, driver: webdriver.Remote, wait: WebDriverWait, bot: str, apps_dir: Path
    ) -> None:
        """Start the mini app."""
        try:
            openApp_button = wait.until(
                EC.presence_of_element_located((AppiumBy.XPATH, '//android.widget.Button[@content-desc="Open App"]'))
            )
            openApp_button.click()
        except TimeoutException:
            # Fallback if wait times out for any reason, though unexpected
            openApp_button = driver.find_elements(AppiumBy.XPATH,'//android.widget.Button[@content-desc="Open App"]')
            if openApp_button:
                openApp_button[0].click()
            else:
                raise Exception("Could not find 'Open App' button")
                
        print("🚀 Mini App started!")
        print("⏳ Waiting 20 seconds for mini app to load completely...")
        time.sleep(20)

    @classmethod
    def process_bot(cls, driver: webdriver.Remote, apps_dir: Path, bot: str) -> bool:
        """Open the bot, launch the mini app and save the initial artifacts."""
        wait = WebDriverWait(driver, 35)

        cls.open_bot_chat(driver, wait, bot)
        cls.scrape_privacypolicy(driver, wait, bot)
        Util.reset_burp_log()
        try:
            driver.switch_to.context("NATIVE_APP")
        except Exception:
            pass
        cls.start_mini_app(driver, wait, bot, apps_dir)
        webview_ctx = Util.wait_for_mini_app_context(driver, timeout=45)
        if not webview_ctx:
            print("⚠️ Mini app WebView not available after start.")
            return False
        Util.save_bot_trafficStart(apps_dir, bot)
        TrafficDumpAnalyzer.analyze(
            bot=bot,
            traffic_dir=(apps_dir / "trafficStart").resolve(),
            output_dir=(apps_dir / "resStart").resolve(),
        )
        report_path = TrafficDumpAnalyzer.report_path_for_bot(
            bot=bot,
            output_dir=(apps_dir / "resStart").resolve(),
        )
        legacy_report_path = apps_dir / "resStart" / f"{bot}_report.json"
        if legacy_report_path != report_path and legacy_report_path.exists():
            legacy_report_path.unlink()
            print(f"🧹 Removed legacy report: {legacy_report_path.name}")
        xml_path, html_path = Util.get_file_paths(apps_dir, bot)
        # Util.dump_ui(driver, xml_path)
        html_content = Util.get_mini_app_html(driver, wait)
        Util.dump_html(html_path, html_content)
        # Util.home_screenshot(bot, driver=driver)

        Util.search_keywords_in_file_html(
            html_path, cls.KEYWORDS, f"HTML ({html_path.name})", report_path
        )
        Util.search_keywords_in_file_xml(
            xml_path, cls.KEYWORDS, f"XML ({xml_path.name})", report_path
        )
        Util.reset_burp_log()

        return True

    @classmethod
    def budget_analysis(
        cls, driver: webdriver.Remote, budget_time: float, bot: str
    ) -> None:
        if not Util.wait_for_mini_app_context(driver, timeout=20):
            print("⚠️ Skipping budget analysis: no WEBVIEW context available.")
            # Util.end_screenshot(bot, driver=driver)
            return

        host_name = Util.resolve_host(driver, timeout=15)
        if not host_name:
            print("⚠️ Skipping budget analysis: unable to resolve initial host.")
            # Util.end_screenshot(bot, driver=driver)
            return

        start_time = time.time()
        miniapp_loaded = False
        click_index = 0
        click_log_path = Util._build_click_log_path(bot)
        last_analysis_mode: str | None = None

        while time.time() - start_time < budget_time:
            try:
                if not (driver.current_context and "WEBVIEW" in driver.current_context):
                    Util._wait_for_webview_context(driver, timeout=2)
                    time.sleep(0.5)
                    continue
                miniapp_loaded = True
                current_domain = Util._safe_get_domain(driver)
                if not current_domain or current_domain != host_name:
                    # A lost context can mean two very different things:
                    #  - the WebView navigated away (adb fine) -> WebView recovery
                    #  - the device adbd is wedged (adb dead) -> WebView recovery is
                    #    pointless, so raise and let the run unwind to the reboot.
                    if not current_domain and not Util.adb_healthcheck(timeout=5):
                        raise DeviceWedgedError(
                            "adb bridge unresponsive during budget analysis"
                        )
                    reason = (
                        "lost WebView context"
                        if not current_domain
                        else f"navigated to '{current_domain}'"
                    )
                    print(f"⚠️ {reason}. Recovering mini app context...")
                    if Util.recover_mini_app_context(driver, host_name, timeout=8):
                        print("✅ Mini app context recovered.")
                        continue
                    print(
                        "❌ Unable to recover mini app context, stopping interaction."
                    )
                    break

                clickables = Util.get_interactable_elements(driver)
                if Util.isValidCanvasPresent(driver) and len(clickables) == 0:
                    if last_analysis_mode != "canvas":
                        print("⚠️ Canvas detected, Start Canvas Analysis.")
                        last_analysis_mode = "canvas"

                    click_index = CanvasAnalysis.canvas_analysis(
                        driver,
                        click_index,
                        host_name,
                        click_log_path,
                    )

                    continue
                elif Util.isValidCanvasPresent(driver) and len(clickables) > 0:
                    if last_analysis_mode != "mixed":
                        print("⚠️ Canvas + DOM elements, Start Mixed Analysis.")
                        last_analysis_mode = "mixed"
                    time.sleep(0.5)
                    from scripts.MixedAnalysis import MixedAnalysis

                    remaining_budget = max(
                        0.0, budget_time - (time.time() - start_time)
                    )
                    MixedAnalysis.mixed_budget_analysis(
                        driver,
                        remaining_budget,
                        bot,
                        host_name,
                    )
                    break

                elif not Util.isValidCanvasPresent(driver) and len(clickables) > 0:
                    if last_analysis_mode != "dom":
                        print("⚠️ DOM Elements detected, Start Dom Analysis.")
                        last_analysis_mode = "dom"
                    element = random.choice(clickables)
                    if not Util.is_really_interactable(driver, element):
                        continue
                    click_index += 1
                    cls.dom_analysis(
                        driver,
                        element,
                        click_index,
                        host_name,
                        click_log_path,
                    )

                else:
                    try:
                        print("⚠️ No Elements detected, going back to previous page.")
                        driver.back()
                        time.sleep(1)
                        Util._wait_for_webview_context(driver, timeout=3)
                        clickables = Util.get_interactable_elements(driver)
                        print("Clickable elements number: ", len(clickables))
                        if len(clickables) == 0:
                            print("⚠️ No Elements detected, stopping interaction.")
                            return
                        continue
                    except Exception:
                        print(
                            "❌ Unable to recover from domain change, stopping interaction."
                        )
                        return
                print("Clickable elements number: ", len(clickables))

            except DeviceWedgedError:
                # Device adbd is wedged: abort the loop and let it propagate so
                # the run unwinds to the device reboot.
                print("🛑 Device wedged: aborting budget analysis to reboot device.")
                raise
            except WebDriverException as exc:
                if "socket hang up" in str(exc).lower():
                    print(f"⚠️ Budget analysis stopped: {exc}")
                    break
                print(f"⚠️ WebDriver error during budget analysis: {exc}")
                continue
            except Exception as exc:
                print(f"⚠️ Error during budget analysis: {exc}")
                continue

        if not miniapp_loaded:
            print("⚠️ Budget time exceeded without loading mini app.")
        # Util.end_screenshot(bot, driver=driver)

        print(f"⏰ Budget analysis completed. Total clicks attempted: {click_index}")

    @classmethod
    def dom_analysis(
        cls,
        driver: webdriver.Remote,
        element,
        click_index: int,
        host_name: str,
        click_log_path: Path,
    ) -> None:
        current_domain = Util._safe_get_domain(driver) or ""
        current_url = Util.get_current_url(driver)
        button_data = Util._extract_element_metadata(driver, element)

        click_record = {
            "click_index": click_index,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "host_name": host_name,
            "domain_before_click": current_domain,
            "url_before_click": current_url,
            "button": button_data,
            "interactable_check": True,
            "click_success": False,
            "error": "",
        }

        try:
            element.click()
            if not Util.verify_webview(driver):
                print("OVERLAY WEBVIEW FOUND")
                Util.recover_mini_app_with_go_back(driver, host_name, 3)
            if Util.isMiniAppMinimize(driver, 3):
                print("Mini App Minimized")
                Util.recover_mini_app_minimized(driver, host_name, 3)
            click_record["click_success"] = True
            time.sleep(0.5)
            click_record["domain_after_click"] = Util._safe_get_domain(driver) or ""
            click_record["url_after_click"] = Util.get_current_url(driver)
            if click_record["url_after_click"] != click_record["url_before_click"]:
                driver.back()
            print(
                f"🔘 Clicked element #{click_index}: {button_data['tag']} with text '{button_data['text'][:30]}...'"
            )
        except Exception as click_error:
            click_record["error"] = str(click_error)
        finally:
            Util.append_click_log(click_log_path, click_record)


__all__ = ["DynamicAnalysis"]
