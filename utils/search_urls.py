import asyncio
from playwright.async_api import async_playwright

async def extraer_urls_desde_catalogo(url_seccion, archivo_salida):
    async with async_playwright() as p:
        print(f"🔄 Abriendo navegador para escanear el catálogo...")
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print(f"🌐 Navegando a la categoría: {url_seccion}")
        await page.goto(url_seccion, wait_until="networkidle", timeout=60000)

        # Esperamos a que carguen las tarjetas de las zapatillas
        await page.wait_for_timeout(4000)

        # Extraemos todos los hrefs de las etiquetas de enlace (<a>)
        todos_los_enlaces = await page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a')).map(a => a.href);
        }''')

        # Filtros para limpiar y quedarnos SOLO con URLs de productos reales
        urls_limpias = set()
        exclusiones = [
            '/about', '/help', '/faq', '/login', '/signup', '/sell', '/buy',
            '/terms', '/privacy', '/news', '/sneakers', '/streetwear', '/watches',
            '/bags', '/collectibles', '/app', 'javascript:', 'google.com'
        ]

        for url in todos_los_enlaces:
            if "stockx.com/" in url:
                # Quitamos parámetros de rastreo (?size=...) o barras finales si existen
                url_base = url.split('?')[0].rstrip('/')
                partes = url_base.split('/')

                # Una URL de producto StockX suele tener la estructura: https://stockx.com/slug-de-zapatilla
                # Al dividir por '/' quedan 4 elementos: ['https:', '', 'stockx.com', 'slug-de-zapatilla']
                if len(partes) == 4:
                    slug = partes[3]
                    # Verificamos que no pertenezca a páginas estáticas o de navegación global
                    if not any(exc in url_base for exc in exclusiones) and len(slug) > 3:
                        urls_limpias.add(url_base)

        print(f"✅ ¡Escaneo completado! Se encontraron {len(urls_limpias)} productos únicos.")

        # Guardamos la lista en un archivo de texto
        with open(archivo_salida, "w", encoding="utf-8") as f:
            for url in sorted(urls_limpias):
                f.write(f"{url}\n")

        print(f"💾 Enlaces guardados en '{archivo_salida}'.")
        await browser.close()

if __name__ == "__main__":
    # Puedes cambiar esta URL por cualquier categoría de StockX (ej. Adidas, New Balance, etc.)
    url_objetivo = "https://stockx.com/brands/jordan"
    archivo_txt = "lista_urls.txt"

    asyncio.run(extraer_urls_desde_catalogo(url_objetivo, archivo_txt))
