"""Prediction logic for the resale-above-retail model. No Streamlit imports here."""
import os
from pathlib import Path

import joblib
import pandas as pd

DEFAULT_BUNDLE_PATH_LOCAL = (
    Path(__file__).resolve().parent.parent / "notebooks" / "bundle_above_retail.pkl"
)

DEFAULT_SCALER_PATH_LOCAL = (
    Path(__file__).resolve().parent.parent / "notebooks" / "scaler_lin.pkl"
)

BRAND_RATES = {
    "Jordan": 0.82,
    "Nike": 0.61,
    "Yeezy": 0.74,
    "Adidas": 0.53,
    "New Balance": 0.58,
    "Puma": 0.41,
    "ASICS": 0.44,
    "Onitsuka": 0.39,
    "Vans": 0.30,
    "Other": 0.45,
}
DEFAULT_UMBRAL = 0.43


def resolve_bundle_path() -> Path:
    return Path(os.environ.get("MODEL_BUNDLE_PATH", str(DEFAULT_BUNDLE_PATH_LOCAL)))

def resolve_scaler_path() -> Path:
    return Path(os.environ.get("SCALER_PATH", str(DEFAULT_SCALER_PATH_LOCAL)))


def load_bundle(path: str | None = None) -> dict:
    bundle_path = Path(path) if path else resolve_bundle_path()
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"Model bundle not found at {bundle_path}. "
            "Set the MODEL_BUNDLE_PATH environment variable to the correct location."
        )
    bundle = joblib.load(bundle_path)
    variables = bundle["variables"]
    scaler = bundle["scaler"]
    return {
        "modelo": bundle["modelo"],
        "scaler": scaler,
        "variables": variables,
        "cols_scale_lin": [v for v in variables if v != "brand_Jordan"],
        "fit_cols": list(scaler.feature_names_in_),
        "umbral": bundle.get("umbral", DEFAULT_UMBRAL),
        "brand_rates": bundle.get("brand_historical_rates", BRAND_RATES),
    }


def predict(
    retail_price: float,
    pre_release_peak: float,
    marca: str,
    num_pre_release_points: int,
    bundle: dict,
) -> dict:
    modelo = bundle["modelo"]
    scaler_lin = bundle["scaler"]
    variables = bundle["variables"]
    cols_scale_lin = bundle["cols_scale_lin"]
    brand_rates = bundle["brand_rates"]
    umbral = bundle["umbral"]


    pre_release_premium_pct = (
        ((pre_release_peak - retail_price) / retail_price * 100)
        if retail_price > 0 and pre_release_peak > 0
        else 0.0
    )
    brand_historical_rate = brand_rates.get(marca, brand_rates["Other"])
    brand_Jordan = int(marca == "Jordan")

    input_dict = {
        "retail_price": retail_price,
        "pre_release_premium_pct": pre_release_premium_pct,
        "brand_historical_rate": brand_historical_rate,
        "num_pre_release_points": num_pre_release_points,
        "brand_Jordan": brand_Jordan,
    }

    X_num = pd.DataFrame([input_dict])[cols_scale_lin]
    X_num_scaled = pd.DataFrame(scaler_lin.transform(X_num), columns=cols_scale_lin)

    X_raw = pd.DataFrame([input_dict])
    X_final = pd.concat(
        [X_num_scaled, X_raw[["brand_Jordan"]]], axis=1
    )[variables]

    proba = float(modelo.predict_proba(X_final.values)[0, 1])
    pred_bin = int(proba >= umbral)

    if proba >= 0.85:
        confidence, ranking = "VERY HIGH", "Top candidate"
    elif proba >= 0.70:
        confidence, ranking = "HIGH", "Medium candidate"
    elif proba >= 0.60:
        confidence, ranking = "MEDIUM", "Medium candidate"
    elif proba >= 0.50:
        confidence, ranking = "MEDIUM", "Discard"
    else:
        confidence, ranking = "LOW", "Discard"

    return {
        "proba": proba,
        "pred_bin": pred_bin,
        "umbral": umbral,
        "confidence": confidence,
        "ranking": ranking,
        "pre_release_premium_pct": pre_release_premium_pct,
        "brand_historical_rate": brand_historical_rate,
    }
