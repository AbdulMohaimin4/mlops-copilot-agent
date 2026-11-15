# Inference API image. The model is exported into inference/model/ first:
#   uv run python -m training.export_model
FROM python:3.12.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MODEL_DIR=/app/inference/model

RUN pip install --no-cache-dir \
    fastapi==0.163.1 \
    "uvicorn[standard]==0.54.0" \
    pandas==3.1.1 \
    scikit-learn==1.9.1 \
    skops==0.16.0

RUN useradd --uid 10001 --no-create-home appuser
WORKDIR /app
COPY inference/__init__.py inference/app.py inference/
COPY inference/model/ inference/model/

USER 10001
EXPOSE 8000
CMD ["uvicorn", "inference.app:app", "--host", "0.0.0.0", "--port", "8000"]
