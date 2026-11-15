"""Tests for the deterministic retraining policy."""

from datetime import UTC, datetime, timedelta

from agent.policy import RetrainingEvidence, evaluate_retraining_need


def evaluate(**kwargs: object) -> dict[str, object]:
    return evaluate_retraining_need(RetrainingEvidence.model_validate(kwargs))


def test_fresh_model_on_current_data_with_passed_gate_needs_no_retraining() -> None:
    result = evaluate(
        model_created_at=datetime.now(UTC) - timedelta(days=1),
        latest_quality_gate="passed",
        model_data_version="data-v2",
        current_data_version="data-v2",
    )
    assert result["retraining_recommended"] is False
    assert result["unknown_inputs"] == []
    assert result["policy"]["auto_trigger"] is False  # type: ignore[index]


def test_each_policy_trigger_is_reported_as_a_reason() -> None:
    result = evaluate(
        model_created_at=datetime.now(UTC) - timedelta(days=30),
        latest_quality_gate="failed",
        model_data_version="data-v1",
        current_data_version="data-v2",
        degradation_evidence="F1 dropped to 0.81 on March data",
    )
    reasons = " | ".join(result["reasons"])  # type: ignore[arg-type]
    assert result["retraining_recommended"] is True
    for fragment in ("failed the quality gate", "beyond the 7-day window", "data-v1", "0.81"):
        assert fragment in reasons


def test_missing_evidence_is_reported_not_assumed() -> None:
    result = evaluate()
    assert result["retraining_recommended"] is False
    assert set(result["unknown_inputs"]) == {  # type: ignore[arg-type]
        "latest quality-gate result",
        "model age",
        "data version comparison",
    }
