#!/usr/bin/env python3
from time import sleep
import sys

from dotenv import load_dotenv

from scripts.DynamicAnalysis import DynamicAnalysis
from scripts.TrafficAnalysis import TrafficDumpAnalyzer
from scripts.Util import Util

load_dotenv()


class Automator:

    @classmethod
    def run(cls, bot: str, budget_time_seconds: float = 120) -> None:
        apps_dir = Util.ensure_apps_dir()
        Util.wait_for_device_ready()
        Util.start_appium_server()
        Util.enable_webview_debug()
        Util.restart_adb_server()

        driver = DynamicAnalysis.launch_telegram()
        processing_ok = False
        try:
            processing_ok = DynamicAnalysis.process_bot(driver, apps_dir, bot)
            if not processing_ok:
                print(f"⏭️ Skipping bot {bot}: mini app not opened.")
        except Exception as exc:
            print(f"❌ Error during processing: {exc}")
        finally:
            if processing_ok:
                try:
                    DynamicAnalysis.budget_analysis(
                        driver,
                        budget_time_seconds,
                        bot,
                    )
                except Exception as exc:
                    print(f"⚠️ Error during budget analysis startup: {exc}")
                finally:
                    Util.save_bot_trafficEnd(apps_dir, bot)
                    TrafficDumpAnalyzer.analyze(
                        bot=bot,
                        traffic_dir=(apps_dir / "trafficEnd").resolve(),
                        output_dir=(apps_dir / "resEnd").resolve(),
                    )
                    report_path = TrafficDumpAnalyzer.report_path_for_bot(
                        bot=bot,
                        output_dir=(apps_dir / "resEnd").resolve(),
                    )
                    legacy_report_path = apps_dir / "resStart" / f"{bot}_report.json"
                    if (
                        legacy_report_path != report_path
                        and legacy_report_path.exists()
                    ):
                        legacy_report_path.unlink()
                        print(f"🧹 Removed legacy report: {legacy_report_path.name}")
                    Util.reset_burp_log()
                    try:
                        import threading
                        def force_quit():
                            try:
                                driver.quit()
                            except Exception:
                                pass
                        t = threading.Thread(target=force_quit)
                        t.start()
                        t.join(timeout=10)
                        if t.is_alive():
                            print("⚠️ driver.quit() timed out (device likely wedged).")
                        else:
                            print("👋 Driver closed.")
                    except Exception as exc:
                        # On a wedged device driver.quit() runs adb cleanup that
                        # can fail/timeout. Don't let it stop the reboot below.
                        print(f"⚠️ driver.quit() failed: {exc}")
                    Util.reboot_device()
            else:
                try:
                    import threading
                    def force_quit():
                        try:
                            driver.quit()
                        except Exception:
                            pass
                    t = threading.Thread(target=force_quit)
                    t.start()
                    t.join(timeout=10)
                    if t.is_alive():
                        print("⚠️ driver.quit() timed out (bot skipped, device likely wedged).")
                    else:
                        print("👋 Driver closed (bot skipped).")
                except Exception:
                    pass
                print("🔄 Rebooting device after 120 seconds...")
                sleep(10)

                Util.reboot_device()


def main(bot: str) -> None:
    Automator.run(bot)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: ./Automator.py @BotUsername")
    main(sys.argv[1])
