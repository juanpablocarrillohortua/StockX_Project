# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

End-to-end pipeline that scrapes sneaker resale data from StockX, cleans it, performs exploratory analysis, and trains a Logistic Regression model that predicts whether a sneaker's resale price will exceed its retail price 90 days after launch. Full methodology, feature dictionary, and model results are documented in [README.md](README.md) — read it before touching the cleaning/feature-engineering/modeling notebooks, since the rationale for most decisions (leakage prevention, scaler choice, train/test split strategy) lives there, not in code comments.

## Commands

```bash
make install            # pip install -r requirements.txt + pycodestyle, nbqa
make clean              # remove __pycache__, .ipynb_checkpoints, .pytest_cache (cross-platform, scripts/clean.py)
make quality-main       # pycodestyle main.py
make quality-utils      # pycodestyle utils/
make quality-notebooks  # pycodestyle via nbqa on notebooks/
make quality            # clean + quality-main + quality-utils
make test               # clean + pytest tests   (NOTE: no tests/ directory exists yet)
make validate           # quality + test
```

Setup (Pipenv is the recommended path; `requirements.txt` is the pip/venv fallback):

```bash
pipenv install && pipenv shell
python -m playwright install chrome   # required once — Phase 1/2 drive real Chrome, not bundled Chromium
```

Requires Python 3.11+ (Pipfile pins 3.13) and Chrome installed on the system.

## Configuration

Settings are loaded via `config.py` (pydantic-settings) from a `.env` file in the project root. Two variables are required and there is no fallback if they're missing — `Settings()` will raise at import time:

```
STOCKX_TOKEN=Bearer eyJhbGci...     # StockX GraphQL JWT, expires every 12h
URL_LIST_NAME=lista1_urls.txt       # filename (not path) read from docs/
```

`utils/scraper.py` checks the token's expiry (decoded client-side from the JWT) before starting and exits with `❌ TOKEN EXPIRADO` if expired — get a fresh one from StockX DevTools → Network → filter `graphql` → copy the `authorization` header.

## Architecture

### Scraping pipeline (3 phases, run in sequence)

`main.py` and `serial_downloads.py` are orchestrators that shell out to each phase script via `subprocess.run([sys.executable, ...])`, **not** importable modules — phases are independent CLI scripts that read/write shared files on disk, not in-memory state.

1. **`utils/search_urls.py`** — Playwright (real Chrome, persistent context) scrolls a StockX category/brand page and extracts product URLs into `docs/<URL_LIST_NAME>` (merges with existing entries, doesn't overwrite).
2. **`utils/copy_html.py`** — Playwright downloads the fully-rendered HTML of every URL in that list into `html_pages/<slug>.html`. Browser context (cookies/localStorage) persists across runs in `.browser_context/state.json` to reduce bot-detection friction.
3. **`utils/scraper.py`** — for each slug with a downloaded HTML, merges two sources into one record and appends to `data/sneakers_data.json`:
   - **Local HTML** → parses `script#__NEXT_DATA__` (Next.js SSR JSON) for title/brand/traits/description, falling back to the `data-component="ProductDetails"` DOM section if that's absent.
   - **StockX GraphQL API** (`fetchSalesGraph`) → the historical price time series, fetched with `curl_cffi` (`impersonate="chrome"`) to pass TLS fingerprinting, not `requests`.

   Each slug is written to disk immediately after processing (not batched at the end), so the pipeline is safely resumable — re-run any phase with `--skip-existing` to skip work already done. `--limit N` caps any phase to the first N items for dry runs. After a successful `scraper.py` run, the URL list file is truncated (cleared) so a re-run of the full pipeline doesn't reprocess everything from scratch.

   All three scripts share anti-detection conventions: randomized human-like delays (Gaussian or uniform depending on script), rotated User-Agents, `navigator.webdriver` masking via injected init scripts, and rotated `x-stockx-device-id`/`x-stockx-session-id` headers per request. Chrome always runs headed (`headless=False`), never headless — headless mode is more easily fingerprinted by StockX.

`serial_downloads.py` is a separate, standalone batch driver (not invoked by `main.py`) that loops `main.py --url ...` across a hardcoded list of brand/page URL combinations for bulk catalog crawling.

### Notebook pipeline (run in order, each depends on the previous output)

1. `notebooks/Data_Cleaning.ipynb`: `data/sneakers_data.json` → quality filtering, imputation, feature engineering → `data/clean_data.pkl`.
2. `notebooks/EDA_1.ipynb`: reads `clean_data.pkl`, exploratory analysis only (no output artifact consumed downstream).
3. `notebooks/model_training.ipynb`: feature selection (mutual information + Pearson, then Sequential Feature Selection with bootstrap stability analysis), trains the deployed Logistic Regression model (L1, `liblinear`) plus an experimental, non-deployed "Model V2" with rolling market-regime features. Maintains **two parallel preprocessed datasets** in parallel throughout — `df_modelo` (target-encoded, for tree models) and `df_lin_model` (one-hot + cyclical-encoded, for linear/KNN models) — because tree-based and linear models need different categorical/temporal encodings.
4. `notebooks/using_the_model.ipynb`: reloads `data/ready_data.pkl` + `data/ref_test_lin.pkl`, refits the same preprocessing + final model, and serializes a deployment bundle (`notebooks/bundle_above_retail.pkl`, via `joblib`) containing the fitted model, scaler, final feature list, and precomputed brand historical rates. Includes a CLI-style interactive predictor as reference for serving the model outside the notebook.

Critical invariant when touching modeling code: **the target is `is_above_retail_90d`, not `is_above_retail`.** The latter compares against whatever the current price is regardless of sneaker age and is explicitly excluded from features (along with `highest_value`, `roi_pct`, `current_value`, `days_to_peak`, `lowest_value_post_release`, `hype_decay_pct`, `price_volatility`, `days_since_release`, `value_at_horizon`) because those are all computed from post-release data unavailable at prediction time — using them would leak the future into training. Train/test splitting is chronological (sorted by `release_date`, last 20% held out), never randomly shuffled, since random splitting would leak future market conditions into the training set.

### Data flow summary

```
docs/<URL_LIST_NAME> → html_pages/*.html → data/sneakers_data.json
  → data/clean_data.pkl → data/ready_data.pkl + data/ref_test_lin.pkl
  → notebooks/bundle_above_retail.pkl (deployable artifact)
```
