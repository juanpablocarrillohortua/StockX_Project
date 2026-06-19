# StockX Scraper

Extrae datos de sneakers desde StockX combinando dos fuentes:

- **HTML local** → título, marca, colorway, style number, retail price, fecha de lanzamiento, descripción
- **GraphQL API** → serie de tiempo de precios de venta (hasta 100 puntos)

Todo se cruza automáticamente en un único archivo `data/sneakers_data.json`.

---

## Estructura del proyecto

```
StockX_Project/
├── main.py                  ← Orquestador: corre el pipeline completo
├── utils/
│   ├── search_urls.py       ← Fase 1: extrae URLs desde una categoría de StockX
│   ├── copy_html.py         ← Fase 2: descarga el HTML de cada producto
│   └── scraper.py           ← Fase 3: cruza HTML + API → JSON final
├── docs/
│   └── lista_urls.txt       ← URLs de los sneakers a procesar
├── html_pages/              ← HTMLs descargados (generado automáticamente)
└── data/
    └── sneakers_data.json   ← Salida final (generado automáticamente)
```

---

## Requisitos

**Python 3.11+** y las siguientes librerías:

```bash
pip install curl_cffi beautifulsoup4 playwright
python -m playwright install chrome
```

**Google Chrome instalado** en el sistema (requerido por `copy_html.py`).

---

## Configuración inicial

### 1. Token de autorización (se renueva cada 12 horas)

El scraper necesita un JWT de tu sesión de StockX para acceder a la API GraphQL.

**Cómo obtenerlo:**

1. Entra a [stockx.com](https://stockx.com) y asegúrate de estar logueado
2. Abre DevTools → pestaña **Network** (`F12` → Network)
3. Filtra por `graphql` en la barra de búsqueda
4. Navega a cualquier página de producto
5. Haz clic en cualquier request que aparezca en Network
6. En la sección **Request Headers**, copia el valor del header `authorization`
   (empieza con `Bearer eyJ...`)

**Cómo configurarlo** (elige una opción):

```bash
# Opción A — Variable de entorno (recomendada, no queda en el código)
export STOCKX_TOKEN="Bearer eyJhbGci..."

# Opción B — Hardcodeado en scraper.py
# Edita la línea AUTHORIZATION = "Bearer TU_TOKEN_AQUI" en utils/scraper.py
```

> **Nota:** el token expira a las 12 horas. El script detecta si está vencido
> y avisa antes de empezar. Si ves `❌ TOKEN EXPIRADO`, repite los pasos anteriores.

### 2. Lista de URLs

Edita `docs/lista_urls.txt` con las URLs de los productos que quieres procesar,
una por línea. Las líneas que empiecen con `#` se ignoran.

```
# Jordan
https://stockx.com/air-jordan-1-retro-high-og-chicago-2015
https://stockx.com/air-jordan-cj1-t-rexx-travis-scott-green-spark

# Nike
https://stockx.com/nike-dunk-low-retro-white-black-2021
```

---

## Uso

Todos los comandos se ejecutan desde la raíz del proyecto (`StockX_Project/`).

### Pipeline completo (recomendado)

Corre las tres fases en secuencia:

```bash
cd StockX_Project
python main.py
```

Con opciones:

```bash
# Cambiar la URL de catálogo de la Fase 1
python main.py --url https://stockx.com/brands/nike

# Probar el pipeline con solo 5 productos
python main.py --limit 5

# Reanudar después de una interrupción (omite lo ya procesado)
python main.py --skip-existing
```

---

### Fases por separado

Si ya tienes las URLs en `lista_urls.txt` puedes saltarte la Fase 1 y correr
las fases 2 y 3 directamente.

#### Fase 2 — Descargar HTMLs

Abre Chrome automáticamente y guarda el HTML de cada producto en `html_pages/`:

```bash
python utils/copy_html.py

# Solo los que aún no tienen HTML descargado
python utils/copy_html.py --skip-existing

# Probar con los primeros 3
python utils/copy_html.py --limit 3
```

> Chrome se abre en modo visible (no headless) para evitar la detección de bots.
> Es normal que veas ventanas abrirse y cerrarse mientras corre.

#### Fase 3 — Cruzar datos y generar JSON

Lee los HTMLs locales y los combina con la serie de tiempo de la API:

```bash
python utils/scraper.py

# Omitir slugs que ya están en sneakers_data.json
python utils/scraper.py --skip-existing

# Con token desde variable de entorno
STOCKX_TOKEN="Bearer eyJ..." python utils/scraper.py
```

---

## Formato del JSON de salida

`data/sneakers_data.json` contiene un array donde cada elemento tiene esta estructura:

```json
{
  "slug": "air-jordan-1-retro-high-og-chicago-2015",
  "url": "https://stockx.com/air-jordan-1-retro-high-og-chicago-2015",
  "scraped_at": "2026-06-12T23:00:00+00:00",

  "product_details": {
    "title": "Air Jordan 1 Retro High OG Chicago 2015",
    "brand": "Jordan",
    "description": "Descripción larga del producto...",
    "source": "next_data",
    "traits": {
      "style":        "575441-101",
      "colorway":     "White/Black-Varsity Red",
      "retail_price": "$160",
      "release_date": "02/14/2015"
    }
  },

  "sales_series": [
    { "xValue": "2015-02-14T00:00:00.000Z", "yValue": 350 },
    { "xValue": "2015-03-01T00:00:00.000Z", "yValue": 320 }
  ],

  "_meta": {
    "title":        "Air Jordan 1 Retro High OG Chicago 2015",
    "brand":        "Jordan",
    "style":        "575441-101",
    "colorway":     "White/Black-Varsity Red",
    "retail_price": "$160",
    "release_date": "02/14/2015",
    "series_count": 100,
    "has_errors":   false
  },

  "errors": []
}
```

`_meta` duplica los campos más usados al nivel raíz para facilitar la carga
en pandas o Excel sin navegar dicts anidados:

```python
import pandas as pd, json

records = json.load(open("data/sneakers_data.json"))
df = pd.DataFrame([r["_meta"] | {"slug": r["slug"]} for r in records])
```

---

## Solución de problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `❌ TOKEN EXPIRADO` | JWT vencido (dura 12h) | Renovar el token desde DevTools |
| `HTML existe pero sin datos` | StockX bloqueó la descarga | Borrar el `.html` y volver a correr `copy_html.py` |
| `HTTP 403` en la API | Token incorrecto o vencido | Verificar que el token empiece con `Bearer eyJ` |
| `HTTP 429` en la API | Rate limit | El script reintenta automáticamente con backoff; espera |
| Chrome no abre | Playwright sin Chrome | Correr `python -m playwright install chrome` |
| `_meta` con campos `null` | `__NEXT_DATA__` con estructura diferente | El fallback HTML debería cubrirlo; revisar `errors` en el JSON |

---

## Notas

- El scraper guarda el JSON **después de cada producto**, así que si se interrumpe no se pierde el trabajo anterior. Usa `--skip-existing` para reanudar.
- Los delays entre requests son aleatorios (distribución gaussiana, ~2–8 segundos) para imitar comportamiento humano y evitar bloqueos.
- El `x-stockx-device-id` y `x-stockx-session-id` se rotan en cada request para evitar fingerprinting por headers fijos.