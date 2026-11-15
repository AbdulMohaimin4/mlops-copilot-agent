"""Deterministic retraining policy, exposed to the LLM as a tool.

Retraining is recommended only on explicit evidence: a failed quality gate, a model
older than the freshness window, a newer dataset version than the one it was trained
on, or degradation results supplied by the user. It never triggers anything.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from agent.config import load_settings
from agent.tools.common import Tool


class RetrainingEvidence(BaseModel):
    model_created_at: datetime | None = Field(
        default=None, description="When the evaluated model/run was created (ISO 8601)."
    )
    latest_quality_gate: Literal["passed", "failed", "unknown"] = Field(
        default="unknown", description="Gate result of the most recent training run."
    )
    model_data_version: str | None = Field(
        default=None, description="Dataset version the model was trained on, e.g. data-v1."
    )
    current_data_version: str | None = Field(
        default=None, description="Dataset version currently in the repository, e.g. data-v2."
    )
    degradation_evidence: str | None = Field(
        default=None, description="User-supplied model degradation results, if any."
    )


def evaluate_retraining_need(args: RetrainingEvidence) -> dict[str, Any]:
    """Apply the retraining policy (failed quality gate, model older than the freshness window,
    stale dataset version, or supplied degradation results) to evidence gathered from other
    tools. Recommends only; never triggers retraining. Unknown inputs are reported, not guessed."""
    settings = load_settings()
    reasons, unknown = [], []

    if args.latest_quality_gate == "failed":
        reasons.append("the most recent training run failed the quality gate")
    elif args.latest_quality_gate == "unknown":
        unknown.append("latest quality-gate result")

    age_days = None
    if args.model_created_at is None:
        unknown.append("model age")
    else:
        created = args.model_created_at
        created = created if created.tzinfo else created.replace(tzinfo=UTC)
        age_days = round((datetime.now(UTC) - created).total_seconds() / 86400, 2)
        if age_days > settings.freshness_max_days:
            reasons.append(
                f"model is {age_days} days old, beyond the {settings.freshness_max_days}-day window"
            )

    if args.model_data_version and args.current_data_version:
        if args.model_data_version != args.current_data_version:
            reasons.append(
                f"model trained on {args.model_data_version} but {args.current_data_version} "
                "is current"
            )
    else:
        unknown.append("data version comparison")

    if args.degradation_evidence:
        reasons.append(f"user-supplied degradation evidence: {args.degradation_evidence}")

    return {
        "retraining_recommended": bool(reasons),
        "reasons": reasons,
        "unknown_inputs": unknown,
        "model_age_days": age_days,
        "policy": {
            "quality_gate_f1": settings.quality_gate_f1,
            "freshness_max_days": settings.freshness_max_days,
            "auto_trigger": False,
        },
    }


TOOLS = [Tool.from_function(evaluate_retraining_need)]
