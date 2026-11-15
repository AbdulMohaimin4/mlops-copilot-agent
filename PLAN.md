# PLAN — Assignment 03: Production-Ready MLOps AI Agent

Source of truth: `Assignment_03_MLOps_AI_Agent.md` (cited below as §N).
Rule: work top to bottom; one item at a time; every item passes the **standard gate** plus its own verification before it is checked.

**Standard gate** (run after every item once code exists):
`uv run ruff check . && uv run ruff format --check . && uv run mypy agent api && uv run pytest -q`

**Decisions (Phase 1, confirmed 2026-11-15):** Gemini Flash (free tier) via `google-genai`, model from `AGENT_MODEL` ·
docker compose for MLflow + Airflow 3 (LocalExecutor + Postgres) · kind cluster, approved namespace `mlops` ·
agent CLI (`agent/main.py`) + FastAPI (`api/main.py`); inference service in `inference/` · Breast Cancer Wisconsin,
LogisticRegression / RandomForest, F1 primary · DVC local-directory remote; `data-v1` = 70 % seeded sample, `data-v2` = full ·
quality gate F1 ≥ 0.90; retrain recommended only on failed gate, model age > 7 days, or stale data version ·
gate pass → MLflow registry alias `candidate`; model baked into image · only K8s write: rollout restart (confirmed + `mlops` only).

---

## 0. Scaffold

- [x] **0.1 Project skeleton** — `pyproject.toml` (Py 3.12, pinned deps, ruff/mypy/pytest config), `uv.lock`, `.gitignore`
  (`.env`, `data/*.csv`, `mlruns/`, `mlflow-data/`, `inference/model/`, `.dvc/cache`, `*.kubeconfig`), `.env.example`,
  package dirs `agent/ agent/tools/ api/ inference/ training/ dags/ tests/ kubernetes/ docs/evidence/ screenshots/`.
  Req: §7 reproducible environment.
  Verify: `uv sync && uv run python -c "import agent, api" && uv run ruff check . && uv run mypy agent api`
- [x] **0.2 Shared tool contract + logging + config** — `agent/schemas.py` (`ToolResult` with `status: ok|error|unavailable`,
  `data`, `error`; per-tool Pydantic input models added per module), `agent/config.py` (env-only settings with defaults),
  `agent/logging_config.py` (JSON formatter, secret redaction), `agent/tools/common.py` (`run_tool` wrapper: validates input,
  enforces timeout, maps exceptions → `error`/`unavailable`, logs tool/status/duration_ms/request_id).
  Req: §6 structured logs, timeouts, service failures.
  Verify: standard gate; `tests/test_common.py` (timeout → `unavailable`, exception → `error`, log record fields, no secret in log).
- [x] 🔒 **COMMIT CHECKPOINT 1** (05b61e5) — scaffold + tool contract (`chore: project scaffold, tool contract and JSON logging`).

## 1. Task 1 — DVC data management (§4 Task 1)

- [x] **1.1 Data stage** — `training/prepare_data.py` (exports sklearn breast-cancer to `data/breast_cancer.csv`, `sample_frac`
  from `params.yaml`, fixed seed), `params.yaml`, `dvc.yaml` (stage `prepare`, outs the CSV), `dvc init`, local remote
  `../mlops-dvc-remote` (outside repo). Req: §4.1 identify tracked datasets, reproducible checkout.
  Verify: `uv run dvc repro && uv run dvc push && uv run dvc status` (clean) `&& wc -l data/breast_cancer.csv` (398 rows + header).
- [ ] 🔒 **COMMIT CHECKPOINT 2 (mandatory — dataset v1)** — `dvc.yaml dvc.lock params.yaml .dvc/config .dvcignore
  training/prepare_data.py` (no `data/.gitignore`: root `.gitignore` already ignores the CSV); `git tag data-v1`.
- [ ] **1.2 DVC tools** — `agent/tools/dvc_tools.py`: `list_tracked_datasets`, `get_current_dataset_version` (git commit,
  `data-v*` tag, md5 from `dvc.lock`), `compare_dataset_versions(rev_a, rev_b)`, `check_data_changes`,
  `reproduce_dataset_version(rev)` (non-destructive `dvc get` into a scratch dir + md5 check + manual steps).
  Single `_run_allowlisted()` helper: argv list, no `shell=True`, subcommand allowlist (dvc: `list status diff get params`;
  git: `rev-parse describe log tag show`), rev regex validation, timeout.
  Req: §4.1 all bullets; "restrict commands to approved operations".
  Verify: standard gate; `tests/test_dvc_tools.py` (disallowed subcommand rejected, injection-style rev rejected, parse of
  mocked `dvc` JSON); live: `uv run python -m agent.tools.dvc_tools` smoke prints real version info.
- [ ] 🔒 **COMMIT CHECKPOINT 3** — `feat(dvc): allowlisted DVC provenance tools`.

## 2. Task 2 — MLflow experiment intelligence (§4 Task 2)

- [ ] **2.1 MLflow server** — `docker-compose.yml` service `mlflow` (pinned image, sqlite backend + local artifacts in gitignored
  volume, port 5000). Req: §4.2 tracking server.
  Verify: `docker compose up -d mlflow && curl -sf localhost:5000/health`.
- [ ] **2.2 Training module** — `training/train.py`: `validate_dataset`, `preprocess`, `train_model`, `evaluate_and_log`,
  `apply_quality_gate` (pure functions reused by the DAG) + CLI. Each run tags `git_commit`, `data_version` (tag),
  `data_md5`, `dvc_rev`, `quality_gate` (passed/failed); gate pass registers model `breast-cancer-classifier` and sets alias
  `candidate`; gate fail raises. Req: §4.2 metrics/params/artifacts/registry, §4.3 stages, provenance.
  Verify: standard gate; `tests/test_training.py` (validation rejects bad schema/NaNs; gate pass/fail); live:
  `uv run python -m training.train --model logreg` and `--model rf` on v1 → runs visible via `MlflowClient` with tags.
- [ ] 🔒 **COMMIT CHECKPOINT 4** — `feat(training): training pipeline with MLflow provenance tags and quality gate`.
- [ ] **2.3 Dataset v2** — set `sample_frac: 1.0`, `dvc repro && dvc push`.
  Verify: `uv run dvc status` clean; `dvc.lock` md5 differs from `data-v1`.
- [ ] 🔒 **COMMIT CHECKPOINT 5 (mandatory — dataset v2)** — `params.yaml dvc.lock`; `git tag data-v2`. Then train both models
  on v2 (runs record the v2 commit/tag).
- [ ] **2.4 MLflow tools** — `agent/tools/mlflow_tools.py` (`MlflowClient` only): `list_experiments`, `list_runs`,
  `get_run_details` (metrics/params/tags/artifacts), `find_best_run(metric)` (returns ranking + rationale + gate status +
  data version + registry alias; explicitly `production_ready: unknown` unless gate passed AND alias set AND deployed),
  `compare_runs(run_ids)`, `compare_experiments`, `get_model_registry_info`.
  Req: §4.2 all bullets; "not assume highest F1 = production-ready".
  Verify: standard gate; `tests/test_mlflow_tools.py` (mocked client: best-run rationale, failed-gate top-F1 not called
  production-ready, server down → `unavailable`); live smoke against compose MLflow.
- [ ] 🔒 **COMMIT CHECKPOINT 6** — `feat(mlflow): MlflowClient experiment intelligence tools`.

## 3. Task 3 — Airflow workflows (§4 Task 3)

- [ ] **3.1 Airflow 3 stack** — `airflow.Dockerfile` (pinned `apache/airflow` 3.x + project deps + git), compose services
  `postgres`, `airflow-init`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor` (LocalExecutor), repo mounted
  read-write at `/opt/airflow/project`; credentials from `.env`. Auth per Airflow 3 docs (verified against installed source).
  Req: §4.3 Airflow 3 API.
  Verify: `docker compose up -d && curl -sf localhost:8080/api/v2/monitor/health`; token via `/auth/token` works.
- [ ] **3.2 Training DAG** — `dags/training_pipeline.py`: validate → preprocess → train → evaluate_log_mlflow → quality_gate,
  calls `training.train` functions; `retries=2`, `retry_delay`; deterministic failures raise `AirflowFailException`
  (no pointless retries); task logging. Failed validate/gate ⇒ run failed, no registry alias set.
  Req: §4.3 DAG figure 2, retries, dependencies, logging, blocks promotion.
  Verify: `tests/test_dag.py` (DagBag import, task order, retries) ; live: REST trigger → all 5 tasks `success`, new MLflow
  run tagged with `airflow_run_id`.
- [ ] **3.3 Airflow tools** — `agent/tools/airflow_tools.py` (httpx, JWT auth, timeout): `list_dags`, `get_dag_runs`,
  `trigger_dag` (allowlist `AIRFLOW_ALLOWED_DAGS`, returns pending action — executes only after user confirmation, see 5.1),
  `get_failed_tasks`, `get_task_logs`.
  Req: §4.3 all bullets.
  Verify: standard gate; `tests/test_airflow_tools.py` (non-allowlisted DAG rejected, 401 → error, timeout → unavailable,
  token never logged); live smoke.
- [ ] 🔒 **COMMIT CHECKPOINT 7** — `feat(airflow): training DAG and authenticated Airflow 3 tools`.

## 4. Task 4 — Kubernetes operations (§4 Task 4)

- [ ] **4.1 Inference service + image** — `inference/app.py` (`/predict`, `/health` liveness, `/ready` readiness = model
  loaded, `/version`), `training/export_model.py` (exports alias `candidate` → `inference/model/` + `model_info.json`),
  `Dockerfile` (non-root, pinned base). Req: §4.4 containerized FastAPI inference.
  Verify: `docker build -t mlops-inference:<ver> . && docker run` → `curl /ready /predict` succeed.
- [ ] **4.2 Manifests** — `kubernetes/namespace.yaml`, `configmap.yaml` (MODEL_NAME, MODEL_VERSION, LOG_LEVEL),
  `deployment.yaml` (replicas 2, readiness + liveness probes, requests/limits, `app.kubernetes.io/version` label),
  `service.yaml` (ClusterIP). kind cluster `mlops`.
  Req: §4.4 manifest bullets.
  Verify: `kind load docker-image … && kubectl apply -f kubernetes/ && kubectl -n mlops rollout status deploy/inference-api`
  → 2/2 Ready; `kubectl -n mlops port-forward svc/inference-api 8081:80` + `curl /predict`.
- [ ] **4.3 Kubernetes tools** — `agent/tools/kubernetes_tools.py` (official client, `_request_timeout`): `list_workloads`,
  `get_pod_status_and_logs`, `list_services`, `find_unhealthy_pods` (phase, waiting reasons, restarts, events),
  `get_deployed_version`, `restart_deployment` (state-changing: namespace must equal `K8S_APPROVED_NAMESPACE` and user
  confirmation required; otherwise rejected).
  Req: §4.4 all bullets; confirmation + approved namespace.
  Verify: standard gate; `tests/test_kubernetes_tools.py` (wrong namespace rejected, unconfirmed rejected, API down →
  `unavailable`); live smoke against kind.
- [ ] 🔒 **COMMIT CHECKPOINT 8** — `feat(k8s): inference service, manifests and Kubernetes ops tools`.

## 5. Integration — multi-tool agent (§5)

- [ ] **5.1 Orchestrator** — `agent/policy.py` (`evaluate_retraining_need`: deterministic freshness/gate/data-version rules),
  `agent/orchestrator.py` (tool registry from Pydantic schemas → Gemini function declarations; manual function-calling loop
  with max steps; system prompt: evidence-only claims, Facts / Uncertainties / Recommended actions, never trigger retraining
  unprompted; state-changing tool calls become pending actions executed only via explicit user confirm; response carries
  tool-call trace + list of unavailable tools computed from real results). SDK usage verified against installed `google-genai`.
  Req: §5, §3 "claims supported by tool responses".
  Verify: standard gate; `tests/test_orchestrator.py` (scripted fake LLM: correct tool dispatched, multi-tool scenario uses
  all 4 modules, no trigger without confirm, unavailable tool surfaced in answer).
- [ ] **5.2 Interfaces** — `agent/main.py` (CLI chat, `/confirm <id>`, `--save-transcript PATH`), `api/main.py` (FastAPI
  `/chat`, `/confirm`, `/health`, request validation).
  Verify: `tests/test_api.py` (TestClient, invalid body → 422); live: Section 5 scenario via CLI with real Gemini + live stack.
- [ ] 🔒 **COMMIT CHECKPOINT 9** — `feat(agent): multi-tool orchestrator with CLI and FastAPI interfaces`.

## 6. Testing, reliability, safety (§6)

- [ ] **6.1 Test audit** — confirm ≥ 8 meaningful tests mapping to every §6 bullet (table in RTM); fill gaps only.
  Verify: `uv run pytest -q` count ≥ 8; RTM rows for §6 all filled.
- [ ] **6.2 Injected failure A — bad image tag** — apply deployment with nonexistent tag → ImagePullBackOff; agent
  diagnosis transcript → `docs/evidence/failure_a_image_pull.md`; restore healthy (`kubectl apply`), confirm 2/2 Ready.
- [ ] **6.3 Injected failure B — failed data validation** — corrupt workspace CSV, trigger DAG (confirmed) → `validate`
  fails, gate never runs, no alias change; agent diagnosis (Airflow logs + DVC change detection) →
  `docs/evidence/failure_b_validation.md`; restore via `dvc checkout`, `dvc status` clean.
- [ ] 🔒 **COMMIT CHECKPOINT 10** — `test: safety coverage and injected-failure evidence`.

## 7. CI (§7)

- [ ] **7.1 GitHub Actions** — `.github/workflows/ci.yml` (on `pull_request` + `push`; `astral-sh/setup-uv`, `uv sync --locked`,
  ruff check, ruff format --check, mypy, pytest; all external services mocked).
  Verify: run the identical commands locally from a clean `uv sync --locked`; real CI run is **unverified until you push and
  open a PR** (I cannot push).
- [ ] 🔒 **COMMIT CHECKPOINT 11** — `ci: lint, type-check and test workflow`.

## 8. Demonstration evidence & documentation (§8, §9)

- [ ] **8.1 Five demo scenarios** (§8) on the live stack with real Gemini → `docs/evidence/scenario_{1..5}_*.md`
  (prompt, tool-call trace with inputs/outputs, final answer). Plus raw DVC/MLflow/Airflow/kubectl CLI exports.
- [ ] **8.2 `README.md` + `architecture.md`** — install, env config, launch each service, tests, agent usage; architecture,
  tool definitions, permissions.
- [ ] **8.3 `docs/REPORT.md`** — every §9 item from real evidence; explicit screenshot checklist with placeholders.
- [ ] **8.4 `docs/CODE_WALKTHROUGH.md`** — reading-order tour, request flow, safety controls, decisions + rejected
  alternatives, failure diagnoses.
- [ ] **8.5 Final verification** — clean `uv sync --locked` + standard gate; every RTM row has impl/test/evidence; line-by-line
  re-read of the spec.
- [ ] 🔒 **COMMIT CHECKPOINT 12 (final)** — `docs: report, walkthrough, evidence`.

---

## Requirements Traceability Matrix

| # | Requirement (spec §) | Implementation | Test(s) | Evidence |
|---|---|---|---|---|
| R1 | Natural-language single conversational interface (§1, §3) | agent/main.py, api/main.py, agent/orchestrator.py | test_api.py, test_orchestrator.py | scenario_1..5 |
| R2 | Operational claims backed by tool responses (§3) | orchestrator.py (trace + unavailable list), system prompt | test_orchestrator.py::unavailable_not_invented | scenario_*, failure_* |
| R3 | ≥ 3 LLM-selectable tools per module (§4) | agent/tools/*.py, orchestrator registry | test_orchestrator.py::registry_has_3_per_module | — |
| R4 | DVC: identify tracked datasets (§4.1) | dvc_tools.list_tracked_datasets | test_dvc_tools.py | scenario_1 |
| R5 | DVC: current version + git commit (§4.1) | dvc_tools.get_current_dataset_version | test_dvc_tools.py | scenario_1 |
| R6 | DVC: compare versions (§4.1) | dvc_tools.compare_dataset_versions | test_dvc_tools.py | scenario_1 |
| R7 | DVC: detect changes (§4.1) | dvc_tools.check_data_changes | test_dvc_tools.py | failure_b |
| R8 | DVC: explain/reproduce previous version; reproducible checkout (§4.1) | dvc_tools.reproduce_dataset_version, dvc.yaml | test_dvc_tools.py | scenario_1 |
| R9 | DVC: version used by experiment X from recorded provenance (§4.1 example) | train.py tags, mlflow_tools.get_run_details | test_training.py, test_mlflow_tools.py | scenario_1 |
| R10 | DVC: subprocess restricted to approved ops (§4.1) | dvc_tools._run_allowlisted | test_dvc_tools.py::rejects_* | — |
| R11 | MLflow: list experiments & runs (§4.2) | mlflow_tools.list_experiments/list_runs | test_mlflow_tools.py | scenario_2 |
| R12 | MLflow: metrics, params, artifacts (§4.2) | mlflow_tools.get_run_details | test_mlflow_tools.py | scenario_2 |
| R13 | MLflow: best run by metric, explained (§4.2) | mlflow_tools.find_best_run | test_mlflow_tools.py | scenario_2 |
| R14 | MLflow: compare ≥ 2 experiments/runs (§4.2) | mlflow_tools.compare_runs/compare_experiments | test_mlflow_tools.py | scenario_2 |
| R15 | MLflow: registry/model versions (§4.2) | mlflow_tools.get_model_registry_info, train.py registration | test_mlflow_tools.py | scenario_2 |
| R16 | Highest F1 ≠ production-ready (§4.2) | find_best_run rationale fields | test_mlflow_tools.py::not_production_ready | scenario_2 |
| R17 | MlflowClient, no UI parsing (§4.2) | mlflow_tools.py | — (code review) | — |
| R18 | Airflow: list DAGs, run status (§4.3) | airflow_tools.list_dags/get_dag_runs | test_airflow_tools.py | scenario_3 |
| R19 | Airflow: trigger approved DAG (§4.3) | airflow_tools.trigger_dag + confirm flow | test_airflow_tools.py | scenario_3 |
| R20 | Airflow: failed tasks + diagnostics (§4.3) | airflow_tools.get_failed_tasks/get_task_logs | test_airflow_tools.py | failure_b |
| R21 | DAG stages validate→preprocess→train→evaluate/log→gate (§4.3 Fig 2) | dags/training_pipeline.py, training/train.py | test_dag.py | scenario_3 |
| R22 | DAG retries, dependencies, logging (§4.3) | dags/training_pipeline.py | test_dag.py | scenario_3 |
| R23 | Failed validation/gate blocks promotion (§4.3) | train.apply_quality_gate, AirflowFailException | test_training.py, test_dag.py | failure_b |
| R24 | Airflow 3 API authenticated (§4.3) | airflow_tools auth | test_airflow_tools.py | scenario_3 |
| R25 | K8s: list pods & deployments (§4.4) | kubernetes_tools.list_workloads | test_kubernetes_tools.py | scenario_4 |
| R26 | K8s: pod status & logs (§4.4) | kubernetes_tools.get_pod_status_and_logs | test_kubernetes_tools.py | scenario_4 |
| R27 | K8s: inspect services (§4.4) | kubernetes_tools.list_services | test_kubernetes_tools.py | scenario_4 |
| R28 | K8s: unhealthy pods & restart events (§4.4) | kubernetes_tools.find_unhealthy_pods | test_kubernetes_tools.py | failure_a |
| R29 | K8s: deployed version (§4.4) | kubernetes_tools.get_deployed_version | test_kubernetes_tools.py | scenario_4 |
| R30 | Deployment ≥ 2 replicas, ClusterIP, probes, requests/limits, ConfigMap (§4.4) | kubernetes/*.yaml, inference/app.py, Dockerfile | — | kubectl export |
| R31 | State changes need confirmation + approved namespace (§4.4) | kubernetes_tools.restart_deployment, orchestrator pending actions | test_kubernetes_tools.py, test_orchestrator.py | — |
| R32 | Multi-tool integration scenario steps 1–6 (§5) | orchestrator.py, policy.py | test_orchestrator.py::integration_scenario | scenario_5 |
| R33 | No automatic retraining; evidence-based recommendation (§5) | orchestrator pending actions, policy.py | test_orchestrator.py, test_policy.py | scenario_5 |
| R34 | Facts / uncertainties / recommended actions (§5) | system prompt | test_orchestrator.py | scenario_5 |
| R35 | ≥ 8 meaningful pytest tests (§6) | tests/ | all | pytest output |
| R36 | Mocked responses for unavailable services (§6) | tests/ fakes | test_*::unavailable | — |
| R37 | Tool selection & execution tests (§6) | — | test_orchestrator.py | — |
| R38 | Timeouts, invalid requests, service failures tests (§6) | tools/common.py | test_common.py, test_api.py | — |
| R39 | Agent doesn't invent results when tool unavailable (§6) | orchestrator.py | test_orchestrator.py | — |
| R40 | Unauthorized K8s modifications rejected (§6) | kubernetes_tools.py | test_kubernetes_tools.py | — |
| R41 | Structured logs: tool, status, duration, request id, no secrets (§6) | logging_config.py, tools/common.py | test_common.py | log sample in report |
| R42 | Two injected failures diagnosed (§6, §8) | — | — | failure_a, failure_b |
| R43 | Repo structure, reproducible env (§7) | pyproject.toml, uv.lock | CI | — |
| R44 | GitHub Actions lint + tests on PRs, external calls mocked (§7) | .github/workflows/ci.yml | — | CI run (after push) |
| R45 | Five demo scenarios (§8) | — | — | docs/evidence/scenario_1..5 |
| R46 | README: install, env, launch, tests, agent usage (§9) | README.md | — | — |
| R47 | Report: architecture, tools & permissions, evidence, 5 convos, 2 failures, limitations (§9) | docs/REPORT.md, architecture.md | — | — |
| R48 | No secrets committed (§9) | .gitignore, .env.example, logging redaction | test_common.py | git grep check |

---

## Progress Log

| Date | Item | Result | Notes |
|---|---|---|---|
| 2026-11-15 | Phase 0–1 | done | Toolchain: Py 3.12.3, uv 0.12.22, git 2.43, Docker 29.6.1 (WSL integration enabled, ~8 GB). kubectl/kind/dvc to install. Decisions recorded above. |
| 2026-11-15 | 0.1 | pass | uv sync; deps pinned exact (google-genai 3.3.0, mlflow-skinny 3.18.0, dvc 3.67.1, kubernetes 37.0.2, fastapi 0.163.1). mlflow-skinny verified to include sklearn flavor + alias APIs. ruff/mypy clean. |
| 2026-11-15 | 0.2 | pass | schemas.py, config.py, logging_config.py, tools/common.py; tests/test_common.py 6 passed (ok, invalid_request, timeout, unavailable, rejected, log fields + redaction). |
| 2026-11-15 | 1.1 | pass | dvc init + remote `local` → ../mlops-dvc-remote. `dvc repro`: 398 rows, data md5 e1c1c4721fa52fd770840aee20006c89. `dvc push` 1 file; `dvc status -c` in sync. Note: `dvc init` auto-staged .dvc/.gitignore, .dvc/config, .dvcignore (DVC default behaviour). |
