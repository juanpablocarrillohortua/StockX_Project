"""Streamlit app for the StockX resale-above-retail predictor."""
import streamlit as st

from inference import BRAND_RATES, load_bundle, predict

st.set_page_config(page_title="StockX Above-Retail Predictor", page_icon="\U0001F45F", layout="wide")

st.title("\U0001F45F StockX Resale-Above-Retail Predictor")
st.caption(
    "Predicts the probability that a sneaker's resale price will exceed its "
    "retail price 90 days after launch. Model: Logistic Regression (L1, 5 features)."
)


@st.cache_resource(show_spinner="Loading model...")
def get_bundle():
    return load_bundle()


try:
    bundle = get_bundle()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()

with st.sidebar:
    st.header("Sneaker details")
    marca = st.selectbox("Brand", options=list(BRAND_RATES.keys()), index=0)
    retail_price = st.number_input("Retail price ($)", min_value=0.0, value=160.0, step=5.0)
    pre_release_peak = st.number_input(
        "Pre-release peak price ($)",
        min_value=0.0,
        value=0.0,
        step=5.0,
        help="Highest resale price observed before the official release date. "
        "Leave at 0 if there was no pre-release speculation.",
    )
    num_pre_release_points = st.number_input(
        "Number of pre-release price records",
        min_value=0,
        value=0,
        step=1,
        help="How many resale price observations exist before the release date.",
    )
    submitted = st.button("Predict", type="primary", use_container_width=True)

if submitted:
    result = predict(retail_price, pre_release_peak, marca, int(num_pre_release_points), bundle)

    col1, col2 = st.columns(2)
    with col1:
        st.metric("Probability above retail (90d)", f"{result['proba']:.1%}")
        st.progress(result["proba"])
        label = "ABOVE RETAIL" if result["pred_bin"] else "AT / BELOW RETAIL"
        st.write(f"**Prediction (threshold {result['umbral']:.2f}):** {label}")
    with col2:
        st.metric("Confidence tier", result["confidence"])
        st.metric("Suggested ranking", result["ranking"])

    with st.expander("Computed inputs (derived features)"):
        st.write(f"- `pre_release_premium_pct`: {result['pre_release_premium_pct']:.1f}%")
        st.write(f"- `brand_historical_rate` ({marca}): {result['brand_historical_rate']:.2f}")
else:
    st.info("Fill in the sneaker details in the sidebar and click **Predict**.")

with st.expander("About this model"):
    st.markdown(
        "- Test-set precision 0.70, recall 0.44, ROC-AUC 0.75 "
        "(818 sneakers, chronological holdout).\n"
        "- Strongest used as a **ranking signal** across many candidates, "
        "not as a single yes/no verdict.\n"
        "- See the project README's *Results at a Glance* section for full details."
    )
