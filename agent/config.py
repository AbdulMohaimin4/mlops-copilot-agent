"""Agent configuration, read only from environment variables with safe defaults."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Secrets are read where they are used, never stored here."""

    log_level: str
    tool_timeout_seconds: float
    project_dir: Path
    scratch_dir: Path
    mlflow_tracking_uri: str
    mlflow_experiment: str
    model_name: str
    airflow_url: str
    airflow_username: str
    airflow_allowed_dags: frozenset[str]
    k8s_approved_namespace: str
    kube_context: str | None
    agent_model: str
    llm_timeout_seconds: float
    max_tool_rounds: int
    quality_gate_f1: float
    freshness_max_days: int


def load_settings() -> Settings:
    """Build settings from the current environment."""
    return Settings(
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        tool_timeout_seconds=float(os.getenv("TOOL_TIMEOUT_SECONDS", "20")),
        project_dir=Path(os.getenv("PROJECT_DIR", ".")).resolve(),
        scratch_dir=Path(os.getenv("SCRATCH_DIR", "scratch")).resolve(),
        mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"),
        mlflow_experiment=os.getenv("MLFLOW_EXPERIMENT", "breast-cancer-classification"),
        model_name=os.getenv("MODEL_NAME", "breast-cancer-classifier"),
        airflow_url=os.getenv("AIRFLOW_URL", "http://localhost:8080"),
        airflow_username=os.getenv("AIRFLOW_USERNAME", "admin"),
        airflow_allowed_dags=frozenset(
            dag.strip() for dag in os.getenv("AIRFLOW_ALLOWED_DAGS", "training_pipeline").split(",")
        ),
        k8s_approved_namespace=os.getenv("K8S_APPROVED_NAMESPACE", "mlops"),
        kube_context=os.getenv("KUBE_CONTEXT") or None,
        agent_model=os.getenv("AGENT_MODEL") or "gemini-3.5-flash",
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
        max_tool_rounds=int(os.getenv("MAX_TOOL_ROUNDS", "8")),
        quality_gate_f1=float(os.getenv("QUALITY_GATE_F1", "0.90")),
        freshness_max_days=int(os.getenv("FRESHNESS_MAX_DAYS", "7")),
    )
