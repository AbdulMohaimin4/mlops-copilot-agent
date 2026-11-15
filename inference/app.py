"""FastAPI inference service deployed to Kubernetes.

Liveness (`/health`) only says the process is up; readiness (`/ready`) says the model is
loaded, so Kubernetes routes traffic only to pods that can actually predict.
"""

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
import skops.io
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("inference")

MODEL_DIR = Path(os.getenv("MODEL_DIR", "inference/model"))
# Only the tree type our own RF models need; anything else in the file is refused.
TRUSTED_TYPES = ["sklearn.tree._tree.Tree"]

state: dict[str, Any] = {"model": None, "info": {}, "error": None}


class PredictRequest(BaseModel):
    instances: list[dict[str, float]] = Field(min_length=1, max_length=100)


def load_model() -> None:
    """Load the skops model and its provenance; record the error instead of crashing."""
    try:
        state["model"] = skops.io.load(MODEL_DIR / "model.skops", trusted=TRUSTED_TYPES)
        state["info"] = json.loads((MODEL_DIR / "model_info.json").read_text())
        state["error"] = None
        logger.info("loaded model %s", state["info"])
    except Exception as exc:  # readiness probe reports this; liveness stays up
        state["model"], state["error"] = None, f"{type(exc).__name__}: {exc}"
        logger.error("model load failed: %s", state["error"])


@asynccontextmanager
async def lifespan(_: FastAPI) -> Any:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    load_model()
    yield


app = FastAPI(title="Breast cancer inference API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process is running."""
    return {"status": "alive"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Readiness: a model is loaded and can serve predictions."""
    if state["model"] is None:
        raise HTTPException(status_code=503, detail=state["error"] or "model not loaded")
    return {"status": "ready"}


@app.get("/version")
def version() -> dict[str, Any]:
    """Application and model version currently served."""
    return {"app_version": os.getenv("APP_VERSION", "unknown"), **state["info"]}


@app.post("/predict")
def predict(request: PredictRequest) -> dict[str, Any]:
    """Predict malignant (0) / benign (1) for each instance of 30 named features."""
    model = state["model"]
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    expected = list(model.feature_names_in_)
    frame = pd.DataFrame(request.instances)
    missing = sorted(set(expected) - set(frame.columns))
    if missing:
        raise HTTPException(status_code=422, detail=f"missing features: {missing[:5]}")
    frame = frame[expected]
    return {
        "predictions": model.predict(frame).tolist(),
        "probability_benign": model.predict_proba(frame)[:, 1].round(4).tolist(),
        "model_version": state["info"].get("model_version"),
    }
