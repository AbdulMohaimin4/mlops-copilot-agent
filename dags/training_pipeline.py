"""Training DAG: validate -> preprocess -> train -> evaluate & log to MLflow -> quality gate.

Each task wraps a stage from `training.train`. Deterministic failures (bad data, failed
gate) raise AirflowFailException so they fail immediately without retries; transient
failures (e.g. MLflow briefly unreachable) are retried. Because the gate is the last
task and only it registers the model, a failed validation or gate means no promotion.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

from training import train

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(os.getenv("PROJECT_DIR", "/opt/airflow/project"))
# LocalExecutor runs every task in the scheduler container, so /tmp is shared between tasks.
WORK_ROOT = Path("/tmp/training-pipeline")


def _work_dir() -> Path:
    return WORK_ROOT / get_current_context()["run_id"].replace(":", "_").replace("+", "_")


@dag(
    dag_id="training_pipeline",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(seconds=20)},
    params={"model_type": "logreg"},
    tags=["ml", "training"],
    doc_md=__doc__,
)
def training_pipeline() -> None:
    @task
    def validate_dataset() -> dict[str, Any]:
        try:
            return train.validate_dataset(PROJECT_DIR / train.DATA_FILE)
        except train.DataValidationError as exc:
            raise AirflowFailException(f"data validation failed: {exc}") from exc

    @task
    def preprocess(report: dict[str, Any]) -> dict[str, str]:
        logger.info("preprocessing %d validated rows", report["rows"])
        return train.preprocess(PROJECT_DIR / train.DATA_FILE, _work_dir())

    @task
    def train_model(split: dict[str, str]) -> dict[str, Any]:
        model_type = get_current_context()["params"]["model_type"]
        return train.train_model(Path(split["train"]), model_type, _work_dir())

    @task
    def evaluate_and_log(trained: dict[str, Any], split: dict[str, str]) -> dict[str, Any]:
        context = get_current_context()
        provenance = train.collect_provenance(PROJECT_DIR, PROJECT_DIR / train.DATA_FILE)
        airflow_tags = {"trigger": "airflow", "airflow_run_id": context["run_id"]}
        return train.evaluate_and_log(
            Path(trained["model_path"]),
            Path(split["test"]),
            trained["params"],
            provenance,
            airflow_tags,
        )

    @task
    def quality_gate(logged: dict[str, Any]) -> dict[str, Any]:
        threshold = float(os.getenv("QUALITY_GATE_F1", "0.90"))
        try:
            return train.apply_quality_gate(logged["run_id"], threshold)
        except train.QualityGateError as exc:
            raise AirflowFailException(str(exc)) from exc

    split = preprocess(validate_dataset())
    trained = train_model(split)
    quality_gate(evaluate_and_log(trained, split))


training_pipeline()
