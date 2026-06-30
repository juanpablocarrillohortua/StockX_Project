# StockX Sneaker Analytics

End-to-end pipeline that scrapes sneaker data from StockX, cleans it, performs exploratory analysis, and trains a machine learning model to predict whether a sneaker's resale price will exceed its retail price 90 days after launch.


if you want skip the scraping proccess download the json from [here](https://drive.google.com/file/d/1mnDucHOcMrMPj2X0U5etTqgJz7Bad34J/view?usp=sharing)

---

## Results at a Glance

**Model:** Logistic Regression (L1, 5 features) · **Target:** `is_above_retail_90d` · **Test set:** 818 sneakers, chronological split

| Metric | Score |
|---|---|
| Precision | 0.70 |
| Recall | 0.44 |
| F1 | 0.54 |
| ROC-AUC | 0.75 |
| Accuracy | 0.67 |

```
              precision    recall  f1-score   support
below_retail       0.66      0.85      0.75       460
above_retail       0.70      0.44      0.54       358
    accuracy                           0.67       818
```

**As a ranking signal, the model is significantly stronger.** Sorting predictions by confidence and looking at the real above-retail rate within the top-K:

| K | Above-retail rate in top-K | Lift vs. base rate (43.8%) |
|---|---|---|
| 10 | 90.0% | 2.06x |
| 25 | 96.0% | 2.19x |
| 50 | 94.0% | 2.15x |
| 100 | 85.0% | 1.94x |
| 200 | 71.5% | 1.63x |

**Takeaway:** at the default threshold the model is conservative (high precision, lower recall) — when it predicts "above retail," it's right 70% of the time, but it misses over half of the true above-retail sneakers. Where it really shines is at the top of the ranking: its 25 most confident predictions are correct 96% of the time, making it best suited for "best bets" recommendations rather than blanket classification. See [Decision Threshold Optimization](#decision-threshold-optimization) for how to trade precision for recall depending on use case.

---

## Project Structure

```
StockX_Project/
├── main.py
├── serial_downloads.py            ← Orchestrator: runs the full pipeline
├── config.py
├── utils/
│   ├── search_urls.py             ← Phase 1: extracts URLs from a StockX category
│   ├── copy_html.py               ← Phase 2: downloads the HTML of each product page
│   └── scraper.py                 ← Phase 3: merges HTML + GraphQL API → final JSON
├── notebooks/
│   ├── Data_Cleaning.ipynb        ← Cleaning, imputation, and feature engineering
│   ├── EDA_1.ipynb                ← Univariate and bivariate exploratory analysis
│   ├── model_training.ipynb       ← Feature selection, model training, and evaluation
│   ├── bundle_above_retail.pkl    ← Deployment bundle: trained model + scaler + feature list + brand rates
│   └── using_the_model.ipynb      ← Loads the final model and runs predictions
├── pickle_cache/                  ← Faster notebook runing
│   ├── sfs_train_lin_cache.pkl
│   ├── sfs_stability_train_lin_LogisticRegression.pkl
│   └── sfs_train_modelo_cache.pkl
│
├── docs/
│   └── lista_urls.txt             ← URLs of sneakers to process
├── html_pages/                    ← Downloaded HTMLs (auto-generated)
└── data/
    ├── sneakers_data.json         ← Raw scraper output (auto-generated)
    ├── clean_data.pkl             ← Cleaned dataset ready for modelling
    ├── ready_data.pkl             ← Preprocessed train/test split (model-ready)
    └──  ref_test_lin.pkl          ← Reference columns (title, brand, dates) for the linear test set

```
[download_pkls](https://drive.google.com/drive/folders/1qF40KKqti4V2d5HqIvhFlcwkQMioOX-I?usp=sharing)

---

## Installation

**Requires Python 3.11+** and Google Chrome installed on the system (Chrome is required by `copy_html.py` for browser automation).

Choose one of the two options below.

### Option A — Pipenv (recommended)

```bash
pip install pipenv

# Install dependencies into an isolated virtual environment
pipenv install

# Activate the environment
pipenv shell

# Install Chrome for Playwright (one-time setup)
python -m playwright install chrome
```

To add a new dependency later: `pipenv install <package>`. The `Pipfile.lock` keeps installs reproducible across machines.

### Option B — pip + venv

```bash
# Create the virtual environment
python -m venv .venv

# Activate it
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows (PowerShell: .venv\Scripts\Activate.ps1)

# Install dependencies
pip install -r requirements.txt

# Install Chrome for Playwright (one-time setup)
python -m playwright install chrome
```

To leave the environment at any point: `deactivate`.

---

## Initial Setup

### 1. Authorization Token (expires every 12 hours)

The scraper needs a JWT from your StockX session to access the GraphQL API.

**How to obtain it:**

1. Go to [stockx.com](https://stockx.com) and make sure you are logged in
2. Open DevTools → **Network** tab (`F12` → Network)
3. Filter by `graphql` in the search bar
4. Navigate to any product page
5. Click on any request that appears in Network
6. Under **Request Headers**, copy the value of the `authorization` header (starts with `Bearer eyJ...`)

**How to configure it (pick one option):**

```bash
# Option A — Environment variable (recommended, keeps it out of the code)
(into .env)

STOCKX_TOKEN="Bearer eyJhbGci..."
URL_LIST_NAME=lista1_urls.txt

# Option B — Hardcoded in scraper.py
# Edit the line AUTHORIZATION = "Bearer YOUR_TOKEN_HERE" in utils/scraper.py
```

> **Note:** the token expires after 12 hours. The script detects expiry before starting.
> If you see `❌ TOKEN EXPIRADO`, repeat the steps above.

### 2. URL List

Edit `docs/lista_urls.txt` with the product URLs you want to scrape, one per line. Lines starting with `#` are ignored.

```
# Jordan
https://stockx.com/air-jordan-1-retro-high-og-chicago-2015

# Nike
https://stockx.com/nike-dunk-low-retro-white-black-2021
```

---

## Usage

All commands run from the project root (`StockX_Project/`).

### Full Pipeline (recommended)

Runs all three phases in sequence:

```bash
cd StockX_Project
python serial_downloads.py
```

With options:

```bash
# Change the catalogue URL for Phase 1
python main.py --url https://stockx.com/brands/nike

# Test the pipeline with only 5 products
python main.py --limit 5

# Resume after an interruption (skips already-processed items)
python main.py --skip-existing
```

---

### Phases Individually

If you already have URLs in `lista_urls.txt`, you can skip Phase 1 and run Phases 2 and 3 directly.

#### Phase 2 — Download HTMLs

Opens Chrome automatically and saves each product's HTML to `html_pages/`:

```bash
python utils/copy_html.py
python utils/copy_html.py --skip-existing   # skip already-downloaded pages
python utils/copy_html.py --limit 3         # test with the first 3
```

> Chrome opens in visible mode (not headless) to avoid bot detection.

#### Phase 3 — Merge Data and Generate JSON

Reads local HTMLs and combines them with the price time series from the API:

```bash
python utils/scraper.py
python utils/scraper.py --skip-existing
STOCKX_TOKEN="Bearer eyJ..." python utils/scraper.py
```

---

## JSON Output Format

`data/sneakers_data.json` contains an array where each element has this structure:

```json
{
  "slug": "air-jordan-1-retro-high-og-chicago-2015",
  "url": "https://stockx.com/air-jordan-1-retro-high-og-chicago-2015",
  "scraped_at": "2026-06-12T23:00:00+00:00",

  "product_details": {
    "title": "Air Jordan 1 Retro High OG Chicago 2015",
    "brand": "Jordan",
    "description": "Long product description...",
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

`_meta` duplicates the most-used fields at the root level for easy loading into pandas:

```python
import pandas as pd, json

records = json.load(open("data/sneakers_data.json"))
df = pd.DataFrame([r["_meta"] | {"slug": r["slug"]} for r in records])
```

---

## Notebooks

### `Data_Cleaning.ipynb` — Cleaning & Feature Engineering

#### Input / Output

| | Path |
|---|---|
| Input | `data/sneakers_data.json` |
| Output | `data/clean_data.pkl` |

#### Cleaning Steps

**1. Initial quality filter**
Rows are dropped if `product_details.source == 'not_found'` or `_meta.has_errors == True`. This removes a negligible number of records (~8) where the scraper could not extract product data.

**2. Column selection**
Only four base columns are kept: `sales_series`, `title`, `retail_price`, `release_date`. All other scraper fields are discarded at this stage.

**3. Drop untrackable records**
Rows where `sales_series` is empty AND both `retail_price` and `release_date` are null are removed (~9 rows). These have no recoverable signal.

**4. Impute `release_date` from the time series**
For the ~7% of rows with a missing `release_date` but with a non-empty `sales_series`, the earliest `xValue` in the series is used as a proxy release date. Rows that are missing both `release_date` and `sales_series` are dropped (~74 rows, < 1.5% of the data).

**5. Clean and impute `retail_price`**
The `$` and `,` characters are stripped and the field is cast to `float`. Missing values are filled with `median − std`, a conservative estimate below the median that avoids overestimating unknown prices.

**6. Drop records without price history**
After imputation, any remaining rows with an empty `sales_series` are dropped. A sneaker without a price series cannot contribute to any downstream analysis or modelling.

**7. Type casting and deduplication**
`release_date` is converted to `datetime` with `format='mixed'` to handle varied input formats. Duplicate titles are removed (keep first occurrence): ~N rows dropped.

#### Feature Engineering

All features are computed from `sales_series`, splitting it into a **pre-release window** (dates before `release_date`) and a **post-release window** (dates on or after `release_date`).

| Feature | Formula / Logic |
|---|---|
| `brand` | Regex extraction from `title` for [Nike, Jordan, Adidas, Yeezy, Puma, New Balance, ASICS, Vans, Onitsuka]. Yeezy is detected separately via a `yeezy\|yzy` pattern and takes precedence over Adidas. |
| `highest_value` | `max(yValue)` over the post-release window. |
| `pre_release_peak` | `max(yValue)` over the pre-release window. `0` if no pre-release data exists. |
| `current_value` | `yValue` of the last chronological point in the full series. |
| `roi_pct` | `(highest_value − retail_price) / retail_price × 100` |
| `is_above_retail` | `current_value > retail_price` |
| `pre_release_premium_pct` | `(pre_release_peak − retail_price) / retail_price × 100` |
| `days_to_peak` | `(date_of_highest_value − release_date).days` |
| `lowest_value_post_release` | `min(yValue)` over the post-release window. |
| `hype_decay_pct` | `(highest_value − current_value) / highest_value × 100` |
| `price_volatility` | `std(yValue)` over the post-release window. `0` if fewer than 2 post-release points. |

---

### `EDA_1.ipynb` — Exploratory Analysis

#### Input

`data/clean_data.pkl`

#### Univariate Analysis

Histograms with KDE overlay and paired horizontal boxplots are generated for all numeric variables. Bar charts are used for `brand` and `is_above_retail`. A time-series bar chart shows the annual distribution of release dates.

**Key observation — sample size imbalance:** the dataset is heavily skewed toward recent years (e.g., 1 record in 1990 vs. 1,567 in 2025). Any time-based comparison must account for this to avoid drawing conclusions from statistically unreliable year-averages.

#### Bivariate Analysis

The following relationships are examined:

- **Pearson correlation heatmap** across all 10 numeric variables.
- **Scatter plots** with regression lines for high-interest pairs: `retail_price vs current_value`, `pre_release_peak vs highest_value`, `price_volatility vs roi_pct`, `days_to_peak vs hype_decay_pct`.
- **Grouped boxplots** of `roi_pct` and `price_volatility` by `is_above_retail` and by `brand` (top 10).
- **Stacked proportion bars** showing the `is_above_retail` rate by brand (top 12).
- **Temporal trend lines** (annual mean) for `retail_price` and `roi_pct`, annotated with sample size to distinguish reliable from unreliable points.

#### Key EDA Findings

**Retail price spike in 2016 is a small-sample artifact.** The year 2016 appears to have an unusually high average retail price (~$204) compared to surrounding years. However, this is driven by only 18 records, dominated by Yeezy ($220, n=3) and Jordan ($220, n=4) models — brands with consistently high retail prices. The visual spike disappears when sample size is considered.

**Sneaker hype peaked around 2019–2020 and has been declining since.** Analyzing `roi_pct`, `pre_release_premium_pct`, and `hype_decay_pct` over time (restricted to years with n ≥ 20 and products with ≥ 730 days of market exposure):

- `roi_pct` peaked at ~168% in 2019, falling to ~57% by 2026.
- `pre_release_premium_pct` peaked at ~103% in 2019–2020, falling to ~25% by 2024.
- `hype_decay_pct` fell from ~50% to ~37% in the same period.

The decline in `pre_release_premium_pct` is the most diagnostic signal: it measures speculative pressure *before* release, making it immune to post-release observation-window bias. The convergence of all three metrics points to a genuine market cool-down, rather than a statistical artifact.

> ⚠️ **Censorship bias caveat:** products from recent years have had less time to appreciate, which structurally depresses their ROI and decay metrics relative to older products. Residual analysis (regressing out `days_since_release`) was used to partially control for this effect.

---

### `model_training.ipynb` — Model Training

#### Problem Definition

**Task:** binary classification — predict whether a sneaker's resale price will be above its retail price at 90 days post-launch.

**Target variable:** `is_above_retail_90d` — the price nearest to `release_date + 90 days` compared against `retail_price`.

**Why not use the original `is_above_retail`?** The original variable compares `current_value` (measured *today*, regardless of how long ago the sneaker launched) against retail price. This creates a variable-length observation window: an older sneaker has had years to appreciate, while a new one only days. Using it as a target would introduce systematic bias. The fixed 90-day horizon standardizes the comparison.

**Scope filter:** only sneakers released from 2017 onwards are used (`MIN_YEAR = 2017`). Earlier years have too few records for reliable model learning.

#### Leakage Prevention

The following columns are **explicitly excluded** from features because they are computed from post-release data and would not be available at prediction time (before or at launch):

`highest_value`, `roi_pct`, `current_value`, `is_above_retail` (original), `days_to_peak`, `lowest_value_post_release`, `hype_decay_pct`, `price_volatility`, `days_since_release`, `value_at_horizon`

All engineered features use **only pre-release data** or information available at launch.

#### Feature Engineering (Modeling Dataset)

| Feature | Type | Description |
|---|---|---|
| `retail_price` | float | Official launch price. |
| `brand_grouped` | string | Brand, with rare brands (< 80 records) collapsed into `"Other"`. |
| `brand_historical_rate` | float | % of the brand's *past* launches (before this release date) that stayed above retail at 90 days. Computed sequentially to avoid leakage. |
| `pre_release_peak` | float | Max price before launch (speculation phase). |
| `pre_release_premium_pct` | float | `(pre_release_peak − retail_price) / retail_price × 100` |
| `has_pre_release_speculation` | bool | Whether any pre-release price points exist. |
| `num_pre_release_points` | int | Count of pre-release price observations. |
| `pre_release_volatility` | float | Std of pre-release prices. `0` if fewer than 2 points. |
| `pre_release_trend` | float | Slope of a linear fit (price vs. days) over the pre-release window. |
| `days_speculation_window` | int | Length (days) of the pre-release observation window. |
| `release_year/month/quarter/dow` | int | Calendar features derived from `release_date`. |
| `title_length` | int | Character length of the sneaker title. |
| `is_special_edition` | bool | Inferred from title keywords. |
| `is_collab` | bool | Inferred from title keywords (e.g. "x", "by", "ft"). |

#### Technical Decisions

**Non-normal distributions → RobustScaler.** Lilliefors tests confirm that numeric features are not normally distributed and contain significant outliers (IQR analysis shows 10–30% outlier rates on several features). `RobustScaler` (centers on median, scales by IQR) is used instead of `StandardScaler`.

**Two parallel datasets.** Because tree-based and linear models have different preprocessing requirements, two copies of the data are maintained throughout:

- `df_modelo` → for tree-based models (Random Forest, Gradient Boosting). Uses **target encoding with Bayesian smoothing** (`smoothing=10`) for `brand_grouped`. Categorical temporal features left as integers.
- `df_lin_model` → for linear models (Logistic Regression, KNN). Uses **one-hot encoding** for `brand_grouped` (with `drop_first=True`). Temporal cyclical features (`release_month`, `release_quarter`, `release_dow`) are encoded as sine/cosine pairs to preserve their circular nature. `pre_release_peak` is dropped here due to VIF > 10 (multicollinearity with `pre_release_premium_pct`).

**Temporal train/test split (no random shuffle).** Data is sorted chronologically before splitting. The test set is the last 20% of records by release date. Random splitting would leak future market conditions into training data.

**No PCA or LDA.** Skipped because input features are non-normal (PCA and LDA both assume or benefit from normality).

#### Feature Selection

**Filter method:** mutual information (MI) with the target + Pearson correlation, both MinMax-normalized and averaged into a combined relevance score. An elbow plot and cumulative score analysis determine the top-k cutoff.

**Wrapper method:** Sequential Feature Selection (SFS), both forward and backward, applied to KNN, Logistic Regression, Random Forest, and Gradient Boosting using 5-fold cross-validation scored by ROC-AUC.

**Stability analysis:** SFS + Bootstrap (30 resampled iterations) on the best model (Logistic Regression) to identify features that are consistently selected regardless of the specific training sample. Only stable features are included in the final model.

#### Market Regime Detection & Model V2 (experiment, not deployed)

Analysis of the monthly `is_above_retail_90d` rate revealed a **structural break around January 2023**: the rate dropped from ~60–70% (pre-2023, "bull market") to ~30–40% (post-2023, "bear market"). This shift was confirmed statistically (Chi-squared test, p < 0.05).

To test whether explicitly modeling this regime shift would help, **Model V2** was built with three changes:

1. **New split point:** the cutoff is placed within the post-2023 period (at the 70th percentile of post-2023 dates), ensuring the test set reflects the current bear-market regime.

2. **Rolling market context features:** two new feature families, computed without leakage:
   - `market_rate_{N}d`: global % of sneakers above retail in the past N days (N ∈ {30, 60, 90, 180}).
   - `brand_rate_{N}d`: same metric filtered to the same brand. NaNs (no prior history) imputed with an expanding global mean.
   - The 180-day window showed the highest correlation with the target for both families.

3. **Walk-Forward Cross-Validation:** a custom `WalkForwardCV` class (expanding window, 5 folds, minimum 50% training set) replaces `StratifiedKFold`, preventing temporal leakage during hyperparameter search.

**Result: Model V2 (7 features, rolling market context) underperformed Model V1 (5 features) on the held-out test set.** The added market/brand rolling-rate features did not generalize better despite the more sophisticated validation scheme, so **V1 was kept as the deployed model**. V2 remains in the codebase as a documented experiment.

#### Final Model (V1 — deployed)

**Algorithm:** Logistic Regression (L1 regularization, `liblinear` solver)

**Hyperparameters (selected via GridSearchCV + Stratified CV, optimizing precision):**

| Parameter | Value |
|---|---|
| `C` | 0.1 |
| `penalty` | L1 |
| `solver` | liblinear |
| `class_weight` | balanced |
| `max_iter` | 2000 |

**Final feature set (5 variables):**

| Feature | Rationale |
|---|---|
| `retail_price` | Strong signal; higher retail prices correlate with lower above-retail probability |
| `pre_release_premium_pct` | Most direct measure of pre-launch hype |
| `brand_historical_rate` | Captures brand-level market track record |
| `brand_Jordan` | Jordan brand dummy; consistently selected across bootstrap samples |
| `num_pre_release_points` | Proxy for how closely the market tracked the pre-launch hype |

**Test set evaluation** (818 sneakers, chronological holdout — see [Results at a Glance](#results-at-a-glance) for the summary table):

```
Confusion matrix:
[[392  68]
 [199 159]]
```

Reading the matrix: of 460 sneakers that stayed at or below retail, the model correctly flagged 392 (true negatives) and missed 68 (false positives). Of 358 sneakers that went above retail, it correctly caught 159 (true positives) but missed 199 (false negatives) — the main source of the model's relatively low recall (0.44).

#### Decision Threshold Optimization

Beyond the default 0.5 cutoff, the test-set probability output is swept across thresholds (0.10–0.90) to find operating points aligned with different business priorities:

| Criterion | Logic |
|---|---|
| **A — Max F1** | Threshold that maximizes the harmonic mean of precision and recall. |
| **B — Recall-first** | Highest precision achievable while keeping recall ≥ 0.60 (favors catching most above-retail sneakers, tolerating false positives). |
| **C — Precision-first** | Highest recall achievable while keeping precision ≥ 0.75 (favors confidence in positive predictions, e.g. for purchase recommendations). |

The right threshold depends on use case: a resale-flipping recommendation tool should favor precision (criterion C), while a broad market-monitoring dashboard might favor recall (criterion B).

#### Ranking Quality (Top-K Analysis)

Beyond binary classification, the model's predicted probabilities were evaluated as a **ranking signal**: sorting test-set sneakers by `y_proba` and checking the real above-retail rate within the top-K highest-confidence predictions.

**Test set base rate: 43.8%**

| K | Above-retail rate in top-K | Lift |
|---|---|---|
| 10 | 90.0% | 2.06x |
| 25 | 96.0% | 2.19x |
| 50 | 94.0% | 2.15x |
| 100 | 85.0% | 1.94x |
| 200 | 71.5% | 1.63x |

The model is strongest at the very top of the ranking: its top 25 most-confident predictions are correct 96% of the time (2.19x better than random), making it well suited for "best bets" style recommendations rather than only blanket binary classification. Lift decays gradually as K grows, which is expected — wider nets pull in lower-confidence (riskier) candidates.

---

## Deployment / Inference

### `using_the_model.ipynb` — Using the Trained Model

Loads the cleaned and split data (`data/ready_data.pkl`, `data/ref_test_lin.pkl`), refits the same preprocessing pipeline used in training (RobustScaler on numeric features, one-hot encoding of `brand_grouped`, cyclical encoding of date features), and trains the final V1 Logistic Regression model.

**Deployment bundle.** The trained model, its fitted scaler, the final feature list, and precalculated brand historical rates are packaged into a single artifact for reuse without retraining:

```python
bundle = {
    'modelo': modelo_final,
    'scaler': scaler_lin,
    'variables': variables_finales,
    'brand_historical_rates': {...},  # precomputed per-brand rates from training data
}
joblib.dump(bundle, 'bundle_above_retail.pkl')
```

**Interactive predictor.** The notebook includes a simple CLI-style predictor: the user selects a brand from a list and enters the remaining feature values, the bundle is loaded, inputs are scaled with the saved `scaler`, and the model outputs a probability that the sneaker will trade above retail at 90 days. This is intended as a lightweight reference implementation for serving the model outside the notebook (e.g. wrapping it in a script or API).

---

## Variable Dictionary (Clean Dataset)

| Variable | Type | Description |
|---|---|---|
| `title` | string | Sneaker model name as it appears in the original source. |
| `sales_series` | list[dict] | Price time series: `[{'xValue': iso_date, 'yValue': price}, ...]`. Includes pre- and post-release points. |
| `retail_price` | float | Official retail (launch) price. |
| `release_date` | datetime | Official release date. |
| `brand` | string | Normalized brand (Nike, Jordan, Adidas, Yeezy, Puma, New Balance, ASICS, Vans, Onitsuka). |
| `highest_value` | float | Max price reached **post-release** (speculation excluded). |
| `pre_release_peak` | float | Max price reached **before release**. `0` if no pre-release data. |
| `roi_pct` | float | `(highest_value − retail_price) / retail_price × 100` |
| `current_value` | float | Most recent price in `sales_series` (last chronological point). |
| `is_above_retail` | bool | `current_value > retail_price` |
| `pre_release_premium_pct` | float | `(pre_release_peak − retail_price) / retail_price × 100`. `0` if no pre-release data. |
| `days_to_peak` | int | Days from `release_date` to the date `highest_value` was reached. Always ≥ 0. |
| `lowest_value_post_release` | float | Min price recorded after release. |
| `hype_decay_pct` | float | `(highest_value − current_value) / highest_value × 100`. Higher = larger price collapse from peak. |
| `price_volatility` | float | Std of post-release prices. Measures price stability after launch. |

---

## Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `❌ TOKEN EXPIRADO` | JWT expired (lasts 12h) | Renew token from DevTools |
| `HTML exists but no data` | StockX blocked the download | Delete the `.html` file and re-run `copy_html.py` |
| `HTTP 403` on API | Incorrect or expired token | Verify the token starts with `Bearer eyJ` |
| `HTTP 429` on API | Rate limit hit | Script retries automatically with exponential backoff; wait |
| Chrome does not open | Playwright missing Chrome | Run `python -m playwright install chrome` |
| `_meta` fields are `null` | `__NEXT_DATA__` has a different structure | HTML fallback should cover it; inspect `errors` field in the JSON |

---

## Notes

- The scraper saves the JSON **after each product**, so interruptions don't lose progress. Use `--skip-existing` to resume.
- Request delays are random (Gaussian distribution, ~2–8 seconds) to mimic human behavior and reduce blocking risk.
- `x-stockx-device-id` and `x-stockx-session-id` headers are rotated on each request to avoid fingerprinting.