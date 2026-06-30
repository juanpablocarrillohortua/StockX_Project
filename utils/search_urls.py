"""
StockX_Project/utils/search_urls.py
-------------------------------------
Extracts product URLs from a StockX category page.

PROBLEMS in the original that this script fixes:
  1. wait_until="networkidle" → StockX never settles into a quiet network state
     (analytics, websockets, etc.), causing a TimeoutError. It's replaced by
     waiting for a specific selector that confirms the products have loaded.

  2. Only captured the visible products without scrolling → the page uses
     infinite scroll and loads more products as you scroll down. This script
     does progressive scrolling until no new content appears.

Usage:
    cd StockX_Project
    python utils/search_urls.py --url https://stockx.com/brands/...
    python utils/search_urls.py --url https://stockx.com/. --archivo jordan.txt
    python utils/search_urls.py --url https://stockx.com/... --max-scrolls 20
"""

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from config import settings


DEFAULT_OUTPUT = str(Path(__file__).resolve().parent.parent / "docs" / settings.URL_LIST_NAME)  # noqa: E501


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Selector that confirms the product cards are already in the DOM
PRODUCT_CARD_SELECTOR = "a[href*='/'][data-testid], a.css-w6gm5p, div[data-component='ProductTile'] a, a[href^='https://stockx.com/']:not([href*='#'])"  # noqa: E501

# Pages/sections that are NOT individual products
URL_EXCLUSIONS = {
    "/about", "/help", "/faq", "/login", "/signup", "/sell", "/buy",
    "/terms", "/privacy", "/news", "/sneakers", "/streetwear", "/watches",
    "/bags", "/collectibles", "/app", "/search", "/brands", "/category",
    "javascript:", "google.com", "support.stockx", "stockx.com/#",
}

# Wait time between scrolls (ms) — higher = safer against blocking
SCROLL_PAUSE_MS = 2000

# Default maximum number of scrolls (each scroll ≈ one screen of products)
DEFAULT_MAX_SCROLLS = 15


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def is_product_url(url: str) -> bool:
    """
    Returns True if the URL appears to be an individual StockX product.
    Expected structure: https://stockx.com/<slug>  (exactly 4)
    """
    url_base = url.split("?")[0].rstrip("/")
    parts = url_base.split("/")

    if "stockx.com" not in url_base:
        return False
    if len(parts) != 4:          # ['https:', '', 'stockx.com', 'slug']
        return False
    if len(parts[3]) < 4:        # very short slugs aren't products
        return False
    if any(exc in url_base for exc in URL_EXCLUSIONS):
        return False

    return True


async def scroll_to_bottom(page, max_scrolls: int) -> int:
    """
    Performs progressive scrolling until the end of the page.
    Returns the number of scrolls performed.
    """
    prev_height = 0
    scrolls = 0

    for i in range(max_scrolls):
        # Scroll to the end of the document
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)

        current_height = await page.evaluate("document.body.scrollHeight")
        scrolls = i + 1

        if current_height == prev_height:
            print(f"  → No new content after scroll {scrolls} — end of page.")
            break

        prev_height = current_height
        productos_actuales = await page.evaluate(
            "document.querySelectorAll('a[href]').length"
        )
        print(f"  → Scroll {scrolls}/{max_scrolls} | links in DOM: {productos_actuales}")  # noqa: E501

    return scrolls


async def extraer_urls_desde_catalogo(
    url_seccion: str,
    archivo_salida: str,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
) -> None:
    async with async_playwright() as p:
        print("🔄 Opening browser...")
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
        )

        context = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        # Anti-bot evasion
        await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3]
                    });
                    window.chrome = { runtime: {} };
                """)

        page = await context.new_page()

        # ── Navigation ───────────────────────────────────────────────────────
        # "domcontentloaded" is enough and never times out on StockX.
        print(f"🌐 Navigating to: {url_seccion}")
        try:
            await page.goto(
                url_seccion,
                wait_until="domcontentloaded",
                timeout=60000
                )
        except PWTimeout:
            print("⚠  Timeout on goto — the page partially loaded, continuing...")  # noqa: E501

        # Wait until at least one product link appears in the DOM
        print("⏳ Waiting for products to load...")
        try:
            await page.wait_for_selector(
                "a[href*='stockx.com/'],"   # any StockX link
                "div[class*='ProductTile'],"
                "div[data-component*='Tile'],"
                "div[class*='product-tile']",
                timeout=20000,
                state="attached",
            )
        except PWTimeout:
            print("⚠  No product cards detected — could be a captcha.")
            print("   Check the Chrome window and solve the captcha if it appears.")  # noqa: E501
            # Give time for manual resolution
            await page.wait_for_timeout(15000)

        # Small initial pause for loading animations to finish
        await page.wait_for_timeout(2500)

        # ── Infinite scroll ──────────────────────────────────────────────────
        print(f"\n📜 Starting scroll (maximum {max_scrolls} passes)...")
        total_scrolls = await scroll_to_bottom(page, max_scrolls)
        print(f"   Scroll completed ({total_scrolls} passes)\n")

        # ── URL extraction ────────────────────────────────────────────────
        todos_los_enlaces: list[str] = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]'))
                       .map(a => a.href)
        """)

        urls_limpias: set[str] = set()
        for url in todos_los_enlaces:
            url_base = url.split("?")[0].rstrip("/")
            if is_product_url(url_base):
                urls_limpias.add(url_base)

        await browser.close()

        # ── Save result ─────────────────────────────────────────────────
        if not urls_limpias:
            print("❌ No product URLs found.")
            print("   Possible causes:")
            print("   · StockX changed its HTML structure")
            print("   · The page required login or a captcha")
            print("   · The category URL doesn't contain direct products")
            sys.exit(1)

        output_path = Path(archivo_salida)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # If the file already exists, merge with previous URLs
        existing: set[str] = set()
        if output_path.exists():
            existing = {
                line.strip() for line in output_path.read_text(encoding="utf-8").splitlines()  # noqa: E501
                if line.strip() and not line.startswith("#")
            }
            if existing:
                print(f"📂 Existing file with {len(existing)} URLs — merging...")  # noqa: E501

        merged = existing | urls_limpias
        nuevas = urls_limpias - existing

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# Generated by search_urls.py — {url_seccion}\n")
            for url in sorted(merged):
                f.write(f"{url}\n")

        print(f"✅ Scan completed:")
        print(f"   New URLs found      : {len(nuevas)}")
        print(f"   Total in file       : {len(merged)}")
        print(f"   Saved to            : {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Product URL extractor from StockX catalog",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python utils/search_urls.py --url https://stockx.com/brands/nike?category=sneakers  # noqa: E501
  python utils/search_urls.py --url https://stockx.com/brands/jordan --max-scrolls 30  # noqa: E501
  python utils/search_urls.py --url https://stockx.com/brands/adidas --archivo docs/adidas.txt  # noqa: E501
        """,
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://stockx.com/brands/jordan",
        help="StockX category or brand URL",
    )
    parser.add_argument(
        "--archivo",
        type=str,
        default=DEFAULT_OUTPUT,
        help="Path to the output TXT file",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=DEFAULT_MAX_SCROLLS,
        metavar="N",
        help=f"Maximum number of scrolls before stopping (default: {DEFAULT_MAX_SCROLLS})",  # noqa: E501
    )

    args = parser.parse_args()

    asyncio.run(
        extraer_urls_desde_catalogo(
            url_seccion=args.url,
            archivo_salida=args.archivo,
            max_scrolls=args.max_scrolls,
        )
    )
