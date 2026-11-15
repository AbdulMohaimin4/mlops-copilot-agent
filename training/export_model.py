"""Export a registered model (default: alias `candidate`) into `inference/model/` for the image.

Writes the MLflow model files plus `model_info.json` (name, version, source run, data
version, git commit) so the running service can report exactly what it serves.
"""

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import mlflow
from mlflow import MlflowClient

EXPORT_DIR = Path("inference/model")


def export(model_name: str, alias: str) -> dict[str, str]:
    """Download the aliased model version and write its provenance next to it."""
    client = MlflowClient()
    version = client.get_model_version_by_alias(model_name, alias)
    shutil.rmtree(EXPORT_DIR, ignore_errors=True)
    mlflow.artifacts.download_artifacts(
        artifact_uri=f"models:/{model_name}/{version.version}", dst_path=str(EXPORT_DIR)
    )
    info = {
        "model_name": model_name,
        "model_version": version.version,
        "alias_at_export": alias,
        "run_id": version.run_id,
        "data_version": version.tags.get("data_version", "unknown"),
        "git_commit": version.tags.get("git_commit", "unknown"),
        "exported_at": datetime.now(UTC).isoformat(),
    }
    (EXPORT_DIR / "model_info.json").write_text(json.dumps(info, indent=2))
    return info


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-name", default=os.getenv("MODEL_NAME", "breast-cancer-classifier"))
    parser.add_argument("--alias", default="candidate")
    args = parser.parse_args()
    print(json.dumps(export(args.model_name, args.alias), indent=2))


if __name__ == "__main__":
    main()
