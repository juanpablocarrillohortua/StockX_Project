"""
StockX_Project/utils/scraper.py
--------------------------------
Orquesta dos fuentes de datos y las cruza en un único JSON:

  FUENTE 1 — HTML local (StockX_Project/html_pages/<slug>.html)
    Parsea el bloque __NEXT_DATA__ (Next.js SSR) para extraer:
    title, brand, traits (Style, Colorway, Retail Price, Release Date), description.
    Fallback: raspa <section data-component="ProductDetails"> si __NEXT_DATA__ falla.

  FUENTE 2 — GraphQL API (fetchSalesGraph)
    Recupera la serie de tiempo de precios de venta del sneaker.
    Usa curl_cffi con impersonate="chrome" para pasar el filtro TLS de StockX.

SLUGS: se derivan automáticamente de StockX_Project/docs/lista_urls.txt.
       Solo se procesan slugs que tengan su .html descargado en html_pages/.

SALIDA: StockX_Project/data/sneakers_data.json

ANTI-BLOQUEO (API):
  - TLS fingerprint real de Chrome vía curl_cffi
  - Delay aleatorio entre requests con distribución gaussiana
  - Detección de token JWT expirado con aviso claro
  - Headers rotados (device-id, session-id) por slug
  - Reintentos con backoff exponencial en errores 429/5xx

USO:
    cd StockX_Project
    python utils/scraper.py

    # Solo slugs sin datos previos en el JSON:
    python utils/scraper.py --skip-existing

    # Probar con las primeras N URLs:
    python utils/scraper.py --limit 5

    # Token desde variable de entorno (evita hardcodearlo):
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
# RUTAS  (siempre relativas a la raíz del proyecto, sin importar el CWD)
# ─────────────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).resolve().parent.parent   # StockX_Project/
URLS_FILE   = ROOT / "docs"       / settings.URL_LIST_NAME
HTML_FOLDER = ROOT / "html_pages"
DATA_DIR    = ROOT / "data"
OUTPUT_FILE = DATA_DIR / "sneakers_data.json"

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN  —  edita aquí o usa variables de entorno
# ─────────────────────────────────────────────────────────────────────────────
# Prioridad: env STOCKX_TOKEN > valor hardcodeado abajo
AUTHORIZATION: str = os.environ.get(
    "STOCKX_TOKEN",
    settings.STOCKX_TOKEN,
)

END_DATE  = "2026-06-12"
CURRENCY  = "USD"
INTERVALS = 100

# Delay entre slugs: se samplea de Normal(mean, sigma) y se clampea en [min, max]
DELAY_MEAN  = 3.5   # segundos
DELAY_SIGMA = 1.2
DELAY_MIN   = 1.8
DELAY_MAX   = 8.0

MAX_RETRIES = 3          # reintentos por slug en la API
GQL_URL     = "https://stockx.com/api/graphql"

# ─────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
# UTILIDADES GENERALES
# ══════════════════════════════════════════════════════════════════════════════

def slug_from_url(url: str) -> str:
    """'https://stockx.com/nike-dunk-low?size=9' → 'nike-dunk-low'"""
    return url.strip().split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]


def random_delay() -> float:
    """Delay con distribución gaussiana para parecer más humano."""
    d = random.gauss(DELAY_MEAN, DELAY_SIGMA)
    return max(DELAY_MIN, min(DELAY_MAX, d))


def _dig(obj, *keys):
    """Navega un dict/list anidado sin lanzar excepciones."""
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
    """Extrae la fecha de expiración del JWT sin validar firma."""
    try:
        payload_b64 = token.removeprefix("Bearer ").split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.b64decode(payload_b64))
        return datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    except Exception:
        return None


def check_token(token: str) -> None:
    """Avisa si el token está expirado o por expirar (< 30 min)."""
    exp = decode_jwt_exp(token)
    if exp is None:
        print("⚠  No se pudo leer la expiración del token JWT.")
        return
    now  = datetime.now(tz=timezone.utc)
    diff = (exp - now).total_seconds()
    if diff <= 0:
        print(f"❌  TOKEN EXPIRADO hace {abs(diff/60):.0f} minutos.")
        print("    Recarga la página de StockX en el browser, captura una nueva")
        print("    request GraphQL en Network y copia el header Authorization.")
        sys.exit(1)
    elif diff < 1800:
        print(f"⚠  TOKEN expira en {diff/60:.0f} minutos — considera renovarlo pronto.")
    else:
        print(f"✓  Token válido por {diff/3600:.1f} horas (expira {exp.strftime('%H:%M UTC')})")


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 1 — PARSEO DE HTML LOCAL
# ══════════════════════════════════════════════════════════════════════════════

def parse_local_html(html_path: Path) -> dict:
    """
    Extrae datos del producto desde el HTML guardado localmente.

    Estrategia 1 (principal): script#__NEXT_DATA__
      Next.js inyecta todo el JSON del producto en el HTML inicial (SSR).
      Es la fuente más completa y estructurada.

    Estrategia 2 (fallback): <section data-component="ProductDetails">
      Por si __NEXT_DATA__ no tiene los campos esperados en alguna versión.
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

    # ── Estrategia 1: __NEXT_DATA__ ──────────────────────────────────────────
    script_tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if script_tag and script_tag.string:
        try:
            nd = json.loads(script_tag.string)

            # StockX ha cambiado el path interno en distintas versiones del frontend
            product = (
                _dig(nd, "props", "pageProps", "productTemplate")
                or _dig(nd, "props", "pageProps", "product")
                or _dig(nd, "props", "pageProps", "initialData", "product")
                or _dig(nd, "props", "pageProps", "serverData", "product")
                or _dig(nd, "props", "pageProps", "browse", "results", 0)
            )

            if product:
                result["title"]       = product.get("title") or product.get("name")
                result["brand"]       = product.get("brand")
                result["description"] = product.get("description")
                result["source"]      = "next_data"

                # traits como lista [{name, value}, ...]
                for t in (product.get("traits") or []):
                    key = re.sub(r"[\s\-/]+", "_", t.get("name", "")).lower().strip("_")
                    result["traits"][key] = t.get("value")

                # Campos sueltos en la raíz del objeto producto
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

    # ── Estrategia 2: section HTML ───────────────────────────────────────────
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

        h1 = soup.find("h1")
        if h1:
            result["title"] = h1.get_text(strip=True)

        result["source"] = "html_section"

    return result


# ══════════════════════════════════════════════════════════════════════════════
# FUENTE 2 — GRAPHQL API  (serie de tiempo)
# ══════════════════════════════════════════════════════════════════════════════

# Sesión compartida: curl_cffi mantiene la conexión HTTP/2 abierta
_SESSION = cfl_requests.Session()


def _gql_headers(slug: str) -> dict:
    """
    Headers por petición.
    device-id y session-id se rotan por slug para evitar patrones fijos
    que los sistemas anti-bot detecten fácilmente.
    """
    return {
        "accept":                      "application/json",
        "accept-language":             "en-US,en;q=0.9",
        "apollographql-client-name":   "Iron",
        "apollographql-client-version":"2026.06.07.03",
        "app-platform":                "Iron",
        "app-version":                 "2026.06.07.03",
        "authorization":               AUTHORIZATION,
        "content-type":                "application/json",
        "origin":                      "https://stockx.com",
        "referer":                     f"https://stockx.com/{slug}",
        "selected-country":            "CO",
        "x-operation-name":            "fetchSalesGraph",
        "x-stockx-device-id":          str(uuid.uuid4()),   # rotado
        "x-stockx-session-id":         str(uuid.uuid4()),   # rotado
    }


def fetch_sales_series(slug: str) -> list[dict] | str:
    """
    Llama a fetchSalesGraph con reintentos y backoff exponencial.
    Devuelve la lista de puntos [{xValue, yValue}] o un string de error.
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
                "sha256Hash": "2bdd0a3f11bfaa85eb8014cf8be8ccdee8839ad4d284eba08e6031b239e12a72",
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
                # 200 pero estructura inesperada
                return f"JSON inesperado: {json.dumps(data)[:200]}"

            elif resp.status_code in (429, 503, 502):
                # Rate-limited o servidor caído → backoff
                wait = (2 ** attempt) + random.uniform(1, 3)
                print(f"     ⚠  HTTP {resp.status_code} — reintento {attempt}/{MAX_RETRIES} en {wait:.1f}s")
                time.sleep(wait)
                last_error = f"HTTP {resp.status_code}"
                continue

            elif resp.status_code == 401:
                return "Error 401: token expirado — renueva AUTHORIZATION"

            else:
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break

        except Exception as e:
            last_error = f"Excepción: {e}"
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)

    return f"Error tras {MAX_RETRIES} intentos: {last_error}"


# ══════════════════════════════════════════════════════════════════════════════
# RESOLUCIÓN DE SLUGS DESDE lista_urls.txt
# ══════════════════════════════════════════════════════════════════════════════

def load_slugs_from_urls(limit: int | None = None) -> list[str]:
    """
    Lee lista_urls.txt y devuelve los slugs cuyo .html ya existe en html_pages/.
    Las URLs sin HTML descargado se listan como aviso pero no se procesan.
    """
    if not URLS_FILE.exists():
        print(f"❌  No se encontró: {URLS_FILE}")
        sys.exit(1)

    lines = URLS_FILE.read_text(encoding="utf-8").splitlines()
    urls  = [l.strip() for l in lines if l.strip() and not l.startswith("#")]

    ready, missing = [], []
    for url in urls:
        slug = slug_from_url(url)
        if (HTML_FOLDER / f"{slug}.html").exists():
            ready.append(slug)
        else:
            missing.append(slug)

    if missing:
        print(f"\n⚠  {len(missing)} URLs sin HTML descargado (ejecuta copy_html.py primero):")
        for s in missing[:10]:
            print(f"   · {s}")
        if len(missing) > 10:
            print(f"   … y {len(missing)-10} más")

    if limit:
        ready = ready[:limit]

    return ready


# ══════════════════════════════════════════════════════════════════════════════
# CRUCE DE DATOS Y ESCRITURA DEL JSON
# ══════════════════════════════════════════════════════════════════════════════

def load_existing_data() -> dict[str, dict]:
    """Carga el JSON existente (si hay) como {slug: record}."""
    if OUTPUT_FILE.exists():
        try:
            records = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
            return {r["slug"]: r for r in records if "slug" in r}
        except Exception:
            pass
    return {}


def scrape(slugs: list[str], skip_existing: bool) -> None:
    existing = load_existing_data()

    # Filtrar slugs ya procesados si --skip-existing
    if skip_existing:
        before = len(slugs)
        slugs  = [s for s in slugs if s not in existing]
        print(f"⏭  --skip-existing: omitiendo {before - len(slugs)} slugs ya en el JSON")

    if not slugs:
        print("✅ Nada nuevo que procesar.")
        return

    print(f"\nProcesando {len(slugs)} sneaker(s)…")
    print(f"{'─'*60}")

    results: dict[str, dict] = {**existing}   # empezar con datos previos

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

        # ── FUENTE 1: detalles desde HTML local ──────────────────────────────
        html_path = HTML_FOLDER / f"{slug}.html"
        print("  → Detalles del producto (HTML local)…")
        try:
            details = parse_local_html(html_path)
            record["product_details"] = details

            src      = details.get("source", "?")
            n_traits = len(details.get("traits", {}))

            if src == "not_found":
                msg = "HTML existe pero no contiene datos reconocibles"
                record["errors"].append(f"product_details: {msg}")
                print(f"     ⚠  {msg}")
            else:
                print(f"     ✓  {n_traits} traits — fuente: {src}")
                if details.get("title"):
                    print(f"     📦 {details['title']}")

        except Exception as e:
            record["errors"].append(f"product_details: {e}")
            print(f"     ✗  {e}")

        # ── FUENTE 2: serie de tiempo vía API ─────────────────────────────────
        print("  → Serie de tiempo (GraphQL)…")
        try:
            series = fetch_sales_series(slug)
            if isinstance(series, list):
                record["sales_series"] = series
                print(f"     ✓  {len(series)} puntos de datos")

                # Estadísticas rápidas para validación
                if series:
                    prices = [p["yValue"] for p in series if p.get("yValue")]
                    if prices:
                        print(f"     📈 Rango: ${min(prices)} – ${max(prices)} USD")
            else:
                record["errors"].append(f"sales_series: {series}")
                print(f"     ✗  {series}")

        except Exception as e:
            record["errors"].append(f"sales_series: {e}")
            print(f"     ✗  {e}")

        # ── CRUCE: agregar campos de traits al nivel raíz para fácil acceso ──
        # Duplica los campos más útiles fuera del dict anidado para facilitar
        # el análisis posterior (pandas, Excel, etc.)
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

        # ── Guardar en el dict de resultados ─────────────────────────────────
        results[slug] = record

        if record["errors"]:
            fail_count += 1
        else:
            ok_count += 1

        # ── Persistir después de cada slug (no perder trabajo si se cae) ─────
        _write_output(list(results.values()))

        # ── Delay anti-bloqueo entre slugs ───────────────────────────────────
        if i < len(slugs):
            wait = random_delay()
            print(f"  ⏳ Esperando {wait:.1f}s…")
            time.sleep(wait)

    # ── Resumen final ─────────────────────────────────────────────────────────
    total = ok_count + fail_count
    print(f"\n{'═'*60}")
    print(f"✅ Completado: {ok_count}/{total} sin errores")
    print(f"   Guardado en: {OUTPUT_FILE}")
    print(f"{'═'*60}")

    for slug, rec in results.items():
        m     = rec.get("_meta", {})
        ts    = m.get("series_count", 0)
        title = (m.get("title") or slug)[:50]
        errs  = len(rec.get("errors", []))
        icon  = "✓" if not errs else "⚠"
        print(f"  {icon} {title}")
        print(f"    style={m.get('style')} | retail={m.get('retail_price')} "
              f"| release={m.get('release_date')} | serie={ts} pts")


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
        description="StockX Scraper — cruza HTML local + GraphQL API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Omite slugs que ya están en sneakers_data.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Procesar solo las primeras N URLs (útil para pruebas)",
    )
    args = parser.parse_args()

    print("StockX Scraper")
    print(f"  URLs file  : {URLS_FILE}")
    print(f"  HTML folder: {HTML_FOLDER}")
    print(f"  Output     : {OUTPUT_FILE}\n")

    # Verificar token antes de empezar
    check_token(AUTHORIZATION)

    slugs = load_slugs_from_urls(limit=args.limit)
    if not slugs:
        print("\n❌ No hay slugs listos para procesar.")
        print("   Asegúrate de haber descargado los HTML con: python utils/copy_html.py")
        sys.exit(1)

    print(f"\n📋 {len(slugs)} sneaker(s) listos para procesar")
    scrape(slugs, skip_existing=args.skip_existing)
    with open(URLS_FILE, "w") as archivo:
        pass


if __name__ == "__main__":
    main()
