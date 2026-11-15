"""Structure and failure-handling tests for the training DAG.

Requires Airflow, so it runs inside the Airflow image (see README / CI `dag-tests` job);
the default environment skips it because Airflow cannot share an env with the agent API.
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("airflow.models", reason="runs inside the Airflow image")

from airflow.models import DagBag  # noqa: E402
from airflow.sdk.exceptions import AirflowFailException  # noqa: E402

from training import train  # noqa: E402

EXPECTED_ORDER = [
    "validate_dataset",
    "preprocess",
    "train_model",
    "evaluate_and_log",
    "quality_gate",
]


@pytest.fixture(scope="module")
def dag() -> Any:
    bag = DagBag(dag_folder=str(Path(__file__).parents[1] / "dags"))
    assert bag.import_errors == {}
    return bag.get_dag("training_pipeline")


def test_stages_run_in_required_order(dag: Any) -> None:
    for upstream, downstream in zip(EXPECTED_ORDER, EXPECTED_ORDER[1:], strict=False):
        assert downstream in dag.get_task(upstream).downstream_task_ids
    assert set(dag.task_ids) == set(EXPECTED_ORDER)


def test_tasks_retry_and_dag_is_manual(dag: Any) -> None:
    assert dag.schedule is None
    assert all(task.retries == 2 for task in dag.tasks)


@pytest.mark.parametrize(
    ("task_id", "stage", "error", "kwargs"),
    [
        ("validate_dataset", "validate_dataset", train.DataValidationError("bad"), {}),
        (
            "quality_gate",
            "apply_quality_gate",
            train.QualityGateError("low f1"),
            {"logged": {"run_id": "r"}},
        ),
    ],
)
def test_deterministic_failures_fail_fast_without_retry(
    dag: Any,
    monkeypatch: pytest.MonkeyPatch,
    task_id: str,
    stage: str,
    error: Exception,
    kwargs: Any,
) -> None:
    def fail(*_: Any) -> None:
        raise error

    monkeypatch.setattr(train, stage, fail)
    with pytest.raises(AirflowFailException):
        dag.get_task(task_id).python_callable(**kwargs)
