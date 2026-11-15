"""Training pipeline stages, shared by the Airflow DAG and the local CLI.

Stages: validate -> preprocess -> train -> evaluate & log to MLflow -> quality gate.
Every MLflow run is tagged with git commit and DVC data provenance; only a run that
passes the quality gate is registered and given the `candidate` alias.
"""

import argparse
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import joblib
import mlflow
import mlflow.sklearn
import pandas as pd
import yaml
from mlflow import MlflowClient
from sklearn.base import ClassifierMixin
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger("training")

TARGET = "target"
EXPECTED_FEATURES = 30
MIN_ROWS = 100
SEED = 42
DATA_FILE = "data/breast_cancer.csv"
CANDIDATE_ALIAS = "candidate"
# skops refuses to save tree internals unless explicitly trusted; we built these models ourselves.
SKOPS_TRUSTED_TYPES = {"rf": ["sklearn.tree._tree.Tree"]}


class DataValidationError(Exception):
    """The dataset failed validation; training must not proceed."""


class QualityGateError(Exception):
    """The trained model did not meet the quality bar; it must not be promoted."""


def validate_dataset(data_path: Path) -> dict[str, Any]:
    """Check schema, missing values, label values and size; raise DataValidationError on failure."""
    frame = pd.read_csv(data_path)
    problems = []
    if TARGET not in frame.columns:
        problems.append(f"missing target column '{TARGET}'")
    features = [column for column in frame.columns if column != TARGET]
    if len(features) != EXPECTED_FEATURES:
        problems.append(f"expected {EXPECTED_FEATURES} feature columns, found {len(features)}")
    non_numeric = [
        column for column in frame.columns if not pd.api.types.is_numeric_dtype(frame[column])
    ]
    if non_numeric:
        problems.append(f"non-numeric columns: {non_numeric}")
    if int(frame.isna().sum().sum()) > 0:
        problems.append(f"{int(frame.isna().sum().sum())} missing values")
    if TARGET in frame.columns and not set(frame[TARGET].unique()) <= {0, 1}:
        problems.append("target must be binary 0/1")
    if len(frame) < MIN_ROWS:
        problems.append(f"only {len(frame)} rows, need at least {MIN_ROWS}")
    if problems:
        raise DataValidationError("; ".join(problems))
    positive_rate = float(frame[TARGET].mean())
    report = {"rows": len(frame), "features": len(features), "positive_rate": positive_rate}
    logger.info("dataset valid: %s", report)
    return report


def preprocess(data_path: Path, work_dir: Path) -> dict[str, str]:
    """Drop duplicate rows and write a stratified 80/20 train/test split to `work_dir`.

    Feature scaling lives inside the model pipeline so training and serving cannot drift.
    """
    frame = pd.read_csv(data_path).drop_duplicates()
    train, test = train_test_split(frame, test_size=0.2, random_state=SEED, stratify=frame[TARGET])
    work_dir.mkdir(parents=True, exist_ok=True)
    paths = {"train": str(work_dir / "train.csv"), "test": str(work_dir / "test.csv")}
    train.to_csv(paths["train"], index=False)
    test.to_csv(paths["test"], index=False)
    logger.info("split %d rows into %d train / %d test", len(frame), len(train), len(test))
    return paths


def build_model(model_type: str) -> tuple[Pipeline, dict[str, Any]]:
    """Return an untrained sklearn pipeline and its hyperparameters."""
    estimator: ClassifierMixin
    if model_type == "logreg":
        params: dict[str, Any] = {"model_type": "logreg", "C": 1.0, "max_iter": 1000}
        estimator = LogisticRegression(C=params["C"], max_iter=params["max_iter"])
    elif model_type == "rf":
        params = {"model_type": "rf", "n_estimators": 200, "max_depth": 6}
        estimator = RandomForestClassifier(
            n_estimators=params["n_estimators"], max_depth=params["max_depth"], random_state=SEED
        )
    else:
        raise ValueError(f"unknown model type: {model_type}")
    return make_pipeline(StandardScaler(), estimator), params


def train_model(train_path: Path, model_type: str, work_dir: Path) -> dict[str, Any]:
    """Fit the model on the training split and save it with joblib."""
    train = pd.read_csv(train_path)
    model, params = build_model(model_type)
    model.fit(train.drop(columns=[TARGET]), train[TARGET])
    model_path = work_dir / f"{model_type}.joblib"
    joblib.dump(model, model_path)
    logger.info("trained %s on %d rows", model_type, len(train))
    return {"model_path": str(model_path), "params": params}


def _locked_md5s(lock: dict[str, Any]) -> dict[str, str]:
    """Map each DVC output path in a parsed dvc.lock to its md5."""
    outs = [out for stage in lock.get("stages", {}).values() for out in stage.get("outs", [])]
    return {out["path"]: out["md5"] for out in outs}


def collect_provenance(project_dir: Path, data_path: Path) -> dict[str, str]:
    """Git commit and DVC data version of the exact file used for training."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=project_dir, capture_output=True, text=True, timeout=30, check=False
        ).stdout.strip()

    data_md5 = hashlib.md5(data_path.read_bytes()).hexdigest()
    locked_md5 = _locked_md5s(yaml.safe_load((project_dir / "dvc.lock").read_text())).get(DATA_FILE)
    tag = git("describe", "--tags", "--match", "data-v*", "--abbrev=0")
    tag_lock = yaml.safe_load(git("show", f"{tag}:dvc.lock") or "{}") if tag else {}
    tag_md5 = _locked_md5s(tag_lock).get(DATA_FILE)
    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": str(bool(git("status", "--porcelain", "--untracked-files=no"))).lower(),
        "data_path": DATA_FILE,
        "data_md5": data_md5,
        "data_matches_dvc_lock": str(locked_md5 == data_md5).lower(),
        "data_version": tag if tag and tag_md5 == data_md5 else "untagged",
    }


def evaluate_and_log(
    model_path: Path,
    test_path: Path,
    params: dict[str, Any],
    provenance: dict[str, str],
    extra_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Score the model on the test split and log params, metrics, tags and the model to MLflow."""
    model = joblib.load(model_path)
    test = pd.read_csv(test_path)
    features, labels = test.drop(columns=[TARGET]), test[TARGET]
    predictions = model.predict(features)
    metrics = {
        "f1": float(f1_score(labels, predictions)),
        "accuracy": float(accuracy_score(labels, predictions)),
        "precision": float(precision_score(labels, predictions)),
        "recall": float(recall_score(labels, predictions)),
        "roc_auc": float(roc_auc_score(labels, model.predict_proba(features)[:, 1])),
        "test_rows": float(len(test)),
    }
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT", "breast-cancer-classification"))
    with mlflow.start_run(run_name=f"{params['model_type']}-{provenance['data_version']}") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        mlflow.set_tags({**provenance, **(extra_tags or {}), "quality_gate": "pending"})
        info = mlflow.sklearn.log_model(
            model,
            name="model",
            input_example=features.head(3),
            skops_trusted_types=SKOPS_TRUSTED_TYPES.get(params["model_type"]),
        )
        mlflow.set_tag("model_uri", info.model_uri)
    logger.info("logged run %s with f1=%.4f", run.info.run_id, metrics["f1"])
    return {"run_id": run.info.run_id, "metrics": metrics, "model_uri": info.model_uri}


def apply_quality_gate(run_id: str, threshold: float) -> dict[str, Any]:
    """Pass only if test F1 >= threshold; a pass registers the model with alias `candidate`.

    A failure tags the run and raises QualityGateError, so nothing is promoted.
    """
    client = MlflowClient()
    run = client.get_run(run_id)
    f1 = run.data.metrics["f1"]
    passed = f1 >= threshold
    client.set_tag(run_id, "quality_gate", "passed" if passed else "failed")
    client.set_tag(run_id, "quality_gate_threshold", str(threshold))
    if not passed:
        raise QualityGateError(f"f1={f1:.4f} below threshold {threshold}; promotion blocked")
    model_name = os.getenv("MODEL_NAME", "breast-cancer-classifier")
    provenance_keys = ("git_commit", "data_version", "data_md5")
    version = mlflow.register_model(
        run.data.tags["model_uri"],
        model_name,
        tags={key: run.data.tags[key] for key in provenance_keys} | {"run_id": run_id},
    )
    client.set_registered_model_alias(model_name, CANDIDATE_ALIAS, version.version)
    logger.info(
        "gate passed (f1=%.4f); %s v%s -> @%s", f1, model_name, version.version, CANDIDATE_ALIAS
    )
    return {"passed": True, "f1": f1, "model_name": model_name, "version": version.version}


def run_pipeline(model_type: str, project_dir: Path, threshold: float) -> dict[str, Any]:
    """Run all stages in order, as the DAG does, in a temporary working directory."""
    data_path = project_dir / DATA_FILE
    validate_dataset(data_path)
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        split = preprocess(data_path, work_dir)
        trained = train_model(Path(split["train"]), model_type, work_dir)
        logged = evaluate_and_log(
            Path(trained["model_path"]),
            Path(split["test"]),
            trained["params"],
            collect_provenance(project_dir, data_path),
            {"trigger": "cli"},
        )
    return {**logged, "gate": apply_quality_gate(logged["run_id"], threshold)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=["logreg", "rf"], required=True)
    parser.add_argument("--project-dir", type=Path, default=Path("."))
    parser.add_argument(
        "--threshold", type=float, default=float(os.getenv("QUALITY_GATE_F1", "0.90"))
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    try:
        result = run_pipeline(args.model, args.project_dir.resolve(), args.threshold)
    except (DataValidationError, QualityGateError) as exc:
        logger.error("pipeline stopped: %s", exc)
        return 1
    print(yaml.safe_dump(result, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
