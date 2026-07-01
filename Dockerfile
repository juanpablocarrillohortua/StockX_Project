FROM python:3.13-slim

WORKDIR /app

COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

COPY app/ ./app/
COPY app/bundle_above_retail.pkl ./model/bundle_above_retail.pkl
COPY app/scaler_lin.pkl ./model/scaler_lin.pkl

ENV MODEL_BUNDLE_PATH=/app/model/bundle_above_retail.pkl \
    SCALER_PATH=/app/model/scaler_lin.pkl \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

CMD ["streamlit", "run", "app/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
