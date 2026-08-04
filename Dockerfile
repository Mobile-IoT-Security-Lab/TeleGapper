# syntax=docker/dockerfile:1
#
# TeleGapper pipeline image.
#
# The container runs the Python side of the pipeline only. Appium, the adb
# server and Burp stay on the host, because the Android device is attached over
# USB and Docker Desktop on macOS has no USB passthrough. The container reaches
# them over TCP (see docker-compose.yml).
#
#   runtime  -> Automator.py + analysis pipeline (adb client, WeasyPrint)
#   scraper  -> runtime + Chromium, for ScraperMiniApp/scraper.py

FROM python:3.14-slim-trixie AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# adb: talks to the host adb server over TCP (ADB_SERVER_SOCKET), no USB needed.
# libpango/libcairo/libgdk-pixbuf/fonts: native deps of WeasyPrint (privacy
# policy -> PDF). Without them `import weasyprint` fails at import time.
RUN apt-get update && apt-get install -y --no-install-recommends \
        adb \
        ca-certificates \
        curl \
        fonts-dejavu-core \
        fonts-liberation \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libharfbuzz0b \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY ScraperMiniApp/ ./ScraperMiniApp/
COPY Automator.py script.sh proxy_config.json ./
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh ./script.sh

# Defaults for the containerized layout; compose overrides what is host-specific.
ENV APPS_DIR="/app/Analysis results" \
    BURP_LOG_FILE=/app/burp_log.txt \
    BURP_CONFIG_FILE=/app/proxy_config.json \
    APPIUM_SERVER_URL=http://host.docker.internal:4723 \
    ADB_SERVER_SOCKET=tcp:host.docker.internal:5037 \
    APPIUM_AUTOSTART=false \
    ADB_MANAGE_SERVER=false

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash", "./script.sh"]


FROM runtime AS scraper

# Chromium instead of Google Chrome: Google ships no linux/arm64 Chrome build,
# and this image is built on Apple Silicon.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
    && rm -rf /var/lib/apt/lists/*

ENV CHROME_BINARY=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    CHROME_HEADLESS=true \
    TAPPS_CSV=/app/out/tapps_apps_live.csv

CMD ["python", "ScraperMiniApp/scraper.py"]
