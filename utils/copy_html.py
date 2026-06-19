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
# RUTAS  (relativas a la raíz del proyecto)
# ─────────────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent   # StockX_Project/
URLS_FILE   = ROOT / "docs"       / settings.URL_LIST_NAME  # cambiar si se va a usar con otra lista
HTML_FOLDER = ROOT / "html_pages"
# Contexto persistente: guarda cookies y localStorage entre ejecuciones
CONTEXT_DIR = ROOT / ".browser_context"

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
# Delay entre páginas: min y max en segundos (distribución uniforme)
DELAY_MIN = 4.0
DELAY_MAX = 9.0

# Timeout de navegación por página (ms)
NAV_TIMEOUT = 90_000

# Reintentos por URL si falla
MAX_RETRIES = 2

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
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


def load_urls(path: Path, skip_existing: bool, limit: int | None) -> list[tuple[str, Path]]:
    """Lee lista_urls.txt y devuelve [(url, html_path), ...] filtrados."""
    if not path.exists():
        print(f"❌  No se encontró el archivo de URLs: {path}")
        sys.exit(1)

    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    urls  = [l for l in lines if l and not l.startswith("#")]

    tasks: list[tuple[str, Path]] = []
    for url in urls:
        slug      = slug_from_url(url)
        html_path = HTML_FOLDER / f"{slug}.html"

        if skip_existing and html_path.exists():
            print(f"⏭  Ya existe, omitiendo: {slug}")
            continue

        tasks.append((url, html_path))

    if limit:
        tasks = tasks[:limit]

    return tasks


async def human_delay(min_s: float = DELAY_MIN, max_s: float = DELAY_MAX) -> None:
    """Pausa con duración aleatoria para imitar comportamiento humano."""
    await asyncio.sleep(random.uniform(min_s, max_s))


async def human_scroll(page: Page) -> None:
    """Scroll suave y aleatorio hacia abajo, luego un poco arriba."""
    total = random.randint(600, 1800)
    step  = random.randint(80, 200)
    for _ in range(total // step):
        await page.mouse.wheel(0, step)
        await asyncio.sleep(random.uniform(0.05, 0.18))
    await asyncio.sleep(random.uniform(0.3, 0.8))
    await page.mouse.wheel(0, -random.randint(100, 300))


async def human_mouse_move(page: Page) -> None:
    """Mueve el mouse por trayectorias curvas aleatorias."""
    for _ in range(random.randint(2, 5)):
        x = random.randint(200, 1400)
        y = random.randint(150, 700)
        await page.mouse.move(x, y, steps=random.randint(8, 20))
        await asyncio.sleep(random.uniform(0.1, 0.4))


# ─────────────────────────────────────────────────────────────────────────────
# ANTI-DETECCIÓN: script inyectado antes de cada página
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
# DESCARGA DE UNA URL
# ─────────────────────────────────────────────────────────────────────────────
async def download_page(
    context: BrowserContext,
    url: str,
    html_path: Path,
    idx: int,
    total: int,
) -> bool:
    """
    Abre una nueva tab, navega a `url`, guarda el HTML en `html_path`.
    Devuelve True si tuvo éxito.
    """
    slug = slug_from_url(url)
    print(f"\n[{idx}/{total}] {slug}")

    for attempt in range(1, MAX_RETRIES + 2):  # +2: intento inicial + reintentos
        page = await context.new_page()
        try:
            # Inyectar evasión antes de cualquier JS de la página
            await page.add_init_script(EVASION_SCRIPT)

            print(f"  → Navegando… (intento {attempt})")
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)

            # Esperar a que __NEXT_DATA__ aparezca en el DOM (señal de que el SSR cargó)
            try:
                await page.wait_for_selector(
                    "script#__NEXT_DATA__",
                    timeout=20_000,
                    state="attached",
                )
            except Exception:
                # No siempre está; continuamos de todas formas
                pass

            # Comportamiento humano: scroll + mouse
            await human_mouse_move(page)
            await human_scroll(page)
            await human_delay(1.5, 3.0)   # pausa corta extra dentro de la página

            html = await page.content()

            # Verificación básica: ¿el HTML tiene contenido real?
            if len(html) < 5_000:
                raise ValueError(f"HTML demasiado corto ({len(html)} bytes) — posible bloqueo")

            if "__NEXT_DATA__" not in html and "ProductDetails" not in html:
                print(f"  ⚠  HTML recibido pero sin datos de producto (posible captcha)")
                # Guardar igualmente para inspección manual
                html_path.write_text(html, encoding="utf-8")
                return False

            html_path.write_text(html, encoding="utf-8")
            print(f"  ✓ Guardado → {html_path.name}  ({len(html):,} bytes)")
            return True

        except Exception as e:
            print(f"  ✗ Error intento {attempt}: {e}")
            if attempt <= MAX_RETRIES:
                wait = random.uniform(8, 15)
                print(f"  ⏳ Reintentando en {wait:.1f}s…")
                await asyncio.sleep(wait)
        finally:
            await page.close()

    print(f"  ❌ Falló después de {MAX_RETRIES + 1} intentos: {slug}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# FLUJO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────
async def main(skip_existing: bool, limit: int | None) -> None:
    HTML_FOLDER.mkdir(parents=True, exist_ok=True)
    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    tasks = load_urls(URLS_FILE, skip_existing, limit)
    if not tasks:
        print("✅ Nada que procesar.")
        return

    print(f"\nStockX HTML Downloader")
    print(f"  URLs a procesar : {len(tasks)}")
    print(f"  HTML folder     : {HTML_FOLDER}")
    print(f"  Contexto        : {CONTEXT_DIR}\n")

    async with async_playwright() as p:
        print("🚀 Lanzando Chrome…")
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",          # usa tu Chrome real instalado en Windows
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-extensions-except=",  # sin extensiones que delaten automation
            ],
            ignore_default_args=["--enable-automation"],   # ← clave: elimina el banner
        )

        # Contexto persistente reutiliza cookies de sesiones anteriores
        # Si ya iniciaste sesión en StockX manualmente una vez, las cookies se guardan.
        context = await browser.new_context(
            storage_state=str(CONTEXT_DIR / "state.json") if (CONTEXT_DIR / "state.json").exists() else None,
            viewport=None,
            user_agent=random.choice(USER_AGENTS),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            },
        )

        # Estadísticas
        ok_count   = 0
        fail_count = 0
        start_time = datetime.now()

        for i, (url, html_path) in enumerate(tasks, 1):
            success = await download_page(context, url, html_path, i, len(tasks))
            if success:
                ok_count += 1
            else:
                fail_count += 1

            # Guardar estado (cookies) después de cada página exitosa
            if success:
                await context.storage_state(path=str(CONTEXT_DIR / "state.json"))

            # Delay entre URLs (solo si no es la última)
            if i < len(tasks):
                wait = random.uniform(DELAY_MIN, DELAY_MAX)
                print(f"  ⏳ Esperando {wait:.1f}s antes de la siguiente URL…")
                await asyncio.sleep(wait)

        await browser.close()

        elapsed = (datetime.now() - start_time).seconds
        print(f"\n{'─'*50}")
        print(f"✅ Completado en {elapsed//60}m {elapsed%60}s")
        print(f"   Éxito  : {ok_count}")
        print(f"   Fallos : {fail_count}")
        print(f"   HTML guardados en: {HTML_FOLDER}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StockX HTML Downloader")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Omite URLs cuyo .html ya existe en html_pages/",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Procesar solo las primeras N URLs (útil para pruebas)",
    )
    args = parser.parse_args()

    asyncio.run(main(skip_existing=args.skip_existing, limit=args.limit))
