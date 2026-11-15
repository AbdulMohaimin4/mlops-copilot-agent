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
- [x] 🔒 **COMMIT CHECKPOINT 2 (mandatory — dataset v1)** (deb5869, tag data-v1) — `dvc.yaml dvc.lock params.yaml .dvc/config .dvcignore
  training/prepare_data.py` (no `data/.gitignore`: root `.gitignore` already ignores the CSV); `git tag data-v1`.
- [x] **1.2 DVC tools** — `agent/tools/dvc_tools.py`: `list_tracked_datasets`, `get_current_dataset_version` (git commit,
  `data-v*` tag, md5 from `dvc.lock`), `compare_dataset_versions(rev_a, rev_b)`, `check_data_changes`,
  `reproduce_dataset_version(rev)` (non-destructive `dvc get` into a scratch dir + md5 check + manual steps).
  Single `_run_allowlisted()` helper: argv list, no `shell=True`, subcommand allowlist (dvc: `list status diff get params`;
  git: `rev-parse describe log tag show`), rev regex validation, timeout.
  Req: §4.1 all bullets; "restrict commands to approved operations".
  Verify: standard gate; `tests/test_dvc_tools.py` (disallowed subcommand rejected, injection-style rev rejected, parse of
  mocked `dvc` JSON); live: run each tool in `TOOLS` via `execute_tool` against the repo.
- [x] 🔒 **COMMIT CHECKPOINT 3** (10fea22) — `feat(dvc): allowlisted DVC provenance tools`.

## 2. Task 2 — MLflow experiment intelligence (§4 Task 2)

- [x] **2.1 MLflow server** — `docker-compose.yml` service `mlflow` (pinned image, sqlite backend + local artifacts in gitignored
  volume, port 5000). Req: §4.2 tracking server.
  Verify: `docker compose up -d mlflow && curl -sf localhost:5000/health`.
- [x] **2.2 Training module** — `training/train.py`: `validate_dataset`, `preprocess`, `train_model`, `evaluate_and_log`,
  `apply_quality_gate` (pure functions reused by the DAG) + CLI. Each run tags `git_commit`, `data_version` (tag),
  `data_md5`, `dvc_rev`, `quality_gate` (passed/failed); gate pass registers model `breast-cancer-classifier` and sets alias
  `candidate`; gate fail raises. Req: §4.2 metrics/params/artifacts/registry, §4.3 stages, provenance.
  Verify: standard gate; `tests/test_training.py` (validation rejects bad schema/NaNs; gate pass/fail); live:
  `uv run python -m training.train --model logreg` and `--model rf` on v1 → runs visible via `MlflowClient` with tags.
- [x] 🔒 **COMMIT CHECKPOINT 4** (144463e) — `feat(training): training pipeline with MLflow provenance tags and quality gate`.
- [x] **2.3 Dataset v2** — set `sample_frac: 1.0`, `dvc repro && dvc push`.
  Verify: `uv run dvc status` clean; `dvc.lock` md5 differs from `data-v1`.
- [x] 🔒 **COMMIT CHECKPOINT 5 (mandatory — dataset v2)** (3cce5da, tag data-v2) — `params.yaml dvc.lock`; `git tag data-v2`. Then train both models
  on v2 (runs record the v2 commit/tag).
- [x] **2.4 MLflow tools** — `agent/tools/mlflow_tools.py` (`MlflowClient` only): `list_experiments`, `list_runs`,
  `get_run_details` (metrics/params/tags/artifacts), `find_best_run(metric)` (returns ranking + rationale + gate status +
  data version + registry alias; explicitly `production_ready: unknown` unless gate passed AND alias set AND deployed),
  `compare_runs(run_ids)`, `compare_experiments`, `get_model_registry_info`.
  Req: §4.2 all bullets; "not assume highest F1 = production-ready".
  Verify: standard gate; `tests/test_mlflow_tools.py` (mocked client: best-run rationale, failed-gate top-F1 not called
  production-ready, server down → `unavailable`); live smoke against compose MLflow.
- [ ] 🔒 **COMMIT CHECKPOINT 6** (batched — user asked for commit list at end, 2026-11-15) — `feat(mlflow): MlflowClient experiment intelligence tools`.

## 3. Task 3 — Airflow workflows (§4 Task 3)

- [x] **3.1 Airflow 3 stack** — `airflow.Dockerfile` (pinned `apache/airflow` 3.x + project deps + git), compose services
  `postgres`, `airflow-init`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor` (LocalExecutor), repo mounted
  read-write at `/opt/airflow/project`; credentials from `.env`. Auth per Airflow 3 docs (verified against installed source).
  Req: §4.3 Airflow 3 API.
  Verify: `docker compose up -d && curl -sf localhost:8080/api/v2/monitor/health`; token via `/auth/token` works.
- [x] **3.2 Training DAG** — `dags/training_pipeline.py`: validate → preprocess → train → evaluate_log_mlflow → quality_gate,
  calls `training.train` functions; `retries=2`, `retry_delay`; deterministic failures raise `AirflowFailException`
  (no pointless retries); task logging. Failed validate/gate ⇒ run failed, no registry alias set.
  Req: §4.3 DAG figure 2, retries, dependencies, logging, blocks promotion.
  Verify: `tests/test_dag.py` (DagBag import, task order, retries) — runs **inside the Airflow image**
  (`docker compose run --rm --no-deps -T -w /opt/airflow/project --entrypoint bash airflow-scheduler -c
  "python -m pip install -q pytest==9.2.0 && python -m pytest -q -p no:cacheprovider tests/test_dag.py"`) because
  apache-airflow-core pins fastapi<0.137, incompatible with the agent API's fastapi; skipped in the main env; live: REST trigger → all 5 tasks `success`, new MLflow
  run tagged with `airflow_run_id`.
- [x] **3.3 Airflow tools** — `agent/tools/airflow_tools.py` (httpx, JWT auth, timeout): `list_dags`, `get_dag_runs`,
  `trigger_dag` (allowlist `AIRFLOW_ALLOWED_DAGS`, returns pending action — executes only after user confirmation, see 5.1),
  `get_failed_tasks`, `get_task_logs`.
  Req: §4.3 all bullets.
  Verify: standard gate; `tests/test_airflow_tools.py` (non-allowlisted DAG rejected, 401 → error, timeout → unavailable,
  token never logged); live smoke.
- [ ] 🔒 **COMMIT CHECKPOINT 7** (batched) — `feat(airflow): training DAG and authenticated Airflow 3 tools`.

## 4. Task 4 — Kubernetes operations (§4 Task 4)

- [x] **4.1 Inference service + image** — `inference/app.py` (`/predict`, `/health` liveness, `/ready` readiness = model
  loaded, `/version`), `training/export_model.py` (exports alias `candidate` → `inference/model/` + `model_info.json`),
  `Dockerfile` (non-root, pinned base). Req: §4.4 containerized FastAPI inference.
  Verify: `docker build -t mlops-inference:<ver> . && docker run` → `curl /ready /predict` succeed.
- [x] **4.2 Manifests** — `kubernetes/namespace.yaml`, `configmap.yaml` (MODEL_NAME, MODEL_VERSION, LOG_LEVEL),
  `deployment.yaml` (replicas 2, readiness + liveness probes, requests/limits, `app.kubernetes.io/version` label),
  `service.yaml` (ClusterIP). kind cluster `mlops`.
  Req: §4.4 manifest bullets.
  Verify: `kind load docker-image … && kubectl apply -f kubernetes/ && kubectl -n mlops rollout status deploy/inference-api`
  → 2/2 Ready; `kubectl -n mlops port-forward svc/inference-api 8081:80` + `curl /predict`.
- [x] **4.3 Kubernetes tools** — `agent/tools/kubernetes_tools.py` (official client, `_request_timeout`): `list_workloads`,
  `get_pod_status_and_logs`, `list_services`, `find_unhealthy_pods` (phase, waiting reasons, restarts, events),
  `get_deployed_version`, `restart_deployment` (state-changing: namespace must equal `K8S_APPROVED_NAMESPACE` and user
  confirmation required; otherwise rejected).
  Req: §4.4 all bullets; confirmation + approved namespace.
  Verify: standard gate; `tests/test_kubernetes_tools.py` (wrong namespace rejected, unconfirmed rejected, API down →
  `unavailable`); live smoke against kind.
- [ ] 🔒 **COMMIT CHECKPOINT 8** (batched) — `feat(k8s): inference service, manifests and Kubernetes ops tools`.

## 5. Integration — multi-tool agent (§5)

- [x] **5.1 Orchestrator** — `agent/policy.py` (`evaluate_retraining_need`: deterministic freshness/gate/data-version rules),
  `agent/orchestrator.py` (tool registry from Pydantic schemas → Gemini function declarations; manual function-calling loop
  with max steps; system prompt: evidence-only claims, Facts / Uncertainties / Recommended actions, never trigger retraining
  unprompted; state-changing tool calls become pending actions executed only via explicit user confirm; response carries
  tool-call trace + list of unavailable tools computed from real results). SDK usage verified against installed `google-genai`.
  Req: §5, §3 "claims supported by tool responses".
  Verify: standard gate; `tests/test_orchestrator.py` (scripted fake LLM: correct tool dispatched, multi-tool scenario uses
  all 4 modules, no trigger without confirm, unavailable tool surfaced in answer).
- [x] **5.2 Interfaces** — `agent/main.py` (CLI chat, `/confirm <id>`, `--save-transcript PATH`), `api/main.py` (FastAPI
  `/chat`, `/confirm`, `/health`, request validation).
  Verify: `tests/test_api.py` (TestClient, invalid body → 422); live: Section 5 scenario via CLI with real Gemini + live stack.
- [ ] 🔒 **COMMIT CHECKPOINT 9** (batched) — `feat(agent): multi-tool orchestrator with CLI and FastAPI interfaces`.

## 6. Testing, reliability, safety (§6)

- [x] **6.1 Test audit** — confirm ≥ 8 meaningful tests mapping to every §6 bullet (table in RTM); fill gaps only.
  Verify: `uv run pytest -q` count ≥ 8; RTM rows for §6 all filled.
- [x] **6.2 Injected failure A — bad image tag** — apply deployment with nonexistent tag → ImagePullBackOff; agent
  diagnosis transcript → `docs/evidence/failure_a_image_pull.md`; restore healthy (`kubectl apply`), confirm 2/2 Ready.
- [x] **6.3 Injected failure B — failed data validation** — corrupt workspace CSV, trigger DAG (confirmed) → `validate`
  fails, gate never runs, no alias change; agent diagnosis (Airflow logs + DVC change detection) →
  `docs/evidence/failure_b_validation.md`; restore via `dvc checkout`, `dvc status` clean.
- [ ] 🔒 **COMMIT CHECKPOINT 10** (batched) — `test: safety coverage and injected-failure evidence`.

## 7. CI (§7)

- [x] **7.1 GitHub Actions** — `.github/workflows/ci.yml` (on `pull_request` + `push`; `astral-sh/setup-uv`, `uv sync --locked`,
  ruff check, ruff format --check, mypy, pytest; all external services mocked).
  Verify: run the identical commands locally from a clean `uv sync --locked`; real CI run is **unverified until you push and
  open a PR** (I cannot push).
- [ ] 🔒 **COMMIT CHECKPOINT 11** (batched) — `ci: lint, type-check and test workflow`.

## 8. Demonstration evidence & documentation (§8, §9)

- [x] **8.1 Five demo scenarios** (§8) on the live stack with real Gemini → `docs/evidence/scenario_{1..5}_*.md`
  (prompt, tool-call trace with inputs/outputs, final answer). Plus raw DVC/MLflow/Airflow/kubectl CLI exports.
- [x] **8.2 `README.md` + `architecture.md`** — install, env config, launch each service, tests, agent usage; architecture,
  tool definitions, permissions.
- [x] **8.3 `docs/REPORT.md`** — every §9 item from real evidence; explicit screenshot checklist with placeholders.
- [x] **8.4 `docs/CODE_WALKTHROUGH.md`** — reading-order tour, request flow, safety controls, decisions + rejected
  alternatives, failure diagnoses.
- [x] **8.5 Final verification** — clean `uv sync --locked` + standard gate; every RTM row has impl/test/evidence; line-by-line
  re-read of the spec.
- [ ] 🔒 **COMMIT CHECKPOINT 12 (final)** — `docs: report, walkthrough, evidence`.

---

## Requirements Traceability Matrix

Evidence paths are relative to `docs/evidence/`. Tests are in `tests/`. "live" = verified against the running stack.

| # | Requirement (spec §) | Implementation | Test(s) | Evidence |
|---|---|---|---|---|
| R1 | Single conversational interface (§1, §3) | agent/main.py, api/main.py, agent/orchestrator.py | test_api.py, test_orchestrator.py | scenario_1–5 (CLI and HTTP API) |
| R2 | Operational claims backed by tool responses (§3) | orchestrator trace + `_response` unavailable list; SYSTEM_PROMPT rules 1–2 | test_unavailable_tool_is_surfaced_even_if_model_text_omits_it | every transcript lists tool calls + raw JSON |
| R3 | ≥ 3 LLM-selectable tools per module (§4) | dvc 5, mlflow 7, airflow 5, kubernetes 6 (+ policy 1) | test_each_module_exposes_at_least_three_valid_tools | live tool use in scenarios |
| R4 | DVC: identify tracked datasets (§4.1) | dvc_tools.list_tracked_datasets | test_dvc_tools.py | system_exports.md (DVC); progress log 1.2 |
| R5 | DVC: current version + git commit (§4.1) | get_current_dataset_version | test_dvc_tools.py | scenario_5, failure_b |
| R6 | DVC: compare versions (§4.1) | compare_dataset_versions | test_dvc_tools.py | system_exports.md (`dvc params diff`, `dvc diff`); progress log 1.2 |
| R7 | DVC: detect changes (§4.1) | check_data_changes | test_check_data_changes_reports_modified_outputs | failure_b (modified file detected) |
| R8 | DVC: explain/reproduce previous version; reproducible checkout (§4.1) | reproduce_dataset_version, dvc.yaml, README | test_unsafe_revisions_are_invalid_requests | scenario_1 (md5 verified), failure_b (`dvc checkout`) |
| R9 | Dataset version used by an experiment, from recorded provenance (§4.1 example) | train.collect_provenance tags; registry version tags | test_quality_gate_pass_registers_candidate_with_provenance | scenario_1, system_exports.md |
| R10 | DVC subprocess restricted to approved ops (§4.1) | dvc_tools._run_allowlisted | test_non_allowlisted_commands_*, test_commands_run_as_argv_list_without_shell | — |
| R11 | MLflow: list experiments & runs (§4.2) | list_experiments, list_runs | test_mlflow_tools.py | scenario_2 |
| R12 | MLflow: metrics, params, artifacts (§4.2) | get_run_details (+ logged-model files) | test_run_details_include_logged_model_artifacts | scenario_3, system_exports.md (last section) |
| R13 | MLflow: best run by metric, explained (§4.2) | find_best_run | test_best_run_explains_choice_and_excludes_unfinished_runs | scenario_2 |
| R14 | MLflow: compare ≥ 2 experiments/runs (§4.2) | compare_runs, compare_experiments | test_malformed_run_id_is_invalid_request | progress log 2.4 (live compare_experiments) |
| R15 | MLflow: registry / model versions (§4.2) | get_model_registry_info; train.apply_quality_gate | test_mlflow_tools.py, test_training.py | scenario_1, scenario_3, system_exports.md |
| R16 | Highest F1 ≠ production-ready (§4.2) | find_best_run.production_readiness | test_highest_f1_is_never_declared_production_ready | scenario_2 |
| R17 | MlflowClient, no UI parsing (§4.2) | mlflow_tools.py (MlflowClient only) | (code review) | — |
| R18 | Airflow: list DAGs, run status (§4.3) | list_dags, get_dag_runs | test_airflow_tools.py | scenario_3, scenario_5 |
| R19 | Airflow: trigger approved DAG (§4.3) | trigger_dag + allowlist + confirmation | test_non_allowlisted_dag_*, test_state_changing_call_waits_for_explicit_confirmation | scenario_3, failure_b |
| R20 | Airflow: failed tasks + diagnostics (§4.3) | get_failed_tasks, get_task_logs (`_log_line`) | test_failed_tasks_found_*, test_task_log_includes_structured_exception_details | failure_b |
| R21 | DAG stages validate→preprocess→train→evaluate/log→gate (§4.3) | dags/training_pipeline.py, training/train.py | test_dag.py::test_stages_run_in_required_order (Airflow image) | system_exports.md (task states) |
| R22 | DAG retries, dependencies, logging (§4.3) | default_args retries=2; TaskFlow deps; loggers | test_tasks_retry_and_dag_is_manual | task logs in progress log 3.2 |
| R23 | Failed validation/gate blocks promotion (§4.3) | AirflowFailException; gate is sole registrar | test_quality_gate_failure_blocks_promotion, test_deterministic_failures_fail_fast_without_retry | failure_b; run 95f960db (gate failed, nothing registered) |
| R24 | Airflow 3 API with authenticated requests (§4.3) | airflow_tools._request (JWT /auth/token) | test_failed_tasks_found_in_latest_failed_run_with_bearer_auth | progress log 3.1 (401 without token) |
| R25 | K8s: list pods & deployments (§4.4) | list_workloads | test_kubernetes_tools.py | scenario_4 |
| R26 | K8s: pod status & logs (§4.4) | get_pod_status_and_logs | test_kubernetes_tools.py | scenario_4 |
| R27 | K8s: inspect services (§4.4) | list_services (EndpointSlices) | test_kubernetes_tools.py | scenario_4, failure_a |
| R28 | K8s: unhealthy pods & restart events (§4.4) | find_unhealthy_pods | test_unhealthy_pods_and_warning_events_are_reported | failure_a |
| R29 | K8s: deployed version (§4.4) | get_deployed_version (live /version via pod proxy) | test_kubernetes_tools.py | scenario_4, scenario_5 |
| R30 | Deployment ≥ 2 replicas, ClusterIP, probes, requests/limits, ConfigMap (§4.4) | kubernetes/*.yaml, inference/app.py, Dockerfile | `kubectl apply --dry-run` | system_exports.md (kubectl get/describe) |
| R31 | State changes need confirmation + approved namespace (§4.4) | Orchestrator pending actions; restart_deployment namespace check | test_restart_outside_approved_namespace_*, test_chat_then_confirm_executes_pending_action_once | scenario_4 (kube-system rejected after confirm) |
| R32 | Multi-tool scenario steps 1–6 (§5) | orchestrator.py, policy.py, prompt rules 4–6 | test_integration_scenario_uses_all_four_systems_without_retraining | scenario_5 attempt 2 |
| R33 | No automatic retraining; evidence-based recommendation (§5) | trigger held as pending; evaluate_retraining_need | test_policy.py, test_integration_scenario_* | scenario_5 |
| R34 | Facts / uncertainties / recommended actions (§5) | SYSTEM_PROMPT rule 6 | test_integration_scenario_* | scenario_2, 3, 4, 5, failure_a, failure_b |
| R35 | ≥ 8 meaningful pytest tests (§6) | tests/ (10 files) | 63 passed + 4 DAG tests | pytest output in progress log |
| R36 | Mocked responses for unavailable services (§6) | fakes in tests | test_unreachable_* ×3, test_missing_dvc_binary_reports_unavailable | — |
| R37 | Tool selection & execution tests (§6) | ScriptedLLM | test_selected_tool_is_executed_and_result_returned_to_model | — |
| R38 | Timeouts, invalid requests, service failures (§6) | tools/common.execute_tool, orchestrator LLM error path | test_timeout_*, test_invalid_*, test_failures_map_to_structured_status, test_llm_failure_* | scenario_4 note (real 429) |
| R39 | Agent doesn't invent results when a tool is unavailable (§6) | `_response`, prompt rule 2 | test_unavailable_tool_is_surfaced_even_if_model_text_omits_it | — |
| R40 | Unauthorized K8s modifications rejected (§6) | restart_deployment | test_restart_outside_approved_namespace_is_rejected_before_any_api_call | scenario_4 |
| R41 | Structured logs: tool, status, duration, request id, no secrets (§6) | logging_config.py, tools/common._log | test_structured_log_has_required_fields_and_no_secrets | logs/*.jsonl |
| R42 | Two injected failures diagnosed (§6, §8) | — | — | failure_a_image_pull.md, failure_b_validation.md |
| R43 | Repo structure, reproducible environment (§7) | layout per §7 (+ inference/), pyproject.toml, uv.lock | clean `uv sync --locked` gate | progress log 8.5 |
| R44 | GitHub Actions lint + tests on PRs, external calls mocked (§7) | .github/workflows/ci.yml | same steps run locally | **real GitHub run pending user push** |
| R45 | Five demo scenarios (§8) | — | — | scenario_1–5 |
| R46 | README: install, env, launch, tests, agent usage (§9) | README.md | — | — |
| R47 | Report: architecture, tools & permissions, evidence, 5 convos, 2 failures, limitations (§9) | docs/REPORT.md, architecture.md, docs/CODE_WALKTHROUGH.md | — | screenshots listed (user to capture) |
| R48 | No secrets committed (§9) | .gitignore, .env.example, redact() | test_structured_log_has_required_fields_and_no_secrets | secret scan of untracked files (progress log 8.5) |

## Progress Log

| Date | Item | Result | Notes |
|---|---|---|---|
| 2026-11-15 | Phase 0–1 | done | Toolchain: Py 3.12.3, uv 0.12.22, git 2.43, Docker 29.6.1 (WSL integration enabled, ~8 GB). kubectl/kind/dvc to install. Decisions recorded above. |
| 2026-11-15 | 0.1 | pass | uv sync; deps pinned exact (google-genai 3.3.0, mlflow-skinny 3.18.0, dvc 3.67.1, kubernetes 37.0.2, fastapi 0.163.1). mlflow-skinny verified to include sklearn flavor + alias APIs. ruff/mypy clean. |
| 2026-11-15 | 0.2 | pass | schemas.py, config.py, logging_config.py, tools/common.py; tests/test_common.py 6 passed (ok, invalid_request, timeout, unavailable, rejected, log fields + redaction). |
| 2026-11-15 | 1.1 | pass | dvc init + remote `local` → ../mlops-dvc-remote. `dvc repro`: 398 rows, data md5 e1c1c4721fa52fd770840aee20006c89. `dvc push` 1 file; `dvc status -c` in sync. Note: `dvc init` auto-staged .dvc/.gitignore, .dvc/config, .dvcignore (DVC default behaviour). |
| 2026-11-15 | 1.2 | pass | 5 DVC tools; allowlist by command prefix (e.g. `params diff`, `remote list`, `tag --list`), argv only. Live: all 5 ok; reproduce data-v1 → md5 e1c1c47… verified; injected append to CSV → check_data_changes reported `modified`, restored with `dvc checkout --force`. pytest 19 passed. |
| 2026-11-15 | 2.1 | pass | compose `mlflow` ghcr.io/mlflow/mlflow:v3.18.0, sqlite + proxied artifacts in named volume. /health 200; `--allowed-hosts` verified: localhost/mlflow → 200, evil.com → 403 on API. |
| 2026-11-15 | 2.2 | pass | train.py stages + CLI. MLflow 3.18 defaults to skops serialization → added `skops`; RF needs explicit trusted type `sklearn.tree._tree.Tree`. 3 early runs FAILED (skops missing/untrusted) — kept as real history. data-v1: logreg run c5bf4b86 f1=0.9811 → v1; rf run 174e46bb f1=0.9720 → v2 @candidate. Gate check with threshold 0.99: run 95f960db tagged failed, no version created, alias unchanged. All runs tagged data_version=data-v1, data_md5 e1c1c47…, git_commit 10fea22, git_dirty=true (code uncommitted at the time). pytest 27 passed. Design: `candidate` = most recent gate-passing model, not highest F1. |
| 2026-11-15 | 2.3 | pass | sample_frac 1.0 → 569 rows, md5 b154f6a78a166306105a372ea2344899 (v1 was e1c1c47…). dvc push 1 file; status clean, cache/remote in sync. |
| 2026-11-15 | 2.3b | pass | Trained on data-v2 (clean tree @3cce5da): logreg 974fb6cc f1=0.9861 → v3; rf 25606340 f1=0.9583 → v4 @candidate. Tags data_version=data-v2, git_dirty=false. |
| 2026-11-15 | 2.4 | pass | 7 MlflowClient tools, live ok. find_best_run → 974fb6cc (v3, not @candidate), margin 0.005 flagged as noise, 3 FAILED runs excluded, verdict "not established". Unreachable server mapped to `unavailable` (MLFLOW_HTTP_REQUEST_MAX_RETRIES=0). pytest 32 passed. User asked to batch remaining commits and receive the list at the end. |
| 2026-11-15 | 3.1 | pass | Airflow 3.3.2 (3.4.0 only 9 days old → chose last patch of previous minor). Custom airflow.Dockerfile (+git, mlflow-skinny, skops, joblib, sklearn; pip check clean). LocalExecutor + Postgres 16, FAB auth manager; all secrets from .env (generated locally, not printed). /api/v2/monitor/health all healthy; no token → 401; JWT via POST /auth/token works. Paths verified from live /openapi.json. |
| 2026-11-15 | 3.2 | pass | dags/training_pipeline.py (TaskFlow, retries=2, AirflowFailException for deterministic failures). REST-triggered run manual__2026-11-15T16:19:52.802014+00:00: 5/5 tasks success → MLflow run ded4b2fc (experiment breast-cancer-pipeline, trigger=airflow, data-v2, f1=0.9861) → v5 @candidate. Task logs include training logger lines. test_dag.py 4 passed inside the Airflow image. |
| 2026-11-15 | 3.3 | pass | 5 Airflow tools (httpx + JWT). Live: list_dags ok; trigger of non-allowlisted DAG rejected with no API call; trigger rf → run manual__2026-11-15T16:24:03.786094+00:00 success (v6 @candidate); get_failed_tasks/get_task_logs ok after fixing duplicate `headers` kwarg. pytest 37 passed, 1 skipped (DAG test, runs in image). |
| 2026-11-15 | 4.3 | pass (unit) | kubernetes_tools.py: 6 tools (5 read + restart_deployment, state_changing). v37 client verified (strict pydantic models, `_request_timeout`). ruff isort `known-third-party = ["kubernetes"]` (repo `kubernetes/` folder). tests: namespace rejection before any API call, ImagePullBackOff + warning-event parsing, unreachable → unavailable. Live check pending deployment (4.1/4.2). kind v0.33.0 + kubectl v1.37.2 installed (sha256 verified); cluster `mlops` (k8s v1.37.0). |
| 2026-11-15 | 5.1 | pass | policy.py (deterministic) + orchestrator.py (Gemini manual function-calling loop; state-changing → pending action; unavailable list computed from results; FunctionResponse carries call id). Model: gemini-2.5-flash closed to new users; 3.7/3.8-flash returned 504 on this key; gemini-3.5-flash OK → default AGENT_MODEL. Live smoke: DVC question answered via get_current_dataset_version with exact commit. tests: orchestrator 7, policy 3. |
| 2026-11-15 | 5.2 | pass (unit) | agent/main.py CLI (-m, --save-transcript, /confirm, /pending), api/main.py (/chat, /confirm, /health; sessions; lifespan). test_api 6 cases. pytest 60 passed, 1 skipped. Live API run pending demos. |
| 2026-11-15 | 7.1 | pass (local) | ci.yml: lint-type-test (uv sync --locked, ruff, mypy, pytest) + dag-tests (Airflow image, `airflow db migrate` needed for DagBag in 3.3, 4 passed locally in standalone container). Real GitHub run unverified until user pushes. |
| 2026-11-15 | 4.1 | pass | export_model.py → v6 (@candidate, run 0344b16e, data-v2). inference/app.py loads model.skops via skops (trusted: Tree only); Dockerfile python:3.12.15-slim-bookworm, non-root uid 10001; image 730MB (build slow: Docker Desktop network ~70 kB/s). Local run: /health alive, /ready ready, /version full provenance, /predict [0,1,0], missing features → 422. |
| 2026-11-15 | 4.2 | pass | kind load + kubectl apply: deployment inference-api 2/2 Ready, ClusterIP 10.96.72.129:80, ConfigMap inference-config (4 keys), probes + requests/limits + non-root/RO rootfs. port-forward /version OK. Live K8s tools: all ok; get_deployed_version reads live /version via API pod proxy (fixed: `_preload_content=False` + json.loads, client otherwise returned a str repr); restart in kube-system → rejected. |
| 2026-11-15 | 8.1 (partial) | 3.5 of 5 | Live demos with gemini-3.5-flash: scenario_1 (registry → reproduce data-v2, md5 verified), scenario_2 (5-run comparison; best = v3 logreg-data-v2, not @candidate; noise warning), scenario_3 via HTTP API (trigger held as pending 1943a805 → /confirm → run manual__2026-11-15T17:02:39.131296+00:00 5/5 success → MLflow c1dca733 → v7 @candidate), scenario_4 via API (healthy 2/2 diagnosis; kube-system restart: pending → confirmed → rejected; mlops restart: pending → confirmed → ok, revision 2). Last scenario_4 message hit Gemini 429 (free tier 20 req/day/model) → API 500: fixed orchestrator LLM-error path + test (61 passed). BLOCKED: scenario 5 + failure diagnoses need LLM quota. |
| 2026-11-15 | 8.1 | done | User chose gemini-3.5-flash-lite for remaining runs (flash quota exhausted). scenario_5 attempt 1 missed Airflow + deployed/best mismatch → prompt rules 4–5 tightened → attempt 2 used MLflow+DVC+K8s+Airflow+policy, Facts/Uncertainties/Actions, flagged v6 vs v3/v7 mismatch, no trigger. |
| 2026-11-15 | 6.2 | done | failure_a: `kubectl set image ... model-v99` → ImagePullBackOff (old pods kept serving). Agent diagnosed via list_services/find_unhealthy_pods/get_deployed_version (pull error quoted). Operator `rollout undo` → 2/2; agent verified. Review notes flash-lite lapses (unbacked "model-v5", alias_at_export vs registry alias). |
| 2026-11-15 | 6.3 | done | failure_b: blanked 5 mean_radius values → agent trigger held 32c61fbb → confirmed → run manual__2026-11-15T17:10:08.165848+00:00: validate failed (AirflowFailException, no retry), 4 upstream_failed, nothing registered. Attempt 1 exposed get_task_logs ignoring Airflow 3 `error_detail` → fixed `_log_line` + test; registry tool description names default model. Attempt 2 named "5 missing values", linked to check_data_changes. Restored with `dvc checkout --force`, md5 b154f6a7 = data-v2, status clean. |
| 2026-11-15 | 6.1 | done | 62 passed + 1 skipped in main env; 4 DAG tests pass in Airflow image. Every §6 bullet mapped in RTM. |
| 2026-11-15 | evidence | done | docs/evidence/system_exports.md (DVC tags/diff/status, MLflow runs+registry, Airflow runs+task states, kubectl state/history/describe). Disclosure: Airflow run 16:24:03 note says "triggered by MLOps agent after explicit user confirmation" but it was triggered by calling trigger_dag directly during item 3.3 verification (hard-coded note). |
| 2026-11-15 | 8.2–8.4 | done | README.md, architecture.md, docs/REPORT.md (all §9 items; 9 screenshots listed with placeholders), docs/CODE_WALKTHROUGH.md. Facts cross-checked against transcripts (fixed: scenario 3 list_runs error, orchestrator line count). |
| 2026-11-15 | 8.5 | done | Line-by-line spec re-read. Found + fixed: get_run_details missed MLflow 3 logged-model files (added search_logged_models/list_logged_model_artifacts + test; live output in system_exports.md); `data/` absent from git → data/.gitkeep. Fresh `uv sync --locked`: ruff/format/mypy clean, 63 passed + 1 skipped; DAG tests 4 passed in Airflow image. Secret scan of all untracked files: none. Remaining unverified: real GitHub Actions run (needs push); UI screenshots (user). |
