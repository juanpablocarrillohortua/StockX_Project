"""
StockX Scraper
--------------
CÓMO OBTENER EL HTML DE CADA SNEAKER (sin automatización):
  1. Abre la página del sneaker en el browser (ya estás logueado)
  2. Ctrl+U  (o clic derecho → "Ver código fuente de la página")
  3. Ctrl+A → Ctrl+C  para copiar todo
  4. Pega el HTML en la carpeta "html_pages/" con nombre = slug + ".html"
     Ejemplo: html_pages/air-jordan-cj1-t-rexx-travis-scott-green-spark.html
  5. Corre el script → lee todos los .html de esa carpeta automáticamente

El script combina:
  - Detalles (title, traits, description) extraídos del __NEXT_DATA__ del HTML
  - Serie de tiempo  vía GraphQL fetchSalesGraph (igual que antes)

Uso:
    mkdir html_pages
    # pegar los .html ahí
    python stockx_scraper.py
"""

import json
import time
import re
import os
from pathlib import Path
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from curl_cffi import requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────
AUTHORIZATION = "Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCIsImtpZCI6ImJoeVZaVHFkZGNMcVhmQS1JQWRsOCJ9.eyJodHRwczovL3N0b2NreC5jb20vY3VzdG9tZXJfdXVpZCI6ImZkZDgyZTg2LTFmYzAtMTFlYi1hMjBlLTEyNDczOGI1MGUxMiIsImh0dHBzOi8vc3RvY2t4LmNvbS9nYV9ldmVudCI6IkxvZ2dlZCBJbiIsImh0dHBzOi8vc3RvY2t4LmNvbS9lbWFpbF92ZXJpZmllZCI6dHJ1ZSwiaHR0cHM6Ly9zdG9ja3guY29tL2F1dGhfbWV0aG9kIjoiR29vZ2xlIiwiaHR0cHM6Ly9zdG9ja3guY29tL2F1dGhfZmxvdyI6IlByb2ZpbGUiLCJodHRwczovL3N0b2NreC5jb20vdG5jQ29uc2VudGVkIjp0cnVlLCJpc3MiOiJodHRwczovL2FjY291bnRzLnN0b2NreC5jb20vIiwic3ViIjoiZ29vZ2xlLW9hdXRoMnwxMTY2MjIzMTYzMjU4MjkxNzM3MTUiLCJhdWQiOlsiZ2F0ZXdheS5zdG9ja3guY29tIiwiaHR0cHM6Ly9zdG9ja3gtcHJvZC5zdG9ja3gtcHJvZC5hdXRoMGFwcC5jb20vdXNlcmluZm8iXSwiaWF0IjoxNzgxMzAyNTgwLCJleHAiOjE3ODEzNDU3ODAsInNjb3BlIjoib3BlbmlkIHByb2ZpbGUiLCJhenAiOiJPVnhydDRWSnFUeDdMSVVLZDY2MVcwRHVWTXBjRkJ5RCJ9.XiLMULN7CeurzN4XiD6JlW_JDbJVrSeWjWg38RT7VHtxSoNR-tADM4Ca1i21ZbxOUn7P9wLhQEveUIQoBnhN3FhcTxxUWCxVTxw26xQ3TV9pQTqsaHSVsJyJRTjRC8QtyHf-izPy9B4K91lvwMM16iH7cbmSj_boDMpBrJjVVX0fk-Irx5ja7ja87vCHmc5o8dUZzBgUCESTmYg-3C5TUxg0Itsw_xL1o29a4ngCxRus5jurdVe7Nv8e7vOymNNtXaln-zdujkJ7wpCS4q6OkDx04kNrno53r4Wzmbd8U7O_1KFVwBtOnKxQE2yPUXO8pDpmvVG_zYQ5ouViOe_7BA"   # ← reemplazar

# Carpeta donde guardas los HTML copiados del browser
HTML_FOLDER = Path("html_pages")

# Si quieres forzar slugs específicos en lugar de leer la carpeta:
# SNEAKERS = ["air-jordan-cj1-t-rexx-travis-scott-green-spark"]
# Si está vacío, usa todos los .html que encuentre en HTML_FOLDER
SNEAKERS: list[str] = []

END_DATE  = "2026-06-12"
CURRENCY  = "USD"
INTERVALS = 100
DELAY_SECONDS = 2
OUTPUT_FILE   = "stockx_data.json"
# ─────────────────────────────────────────────────────────────────────────────


SESSION = requests.Session()

GQL_URL = "https://stockx.com/api/graphql"
GQL_HEADERS_BASE = {
    "accept": "application/json",
    "accept-language": "en-US",
    "apollographql-client-name": "Iron",
    "apollographql-client-version": "2026.06.07.03",
    "app-platform": "Iron",
    "app-version": "2026.06.07.03",
    "authorization": AUTHORIZATION,
    "content-type": "application/json",
    "origin": "https://stockx.com",
    "selected-country": "CO",
    "x-stockx-device-id": "42ae1262-2b5b-4d14-a8bc-6e1c373a74f5",
    "x-stockx-session-id": "9da3507c-bfdd-4cdb-9e37-b80fc8cb1470",
}


# ══════════════════════════════════════════════════════════════════════════════
# 1. PARSEAR HTML LOCAL  →  detalles del producto
# ══════════════════════════════════════════════════════════════════════════════
def parse_local_html(html_path: Path) -> dict:
    """
    Lee el HTML guardado del browser y extrae los datos del producto.

    Estrategia 1 (principal): __NEXT_DATA__ — JSON embebido por Next.js
      StockX inyecta TODOS los datos del producto aquí. Es limpio y estructurado.

    Estrategia 2 (fallback): raspar el <section data-component="ProductDetails">
      Por si el __NEXT_DATA__ tiene una estructura diferente en algún sneaker.
    """
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")

    result = {
        "traits": {},
        "description": None,
        "title": None,
        "brand": None,
        "source": None,
    }

    # ── Estrategia 1: __NEXT_DATA__ ──────────────────────────────────────────
    next_script = soup.find("script", {"id": "__NEXT_DATA__"})
    if next_script and next_script.string:
        try:
            next_data = json.loads(next_script.string)

            # StockX puede anidar el producto en distintas rutas según la versión
            product = (
                _dig(next_data, "props", "pageProps", "productTemplate")
                or _dig(next_data, "props", "pageProps", "product")
                or _dig(next_data, "props", "pageProps", "initialData", "product")
                or _dig(next_data, "props", "pageProps", "browse", "results", 0)
            )

            if product:
                result["title"]       = product.get("title") or product.get("name")
                result["brand"]       = product.get("brand")
                result["description"] = product.get("description")
                result["source"]      = "next_data"

                # traits como lista [{name, value}, ...]
                for t in product.get("traits", []):
                    key = re.sub(r"[\s\-]+", "_", t.get("name", "")).lower()
                    result["traits"][key] = t.get("value")

                # Algunos campos vienen sueltos a nivel raíz
                flat_map = {
                    "styleId":      "style",
                    "sku":          "style",
                    "colorway":     "colorway",
                    "retailPrice":  "retail_price",
                    "releaseDate":  "release_date",
                    "gender":       "gender",
                    "category":     "category",
                }
                for src_key, dst_key in flat_map.items():
                    val = product.get(src_key) or _dig(product, "productAttributes", src_key)
                    if val and dst_key not in result["traits"]:
                        result["traits"][dst_key] = str(val)

                if result["traits"] or result["description"]:
                    return result

        except (json.JSONDecodeError, Exception) as e:
            print(f"     ⚠ __NEXT_DATA__ parse error: {e}")

    # ── Estrategia 2: raspar sección HTML directamente ────────────────────────
    section = soup.find("section", {"data-component": "ProductDetails"})
    if section:
        for div in section.find_all("div", {"data-component": "product-trait"}):
            label = div.find("span")
            value = div.find("p")
            if label and value:
                key = re.sub(r"\s+", "_", label.get_text(strip=True)).lower()
                result["traits"][key] = value.get_text(strip=True)

        desc_div = section.find("div", {"data-component": "ProductDescription"})
        if desc_div:
            ps = desc_div.find_all("p")
            result["description"] = "\n\n".join(
                p.get_text(separator="\n", strip=True) for p in ps
            )

        # Título desde <h1>
        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)

        result["source"] = "html_section"

    if not result["traits"] and not result["description"]:
        result["source"] = "not_found"

    return result


def _dig(obj: dict, *keys):
    """Navega un dict anidado de forma segura."""
    for k in keys:
        if isinstance(obj, dict):
            obj = obj.get(k)
        elif isinstance(obj, list) and isinstance(k, int) and k < len(obj):
            obj = obj[k]
        else:
            return None
    return obj


# ══════════════════════════════════════════════════════════════════════════════
# 2. SERIE DE TIEMPO  —  GraphQL fetchSalesGraph
# ══════════════════════════════════════════════════════════════════════════════
def fetch_sales_series(slug: str) -> list[dict] | str:
    headers = {
        **GQL_HEADERS_BASE,
        "referer": f"https://stockx.com/{slug}",
        "x-operation-name": "fetchSalesGraph",
    }
    payload = {
        "operationName": "fetchSalesGraph",
        "variables": {
            "productId": slug,
            "startDate": "all",
            "endDate": END_DATE,
            "intervals": INTERVALS,
            "currencyCode": CURRENCY,
            "isVariant": False,
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": "2bdd0a3f11bfaa85eb8014cf8be8ccdee8839ad4d284eba08e6031b239e12a72",
            }
        },
    }
    resp = SESSION.post(GQL_URL, headers=headers, json=payload, impersonate="chrome")
    if resp.status_code == 200:
        try:
            return resp.json()["data"]["product"]["salesChart"]["series"]
        except (KeyError, TypeError) as e:
            return f"JSON inesperado: {e}"
    return f"Error {resp.status_code}: {resp.text[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. ORQUESTADOR
# ══════════════════════════════════════════════════════════════════════════════
def resolve_slugs() -> list[str]:
    """Devuelve la lista de slugs a procesar."""
    if SNEAKERS:
        return SNEAKERS

    # Auto-detectar desde los archivos .html en HTML_FOLDER
    if not HTML_FOLDER.exists():
        print(f"⚠ Carpeta '{HTML_FOLDER}' no existe. Créala y pega los HTML ahí.")
        return []

    slugs = [f.stem for f in sorted(HTML_FOLDER.glob("*.html"))]
    if not slugs:
        print(f"⚠ No se encontraron archivos .html en '{HTML_FOLDER}'.")
    return slugs


def scrape_sneakers(slugs: list[str]) -> list[dict]:
    results = []

    for i, slug in enumerate(slugs, 1):
        print(f"\n[{i}/{len(slugs)}] {slug}")
        record = {
            "slug": slug,
            "url": f"https://stockx.com/{slug}",
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "product_details": {},
            "sales_series": [],
            "errors": [],
        }

        # ── Detalles desde HTML local ──
        html_path = HTML_FOLDER / f"{slug}.html"
        if html_path.exists():
            print(f"  → Parseando HTML local…")
            try:
                details = parse_local_html(html_path)
                record["product_details"] = details
                n_traits = len(details.get("traits", {}))
                src = details.get("source", "?")
                if src == "not_found":
                    print(f"     ⚠ No se encontraron datos en el HTML")
                    record["errors"].append("product_details: datos no encontrados en HTML")
                else:
                    print(f"     ✓ {n_traits} traits  |  fuente: {src}")
            except Exception as e:
                record["errors"].append(f"product_details: {e}")
                print(f"     ✗ {e}")
        else:
            msg = f"HTML no encontrado: {html_path}"
            record["errors"].append(msg)
            print(f"  ⚠ {msg}")

        # ── Serie de tiempo ──
        print(f"  → Serie de tiempo…")
        try:
            series = fetch_sales_series(slug)
            if isinstance(series, list):
                record["sales_series"] = series
                print(f"     ✓ {len(series)} puntos")
            else:
                record["errors"].append(f"sales_series: {series}")
                print(f"     ✗ {series}")
        except Exception as e:
            record["errors"].append(f"sales_series: {e}")

        results.append(record)
        if i < len(slugs):
            time.sleep(DELAY_SECONDS)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 4. PUNTO DE ENTRADA
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    slugs = resolve_slugs()
    if not slugs:
        exit(1)

    print(f"StockX Scraper — {len(slugs)} sneaker(s)\n")
    data = scrape_sneakers(slugs)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Guardado en '{OUTPUT_FILE}'  ({len(data)} registros)")
    for rec in data:
        ts     = len(rec.get("sales_series", []))
        src    = rec.get("product_details", {}).get("source", "—")
        traits = rec.get("product_details", {}).get("traits", {})
        errs   = len(rec.get("errors", []))
        status = "✓" if not errs else "⚠"
        print(f"  {status} {rec['slug']}")
        print(f"    serie: {ts} pts | traits: {len(traits)} ({src}) | errores: {errs}")
