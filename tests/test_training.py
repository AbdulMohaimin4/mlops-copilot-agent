"""Tests for training stages: data validation and the quality gate that blocks promotion."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from training import train
from training.prepare_data import build_dataset


@pytest.fixture
def dataset_path(tmp_path: Path) -> Path:
    path = tmp_path / "data.csv"
    build_dataset(sample_frac=1.0, seed=42).to_csv(path, index=False)
    return path


def test_valid_dataset_passes(dataset_path: Path) -> None:
    report = train.validate_dataset(dataset_path)
    assert report["rows"] == 569
    assert report["features"] == train.EXPECTED_FEATURES


@pytest.mark.parametrize(
    ("corrupt", "expected"),
    [
        (lambda f: f.drop(columns=["target"]), "missing target column"),
        (lambda f: f.assign(mean_radius=None), "missing values"),
        (lambda f: f.assign(target=f["target"] + 5), "target must be binary"),
        (lambda f: f.head(20), "need at least"),
        (lambda f: f.assign(mean_radius="abc"), "non-numeric"),
    ],
)
def test_invalid_dataset_is_rejected(dataset_path: Path, corrupt: Any, expected: str) -> None:
    corrupt(pd.read_csv(dataset_path)).to_csv(dataset_path, index=False)
    with pytest.raises(train.DataValidationError, match=expected):
        train.validate_dataset(dataset_path)


class FakeClient:
    """Records tag and alias writes made by the quality gate."""

    def __init__(self, f1: float) -> None:
        tags = {"model_uri": "models:/m-1", "git_commit": "abc", "data_version": "data-v2"}
        self.run = SimpleNamespace(
            data=SimpleNamespace(metrics={"f1": f1}, tags=tags | {"data_md5": "x"})
        )
        self.tags: dict[str, str] = {}
        self.aliases: list[tuple[str, str, str]] = []

    def get_run(self, run_id: str) -> Any:
        return self.run

    def set_tag(self, run_id: str, key: str, value: str) -> None:
        self.tags[key] = value

    def set_registered_model_alias(self, name: str, alias: str, version: str) -> None:
        self.aliases.append((name, alias, version))


def _patch_mlflow(monkeypatch: pytest.MonkeyPatch, f1: float) -> tuple[FakeClient, list[Any]]:
    client, registered = FakeClient(f1), []
    monkeypatch.setattr(train, "MlflowClient", lambda: client)

    def register(uri: str, name: str, tags: dict[str, str]) -> Any:
        registered.append((uri, name, tags))
        return SimpleNamespace(version="7")

    monkeypatch.setattr(train.mlflow, "register_model", register)
    return client, registered


def test_quality_gate_failure_blocks_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    client, registered = _patch_mlflow(monkeypatch, f1=0.85)
    with pytest.raises(train.QualityGateError, match="promotion blocked"):
        train.apply_quality_gate("run-1", threshold=0.90)
    assert client.tags["quality_gate"] == "failed"
    assert registered == [] and client.aliases == []


def test_quality_gate_pass_registers_candidate_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, registered = _patch_mlflow(monkeypatch, f1=0.95)
    result = train.apply_quality_gate("run-1", threshold=0.90)
    assert result["passed"] is True
    assert client.tags["quality_gate"] == "passed"
    assert registered[0][2]["data_version"] == "data-v2"
    assert client.aliases == [("breast-cancer-classifier", "candidate", "7")]
