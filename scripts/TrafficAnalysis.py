#!/usr/bin/env python3
import base64
import json
import re
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
APPS_DIR = PROJECT_ROOT / "Analysis results"


BLOCK_RE = re.compile(
    r"=+\r?\n(?P<ts>\d{2}:\d{2}:\d{2})\s+(?P<base>https?://\S+)\s+\[(?P<ip>[^\]]+)\]\r?\n=+\r?\n",
    re.MULTILINE,
)
REQ_RE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\s+(\S+)\s+HTTP/[\d.]+$")
SEPARATOR_RE = re.compile(r"^=+$")
MAX_EXPANSION_ITEMS = 120
MAX_EXPANSION_TEXT = 250_000
COMPACT_VALUE_TYPES = {"ton_wallet_address"}
COMPACT_VALUE_THRESHOLD = 3
COMPACT_SAMPLE_LIMIT = 3

# GPS coordinates: a single request that carries more than this many distinct
# latitude (or longitude) values is a list of places/POIs, not the user's own
# location, so those coordinates are dropped to avoid mislabeling venue data as
# the user's GPS position.
GPS_TYPES = {"gps_latitude", "gps_longitude"}
GPS_POI_LIST_THRESHOLD = 2

EXCLUDED_HOSTS = {
    "tganalytics.xyz",
    "android.googleapis.com",
    "content-autofill.googleapis.com",
    "play.googleapis.com",
    "digitalassetlinks.googleapis.com",
    "ondevicesafety-pa.googleapis.com",
    "www.gstatic.com",
    "people-pa.googleapis.com",
    "content-autofill.googleapis.com",
    "digitalassetlinks.googleapis.com",
    "fonts.googleapis.com",
    "cryptauthenrollment.googleapis.com",
    "fonts.gstatic.com",
    "restrictedapps-pa.googleapis.com",
    "connectivitycheck.gstatic.com",
    "gmscompliance-pa.googleapis.com",
    "nearbysharing-pa.googleapis.com",
    "phonedeviceverification-pa.googleapis.com",
    "google-analytics.com",
    "nearbydevices-pa.googleapis.com",
    "cdn4.telesco.pe",
    "t.me",
    "unpkg.com",
    "cdn.jsdelivr.net",
    "nft.fragment.com",
    "aws-v2-cdn.token.im",
    "cdn.mirailabs.co",
    "chain-cdn.uxuy.com",
    "pub.tomo.inc",
    "raw.githubusercontent.com",
    "static.gramwallet.io",
    "static.mywallet.io",
    "static.nicegram.app",
    "static.okx.com",
    "tonhub.com",
    "tonkeeper.com",
    "uni.onekey-asset.com",
    "wallet.tg",
    "xtonwallet.com",
    "cdn.echooo.xyz",
    "downloads.mycactus.com",
    "fs.defiway.com",
    "hk.tpstatic.net",
    "img.gatedataimg.com",
    "assets.stower.money",
    "static.mytonwallet.io"

}

# Excluded (substring) patterns: any host containing one of these
# strings is excluded, without having to enumerate every subdomain.
EXCLUDED_HOST_SUBSTRINGS = {
    "googleapis",
    "innerworks",
    "supabase.co"
}

# Root domains excluded only from domain inference (not from data extraction).
# These are analytics SDKs / game platforms that embed inside mini-apps and
# would otherwise pollute the inferred mini-app domain.
DOMAIN_INFERENCE_BLOCKLIST: set[str] = {
    "pluto.vision",
    "posthog.com",
    "yandex.ru",
    "github.com",
    "x.com",
    "twitter.com",
    "adsgram.ai",
    "telega.io",
    "cloudflare.com",
    "challenges.cloudflare.com",
    "e8ys.com",
    
}

EXTRACTORS = [
    (
        "telegram_init_data",
        r"(?<![a-zA-Z0-9_])(?:tgWebAppData|initData|init_data|initdata)(?:=|[\"']?\s*:\s*[\"']?)([^\"'\s<]{8,})",
    ),
    (
        "telegram_user_id",
        r"(?<![a-zA-Z0-9_])(?:tg[_]?id|user[_]?id|userId|telegram[_]?id|user\.id|chatId|chat[_]?id|from[_]?id|from\.id|[\"']id[\"'])\s*(?:=|:)\s*[\"']?(\d{5,13})(?![\d])[\"']?",
    ),
    (
        "internal_user_id",
        r"(?<![a-zA-Z0-9_])(?:user[_]?id)\s*(?:=|:)\s*[\"']?([a-f0-9]{16,32})",
    ),
    (
        "telegram_username",
        r"(?<![a-zA-Z0-9_])username\s*(?:=|:)\s*[\"']?(@?[a-zA-Z0-9_]{3,32})",
    ),
    (
        "telegram_first_name",
        r"(?<![a-zA-Z0-9_])(?:first_name|[\"']?first_name[\"']?)\s*(?:=|:)\s*[\"']?([^&\"',}\n]{1,})",
    ),
    (
        "telegram_last_name",
        r"(?<![a-zA-Z0-9_])(?:last_name|[\"']?last_name[\"']?)\s*(?:=|:)\s*[\"']?([^&\"',}\n]{1,})",
    ),
    (
        "telegram_photo_url",
        r"(?<![a-zA-Z0-9_])[\"']?photo_url[\"']?\s*(?:=|:)\s*[\"']?(https?://[^&\"'\s}]+)",
    ),
    (
        "telegram_language_code",
        r"(?<![a-zA-Z0-9_])language_code\s*(?:=|:)\s*[\"']?([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)",
    ),
    (
        "telegram_allows_write_to_pm",
        r"(?<![a-zA-Z0-9_])allows_write_to_pm\s*(?:=|:)\s*[\"']?(true|false|0|1)",
    ),
    (
        "telegram_chat_instance",
        r"(?<![a-zA-Z0-9_])chat_instance\s*(?:=|:)\s*[\"']?(-?\d{5,})",
    ),
    (
        "telegram_chat_type",
        r"(?<![a-zA-Z0-9_])chat_type\s*(?:=|:)\s*[\"']?([a-zA-Z_]{3,})",
    ),
    (
        "telegram_start_param",
        r"(?<![a-zA-Z0-9_])start_param\s*(?:=|:)\s*[\"']?([^&\"',}\s]{1,})",
    ),
    (
        "telegram_signature",
        r"(?<![a-zA-Z0-9_])signature\s*(?:=|:)\s*[\"']?([A-Za-z0-9_-]{20,})",
    ),
    (
        "oauth_bearer_token",
        r"Authorization:\s*Bearer\s+([A-Za-z0-9._\-\/=%&]{16,})",
    ),
    (
        "jwt_token",
        r"(eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_=/+\-]{4,})",
    ),
    (
        "api_key",
        r"(?<![a-zA-Z0-9_\-])(?:api[-_]?key|apiKey|X-Goog-Api-Key)[=:\s]+([A-Za-z0-9_\-]{20,})|(?<![a-zA-Z0-9_\-:])key[=:\s]+(?=.*[0-9])([A-Za-z0-9_\-]{20,})",
    ),
    (
        "email_address",
        r"\b([a-zA-Z0-9_.+-]+(?:%40|@)[a-zA-Z0-9.-]+\.(?!json|png|jpe?g|gif|svg|css|js|woff2?|ttf|eot|wasm|mp[34]|webm|txt)[a-zA-Z]{2,})\b",
    ),
    ("cookie_ga", r"(_ga=GA[\d.]+\.\d+\.\d+)"),
    ("cookie_ga_session", r"(_ga_[A-Z0-9]+=GS[\d.A-Za-z$]+)"),
    ("cookie_ym_uid", r"(_ym_uid=\d{6,})"),
    ("cookie_custom", r"(_[A-Za-z0-9]{4,}=[a-f0-9\-]{20,})"),
    (
        "device_fingerprint",
        r"(?:Pixel|Samsung|iPhone|OnePlus|Xiaomi|Galaxy|Nokia|OPPO|Google Pixel)[^\";'<\n]*Build/[A-Za-z0-9._]+",
    ),
    (
        "telegram_app_version",
        r"Telegram-(?:Android|iOS)/([0-9]+(?:\.[0-9]+)+)",
    ),
    (
        "browser_version",
        r"(?:Chrome|Chromium)/([0-9]+(?:\.[0-9]+)+)",
    ),
    (
        "android_version",
        r"Android\s+([0-9]+(?:\.[0-9]+)*)(?=[;)\s]|$)",
    ),
    ("gps_latitude", r"lat(?:itude)?[=:\"]+(-?\d{1,3}\.\d{4,})"),
    ("gps_longitude", r"lon(?:gitude)?[=:\"]+(-?\d{1,3}\.\d{4,})"),
    ("telegram_data_check_string", r"data_check_string=([A-Za-z0-9+/=_-]{20,})"),
    (
        "ton_wallet_address",
        r"(?<![a-zA-Z0-9_-])(UQ[a-zA-Z0-9_-]{46}|EQ[a-zA-Z0-9_-]{46}|0:[a-fA-F0-9]{64})(?![a-zA-Z0-9_-])",
    ),
    (
        "telegram_hash",
        r"(?<![a-zA-Z0-9_])(?:hash|[\"']?hash[\"']?)(?:=|[\"']?\s*:\s*[\"']?)([a-f0-9]{64})",
    ),
    (
        "telegram_auth_date",
        r"(?<![a-zA-Z0-9_])(?:auth_date|[\"']?auth_date[\"']?)(?:=|[\"']?\s*:\s*[\"']?)(\d{10,})",
    ),
    (
        "screen_resolution",
        r"(?<![a-zA-Z0-9_])(?:sr|screen[_\-]?res(?:olution)?)[=:\s]+([0-9]{3,4}x[0-9]{3,4}(?:x[0-9]{1,2})?)",
    ),
    (
        "screen_resolution",
        r"(?<![a-zA-Z0-9_])(?:w|s):([0-9]{3,4}x[0-9]{3,4}(?:x[0-9]{1,2})?)",
    ),
    (
        "device_os",
        r"(?<![a-zA-Z0-9_])(?:uap|platform|os(?:_name)?)\s*[=:]\s*([A-Za-z][A-Za-z0-9_-]{2,})",
    ),
    (
        "os_version",
        r"(?<![a-zA-Z0-9_])(?:uapv|os[_\-]?version)[=:\s]+([0-9]+(?:\.[0-9]+)*)",
    ),
    (
        "device_language",
        r"(?<![a-zA-Z0-9_])(?:ul|lang(?:uage)?|locale)\s*[=:]\s*[\"']?([a-zA-Z]{2}(?:-[a-zA-Z]{2})?)",
    ),
    (
        "page_visited",
        r"(?<![a-zA-Z0-9_])(?:dl|page[_\-]?url|page\.url|[\"']?url[\"']?|[\"']?referrer[\"']?)\s*[=:]\s*(https?://[^&\"'\s]+)",
    ),
    (
        "device_model",
        r"(?:Telegram-Android|Telegram-iOS)/[0-9.]+\s*\(([^;)]+)",
    ),
    (
        "analytics_session_id",
        r"(?<![a-zA-Z0-9_])(?:session[_\-]?id|sid)\s*[=:]\s*[\"']?([A-Za-z0-9._-]{8,})",
    ),
    (
        "analytics_client_id",
        r"(?<![a-zA-Z0-9_])(?:cid|client[_\-]?id)[=:\s]+(\d{8,}\.\d{8,})",
    ),
    (
        "analytics_event_name",
        r"(?<![a-zA-Z0-9_])event[_\-]?name[=:\s]+[\"']?([A-Za-z0-9._:-]{2,})",
    ),
    (
        "app_name",
        r"(?<![a-zA-Z0-9_])app[_\-]?name[=:\s]+[\"']?([A-Za-z0-9._:-]{2,})",
    ),
]

COMPILED_EXTRACTORS = [
    (dtype, re.compile(pattern, re.IGNORECASE)) for dtype, pattern in EXTRACTORS
]

# telegram_signature / telegram_hash are only genuine when they travel inside
# Telegram init data. Standalone "signature="/"hash=" fields on CDN, AWS, video
# and ads URLs (e.g. Google gvt1 playback signatures, X-Amz-Signature) are false
# positives, so these two types are only accepted when the same request also
# carries an init-data marker below. The marker set deliberately excludes
# "signature="/"hash=" themselves so a lone CDN signature cannot self-validate.
TELEGRAM_AUTH_CONTEXT_TYPES = {"telegram_signature", "telegram_hash"}
TELEGRAM_AUTH_CONTEXT_RE = re.compile(
    r"tgWebAppData|initData|init_data|initdata|auth_date=\d{9,}|"
    r"query_id=AA|user=(?:%7B|%7b|\{)",
    re.IGNORECASE,
)

DATA_TYPE_META = {
    "telegram_init_data": ("telegram", "high"),
    "telegram_user_id": ("telegram", "high"),
    "telegram_username": ("telegram", "high"),
    "telegram_first_name": ("telegram", "high"),
    "telegram_last_name": ("telegram", "high"),
    "telegram_photo_url": ("telegram", "high"),
    "telegram_language_code": ("telegram", "low"),
    "telegram_allows_write_to_pm": ("telegram", "low"),
    "telegram_chat_instance": ("telegram", "medium"),
    "telegram_chat_type": ("telegram", "low"),
    "telegram_start_param": ("telegram", "medium"),
    "telegram_signature": ("telegram", "high"),
    "telegram_hash": ("telegram", "high"),
    "telegram_auth_date": ("telegram", "medium"),
    "telegram_data_check_string": ("telegram", "high"),
    "internal_user_id": ("identifier", "medium"),
    "oauth_bearer_token": ("auth", "high"),
    "jwt_token": ("auth", "high"),
    "api_key": ("auth", "medium"),
    "email_address": ("identifier", "high"),
    "cookie_ga": ("cookie", "medium"),
    "cookie_ga_session": ("cookie", "medium"),
    "cookie_ym_uid": ("cookie", "medium"),
    "cookie_custom": ("cookie", "medium"),
    "device_fingerprint": ("device", "medium"),
    "telegram_app_version": ("device", "low"),
    "browser_version": ("device", "low"),
    "android_version": ("device", "low"),
    "gps_latitude": ("location", "high"),
    "gps_longitude": ("location", "high"),
    "ton_wallet_address": ("wallet", "high"),
    "screen_resolution": ("device", "low"),
    "device_os": ("device", "low"),
    "device_model": ("device", "medium"),
    "os_version": ("device", "low"),
    "device_language": ("device", "low"),
    "page_visited": ("analytics", "medium"),
    "analytics_session_id": ("analytics", "medium"),
    "analytics_client_id": ("analytics", "medium"),
    "analytics_event_name": ("analytics", "low"),
    "app_name": ("analytics", "low"),
}


class TrafficDumpAnalyzer:
    TRAFFIC_DIR = PROJECT_ROOT / "Analysis results" / "traffic"
    OUTPUT_DIR = PROJECT_ROOT / "Analysis results" / "res"
    MAIN_DOMAIN = None
    REDACT = True
    INCLUDE_OCCURRENCE_COUNT = False
    INCLUDE_LOW_SENSITIVITY_FINDINGS = False
    INCLUDE_VERBOSE_ITEM_FIELDS = False
    EXCLUDED_HOSTS = EXCLUDED_HOSTS
    EXCLUDED_HOST_SUBSTRINGS = EXCLUDED_HOST_SUBSTRINGS
    OS_NOISE_LIST = EXCLUDED_HOSTS

    @staticmethod
    def _normalize_bot_name(bot: str) -> str:
        return bot.lstrip("@")

    @classmethod
    def report_path_for_bot(cls, bot: str, output_dir: Path | None = None) -> Path:
        out_dir = output_dir or cls.OUTPUT_DIR
        return out_dir / f"{cls._normalize_bot_name(bot)}_report.json"

    @classmethod
    def analyze(
        cls, bot: str, traffic_dir: Path | None = None, output_dir: Path | None = None
    ) -> dict:
        normalized_bot = cls._normalize_bot_name(bot)
        t_dir = traffic_dir or cls.TRAFFIC_DIR
        text = (t_dir / f"@{normalized_bot}_traffic.txt").read_text(
            encoding="utf-8", errors="replace"
        )
        return cls._run(
            text,
            normalized_bot,
            output_dir=output_dir,
            traffic_dir=t_dir,
        )

    @classmethod
    def analyze_file(cls, file_path, output_dir: Path | None = None) -> dict:
        p = Path(file_path)
        return cls._run(
            p.read_text(encoding="utf-8", errors="replace"),
            p.stem,
            output_dir=output_dir,
            input_path=p,
        )

    @classmethod
    def print_summary(cls, r: dict) -> None:
        m = r.get("_meta", {})
        print(f"  Domain   : {m.get('inferred_mini_app_domain', 'unknown')}")
        print(f"  Requests : {m.get('total_requests_parsed')}")
        print(f"  Types    : {m.get('unique_data_types_found')}")
        print(f"  3rd-party: {', '.join(m.get('third_party_recipients', []))}")

    @classmethod
    def _run(
        cls,
        text: str,
        source: str,
        output_dir: Path | None = None,
        traffic_dir: Path | None = None,
        input_path: Path | None = None,
    ) -> dict:
        reqs, parse_warnings = cls._parse_with_warnings(text)
        domain = cls.MAIN_DOMAIN or cls._infer_domain(reqs)
        findings, extract_stats = cls._extract(reqs, domain, return_stats=True)
        report = cls._build_report(
            findings,
            source,
            domain,
            reqs,
            parse_warnings=parse_warnings,
            extract_stats=extract_stats,
        )
        out_dir = output_dir or cls.OUTPUT_DIR
        out = out_dir / f"{source}_report.json"
        cls._merge_existing_report_fields(out, report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[OK] Report: {out}")
        return report

    @classmethod
    def _parse(cls, text: str) -> list:
        reqs, _ = cls._parse_with_warnings(text)
        return reqs

    @classmethod
    def _parse_with_warnings(cls, text: str) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        if not text.strip():
            return [], ["file_empty"]

        blocks = list(BLOCK_RE.finditer(text))
        if not blocks:
            return [], ["no_request_blocks_found"]

        result = []
        skipped_blocks = 0
        binary_body_blocks = 0

        for i, match in enumerate(blocks):
            chunk = text[
                match.end() : (
                    blocks[i + 1].start() if i + 1 < len(blocks) else len(text)
                )
            ]
            lines = chunk.splitlines()
            request_index = cls._find_request_line(lines)
            if request_index is None:
                skipped_blocks += 1
                continue

            req_match = REQ_RE.match(lines[request_index].strip())
            if not req_match:
                skipped_blocks += 1
                continue

            method, path = req_match.group(1), req_match.group(2)
            headers, body_start = cls._parse_headers(lines, request_index + 1)
            host = (
                (
                    headers.get("Host")
                    or urllib.parse.urlsplit(match.group("base")).hostname
                    or ""
                )
                .split(":")[0]
                .lower()
            )
            body = cls._clean_body("\n".join(lines[body_start:]))
            if "\x00" in body:
                binary_body_blocks += 1
            result.append(
                {
                    "ts": match.group("ts"),
                    "host": host,
                    "ip": match.group("ip"),
                    "method": method,
                    "path": path,
                    "full_url": cls._build_full_url(match.group("base"), path),
                    "headers": headers,
                    "body": body,
                }
            )

        if skipped_blocks:
            warnings.append(f"skipped_blocks:{skipped_blocks}")
        if binary_body_blocks:
            warnings.append(f"binary_or_opaque_bodies:{binary_body_blocks}")
        return result, warnings

    @staticmethod
    def _find_request_line(lines: list[str]) -> int | None:
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or SEPARATOR_RE.match(stripped):
                continue
            if REQ_RE.match(stripped):
                return index
            return None
        return None

    @staticmethod
    def _validate_telegram_user_id(value: str) -> bool:
        try:
            v = int(value)
            return 1_000_000 <= v <= 10_000_000_000
        except Exception:
            return False

    @staticmethod
    def _parse_headers(
        lines: list[str], start_index: int
    ) -> tuple[dict[str, str], int]:
        headers = {}
        index = start_index
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                break
            if SEPARATOR_RE.match(line.strip()):
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip()] = value.strip()
            index += 1
        return headers, index

    @staticmethod
    def _clean_body(body: str) -> str:
        cleaned = []
        for line in body.splitlines():
            if SEPARATOR_RE.match(line.strip()):
                continue
            cleaned.append(line)
        return "\n".join(cleaned).strip()

    @staticmethod
    def _build_full_url(base: str, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return base.rstrip("/") + path

    @classmethod
    def _extract(
        cls, reqs: list, domain: str | None, return_stats: bool = False
    ) -> dict | tuple[dict, dict]:
        findings: dict[tuple[str, str], dict[str, Any]] = defaultdict(cls._finding)
        stats = {
            "first_party_requests_excluded": 0,
            "excluded_recipient_requests": Counter(),
            "third_party_requests_analyzed": 0,
            "third_party_recipients_observed": set(),
            "recipient_classes": {},
            "non_text_body_requests": 0,
        }

        for request_index, req in enumerate(reqs):
            host = req.get("host", "")
            if not host:
                continue
            if cls._is_excluded_host(host):
                stats["excluded_recipient_requests"][host] += 1
                continue
            if cls._is_first_party(host, domain):
                stats["first_party_requests_excluded"] += 1
                continue

            recipient_class = cls._classify_recipient(host)
            stats["third_party_requests_analyzed"] += 1
            stats["third_party_recipients_observed"].add(host)
            stats["recipient_classes"][host] = recipient_class
            if "\x00" in req.get("body", ""):
                stats["non_text_body_requests"] += 1

            # GPS coordinates are buffered per request so a list of POIs (many
            # distinct lat/lon values) can be discarded before it reaches the
            # findings; only a single user location survives the guard below.
            gps_local: dict[str, dict[str, list]] = {t: {} for t in GPS_TYPES}

            for carrier, text in cls._request_texts(req):
                expanded = cls._expand(text)
                has_telegram_auth_context = bool(
                    TELEGRAM_AUTH_CONTEXT_RE.search(expanded)
                )
                for dtype, pattern in COMPILED_EXTRACTORS:
                    if (
                        dtype in TELEGRAM_AUTH_CONTEXT_TYPES
                        and not has_telegram_auth_context
                    ):
                        continue
                    for hit in pattern.finditer(expanded):
                        value = (
                            urllib.parse.unquote_plus(
                                hit.group(hit.lastindex) if hit.lastindex else hit.group(0)
                            )
                            .strip()
                            .strip('"')
                            .strip("'")
                        )
                        value = cls._normalize_text(value)
                        value = cls._canonicalize_value(dtype, value)
                        if not value:
                            continue
                        # 🔥 filtro semantico
                        if (
                            dtype == "telegram_user_id"
                            and not cls._validate_telegram_user_id(value)
                        ):
                            continue
                        if dtype in GPS_TYPES:
                            entry = gps_local[dtype].setdefault(value, [0, set()])
                            entry[0] += 1
                            entry[1].add(carrier)
                            continue
                        item = findings[(dtype, value)]
                        item["hosts"].add(host)
                        item["recipient_classes"].add(recipient_class)
                        item["carriers"].add(carrier)
                        item["evidence_requests"].add((request_index, host, carrier))
                        item["occurrence_count"] += 1

            for dtype, entries in gps_local.items():
                if len(entries) > GPS_POI_LIST_THRESHOLD:
                    continue
                for value, (occurrences, carriers) in entries.items():
                    item = findings[(dtype, value)]
                    item["hosts"].add(host)
                    item["recipient_classes"].add(recipient_class)
                    item["carriers"].update(carriers)
                    for carrier in carriers:
                        item["evidence_requests"].add((request_index, host, carrier))
                    item["occurrence_count"] += occurrences

        if not return_stats:
            return findings

        stats["excluded_recipient_requests"] = dict(
            sorted(stats["excluded_recipient_requests"].items())
        )
        stats["third_party_recipients_observed"] = sorted(
            stats["third_party_recipients_observed"]
        )
        stats["recipient_classes"] = dict(sorted(stats["recipient_classes"].items()))
        return findings, stats

    @staticmethod
    def _finding() -> dict[str, Any]:
        return {
            "hosts": set(),
            "recipient_classes": set(),
            "carriers": set(),
            "evidence_requests": set(),
            "occurrence_count": 0,
        }

    @classmethod
    def _request_texts(cls, req: dict[str, Any]) -> list[tuple[str, str]]:
        headers_text = "\n".join(
            f"{key}: {value}" for key, value in req["headers"].items()
        )
        body = req.get("body", "")
        texts = [
            ("url", "\n".join([req.get("full_url", ""), req.get("path", "")])),
            ("headers", headers_text),
        ]
        if body:
            texts.append(("body", body))
        return texts

    @classmethod
    def _expand(cls, text: str) -> str:
        queue = [text]
        seen = set()
        expanded = []

        while queue and len(expanded) < MAX_EXPANSION_ITEMS:
            current = cls._normalize_text(queue.pop(0))
            if not current:
                continue
            if len(current) > MAX_EXPANSION_TEXT:
                current = current[:MAX_EXPANSION_TEXT]
            marker = current
            if marker in seen:
                continue
            seen.add(marker)
            expanded.append(current)

            decoded = urllib.parse.unquote_plus(current)
            decoded = cls._normalize_text(decoded)
            if decoded and decoded != current:
                queue.append(decoded)

            cls._enqueue_url_parts(current, queue)
            cls._enqueue_form_parts(current, queue)
            cls._enqueue_json_parts(current, queue)
            cls._enqueue_base64_parts(current, queue)

        return "\n".join(expanded)

    @staticmethod
    def _normalize_text(text: str) -> str:
        return (
            text.replace("\\/", "/")
            .replace("\\u0026", "&")
            .replace("\\u003d", "=")
            .replace("\\u003D", "=")
            .replace("\\u003a", ":")
            .replace("\\u003A", ":")
        )

    @classmethod
    def _canonicalize_value(cls, dtype: str, value: str) -> str:
        if dtype == "telegram_init_data":
            return cls._canonicalize_telegram_init_data(value)
        if dtype == "page_visited":
            return cls._canonicalize_page_visited(value)
        return value

    @classmethod
    def _canonicalize_telegram_init_data(cls, value: str) -> str:
        current = cls._recursive_unquote(value)
        for marker in ("tgWebAppData", "initData", "init_data", "initdata"):
            match = re.search(rf"(?:^|[?#&]){marker}=([^\s\"'<>]+)", current)
            if match:
                current = cls._recursive_unquote(match.group(1))
                break

        user_index = current.find("user=")
        if user_index > 0:
            current = current[user_index:]

        stop_markers = (
            "&tgWebAppVersion=",
            "&tgWebAppPlatform=",
            "&tgWebAppThemeParams=",
            "&charset=",
            "&uah=",
            "&browser-info=",
            "&wmode=",
        )
        stop_positions = [current.find(marker) for marker in stop_markers]
        stop_positions = [position for position in stop_positions if position >= 0]
        if stop_positions:
            current = current[: min(stop_positions)]

        try:
            pairs = urllib.parse.parse_qsl(current, keep_blank_values=True)
        except ValueError:
            return current

        values_by_key = {}
        for key, parsed_value in pairs:
            if key and key not in values_by_key:
                values_by_key[key] = parsed_value

        if "user" in values_by_key and len(values_by_key["user"].strip()) < 10:
            return ""

        ordered_keys = (
            "query_id",
            "user",
            "receiver",
            "chat",
            "chat_instance",
            "chat_type",
            "start_param",
            "auth_date",
            "signature",
            "hash",
        )
        canonical_pairs = [
            (key, values_by_key[key]) for key in ordered_keys if key in values_by_key
        ]
        if canonical_pairs:
            return urllib.parse.urlencode(canonical_pairs)
        return current

    @classmethod
    def _canonicalize_page_visited(cls, value: str) -> str:
        current = cls._recursive_unquote(value)
        contains_init_data = any(
            marker in current
            for marker in (
                "tgWebAppData",
                "initData",
                "init_data",
                "user={",
                "user=%7B",
                "auth_date=",
            )
        )

        try:
            parsed = urllib.parse.urlsplit(current)
        except ValueError:
            return current

        if not parsed.scheme or not parsed.netloc:
            return current

        base_url = urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
        )
        if contains_init_data:
            return base_url + "#tgWebAppData"
        return current

    @classmethod
    def _recursive_unquote(cls, value: str, limit: int = 5) -> str:
        current = cls._normalize_text(value)
        for _ in range(limit):
            decoded = cls._normalize_text(urllib.parse.unquote_plus(current))
            if decoded == current:
                break
            current = decoded
        return current

    @classmethod
    def _enqueue_url_parts(cls, text: str, queue: list[str]) -> None:
        candidates = []
        if text.startswith(("http://", "https://")):
            candidates.append(text)
        candidates.extend(re.findall(r"https?://[^\s\"'<>]+", text))

        for candidate in candidates[:20]:
            try:
                parsed = urllib.parse.urlsplit(candidate)
            except ValueError:
                continue
            for piece in (parsed.query, parsed.fragment):
                if not piece:
                    continue
                queue.append(piece)
                for key, value in urllib.parse.parse_qsl(piece, keep_blank_values=True):
                    queue.append(f"{key}={value}")
                    queue.append(value)

    @staticmethod
    def _enqueue_form_parts(text: str, queue: list[str]) -> None:
        if "=" not in text:
            return
        if len(text) > MAX_EXPANSION_TEXT:
            return
        if "&" not in text and "%3D" not in text and "%3d" not in text and "\n" in text:
            return
        try:
            pairs = urllib.parse.parse_qsl(text, keep_blank_values=True)
        except ValueError:
            return
        if not pairs:
            return
        for key, value in pairs[:80]:
            queue.append(f"{key}={value}")
            queue.append(value)

    @classmethod
    def _enqueue_json_parts(cls, text: str, queue: list[str]) -> None:
        value = text.strip()
        if not value or value[0] not in "[{":
            return
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return
        for key, scalar in cls._flatten_json(parsed):
            queue.append(f"{key}={scalar}")
            queue.append(str(scalar))

    @classmethod
    def _flatten_json(cls, value: Any, prefix: str = "") -> list[tuple[str, Any]]:
        items = []
        if isinstance(value, dict):
            for key, child in value.items():
                child_key = f"{prefix}.{key}" if prefix else str(key)
                items.extend(cls._flatten_json(child, child_key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                child_key = f"{prefix}.{index}" if prefix else str(index)
                items.extend(cls._flatten_json(child, child_key))
        elif isinstance(value, (str, int, float, bool)) or value is None:
            items.append((prefix, value))
        return items

    @staticmethod
    def _enqueue_base64_parts(text: str, queue: list[str]) -> None:
        for payload in re.findall(r"data_check_string=([A-Za-z0-9+/=_-]{20,})", text):
            try:
                decoded = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                continue
            queue.append(decoded)

    # Regex patterns that indicate Telegram init data in a request body/URL
    _TELEGRAM_INITDATA_RE = re.compile(
        r"(?:tgWebAppData|initData|init_data|initdata|query_id=AA|auth_date=\d{9,}|"
        r"user=(?:%7B|\{)|signature=[\w-]{20,})",
        re.IGNORECASE,
    )

    @classmethod
    def _infer_domain(cls, reqs: list) -> str | None:
        # The real mini-app domain is identified by the intersection of two signals:
        #  1. Domains that appear as cross-origin Referer/Origin (i.e. "source" of requests)
        #  2. Domains that receive Telegram init data (tgWebAppData, initdata, auth_date…)
        # Third-party SDKs may also receive Telegram data, but they never appear
        # as Origin — so the intersection cleanly identifies the mini-app backend.
        tg_data_hosts: set[str] = set()
        origin_counts: Counter = Counter()
        recipient_hosts: set[str] = set()

        for req in reqs:
            req_host = req.get("host", "").lower()
            if req_host:
                recipient_hosts.add(req_host)

            if req["headers"].get("X-Requested-With") == "org.telegram.messenger":
                source = req["headers"].get("Origin") or req["headers"].get("Referer")

                # Build a text blob from URL + body + headers to check for Telegram data
                headers_text = "\n".join(
                    f"{k}: {v}" for k, v in req.get("headers", {}).items()
                )
                searchable = (
                    req.get("path", "")
                    + "\n"
                    + req.get("body", "")
                    + "\n"
                    + headers_text
                )
                if cls._TELEGRAM_INITDATA_RE.search(searchable):
                    if req_host and not cls._is_excluded_host(req_host):
                        tg_data_hosts.add(req_host)

                if source and (origin_host := urllib.parse.urlsplit(source).hostname):
                    if not cls._is_excluded_host(origin_host):
                        # Only count cross-origin requests to avoid inflating
                        # embedded widgets that mostly talk to themselves.
                        if origin_host.lower() != req_host:
                            origin_counts[origin_host.lower()] += 1

        if not origin_counts:
            return None

        # Helper: extract the root domain (last two labels, e.g. catizen.ai from lg1.catizen.ai)
        def root(h: str) -> str:
            parts = h.split(".")
            return ".".join(parts[-2:]) if len(parts) >= 2 else h

        def _is_infer_blocked(h: str) -> bool:
            r = root(h)
            return r in DOMAIN_INFERENCE_BLOCKLIST or h in DOMAIN_INFERENCE_BLOCKLIST

        tg_root_domains: set[str] = {
            root(h) for h in tg_data_hosts if not _is_infer_blocked(h)
        }

        # Priority 1: origin domain whose root matches a TG-data recipient's root,
        # excluding known analytics/game SDK domains from both sides.
        intersection = Counter(
            {
                h: c
                for h, c in origin_counts.items()
                if not _is_infer_blocked(h) and root(h) in tg_root_domains
            }
        )
        if intersection:
            return intersection.most_common(1)[0][0]

        # Priority 2: domain appears both as origin AND as any recipient host
        both = Counter(
            {
                h: c
                for h, c in origin_counts.items()
                if not _is_infer_blocked(h) and h in recipient_hosts
            }
        )
        if both:
            return both.most_common(1)[0][0]

        # Fallback: plain cross-origin count (still filtered)
        filtered = Counter(
            {h: c for h, c in origin_counts.items() if not _is_infer_blocked(h)}
        )
        return filtered.most_common(1)[0][0] if filtered else None

    @classmethod
    def _is_first_party(cls, host: str, domain: str | None) -> bool:
        if not domain:
            return False
        host = host.lower()
        domain = domain.lower()
        return host == domain or host.endswith("." + domain)

    @classmethod
    def _is_excluded_host(cls, host: str) -> bool:
        host = host.lower()
        if any(sub in host for sub in cls.EXCLUDED_HOST_SUBSTRINGS):
            return True
        return any(
            host == excluded or host.endswith("." + excluded)
            for excluded in cls.EXCLUDED_HOSTS
        )

    @staticmethod
    def _classify_recipient(host: str) -> str:
        host = host.lower()
        if any(
            token in host
            for token in (
                "google-analytics",
                "googletagmanager",
                "yandex",
                "clarity.ms",
                "rtmark",
            )
        ):
            return "analytics"
        if host.endswith(
            (
                "googleapis.com",
                "gstatic.com",
                "google.com",
                "firebaseio.com",
                "firebaseapp.com",
            )
        ):
            return "google_firebase"
        if any(
            token in host for token in ("adsgram", "doubleclick", "adservice", "ads.")
        ):
            return "ads"
        if any(
            token in host
            for token in (
                "unpkg.com",
                "jsdelivr",
                "cdn.",
                "static.",
                "raw.githubusercontent.com",
                "cloudflare",
            )
        ):
            return "cdn"
        if any(
            token in host
            for token in (
                "tonhub",
                "tonkeeper",
                "mytonwallet",
                "okx",
                "uxuy",
                "tomo",
                "token.im",
                "bybit",
                "bitget",
                "wallet",
            )
        ):
            return "wallet"
        if host.endswith(("telegram.org", "t.me")) or "innerworks" in host:
            return "sdk"
        if host.startswith("api.") or host.endswith(
            ("workers.dev", "vercel.app", "render.com")
        ):
            return "backend_third_party"
        return "unknown"

    @classmethod
    def _build_report(
        cls,
        findings: dict,
        source: str,
        domain: str | None,
        reqs: list,
        parse_warnings: list[str] | None = None,
        extract_stats: dict | None = None,
    ) -> dict:
        extract_stats = extract_stats or {}
        all_items = []
        for (dtype, value), info in sorted(
            findings.items(), key=lambda item: cls._finding_sort_key(item)
        ):
            hosts = info["hosts"] if isinstance(info, dict) else info
            category, sensitivity = DATA_TYPE_META.get(dtype, ("unknown", "medium"))
            evidence_count = (
                len(info.get("evidence_requests", []))
                if isinstance(info, dict)
                else len(hosts)
            )
            occurrence_count = (
                info.get("occurrence_count", evidence_count)
                if isinstance(info, dict)
                else evidence_count
            )
            item = {
                "type": dtype,
                "value": cls._display_value(value),
                "sent_to": sorted(hosts),
                "evidence_count": evidence_count,
            }
            if cls.INCLUDE_VERBOSE_ITEM_FIELDS:
                item["category"] = category
                item["recipient_class"] = (
                    sorted(info.get("recipient_classes", []))
                    if isinstance(info, dict)
                    else []
                )
                item["carrier"] = (
                    sorted(info.get("carriers", [])) if isinstance(info, dict) else []
                )
            if cls.INCLUDE_OCCURRENCE_COUNT and occurrence_count != evidence_count:
                item["occurrence_count"] = occurrence_count
            all_items.append(item)

        visible_items, omitted_items = cls._select_report_items(all_items)
        items, compacted_count = cls._compact_report_items(visible_items)
        return {
            "app": source,
            "domain": domain or "unknown",
            "exfiltrated_data": items,
        }

    @classmethod
    def _finding_sort_key(cls, item: tuple[tuple[str, str], dict]) -> tuple:
        (dtype, value), info = item
        category, sensitivity = DATA_TYPE_META.get(dtype, ("unknown", "medium"))
        sensitivity_rank = {"high": 0, "medium": 1, "low": 2}.get(sensitivity, 3)
        host_count = len(info.get("hosts", [])) if isinstance(info, dict) else 0
        evidence_count = (
            len(info.get("evidence_requests", [])) if isinstance(info, dict) else 0
        )
        return (sensitivity_rank, category, dtype, -host_count, -evidence_count, value)

    @classmethod
    def _select_report_items(cls, items: list[dict]) -> tuple[list[dict], list[dict]]:
        if cls.INCLUDE_LOW_SENSITIVITY_FINDINGS:
            return items, []
        visible = []
        omitted = []
        for item in items:
            if item.get("sensitivity") == "low":
                omitted.append(item)
            else:
                visible.append(item)
        return visible, omitted

    @classmethod
    def _compact_report_items(cls, items: list[dict]) -> tuple[list[dict], int]:
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        passthrough = []
        for item in items:
            if item.get("type") not in COMPACT_VALUE_TYPES:
                passthrough.append(item)
                continue
            key = (
                item.get("type"),
                tuple(item.get("sent_to", [])),
                item.get("sensitivity"),
            )
            grouped[key].append(item)

        compacted = []
        compacted_count = 0
        for (dtype, sent_to, sensitivity), group in sorted(grouped.items()):
            if len(group) <= COMPACT_VALUE_THRESHOLD:
                compacted.extend(group)
                continue
            evidence_count = sum(item.get("evidence_count", 0) for item in group)
            samples = [item.get("value") for item in group[:COMPACT_SAMPLE_LIMIT]]
            compacted.append(
                {
                    "type": dtype,
                    "value": f"<{len(group)} values>",
                    "sent_to": list(sent_to),
                    "sensitivity": sensitivity,
                    "evidence_count": evidence_count,
                    "value_count": len(group),
                    "sample_values": samples,
                }
            )
            compacted_count += len(group) - 1

        return (
            sorted(
                passthrough + compacted,
                key=lambda item: (
                    cls._sensitivity_rank(item.get("sensitivity", "unknown")),
                    item.get("type", ""),
                    item.get("value", ""),
                ),
            ),
            compacted_count,
        )

    @classmethod
    def _max_sensitivity(cls, left: str, right: str) -> str:
        return (
            left
            if cls._sensitivity_rank(left) <= cls._sensitivity_rank(right)
            else right
        )

    @staticmethod
    def _sensitivity_rank(value: str) -> int:
        return {"high": 0, "medium": 1, "low": 2}.get(value, 3)

    @classmethod
    def _display_value(cls, value: str) -> str:
        if cls.REDACT and len(value) > 30:
            return value[:10] + "..." + value[-6:]
        return value

    @staticmethod
    def _merge_existing_report_fields(path: Path, report: dict) -> None:
        if not path.exists():
            return
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for key, value in existing.items():
            if key not in report:
                report[key] = value


class TrafficAnalysis(TrafficDumpAnalyzer):
    """Alias consistent with the module name."""


__all__ = ["TrafficAnalysis", "TrafficDumpAnalyzer"]
