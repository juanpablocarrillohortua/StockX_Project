"""
StockX_Project/utils/copy_html.py
----------------------------------
Descarga el HTML renderizado de cada URL en docs/lista_urls.txt
y lo guarda en html_pages/<slug>.html

Estrategias anti-detección:
  - Chrome real con channel="chrome" (no Chromium descargado)
  - Persistencia de contexto (cookies/localStorage entre sesiones)
  - Movimientos de mouse aleatorios + scroll humano
  - Delays variables con distribución humana
  - Rotación de User-Agent realista
  - Una sola instancia de browser reutilizada para todas las URLs
    (evita el patrón "abrir/cerrar browser por cada request")

Uso:
    cd StockX_Project
    python utils/copy_html.py

    # Solo procesar URLs que aún no tienen HTML guardado:
    python utils/copy_html.py --skip-existing

    # Límite de URLs (útil para pruebas):
    python utils/copy_html.py --limit 5
"""

import asyncio
import random
import argparse
import re
import sys
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright, Page, BrowserContext

from config import settings

# ─────────────────────────────────────────────────────────────────────────────
# PATHS (relative to the project root)
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent  # StockX_Project/
URLS_FILE = ROOT / "docs" / settings.URL_LIST_NAME
HTML_FOLDER = ROOT / "html_pages"
# Contexto persistente: guarda cookies y localStorage entre ejecuciones
CONTEXT_DIR = ROOT / ".browser_context"

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
# Inter-page delay: min and max thresholds in seconds (uniform distribution)
DELAY_MIN = 4.0
DELAY_MAX = 9.0

NAV_TIMEOUT = 90_000

MAX_RETRIES = 2

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",  # noqa: E501
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",  # noqa: E501
]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────


def slug_from_url(url: str) -> str:
    """
    'https://stockx.com/air-jordan-1-retro-low-dior?size=10'
     → 'air-jordan-1-retro-low-dior'
    """
    url = url.strip().split("?")[0].split("#")[0].rstrip("/")
    return url.rsplit("/", 1)[-1]


def load_urls(
        path: Path,
        skip_existing: bool,
        limit: int | None) -> list[tuple[str, Path]]:
    """
    Parses lista_urls.txt and yields filtered.

    [(url, html_path), ...] mappings.
    """
    if not path.exists():
        print(f"❌ URL manifest not found at: {path}")
        sys.exit(1)

    lines = [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        ]
    urls = [line for line in lines if line and not line.startswith("#")]

    tasks: list[tuple[str, Path]] = []
    for url in urls:
        slug = slug_from_url(url)
        html_path = HTML_FOLDER / f"{slug}.html"

        if skip_existing and html_path.exists():
            print(f"Artifact already exists, skipping: {slug}")
            continue

        tasks.append((url, html_path))

    if limit:
        tasks = tasks[:limit]

    return tasks


async def human_delay(
        min_s: float = DELAY_MIN,
        max_s: float = DELAY_MAX
        ) -> None:
    """Executes a randomized pause duration to match human-like browsing patterns."""  # noqa: E501
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_scroll(page: Page) -> None:
    """Smooth, randomized downward scrolling and a slight upward adjustment."""
    total = random.randint(600, 1800)
    step = random.randint(80, 200)
    for _ in range(total // step):
        await page.mouse.wheel(0, step)
        await asyncio.sleep(random.uniform(0.05, 0.18))
    await asyncio.sleep(random.uniform(0.3, 0.8))
    await page.mouse.wheel(0, -random.randint(100, 300))


async def human_mouse_move(page: Page) -> None:
    """Simulates curved mouse trajectories to mimic human interaction."""
    for _ in range(random.randint(2, 5)):
        x = random.randint(200, 1400)
        y = random.randint(150, 700)
        await page.mouse.move(x, y, steps=random.randint(8, 20))
        await asyncio.sleep(random.uniform(0.1, 0.4))


# ─────────────────────────────────────────────────────────────────────────────
# ANTI-DETECTION: script injected prior to loading each page
# ─────────────────────────────────────────────────────────────────────────────
EVASION_SCRIPT = """
() => {
    // Ocultar la firma de WebDriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // Fingir plugins reales (browser vacío es sospechoso)
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    // Idiomas reales
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });

    // chrome.runtime debe existir en Chrome real
    if (!window.chrome) {
        window.chrome = { runtime: {} };
    }

    // Eliminar propiedades que delatan Playwright/CDP
    const toDelete = ['__playwright', '__pw_manual', '__PW_inspect'];
    toDelete.forEach(k => { try { delete window[k]; } catch(_) {} });
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE URL INGESTION AND DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────
async def download_page(
    context: BrowserContext,
    url: str,
    html_path: Path,
    idx: int,
    total: int,
) -> bool:
    """
    Spawns a new tab, routes to `url`, and serializes the DOM to `html_path`.

    Returns True if the routine completes successfully.
    """
    slug = slug_from_url(url)
    print(f"\n[{idx}/{total}] {slug}")

    for attempt in range(1, MAX_RETRIES + 2):
        page = await context.new_page()
        try:

            await page.add_init_script(EVASION_SCRIPT)

            print(f"  → Navigating… (attempt {attempt})")
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT
                )

            try:
                await page.wait_for_selector(
                    "script#__NEXT_DATA__",
                    timeout=20_000,
                    state="attached",
                )
            except Exception:
                pass

            await human_mouse_move(page)
            await human_scroll(page)
            await human_delay(1.5, 3.0)

            html = await page.content()

            if len(html) < 5_000:
                raise ValueError(
                    f"Payload anomaly: HTML too short ({len(html)} bytes) — potential block detected"  # noqa: E501
                    )

            if "__NEXT_DATA__" not in html and "ProductDetails" not in html:
                print(f"  ⚠  DOM serialized but missing product payloads (potential CAPTCHA interdiction)")  # noqa: E501
                html_path.write_text(html, encoding="utf-8")
                return False

            html_path.write_text(html, encoding="utf-8")
            print(f"  ✓ Saved → {html_path.name}  ({len(html):,} bytes)")
            return True

        except Exception as e:
            print(f"  ✗ Exception caught on attempt {attempt}: {e}")
            if attempt <= MAX_RETRIES:
                wait = random.uniform(8, 15)
                print(f"  ⏳ Retrying execution in {wait:.1f}s…")
                await asyncio.sleep(wait)
        finally:
            await page.close()

    print(f"  ❌ Terminal failure after {MAX_RETRIES + 1} attempts: {slug}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
async def main(skip_existing: bool, limit: int | None) -> None:
    HTML_FOLDER.mkdir(parents=True, exist_ok=True)
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    tasks = load_urls(URLS_FILE, skip_existing, limit)
    if not tasks:
        print("✅ No payloads to process.")
        return

    print(f"\nStockX HTML Downloader")
    print(f"  URLs a procesar : {len(tasks)}")
    print(f"  HTML folder     : {HTML_FOLDER}")
    print(f"  Contexto        : {CONTEXT_DIR}\n")

    async with async_playwright() as p:
        print("🚀 Launching Chrome…")
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions-except=",
            ],
            ignore_default_args=["--enable-automation"],
        )

        # Persistent context reuses cookies from prior sessions
        context = await browser.new_context(
            storage_state=str(CONTEXT_DIR / "state.json") if (CONTEXT_DIR / "state.json").exists() else None,  # noqa: E501
            viewport=None,
            user_agent=random.choice(USER_AGENTS),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",  # noqa: E501
            },
        )

        ok_count = 0
        fail_count = 0
        start_time = datetime.now()

        for i, (url, html_path) in enumerate(tasks, 1):
            success = await download_page(
                context,
                url,
                html_path,
                i, len(tasks)
                )
            if success:
                ok_count += 1
            else:
                fail_count += 1

            if success:
                await context.storage_state(
                    path=str(CONTEXT_DIR / "state.json")
                    )

            if i < len(tasks):
                wait = random.uniform(DELAY_MIN, DELAY_MAX)
                print(f"  ⏳ Throttling execution: waiting {wait:.1f}s before next URL dispatch…")  # noqa: E501
                await asyncio.sleep(wait)

        await browser.close()

        elapsed = (datetime.now() - start_time).seconds
        print(f"\n{'─'*50}")
        print(f"✅ Completed in {elapsed // 60}m {elapsed % 60}s")
        print(f"   Success count : {ok_count}")
        print(f"   Failure count : {fail_count}")
        print(f"   HTMLS saved in: {HTML_FOLDER}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StockX HTML Downloader")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Bypasses URLs whose target .html payload already exists within html_pages/",  # noqa: E501
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit execution queue to the first N URLs (optimized for debugging/dry runs)",  # noqa: E501
    )
    args = parser.parse_args()

    asyncio.run(main(skip_existing=args.skip_existing, limit=args.limit))
