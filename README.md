# MLOps Copilot Agent

A conversational agent that answers operational questions about a machine-learning system by
calling real tools: **DVC** (dataset versions), **MLflow** (experiments, runs, registry),
**Apache Airflow 3** (training pipeline) and **Kubernetes** (the deployed inference API).
Every operational claim in an answer comes from a tool result, and state-changing actions
(triggering training, restarting the deployment) run only after explicit user confirmation.

See [architecture.md](architecture.md) for the design, tool catalogue and permission model,
and [docs/REPORT.md](docs/REPORT.md) for the technical report and evidence.

## Repository layout

| Path | Purpose |
|---|---|
| `agent/` | Orchestrator, CLI (`main.py`), retraining policy, config, logging, tool contract (`schemas.py`) |
| `agent/tools/` | `dvc_tools.py`, `mlflow_tools.py`, `airflow_tools.py`, `kubernetes_tools.py`, shared `common.py` |
| `api/main.py` | FastAPI interface to the agent (`/chat`, `/confirm`, `/health`) |
| `training/` | `prepare_data.py` (DVC stage), `train.py` (pipeline stages + CLI), `export_model.py` |
| `dags/training_pipeline.py` | Airflow DAG: validate → preprocess → train → evaluate/log → quality gate |
| `inference/app.py` + `Dockerfile` | FastAPI inference service deployed to Kubernetes |
| `kubernetes/` | Namespace, ConfigMap, Deployment (2 replicas, probes, limits), ClusterIP Service |
| `docker-compose.yml`, `airflow.Dockerfile` | MLflow + Airflow 3 (LocalExecutor, Postgres) |
| `dvc.yaml`, `params.yaml`, `dvc.lock` | Dataset pipeline and versions (`data-v1`, `data-v2` git tags) |
| `tests/` | pytest suite; all external services mocked |
| `docs/` | Report, code walkthrough, evidence transcripts |

## Prerequisites

Linux or WSL2 with: Python 3.12, [uv](https://docs.astral.sh/uv/), Docker (with Compose v2),
[kind](https://kind.sigs.k8s.io/) and `kubectl`. About 6 GB of memory for Docker.
A Gemini API key (free tier works) from Google AI Studio.

## 1. Install

```bash
uv sync                      # creates .venv from uv.lock
cp .env.example .env         # then edit .env (never commit it)
```

## 2. Configure environment

All configuration is via environment variables; `.env.example` documents every one.

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY`, `AGENT_MODEL` | LLM credentials and model (default `gemini-3.5-flash`) |
| `MLFLOW_TRACKING_URI`, `MLFLOW_EXPERIMENT`, `MODEL_NAME` | MLflow server, default experiment, registered model |
| `AIRFLOW_URL`, `AIRFLOW_USERNAME`, `AIRFLOW_PASSWORD` | Airflow API (JWT obtained from `/auth/token`) |
| `AIRFLOW_ALLOWED_DAGS` | DAGs the agent may trigger (default `training_pipeline`) |
| `AIRFLOW_FERNET_KEY`, `AIRFLOW_JWT_SECRET`, `AIRFLOW_DB_PASSWORD`, `AIRFLOW_UID` | Used by docker compose; generate random values |
| `K8S_APPROVED_NAMESPACE`, `KUBE_CONTEXT` | Only namespace where cluster changes are allowed (`mlops`), kube context |
| `QUALITY_GATE_F1`, `FRESHNESS_MAX_DAYS` | Quality gate (0.90) and retraining freshness window (7 days) |
| `TOOL_TIMEOUT_SECONDS`, `LLM_TIMEOUT_SECONDS`, `MAX_TOOL_ROUNDS` | Timeouts and loop bound |

Generate the Airflow secrets, for example:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"            # JWT secret / passwords
python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"  # Fernet key
```

## 3. Launch the services

### Data (DVC)

```bash
mkdir -p ../mlops-dvc-remote             # local DVC remote (configured in .dvc/config)
uv run dvc pull                          # or: uv run dvc repro  (rebuild from params.yaml)
uv run dvc status                        # "Data and pipelines are up to date."
git checkout data-v1 -- dvc.lock params.yaml && uv run dvc checkout   # inspect v1, then restore:
git checkout HEAD -- dvc.lock params.yaml && uv run dvc checkout
```

### MLflow and Airflow

```bash
docker compose up -d --build             # MLflow :5000, Airflow API/UI :8080
curl -s localhost:5000/health
curl -s localhost:8080/api/v2/monitor/health
```

Train locally (optional; the DAG does the same steps):

```bash
MLFLOW_TRACKING_URI=http://localhost:5000 uv run python -m training.train --model logreg
```

### Kubernetes inference API

```bash
kind create cluster --name mlops
MLFLOW_TRACKING_URI=http://localhost:5000 uv run python -m training.export_model   # alias @candidate
docker build -t mlops-inference:model-v6 .        # tag = model version exported
kind load docker-image mlops-inference:model-v6 --name mlops
kubectl apply -f kubernetes/namespace.yaml && kubectl apply -f kubernetes/
kubectl -n mlops rollout status deploy/inference-api
kubectl -n mlops port-forward svc/inference-api 8081:80   # then: curl localhost:8081/version
```

When deploying a different model version, update the image tag, the `app.kubernetes.io/version`
label in `kubernetes/deployment.yaml` and `APP_VERSION` in `kubernetes/configmap.yaml`.

## 4. Run the tests

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy agent api && uv run pytest -q
```

The DAG tests need Airflow, which pins an older FastAPI than the agent API, so they run inside
the Airflow image (CI job `dag-tests` does the same):

```bash
docker run --rm -v "$PWD:/opt/airflow/project:ro" -w /opt/airflow/project \
  -e PYTHONPATH=/opt/airflow/project --entrypoint bash mlops-airflow:3.3.2 -c \
  "airflow db migrate >/dev/null && python -m pip install -q pytest==9.2.0 && \
   python -m pytest -q -p no:cacheprovider tests/test_dag.py"
```

## 5. Talk to the agent

CLI (interactive, or one-shot with `-m`; `--save-transcript` writes Markdown evidence):

```bash
uv run --env-file .env python -m agent.main
> Find the best recent model, identify the dataset version used to train it, check the deployment status, and explain whether retraining is necessary.
> Trigger the training pipeline with the random forest model
> /confirm 1a2b3c4d          # the action ID the agent printed
```

HTTP API:

```bash
uv run --env-file .env uvicorn api.main:app --port 8000
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "Why is my prediction API unavailable?"}'
curl -s localhost:8000/confirm -H 'Content-Type: application/json' \
  -d '{"session_id": "<from /chat>", "action_id": "<from pending_actions>"}'
```

Each response contains the answer, the full tool-call trace (arguments and structured results),
the list of tools that were unavailable, and any pending actions awaiting confirmation.
Logs are JSON lines on stderr with `request_id`, `tool`, `status` and `duration_ms`.
