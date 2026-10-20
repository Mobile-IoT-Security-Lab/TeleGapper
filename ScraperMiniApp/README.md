# ScraperMiniApp

Selenium scraper that incrementally extracts the app catalog from `https://tapps.center/` and saves it to CSV for [TeleGapper](../README.md), the black-box dynamic analysis tool. It does not automatically create the bot list used by the batch pipeline.

The repository documents this scraper as functional until June 2026, before the
site interface changed in August 2026. Its selectors target that earlier layout;
the script may need updates to scrape the live site. The historical reference is
the [June 6, 2026 Wayback Machine snapshot](https://web.archive.org/web/20260606072458/https://tapps.center/).

## Main files

- [scraper.py](scraper.py): main script
- [D_ExcludedHost.txt](D_ExcludedHost.txt): existing list of bot usernames; not read by the scraper
- [../tapps_apps_live_June.csv](../tapps_apps_live_June.csv): bundled June catalog snapshot at the repository root
- [../script.sh](../script.sh): batch pipeline; reads `ScraperMiniApp/rerun.txt`, a local input file that must be created before use (see the [main README](../README.md#usage))

## Requirements

- Python `3.10+`
- Google Chrome or Chromium installed
- Project dependencies installed; run this command from the repository root:

```bash
pip install -r requirements.txt
```

`selenium` is required. With `CHROMEDRIVER_PATH` set, the script uses that driver.
Otherwise it uses `webdriver-manager` if installed, or the built-in Selenium
Manager as a fallback. `webdriver-manager` is optional and is not listed in the
project requirements.

## Running

From the repository root:

```bash
python ScraperMiniApp/scraper.py
```

Optional environment variables:

| Variable | Purpose |
|---|---|
| `TAPPS_CSV` | Output CSV path; relative paths are anchored at the repository root |
| `CHROME_HEADLESS` | Set to `true`, `1` or `yes` to run without a browser window |
| `CHROME_BINARY` | Path to a Chrome or Chromium executable |
| `CHROMEDRIVER_PATH` | Path to a matching Chromedriver executable |

Set these in the shell environment; the standalone scraper does not load `.env`.
For example:

```bash
CHROME_HEADLESS=true TAPPS_CSV=tapps_apps_live.csv python ScraperMiniApp/scraper.py
```

For the containerized browser, see the [Docker README](../docker/README-docker.md).

## Output

By default the script creates/updates:

- `tapps_apps_live.csv` at the repository root, regardless of the working directory

`TAPPS_CSV` overrides that location. Its parent directory must already exist.
This generated file is separate from the bundled `tapps_apps_live_June.csv`.

The saved columns are:

- `categoria`
- `cat_url`
- `app_name`
- `card_url`
- `before_open_url`
- `new_tab_open_url`
- `final_url`

## Operational notes

- The CSV is updated row by row during the scan.
- The script opens extra tabs when it finds the `OPEN` button and saves their final URL.
- If the run is interrupted, the data already written to the CSV stays available.
- A new run appends to an existing CSV; it does not resume from the last visited app or deduplicate rows.
