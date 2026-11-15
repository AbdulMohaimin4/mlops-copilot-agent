"""MLflow experiment-intelligence tools built only on `MlflowClient` (no web-UI parsing).

Selection logic never equates "highest metric" with "production-ready": the best-run
tool reports the evidence it has (quality gate, registry alias, provenance) and what
it cannot know from MLflow alone (deployment health, human review).
"""

import os
from datetime import UTC, datetime
from typing import Annotated, Any

from mlflow import MlflowClient
from mlflow.entities import Run
from mlflow.exceptions import MlflowException
from pydantic import BaseModel, Field

from agent.config import load_settings
from agent.schemas import NoInput
from agent.tools.common import ServiceUnavailableError, Tool

RUN_ID_PATTERN = r"^[0-9a-f]{32}$"
NAME_PATTERN = r"^[A-Za-z0-9_.\- ]{1,100}$"
RunId = Annotated[str, Field(pattern=RUN_ID_PATTERN)]
ExperimentName = Annotated[str, Field(pattern=NAME_PATTERN)]
PROVENANCE_TAGS = (
    "data_version",
    "data_md5",
    "git_commit",
    "git_dirty",
    "quality_gate",
    "trigger",
    "airflow_run_id",
)


class ListRunsInput(BaseModel):
    experiment_name: str | None = Field(
        default=None,
        pattern=NAME_PATTERN,
        description="Experiment name; default experiment if omitted.",
    )
    max_results: int = Field(default=10, ge=1, le=50, description="Most recent runs to return.")


class RunInput(BaseModel):
    run_id: str = Field(pattern=RUN_ID_PATTERN, description="32-character MLflow run ID.")


class BestRunInput(BaseModel):
    metric: str = Field(
        default="f1", pattern=r"^[A-Za-z0-9_]{1,50}$", description="Metric to rank by."
    )
    experiment_name: str | None = Field(default=None, pattern=NAME_PATTERN)
    max_results: int = Field(
        default=10, ge=1, le=50, description="How many recent runs to consider."
    )
    higher_is_better: bool = True


class CompareRunsInput(BaseModel):
    run_ids: list[RunId] = Field(min_length=2, max_length=10, description="Run IDs to compare.")


class CompareExperimentsInput(BaseModel):
    experiment_names: list[ExperimentName] = Field(min_length=2, max_length=5)
    metric: str = Field(default="f1", pattern=r"^[A-Za-z0-9_]{1,50}$")


class RegistryInput(BaseModel):
    model_name: str | None = Field(
        default=None,
        pattern=NAME_PATTERN,
        description="Registered model name; omit it to use the configured model "
        "(breast-cancer-classifier by default).",
    )


def _client() -> MlflowClient:
    # Fail fast instead of MLflow's default multi-retry backoff; the tool wrapper also times out.
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
    return MlflowClient(tracking_uri=load_settings().mlflow_tracking_uri)


def _call(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Call an MlflowClient method, turning connection failures into ServiceUnavailableError."""
    try:
        return func(*args, **kwargs)
    except MlflowException as exc:
        if "failed with exception" in str(exc) or "Max retries exceeded" in str(exc):
            raise ServiceUnavailableError(f"MLflow server unreachable: {str(exc)[:200]}") from exc
        raise


def _iso(millis: int | None) -> str | None:
    return datetime.fromtimestamp(millis / 1000, UTC).isoformat() if millis else None


def _experiment_id(client: MlflowClient, name: str | None) -> str:
    name = name or load_settings().mlflow_experiment
    experiment = _call(client.get_experiment_by_name, name)
    if experiment is None:
        available = [e.name for e in _call(client.search_experiments)]
        raise ValueError(f"unknown experiment '{name}'; available: {available}")
    return str(experiment.experiment_id)


def _recent_runs(client: MlflowClient, experiment_id: str, max_results: int) -> list[Run]:
    order = ["attributes.start_time DESC"]
    runs: list[Run] = _call(
        client.search_runs, [experiment_id], max_results=max_results, order_by=order
    )
    return runs


def _run_summary(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.info.run_id,
        "run_name": run.info.run_name,
        "status": run.info.status,
        "start_time": _iso(run.info.start_time),
        "model_type": run.data.params.get("model_type"),
        "metrics": {key: round(value, 4) for key, value in run.data.metrics.items()},
        "provenance": {key: run.data.tags.get(key) for key in PROVENANCE_TAGS},
    }


def _registry_entries(client: MlflowClient, run_id: str) -> list[dict[str, Any]]:
    versions = _call(client.search_model_versions, f"run_id='{run_id}'")
    entries = []
    for version in versions:
        model = _call(client.get_registered_model, version.name)
        aliases = [alias for alias, v in model.aliases.items() if v == version.version]
        entries.append({"model": version.name, "version": version.version, "aliases": aliases})
    return entries


def list_experiments(_: NoInput) -> dict[str, Any]:
    """List MLflow experiments with their IDs, names and last update time."""
    experiments = _call(_client().search_experiments)
    return {
        "experiments": [
            {
                "experiment_id": e.experiment_id,
                "name": e.name,
                "lifecycle_stage": e.lifecycle_stage,
                "last_update": _iso(e.last_update_time),
            }
            for e in experiments
        ]
    }


def list_runs(args: ListRunsInput) -> dict[str, Any]:
    """List the most recent training runs of an experiment with status, key metrics, model type
    and recorded provenance (data version, data md5, git commit, quality gate result)."""
    client = _client()
    experiment_id = _experiment_id(client, args.experiment_name)
    runs = _recent_runs(client, experiment_id, args.max_results)
    return {"experiment_id": experiment_id, "runs": [_run_summary(run) for run in runs]}


def get_run_details(args: RunInput) -> dict[str, Any]:
    """Get one run's parameters, metrics, provenance tags (dataset version, data md5, git commit),
    run artifacts, the files of models logged by the run, and any model-registry versions created
    from it."""
    client = _client()
    run = _call(client.get_run, args.run_id)
    artifacts = [a.path for a in _call(client.list_artifacts, args.run_id)]
    # MLflow 3 stores models as separate "logged models", not under the run's own artifacts.
    logged_models = _call(
        client.search_logged_models,
        experiment_ids=[run.info.experiment_id],
        filter_string=f"source_run_id='{args.run_id}'",
    )
    models = [
        {
            "model_id": model.model_id,
            "name": model.name,
            "files": [f.path for f in _call(client.list_logged_model_artifacts, model.model_id)],
        }
        for model in logged_models
    ]
    user_tags = {k: v for k, v in run.data.tags.items() if not k.startswith("mlflow.")}
    return {
        **_run_summary(run),
        "params": run.data.params,
        "tags": user_tags,
        "artifacts": artifacts,
        "logged_models": models,
        "registry": _registry_entries(client, args.run_id),
    }


def find_best_run(args: BestRunInput) -> dict[str, Any]:
    """Rank recent finished runs by a metric (default F1) and explain the choice: margin over the
    runner-up, quality-gate result, registry alias and data provenance. It deliberately does not
    declare the run production-ready; it lists the readiness evidence that exists and what is
    missing."""
    client = _client()
    runs = _recent_runs(client, _experiment_id(client, args.experiment_name), args.max_results)
    eligible = [r for r in runs if r.info.status == "FINISHED" and args.metric in r.data.metrics]
    excluded = [
        {"run_id": r.info.run_id, "reason": f"status {r.info.status}"}
        if r.info.status != "FINISHED"
        else {"run_id": r.info.run_id, "reason": f"metric '{args.metric}' not logged"}
        for r in runs
        if r not in eligible
    ]
    if not eligible:
        return {"best_run": None, "considered": len(runs), "excluded": excluded}

    ranked = sorted(
        eligible, key=lambda r: r.data.metrics[args.metric], reverse=args.higher_is_better
    )
    best = ranked[0]
    best_value = best.data.metrics[args.metric]
    ties = [r.info.run_id for r in ranked[1:] if r.data.metrics[args.metric] == best_value]
    margin = abs(best_value - ranked[1].data.metrics[args.metric]) if len(ranked) > 1 else None
    registry = _registry_entries(client, best.info.run_id)
    gate = best.data.tags.get("quality_gate", "unknown")
    direction = "highest" if args.higher_is_better else "lowest"
    return {
        "best_run": _run_summary(best),
        "ranking": [
            {
                "run_id": r.info.run_id,
                "run_name": r.info.run_name,
                args.metric: round(r.data.metrics[args.metric], 4),
                "quality_gate": r.data.tags.get("quality_gate"),
                "data_version": r.data.tags.get("data_version"),
            }
            for r in ranked
        ],
        "rationale": {
            "selected_by": f"{direction} {args.metric} among {len(eligible)} finished runs "
            f"of the {len(runs)} most recent",
            "margin_over_runner_up": round(margin, 4) if margin is not None else None,
            "tied_with": ties,
            "small_test_set_warning": "margins below ~0.01 F1 are within noise for a test set of "
            f"{int(best.data.metrics.get('test_rows', 0))} rows",
        },
        "production_readiness": {
            "verdict": "not established: a top metric alone does not make a model production-ready",
            "evidence": {
                "quality_gate": gate,
                "registry": registry,
                "data_version": best.data.tags.get("data_version"),
                "code_committed": best.data.tags.get("git_dirty") == "false",
            },
            "missing_evidence": [
                "deployment health of this model version (check Kubernetes)",
                "evaluation on fresh or production data",
                "human review / approval",
            ],
        },
        "excluded": excluded,
    }


def compare_runs(args: CompareRunsInput) -> dict[str, Any]:
    """Compare two or more runs side by side: metrics, parameters, data version and quality gate,
    plus which run is best on each metric."""
    client = _client()
    runs = [_call(client.get_run, run_id) for run_id in args.run_ids]
    metric_names = sorted({name for run in runs for name in run.data.metrics})
    best_per_metric = {
        name: max(
            (r for r in runs if name in r.data.metrics), key=lambda r: r.data.metrics[name]
        ).info.run_id
        for name in metric_names
    }
    return {
        "runs": [_run_summary(run) | {"params": run.data.params} for run in runs],
        "highest_per_metric": best_per_metric,
    }


def compare_experiments(args: CompareExperimentsInput) -> dict[str, Any]:
    """Compare experiments: run counts by status, quality-gate pass rate, data versions used,
    and the best and mean value of a metric across finished runs."""
    client = _client()
    summaries = []
    for name in args.experiment_names:
        runs = _recent_runs(client, _experiment_id(client, name), 200)
        finished = [
            r for r in runs if r.info.status == "FINISHED" and args.metric in r.data.metrics
        ]
        values = [r.data.metrics[args.metric] for r in finished]
        gated = [r for r in finished if r.data.tags.get("quality_gate") in ("passed", "failed")]
        passed = sum(r.data.tags.get("quality_gate") == "passed" for r in gated)
        status_counts: dict[str, int] = {}
        for run in runs:
            status_counts[run.info.status] = status_counts.get(run.info.status, 0) + 1
        best = max(finished, key=lambda r: r.data.metrics[args.metric]) if finished else None
        summaries.append(
            {
                "experiment": name,
                "runs_by_status": status_counts,
                "gate_pass_rate": round(passed / len(gated), 3) if gated else None,
                "data_versions": sorted({r.data.tags.get("data_version", "?") for r in runs}),
                f"best_{args.metric}": round(max(values), 4) if values else None,
                f"mean_{args.metric}": round(sum(values) / len(values), 4) if values else None,
                "best_run_id": best.info.run_id if best else None,
            }
        )
    return {"metric": args.metric, "experiments": summaries}


def get_model_registry_info(args: RegistryInput) -> dict[str, Any]:
    """Get a registered model's aliases (e.g. candidate) and versions with source run, status,
    creation time and provenance tags."""
    client = _client()
    name = args.model_name or load_settings().model_name
    model = _call(client.get_registered_model, name)
    versions = _call(client.search_model_versions, f"name='{name}'")
    return {
        "model": name,
        "aliases": dict(model.aliases),
        "versions": [
            {
                "version": v.version,
                "run_id": v.run_id,
                "status": v.status,
                "created": _iso(v.creation_timestamp),
                "tags": dict(v.tags),
            }
            for v in sorted(versions, key=lambda v: int(v.version), reverse=True)
        ],
    }


TOOLS = [
    Tool.from_function(func)
    for func in (
        list_experiments,
        list_runs,
        get_run_details,
        find_best_run,
        compare_runs,
        compare_experiments,
        get_model_registry_info,
    )
]
