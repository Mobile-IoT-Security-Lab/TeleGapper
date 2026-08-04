# ScraperMiniApp

Selenium scraper that incrementally extracts the app catalog from `https://tapps.center/` and saves it to CSV. It feeds the app list consumed by [TeleGapper](../README.md), the black-box dynamic analysis tool.

## Main files

- `scraper.py`: main script
- `botList.txt`: bot list used by the project batch scripts
- `tapps_apps_clean.csv`: existing cleaned dataset
- `tapps_apps_27April.csv`: existing historical snapshot

## Requirements

- Python `3.10+`
- Google Chrome installed
- Project dependencies installed:

```bash
pip install -r requirements.txt
```

Note: `selenium` is required. `webdriver-manager` is optional; if it is not present, the script falls back to the built-in Selenium Manager.

## Running

From the repository root:

```bash
python ScraperMiniApp/scraper.py
```

## Output

By default the script creates/updates:

- `tapps_apps_live.csv` (in the working directory the command is launched from)

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
