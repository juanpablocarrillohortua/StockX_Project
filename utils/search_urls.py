"""
StockX_Project/utils/search_urls.py
-------------------------------------
Extrae URLs de productos desde una página de categoría de StockX.

PROBLEMAS del original que corrige este script:
  1. wait_until="networkidle" → StockX nunca queda en red quieta (analytics,
     websockets, etc.), causando TimeoutError. Se reemplaza por esperar un
     selector concreto que confirme que los productos ya cargaron.

  2. Solo capturaba los productos visibles sin hacer scroll → la página usa
     scroll infinito y carga más productos al bajar. Este script hace scroll
     progresivo hasta que deja de aparecer contenido nuevo.

Uso:
    cd StockX_Project
    python utils/search_urls.py --url https://stockx.com/brands/nike?category=sneakers
    python utils/search_urls.py --url https://stockx.com/brands/jordan --archivo jordan.txt
    python utils/search_urls.py --url https://stockx.com/brands/nike --max-scrolls 20
"""

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from config import settings


DEFAULT_OUTPUT = str(Path(__file__).resolve().parent.parent / "docs" / settings.URL_LIST_NAME)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

# Selector que confirma que las tarjetas de producto ya están en el DOM
PRODUCT_CARD_SELECTOR = "a[href*='/'][data-testid], a.css-w6gm5p, div[data-component='ProductTile'] a, a[href^='https://stockx.com/']:not([href*='#'])"

# Páginas/secciones que NO son productos individuales
URL_EXCLUSIONS = {
    "/about", "/help", "/faq", "/login", "/signup", "/sell", "/buy",
    "/terms", "/privacy", "/news", "/sneakers", "/streetwear", "/watches",
    "/bags", "/collectibles", "/app", "/search", "/brands", "/category",
    "javascript:", "google.com", "support.stockx", "stockx.com/#",
}

# Tiempo de espera entre scrolls (ms) — más alto = más seguro contra bloqueos
SCROLL_PAUSE_MS = 2000

# Máximo de scrolls por defecto (cada scroll ≈ una pantalla de productos)
DEFAULT_MAX_SCROLLS = 15


# ─────────────────────────────────────────────────────────────────────────────
# LÓGICA PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def is_product_url(url: str) -> bool:
    """
    Devuelve True si la URL parece ser un producto individual de StockX.
    Estructura esperada: https://stockx.com/<slug>  (exactamente 4 partes al dividir por /)
    """
    url_base = url.split("?")[0].rstrip("/")
    parts    = url_base.split("/")

    if "stockx.com" not in url_base:
        return False
    if len(parts) != 4:          # ['https:', '', 'stockx.com', 'slug']
        return False
    if len(parts[3]) < 4:        # slugs muy cortos no son productos
        return False
    if any(exc in url_base for exc in URL_EXCLUSIONS):
        return False

    return True


async def scroll_to_bottom(page, max_scrolls: int) -> int:
    """
    Hace scroll progresivo hasta el final de la página o hasta max_scrolls.
    Devuelve el número de scrolls realizados.
    """
    prev_height = 0
    scrolls     = 0

    for i in range(max_scrolls):
        # Scroll hasta el final del documento
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)

        current_height = await page.evaluate("document.body.scrollHeight")
        scrolls = i + 1

        if current_height == prev_height:
            print(f"  → Sin contenido nuevo tras scroll {scrolls} — fin de página.")
            break

        prev_height = current_height
        productos_actuales = await page.evaluate(
            "document.querySelectorAll('a[href]').length"
        )
        print(f"  → Scroll {scrolls}/{max_scrolls} | links en DOM: {productos_actuales}")

    return scrolls


async def extraer_urls_desde_catalogo(
    url_seccion: str,
    archivo_salida: str,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
) -> None:
    async with async_playwright() as p:
        print("🔄 Abriendo navegador...")
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

        # Evasión anti-bot
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # ── Navegación ────────────────────────────────────────────────────────
        # "domcontentloaded" es suficiente y nunca hace timeout en StockX.
        # Luego esperamos un selector concreto que confirme que hay productos.
        print(f"🌐 Navegando a: {url_seccion}")
        try:
            await page.goto(url_seccion, wait_until="domcontentloaded", timeout=60000)
        except PWTimeout:
            print("⚠  Timeout en goto — la página cargó parcialmente, continuando...")

        # Esperar a que aparezca al menos un link de producto en el DOM
        print("⏳ Esperando que carguen los productos...")
        try:
            # Intentamos varios selectores que StockX ha usado en distintas versiones
            await page.wait_for_selector(
                "a[href*='stockx.com/'],"   # cualquier link de StockX
                "div[class*='ProductTile'],"
                "div[data-component*='Tile'],"
                "div[class*='product-tile']",
                timeout=20000,
                state="attached",
            )
        except PWTimeout:
            print("⚠  No se detectaron tarjetas de producto — puede ser un captcha.")
            print("   Comprueba la ventana de Chrome y resuelve el captcha si aparece.")
            # Damos tiempo para resolución manual
            await page.wait_for_timeout(15000)

        # Pequeña pausa inicial para que terminen las animaciones de carga
        await page.wait_for_timeout(2500)

        # ── Scroll infinito ───────────────────────────────────────────────────
        print(f"\n📜 Iniciando scroll (máximo {max_scrolls} vueltas)...")
        total_scrolls = await scroll_to_bottom(page, max_scrolls)
        print(f"   Scroll completado ({total_scrolls} vueltas)\n")

        # ── Extracción de URLs ────────────────────────────────────────────────
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

        # ── Guardar resultado ─────────────────────────────────────────────────
        if not urls_limpias:
            print("❌ No se encontraron URLs de productos.")
            print("   Posibles causas:")
            print("   · StockX cambió su estructura HTML")
            print("   · La página requería login o captcha")
            print("   · La URL de categoría no contiene productos directos")
            sys.exit(1)

        output_path = Path(archivo_salida)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Si ya existe el archivo, hacer merge con URLs previas
        existing: set[str] = set()
        if output_path.exists():
            existing = {
                l.strip() for l in output_path.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.startswith("#")
            }
            if existing:
                print(f"📂 Archivo existente con {len(existing)} URLs — haciendo merge...")

        merged = existing | urls_limpias
        nuevas = urls_limpias - existing

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(f"# Generado por search_urls.py — {url_seccion}\n")
            for url in sorted(merged):
                f.write(f"{url}\n")

        print(f"✅ Escaneo completado:")
        print(f"   Nuevas URLs encontradas : {len(nuevas)}")
        print(f"   Total en archivo        : {len(merged)}")
        print(f"   Guardado en             : {output_path}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extractor de URLs de productos desde catálogo de StockX",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python utils/search_urls.py --url https://stockx.com/brands/nike?category=sneakers
  python utils/search_urls.py --url https://stockx.com/brands/jordan --max-scrolls 30
  python utils/search_urls.py --url https://stockx.com/brands/adidas --archivo docs/adidas.txt
        """,
    )
    parser.add_argument(
        "--url",
        type=str,
        default="https://stockx.com/brands/jordan",
        help="URL de la categoría o marca en StockX",
    )
    parser.add_argument(
        "--archivo",
        type=str,
        default=DEFAULT_OUTPUT,
        help="Ruta del archivo TXT de salida",
    )
    parser.add_argument(
        "--max-scrolls",
        type=int,
        default=DEFAULT_MAX_SCROLLS,
        metavar="N",
        help=f"Número máximo de scrolls antes de parar (default: {DEFAULT_MAX_SCROLLS})",
    )

    args = parser.parse_args()

    asyncio.run(
        extraer_urls_desde_catalogo(
            url_seccion=args.url,
            archivo_salida=args.archivo,
            max_scrolls=args.max_scrolls,
        )
    )
