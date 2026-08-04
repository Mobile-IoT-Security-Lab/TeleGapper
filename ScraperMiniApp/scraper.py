#!/usr/bin/env python3
import csv
import os
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# Repository root, derived from this file's location so the output lands in the
# same place regardless of the machine or the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = PROJECT_ROOT / "tapps_apps_live.csv"


class TappsScraper:
    """Scraper for the tapps.center catalog with incremental CSV saving."""

    fieldnames = [
        "categoria",
        "cat_url",
        "app_name",
        "card_url",
        "before_open_url",
        "new_tab_open_url",
        "final_url",
    ]

    def __init__(self, csv_filename: str | None = None) -> None:
        # A relative TAPPS_CSV is anchored at the repository root, not at the
        # current working directory; an absolute one (the container path in
        # docker-compose.yml) is kept as is.
        configured = csv_filename or os.getenv("TAPPS_CSV")
        self.csv_filename = str(
            PROJECT_ROOT / configured if configured else DEFAULT_CSV
        )
        self.categories_visited: list[str] = []
        self.apps_data: list[dict] = []
        self.driver: webdriver.Chrome | None = None

    @staticmethod
    def _build_chrome_options() -> Options:
        chrome_options = Options()
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        # In a container there is no display and Chrome ships as Chromium under
        # a different path, so both are configurable.
        if os.getenv("CHROME_HEADLESS", "").lower() in {"1", "true", "yes"}:
            chrome_options.add_argument("--headless=new")
        chrome_binary = os.getenv("CHROME_BINARY")
        if chrome_binary:
            chrome_options.binary_location = chrome_binary
        return chrome_options

    def _ensure_driver(self) -> webdriver.Chrome:
        if self.driver is None:
            chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
            if chromedriver_path:
                # Distro-provided chromedriver: skip any download attempt, which
                # would fail offline and pull the wrong arch anyway.
                self.driver = webdriver.Chrome(
                    service=Service(chromedriver_path),
                    options=self._build_chrome_options(),
                )
            else:
                try:
                    from webdriver_manager.chrome import ChromeDriverManager
                except ModuleNotFoundError:
                    self.driver = webdriver.Chrome(options=self._build_chrome_options())
                else:
                    service = Service(ChromeDriverManager().install())
                    self.driver = webdriver.Chrome(
                        service=service,
                        options=self._build_chrome_options(),
                    )
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
        return self.driver

    def _init_csv(self) -> None:
        file_exists = os.path.isfile(self.csv_filename)
        with open(self.csv_filename, "a", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=self.fieldnames)
            if not file_exists:
                writer.writeheader()
                print(f"✅ CSV initialized: {self.csv_filename}")

    def _append_row(self, row: dict) -> None:
        with open(self.csv_filename, "a", newline="", encoding="utf-8") as file_handle:
            writer = csv.DictWriter(file_handle, fieldnames=self.fieldnames)
            writer.writerow(row)

    def run(self) -> None:
        driver = self._ensure_driver()
        self._init_csv()

        try:
            driver.get("https://tapps.center/")
            wait = WebDriverWait(driver, 20)
            wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, ".styles_scrollContainer__ICkJv a[href]")
                )
            )
            print("✅ Started!")

            original_window = driver.current_window_handle
            all_windows = driver.window_handles

            category_links = driver.find_elements(
                By.CSS_SELECTOR, ".styles_scrollContainer__ICkJv a[href]"
            )

            for category_index in range(len(category_links)):
                try:
                    category_links = driver.find_elements(
                        By.CSS_SELECTOR, ".styles_scrollContainer__ICkJv a[href]"
                    )
                    if category_index >= len(category_links):
                        break

                    category_link = category_links[category_index]
                    cat_name = category_link.text.strip() or f"Cat_{category_index + 1}"
                    cat_url = category_link.get_attribute("href") or ""

                    print(f"\n🔄 [{category_index + 1}] '{cat_name}'")

                    driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});",
                        category_link,
                    )
                    time.sleep(0.5)
                    category_link.click()

                    wait.until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, ".styles_applicationCardLink__uYHrK")
                        )
                    )

                    app_index = 0
                    while True:
                        app_links = driver.find_elements(
                            By.CSS_SELECTOR, ".styles_applicationCardLink__uYHrK"
                        )
                        if app_index >= len(app_links):
                            break

                        app_link = app_links[app_index]
                        app_name = (app_link.text.strip() or f"App_{app_index + 1}")[:80]
                        card_url = app_link.get_attribute("href") or ""

                        print(f"     📱 [{app_index + 1}] '{app_name}'")

                        driver.execute_script("arguments[0].click();", app_link)
                        time.sleep(0.5)

                        open_xpath = "/html/body/main/div[1]/div[2]/div[1]/div[2]/div[2]/button[1]/span"
                        new_tab_url = ""
                        before_open_url = driver.current_url

                        try:
                            open_btn = WebDriverWait(driver, 1).until(
                                EC.element_to_be_clickable((By.XPATH, open_xpath))
                            )
                            driver.execute_script("arguments[0].click();", open_btn)
                            print("       ✅ OPEN!")
                            time.sleep(0.5)

                            all_windows_after = driver.window_handles
                            if len(all_windows_after) > len(all_windows):
                                new_window = [
                                    window
                                    for window in all_windows_after
                                    if window != original_window
                                ][0]
                                driver.switch_to.window(new_window)
                                new_tab_url = driver.current_url
                                print(f"       🌐 NEW TAB: {new_tab_url}")
                                driver.close()
                                driver.switch_to.window(original_window)
                            else:
                                new_tab_url = driver.current_url

                        except Exception as open_error:
                            print(f"       ⚠️ OPEN: {str(open_error)[:40]}")
                            new_tab_url = driver.current_url

                        popup_selectors = [
                            "//button[@aria-label='Close'] | //button[contains(@class,'close')]",
                            ".modal-close",
                            "//svg[@aria-label='close']",
                            "//button[.//span[text()='✕' or text()='X']]",
                        ]

                        for popup_selector in popup_selectors:
                            try:
                                close_btn = WebDriverWait(driver, 0.5).until(
                                    EC.element_to_be_clickable((By.XPATH, popup_selector))
                                )
                                driver.execute_script("arguments[0].click();", close_btn)
                                print("       ✅ Popup closed!")
                                time.sleep(0.5)
                                break
                            except Exception:
                                continue

                        driver.back()
                        time.sleep(0.5)
                        wait.until(
                            EC.presence_of_element_located(
                                (By.CSS_SELECTOR, ".styles_applicationCardLink__uYHrK")
                            )
                        )

                        app_row = {
                            "categoria": cat_name,
                            "cat_url": cat_url,
                            "app_name": app_name,
                            "card_url": card_url,
                            "before_open_url": before_open_url,
                            "new_tab_open_url": new_tab_url,
                            "final_url": driver.current_url,
                        }

                        self._append_row(app_row)
                        self.apps_data.append(app_row)
                        print(f"     ✅ APP SAVED #{len(self.apps_data)}: {new_tab_url[:80]}...")

                        app_index += 1

                    print(f"   ✅ {app_index} app")
                    driver.back()
                    time.sleep(0.5)
                    wait.until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, ".styles_scrollContainer__ICkJv a[href]")
                        )
                    )
                    self.categories_visited.append(cat_name)

                except Exception as exc:
                    print(f"   ❌: {str(exc)[:60]}")
                    driver.back()
                    time.sleep(0.5)
                    continue

            print(f"\n🎉 TOTAL: {len(self.apps_data)} apps saved live!")
            print(f"📄 File: {self.csv_filename}")
            if sys.stdin is not None and sys.stdin.isatty():
                input("\nEnter...")

        finally:
            self.close()

    def close(self) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None


def main() -> None:
    TappsScraper().run()


if __name__ == "__main__":
    main()
