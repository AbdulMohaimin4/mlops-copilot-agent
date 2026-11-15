# Airflow 3 image with the project's training dependencies (versions match pyproject.toml).
FROM apache/airflow:3.3.2-python3.12

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

USER airflow
RUN pip install --no-cache-dir \
    "apache-airflow==3.3.2" \
    mlflow-skinny==3.18.0 \
    skops==0.16.0 \
    joblib==1.6.0 \
    scikit-learn==1.9.1
