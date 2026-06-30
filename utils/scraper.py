"""
StockX_Project/utils/scraper.py
--------------------------------
Orchestrates two data sources and merges them into a single JSON:

  SOURCE 1 — Local HTML (StockX_Project/html_pages/<slug>.html)
    Parses the __NEXT_DATA__ block (Next.js SSR) to extract:
    title, brand, traits (Style, Colorway, Retail Price, Release Date),
    description.
    Fallback: scrapes <section data-component="ProductDetails">
    if __NEXT_DATA__ fails.

  SOURCE 2 — GraphQL API (fetchSalesGraph)
    Retrieves the sneaker's sale price time series.
    Uses curl_cffi with impersonate="chrome" to pass StockX's TLS filter.

SLUGS: automatically derived from StockX_Project/docs/lista_urls.txt.
       Only slugs that have their .html already downloaded in html_pages/
       are processed.

OUTPUT: StockX_Project/data/sneakers_data.json

ANTI-BLOCKING (API):
  - Real Chrome TLS fingerprint via curl_cffi
  - Random delay between requests with Gaussian distribution
  - Detection of expired JWT token with clear warning
  - Rotated headers (device-id, session-id) per slug
  - Retries with exponential backoff on 429/5xx errors

USAGE:
    cd StockX_Project
    python utils/scraper.py

    # Only slugs without prior data in the JSON:
    python utils/scraper.py --skip-existing

    # Test with the first N URLs:
    python utils/scraper.py --limit 5

    # Token from environment variable (avoids hardcoding it):
    STOCKX_TOKEN="Bearer eyJ..." python utils/scraper.py
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cfl_requests

from config import settings
# ─────────────────────────────────────────────────────────────────────────────
# PATHS  (always relative to the project root, regardless of CWD)
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent   # StockX_Project/
URLS_FILE = ROOT / "docs" / settings.URL_LIST_NAME
HTML_FOLDER = ROOT / "html_pages"
DATA_DIR = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "sneakers_data.json"

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  —  edit here or use environment variables
# ─────────────────────────────────────────────────────────────────────────────
# Priority: env STOCKX_TOKEN > hardcoded value below
AUTHORIZATION: str = os.environ.get(
    "STOCKX_TOKEN",
    settings.STOCKX_TOKEN,
)

END_DATE = "2026-06-12"
CURRENCY = "USD"
INTERVALS = 100

DELAY_MEAN = 3.5   # seconds
DELAY_SIGMA = 1.2
DELAY_MIN = 1.8
DELAY_MAX = 8.0

MAX_RETRIES = 3          # retries per slug on the API
GQL_URL = "https://stockx.com/api/graphql"

# ─────────────────────────────────────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════════
# GENERAL UTILITIES
# ═════════════════════════════════════════════════════════════════════════════

def slug_from_url(url: str) -> str:
    """'https://stockx.com/nike-dunk-low?size=9' → 'nike-dunk-low'"""
    return url.strip().split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]  # noqa: E501


def random_delay() -> float:
    """Gaussian-distributed delay to appear more human-like."""
    d = random.gauss(DELAY_MEAN, DELAY_SIGMA)
    return max(DELAY_MIN, min(DELAY_MAX, d))


def _dig(obj, *keys):
    """Navigates a nested dict/list without raising exceptions."""
    for k in keys:
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int) and k < len(obj):
            obj = obj[k]
        else:
            return None
    return obj


def decode_jwt_exp(token: str) -> datetime | None:
    """Extracts the expiration date from the JWT without validating the signature."""  # noqa: E501
    try:
        payload_b64 = token.removeprefix("Bearer ").split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        return datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    except Exception:
        return None


def check_token(token: str) -> None:
    """Warns if the token is expired or about to expire (< 30 min)."""
    exp = decode_jwt_exp(token)
    if exp is None:
        print("⚠  Could not read the JWT token's expiration.")
        return
    now = datetime.now(tz=timezone.utc)
    diff = (exp - now).total_seconds()
    if diff <= 0:
        print(f"❌  EXPIRED TOKEN {abs(diff/60):.0f} minutes ago.")
        print("    Reload the StockX page in the browser, capture a new")
        print("    GraphQL request in Network and copy the Authorization header.")  # noqa: E501
        sys.exit(1)
    elif diff < 1800:
        print(f"⚠  TOKEN expires in {diff/60:.0f} minutes — consider renewing it soon.")  # noqa: E501
    else:
        print(f"✓  Token valid for {diff/3600:.1f} hours (expires {exp.strftime('%H:%M UTC')})")  # noqa: E501


# ═════════════════════════════════════════════════════════════════════════════
# SOURCE 1 — LOCAL HTML PARSING
# ═════════════════════════════════════════════════════════════════════════════

def parse_local_html(html_path: Path) -> dict:
    """
    Extracts product data from the locally saved HTML.

    Strategy 1 (primary): script#__NEXT_DATA__
      Next.js injects all the product JSON into the initial HTML (SSR).
      This is the most complete and structured source.

    Strategy 2 (fallback): <section data-component="ProductDetails">
      In case __NEXT_DATA__ doesn't have the expected fields in some version.
    """
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    result: dict = {
        "title":       None,
        "brand":       None,
        "traits":      {},
        "description": None,
        "source":      "not_found",
    }

    # ── Strategy 1: __NEXT_DATA__ ──────────────────────────────────────────
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if script_tag and script_tag.string:
        try:
            nd = json.loads(script_tag.string)

            product = (
                _dig(nd, "props", "pageProps", "productTemplate")
                or _dig(nd, "props", "pageProps", "product")
                or _dig(nd, "props", "pageProps", "initialData", "product")
                or _dig(nd, "props", "pageProps", "serverData", "product")
                or _dig(nd, "props", "pageProps", "browse", "results", 0)
            )

            if product:
                result["title"] = product.get("title") or product.get("name")
                result["brand"] = product.get("brand")
                result["description"] = product.get("description")
                result["source"] = "next_data"

                # traits as a list [{name, value}, ...]
                for t in (product.get("traits") or []):
                    key = re.sub(r"[\s\-/]+", "_", t.get("name", "")).lower().strip("_")  # noqa: E501
                    result["traits"][key] = t.get("value")

                # Loose fields at the root of the product object
                FLAT_MAP = {
                    "styleId":     "style",
                    "sku":         "style",
                    "colorway":    "colorway",
                    "retailPrice": "retail_price",
                    "releaseDate": "release_date",
                    "gender":      "gender",
                    "category":    "category",
                    "shoe":        "model",
                }
                for src_key, dst_key in FLAT_MAP.items():
                    val = (
                        product.get(src_key)
                        or _dig(product, "productAttributes", src_key)
                        or _dig(product, "market", src_key)
                    )
                    if val and dst_key not in result["traits"]:
                        result["traits"][dst_key] = str(val)

                if result["traits"] or result["description"]:
                    return result

        except (json.JSONDecodeError, Exception) as e:
            print(f"     ⚠  __NEXT_DATA__ parse error: {e}")

    # ── Strategy 2: HTML section ───────────────────────────────────────────
    section = soup.find("section", {"data-component": "ProductDetails"})
    if section:
        for div in section.find_all("div", {"data-component": "product-trait"}):  # noqa: E501
            label = div.find("span")
            value = div.find("p")
            if label and value:
                key = re.sub(r"\s+", "_", label.get_text(strip=True)).lower()
                result["traits"][key] = value.get_text(strip=True)

        desc_div = section.find(
            "div",
            {"data-component": "ProductDescription"}
            )
        if desc_div:
            ps = desc_div.find_all("p")
            result["description"] = "\n\n".join(
                p.get_text(separator="\n", strip=True) for p in ps
            )

        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)

        result["source"] = "html_section"

    return result


# ═════════════════════════════════════════════════════════════════════════════
# SOURCE 2 — GRAPHQL API  (time series)
# ═════════════════════════════════════════════════════════════════════════════

# Shared session: curl_cffi keeps the HTTP/2 connection open
_SESSION = cfl_requests.Session()


def _gql_headers(slug: str) -> dict:
    """
    Headers per request.
    device-id and session-id are rotated per slug to avoid fixed patterns
    that anti-bot systems could easily detect.
    """
    return {
        "accept":                      "application/json",
        "accept-language":             "en-US,en;q=0.9",
        "apollographql-client-name":   "Iron",
        "apollographql-client-version": "2026.06.07.03",
        "app-platform":                "Iron",
        "app-version":                 "2026.06.07.03",
        "authorization":               AUTHORIZATION,
        "content-type":                "application/json",
        "origin":                      "https://stockx.com",
        "referer":                     f"https://stockx.com/{slug}",
        "selected-country":            "CO",
        "x-operation-name":            "fetchSalesGraph",
        "x-stockx-device-id":          str(uuid.uuid4()),   # rotated
        "x-stockx-session-id":         str(uuid.uuid4()),   # rotated
    }


def fetch_sales_series(slug: str) -> list[dict] | str:
    """
    Calls fetchSalesGraph with retries and exponential backoff.
    Returns the list of points [{xValue, yValue}] or an error string.
    """
    payload = {
        "operationName": "fetchSalesGraph",
        "variables": {
            "productId": slug,
            "startDate": "all",
            "endDate":   END_DATE,
            "intervals": INTERVALS,
            "currencyCode": CURRENCY,
            "isVariant": False,
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "2bdd0a3f11bfaa85eb8014cf8be8ccdee8839ad4d284eba08e6031b239e12a72",  # noqa: E501
            }
        },
    }

    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = _SESSION.post(
                GQL_URL,
                headers=_gql_headers(slug),
                json=payload,
                impersonate="chrome",
                timeout=30,
            )

            if resp.status_code == 200:
                data = resp.json()
                series = _dig(data, "data", "product", "salesChart", "series")
                if series is not None:
                    return series
                # 200 but unexpected structure
                return f"Unexpected JSON: {json.dumps(data)[:200]}"

            elif resp.status_code in (429, 503, 502):
                # Rate-limited or server down → backoff
                wait = (2 ** attempt) + random.uniform(1, 3)
                print(f"     ⚠  HTTP {resp.status_code} — retry {attempt}/{MAX_RETRIES} in {wait:.1f}s")  # noqa: E501
                time.sleep(wait)
                last_error = f"HTTP {resp.status_code}"
                continue

            elif resp.status_code == 401:
                return "Error 401: expired token — renew AUTHORIZATION"

            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break

        except Exception as e:
            last_error = f"Exception: {e}"
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return f"Error after {MAX_RETRIES} attempts: {last_error}"


# ═════════════════════════════════════════════════════════════════════════════
# SLUG RESOLUTION FROM lista_urls.txt
# ═════════════════════════════════════════════════════════════════════════════

def load_slugs_from_urls(limit: int | None = None) -> list[str]:
    """
    Reads lista_urls.txt and returns the slugs whose .html already exists.

    URLs without downloaded HTML are listed as a warning but not processed.
    """
    if not URLS_FILE.exists():
        print(f"❌  Not found: {URLS_FILE}")
        sys.exit(1)

    lines = URLS_FILE.read_text(encoding="utf-8").splitlines()
    urls = [
        line.strip() for line in lines if line.strip() and not line.startswith("#")  # noqa: E501
        ]

    ready, missing = [], []
    for url in urls:
        slug = slug_from_url(url)
        if (HTML_FOLDER / f"{slug}.html").exists():
            ready.append(slug)
        else:
            missing.append(slug)

    if missing:
        print(f"\n⚠  {len(missing)} URLs without downloaded HTML (run copy_html.py first):")  # noqa: E501
        for s in missing[:10]:
            print(f"   · {s}")
        if len(missing) > 10:
            print(f"   … and {len(missing)-10} more")

    if limit:
        ready = ready[:limit]

    return ready


# ═════════════════════════════════════════════════════════════════════════════
# DATA MERGING AND JSON WRITING
# ═════════════════════════════════════════════════════════════════════════════

def load_existing_data() -> dict[str, dict]:
    """Loads the existing JSON (if any) as {slug: record}."""
    if OUTPUT_FILE.exists():
        try:
            records = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            return {r["slug"]: r for r in records if "slug" in r}
        except Exception:
            pass
    return {}


def scrape(slugs: list[str], skip_existing: bool) -> None:
    existing = load_existing_data()

    # Filter out already-processed slugs if --skip-existing
    if skip_existing:
        before = len(slugs)
        slugs = [s for s in slugs if s not in existing]
        print(f"⏭  --skip-existing: skipping {before - len(slugs)} slugs already in the JSON")  # noqa: E501

    if not slugs:
        print("✅ Nothing new to process.")
        return

    print(f"\nProcessing {len(slugs)} sneaker(s)…")
    print(f"{'─'*60}")

    results: dict[str, dict] = {**existing}   # start with previous data

    ok_count = fail_count = 0

    for i, slug in enumerate(slugs, 1):
        print(f"\n[{i}/{len(slugs)}] {slug}")

        record: dict = {
            "slug":            slug,
            "url":             f"https://stockx.com/{slug}",
            "scraped_at":      datetime.now(timezone.utc).isoformat(),
            "product_details": {},
            "sales_series":    [],
            "errors":          [],
        }

        # ── SOURCE 1: details from local HTML ──────────────────────────────
        html_path = HTML_FOLDER / f"{slug}.html"
        print("  → Product details (local HTML)…")
        try:
            details = parse_local_html(html_path)
            record["product_details"] = details

            src = details.get("source", "?")
            n_traits = len(details.get("traits", {}))

            if src == "not_found":
                msg = "HTML exists but contains no recognizable data"
                record["errors"].append(f"product_details: {msg}")
                print(f"     ⚠  {msg}")
            else:
                print(f"     ✓  {n_traits} traits — source: {src}")
                if details.get("title"):
                    print(f"     📦 {details['title']}")

        except Exception as e:
            record["errors"].append(f"product_details: {e}")
            print(f"     ✗  {e}")

        # ── SOURCE 2: time series via API ─────────────────────────────────
        print("  → Time series (GraphQL)…")
        try:
            series = fetch_sales_series(slug)
            if isinstance(series, list):
                record["sales_series"] = series
                print(f"     ✓  {len(series)} data points")

                # Quick stats for validation
                if series:
                    prices = [p["yValue"] for p in series if p.get("yValue")]
                    if prices:
                        print(f"     📈 Range: ${min(prices)} – ${max(prices)} USD")  # noqa: E501
            else:
                record["errors"].append(f"sales_series: {series}")
                print(f"     ✗  {series}")

        except Exception as e:
            record["errors"].append(f"sales_series: {e}")
            print(f"     ✗  {e}")

        # ── MERGE: add trait fields at the root level for easy access ──
        # Duplicates the most useful fields outside the nested dict to make
        # later analysis easier (pandas, Excel, etc.)
        traits = record["product_details"].get("traits", {})
        record["_meta"] = {
            "style":        traits.get("style"),
            "colorway":     traits.get("colorway"),
            "retail_price": traits.get("retail_price"),
            "release_date": traits.get("release_date"),
            "brand":        record["product_details"].get("brand"),
            "title":        record["product_details"].get("title"),
            "series_count": len(record["sales_series"]),
            "has_errors":   bool(record["errors"]),
        }

        # ── Store in the results dict ─────────────────────────────────
        results[slug] = record

        if record["errors"]:
            fail_count += 1
        else:
            ok_count += 1

        # ── Persist after each slug (don't lose work if it crashes) ─────
        _write_output(list(results.values()))

        # ── Anti-blocking delay between slugs ───────────────────────────────
        if i < len(slugs):
            wait = random_delay()
            print(f"  ⏳ Waiting {wait:.1f}s…")
            time.sleep(wait)

    # ── Final summary ────────────────────────────────────────────────────────
    total = ok_count + fail_count
    print(f"\n{'═'*60}")
    print(f"✅ Completed: {ok_count}/{total} without errors")
    print(f"   Saved to: {OUTPUT_FILE}")
    print(f"{'═'*60}")

    for slug, rec in results.items():
        m = rec.get("_meta", {})
        ts = m.get("series_count", 0)
        title = (m.get("title") or slug)[:50]
        errs = len(rec.get("errors", []))
        icon = "✓" if not errs else "⚠"
        print(f"  {icon} {title}")
        print(f"    style={m.get('style')} | retail={m.get('retail_price')} "
              f"| release={m.get('release_date')} | series={ts} pts")


def _write_output(records: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="StockX Scraper — merges local HTML + GraphQL API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip slugs that are already in sneakers_data.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process only the first N URLs (useful for testing)",
    )
    args = parser.parse_args()

    print("StockX Scraper")
    print(f"  URLs file  : {URLS_FILE}")
    print(f"  HTML folder: {HTML_FOLDER}")
    print(f"  Output     : {OUTPUT_FILE}\n")

    # Verify token before starting
    check_token(AUTHORIZATION)

    slugs = load_slugs_from_urls(limit=args.limit)
    if not slugs:
        print("\n❌ No slugs ready to process.")
        print("   Make sure you've downloaded the HTML with: python utils/copy_html.py")  # noqa: E501
        sys.exit(1)

    print(f"\n📋 {len(slugs)} sneaker(s) ready to process")
    scrape(slugs, skip_existing=args.skip_existing)
    with open(URLS_FILE, "w") as archivo:
        pass


if __name__ == "__main__":
    main()
