import random
import time

from appium import webdriver

from scripts.CanvasAnalysis import CanvasAnalysis
from scripts.DynamicAnalysis import DynamicAnalysis
from scripts.Util import Util


class MixedAnalysis:
    """Analisi ibrida DOM + canvas."""


    @classmethod
    def mixed_budget_analysis(
        cls,
        driver: webdriver.Remote,
        budget_time: float,
        bot: str,
        host_name: str | None = None,
    ) -> None:
        print("🔀 Starting mixed analysis...")

        dom_budget = budget_time / 2
        canvas_budget = max(0.0, budget_time - dom_budget)

        if not Util._wait_for_webview_context(driver, timeout=5):
            print("⚠️ Skipping mixed analysis: no WEBVIEW context available.")
            print("✅ Mixed analysis completed.")
            return

        resolved_host = host_name or Util._safe_get_domain(driver)
        if not resolved_host:
            print("⚠️ Skipping mixed analysis: unable to resolve host.")
            print("✅ Mixed analysis completed.")
            return

        click_log_path = Util._build_click_log_path(bot)
        click_index = 0

        if dom_budget > 0:
            print(f"🔘 DOM phase: {dom_budget:.1f}s")
            dom_deadline = time.time() + dom_budget
            while time.time() < dom_deadline:
                clickables = Util.get_interactable_elements(driver)
                if not clickables:
                    time.sleep(0.5)
                    continue

                element = random.choice(clickables)
                if not Util.is_really_interactable(driver, element):
                    continue

                click_index += 1
                DynamicAnalysis.dom_analysis(
                    driver,
                    element,
                    click_index,
                    resolved_host,
                    click_log_path,
                )
        else:
            print("ℹ️ Skipping DOM phase: no DOM budget available.")

        if canvas_budget <= 0:
            print("✅ Mixed analysis completed.")
            return

        if not Util._wait_for_webview_context(driver, timeout=5):
            print("⚠️ Skipping canvas phase: no WEBVIEW context available.")
            print("✅ Mixed analysis completed.")
            return

        if not Util.isValidCanvasPresent(driver):
            print("⚠️ Skipping canvas phase: no visible canvas found.")
            print("✅ Mixed analysis completed.")
            return

        print(f"🖼️ Canvas phase: {canvas_budget:.1f}s")
        canvas_deadline = time.time() + canvas_budget
        # If the app navigated to a view without a visible canvas (common in
        # multi-game hubs after DOM clicks), don't spin re-checking the canvas
        # at full speed: back off, and bail out after a few consecutive misses.
        missing_canvas_streak = 0
        max_missing_streak = 5
        while time.time() < canvas_deadline:
            if not Util.isValidCanvasPresent(driver):
                missing_canvas_streak += 1
                if missing_canvas_streak >= max_missing_streak:
                    print(
                        "ℹ️ No valid canvas for "
                        f"{max_missing_streak} checks, ending canvas phase early."
                    )
                    break
                time.sleep(1.0)
                continue
            missing_canvas_streak = 0
            click_index += 1
            CanvasAnalysis.canvas_analysis(
                driver,
                click_index,
                resolved_host,
                click_log_path,
            )
        print("✅ Mixed analysis completed.")


__all__ = ["MixedAnalysis"]
