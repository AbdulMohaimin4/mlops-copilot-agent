# Code Walkthrough (defense guide)

This guide follows the code in reading order, traces one request end to end, lists every safety control
and where it lives, records the design decisions with the alternatives rejected, and explains how the two
injected failures were diagnosed. Function names are given so you can jump straight to them.

## 1. File tour in reading order

### Foundation: read these first

1. **`agent/schemas.py`**: the contract every tool honours. `ToolStatus` (`ok` / `error` /
   `unavailable`), `ToolError(type, message)`, and `ToolResult(tool, status, data, error, duration_ms)`.
   `NoInput` is the input model for tools without arguments.
2. **`agent/config.py`**: a frozen `Settings` dataclass built by `load_settings()` from environment
   variables with defaults. It holds no secrets: passwords and keys are read where they are used
   (`AIRFLOW_PASSWORD` in `airflow_tools._request`, `GEMINI_API_KEY` by the SDK).
3. **`agent/logging_config.py`**:
   - `request_id_var` is a `ContextVar`, so every log line in a request carries its ID without passing it around.
   - `JsonFormatter` writes one JSON object per line with `tool`, `status`, `duration_ms` and `error_type`.
   - `redact()` replaces the values of `GEMINI_API_KEY` and `AIRFLOW_PASSWORD` and any `Bearer …` token.
4. **`agent/tools/common.py`**: the single execution path.
   - `Tool.from_function(func)` builds a tool from a function: its name, its docstring (which becomes the
     LLM-facing description), and the annotation of its one parameter (the Pydantic input model).
   - `execute_tool(tool, raw_args, timeout)` validates the input, runs the function in a shared thread pool
     with `future.result(timeout=…)`, maps exceptions to statuses, redacts error messages and writes one log
     line. It never raises.
   - `ServiceUnavailableError` and `ActionRejectedError` are how tools signal "unreachable" and "refused by policy".

### The four tool modules

5. **`agent/tools/dvc_tools.py`**:
   - `_run_allowlisted(program, args)` is the only place a subprocess starts. It accepts only argument
     lists whose first words match `ALLOWED_COMMANDS` (e.g. `("params","diff")`, `("tag","--list")`), runs
     `[python -m dvc | git, *args]` with no shell, in `PROJECT_DIR`, with a timeout.
   - `_locked_outputs(rev)` reads `dvc.lock` at any revision via `git show rev:dvc.lock`, giving per-file md5s.
   - The five tools build on these two helpers. `reproduce_dataset_version` checks that the output path
     stays inside `scratch/` (`is_relative_to`) before `dvc get`, then compares md5s.
6. **`agent/tools/mlflow_tools.py`**:
   - `_client()` builds an `MlflowClient` and sets MLflow's own retry/timeout environment variables, so it fails fast.
   - `_call()` turns connection-level `MlflowException`s into `ServiceUnavailableError`.
   - `get_run_details` lists the run's artifacts **and** the files of the models it logged
     (`search_logged_models` + `list_logged_model_artifacts`), because MLflow 3 stores models separately.
   - `find_best_run` ranks only `FINISHED` runs that logged the metric and lists the excluded runs with
     reasons. It reports the margin over the runner-up and a small-test-set warning. It returns
     `production_readiness.verdict = "not established…"` with the evidence that exists (gate, registry
     aliases, data version, whether the code was committed) and what is missing (deployment health,
     fresh-data evaluation, human review).
7. **`agent/tools/airflow_tools.py`**:
   - `_request()` gets a JWT from `POST /auth/token` and then calls `/api/v2…` with `Authorization: Bearer`.
     It maps connect/timeout errors to `unavailable`, 401/403 to `PermissionError` and 404 to `LookupError`.
   - `trigger_dag` checks `AIRFLOW_ALLOWED_DAGS` before any HTTP call.
   - `_log_line()` flattens Airflow 3 structured logs, including `error_detail` (see failure B).
8. **`agent/tools/kubernetes_tools.py`**:
   - `_api_client()` loads the kubeconfig context; `_call()` adds `_request_timeout` and maps errors.
   - `_pod_summary` / `_container_states` extract readiness, restarts, waiting reasons and last termination.
   - `list_services` counts ready endpoints from EndpointSlices.
   - `get_deployed_version` combines the image, labels, rollout revision and ConfigMap with the live
     `/version` of a ready pod, fetched through the API server's pod proxy (`_preload_content=False`).
   - `restart_deployment` rejects any namespace other than `K8S_APPROVED_NAMESPACE` *before* creating a
     client, then patches the `kubectl.kubernetes.io/restartedAt` annotation, which is what
     `kubectl rollout restart` does.
9. **`agent/policy.py`**: `evaluate_retraining_need` is a pure function of the evidence passed in
   (gate result, model creation time, model and current data versions, optional degradation results). It
   returns `retraining_recommended`, `reasons`, `unknown_inputs` and the policy, with `auto_trigger: False`.

### Orchestration and interfaces

10. **`agent/orchestrator.py`**: the agent loop. Read `SYSTEM_PROMPT` first, then `Orchestrator`
    (`chat` → `_chat` → `_dispatch` → `_execute` / `_hold_for_confirmation`, `confirm`, `_response`).
    `GeminiLLM` is the only Gemini-specific class; the `LLM` protocol lets tests swap in a scripted model.
11. **`agent/main.py`**: the CLI. `handle()` routes `/confirm <id>` and `/pending`. `render_markdown()`
    writes the transcripts in `docs/evidence/`.
12. **`api/main.py`**: the FastAPI interface. `ChatRequest` / `ConfirmRequest` validate input (length,
    hex IDs). There is one `Orchestrator` per session (an LRU of 100), and `/confirm` returns 404 for
    unknown or already-executed actions.

### The ML system the agent operates

13. **`training/prepare_data.py`** + `dvc.yaml` + `params.yaml`: the DVC `prepare` stage. `sample_frac`
    selects the dataset version, and the seed makes it deterministic.
14. **`training/train.py`**: the stages as plain functions (`validate_dataset`, `preprocess`,
    `train_model`, `evaluate_and_log`, `apply_quality_gate`) plus `collect_provenance` and a CLI.
    `apply_quality_gate` is the **only** code that registers a model and moves the `candidate` alias.
15. **`dags/training_pipeline.py`**: a TaskFlow DAG wrapping those stages. `default_args` sets
    `retries=2`; `DataValidationError` and `QualityGateError` become `AirflowFailException` (fail now,
    don't retry).
16. **`training/export_model.py`**, **`inference/app.py`**, **`Dockerfile`**, **`kubernetes/*.yaml`**:
    the model export with `model_info.json`; the inference service (`/health` = liveness, `/ready` = model
    loaded, `/version`, `/predict`); and the Deployment, Service and ConfigMap.
17. **`docker-compose.yml`**, **`airflow.Dockerfile`**: the MLflow and Airflow runtime.
    **`.github/workflows/ci.yml`**: CI.

## 2. Request flow: from user message to answer

Example: *"Why is my prediction API unavailable?"*

1. `api/main.py:chat` (or `agent/main.py:handle`) validates the body and calls `Orchestrator.chat`.
2. `chat` sets a fresh `request_id` in `request_id_var`; `_chat` appends the user message to `history`.
3. `GeminiLLM.generate` sends the history together with `SYSTEM_PROMPT` and one `FunctionDeclaration`
   per tool (name, docstring, `input_model.model_json_schema()`), with automatic function calling
   **disabled**: Gemini proposes calls, our code decides.
4. Gemini returns function calls, e.g. `list_services`, `find_unhealthy_pods`, `get_deployed_version`.
   For each one, `_dispatch`:
   - looks the name up in `self.tools` (an unknown name gives an `unknown_tool` error);
   - if `tool.state_changing`, calls `_hold_for_confirmation`: validates the arguments, stores a
     `PendingAction`, and returns `error.type = "confirmation_required"` with the action ID. **Nothing runs**;
   - otherwise calls `execute_tool`: validate → run with timeout → `ToolResult` → log line.
5. All results of the round go back in one `Content(role="user")` of `FunctionResponse` parts, each
   carrying the call's `id`. The model's own turn is appended unchanged, which preserves Gemini's thought
   signatures.
6. The loop repeats until the model replies without calling tools, or until `MAX_TOOL_ROUNDS` is reached.
7. `_response` collects the tools whose status is `unavailable`, appends a fixed notice to the answer if
   there are any, and returns `AgentResponse(answer, tool_calls, unavailable_tools, pending_actions)`.
8. If the user later sends `/confirm <id>`, `confirm` pops the pending action (so it can run only once),
   runs it through the same `execute_tool`, and records the result in the history.
9. If `generate` raises (e.g. `429 RESOURCE_EXHAUSTED`), `_chat` deletes this turn from the history and
   answers "The language model is unavailable…".

## 3. Safety controls and where they live

| # | Control | Code | Test |
|---|---|---|---|
| 1 | State-changing tools never execute from an LLM call | `Orchestrator._dispatch`, `_hold_for_confirmation`, `confirm` | `test_state_changing_call_waits_for_explicit_confirmation`, `test_chat_then_confirm_executes_pending_action_once` |
| 2 | Cluster writes only in the approved namespace, checked even after confirmation | `kubernetes_tools.restart_deployment` | `test_restart_outside_approved_namespace_is_rejected_before_any_api_call` |
| 3 | Only allowlisted DAGs can be triggered | `airflow_tools.trigger_dag` | `test_non_allowlisted_dag_is_rejected_without_calling_airflow` |
| 4 | Only read-only git/dvc commands, argv without a shell | `dvc_tools._run_allowlisted` | `test_non_allowlisted_commands_are_rejected_without_running`, `test_commands_run_as_argv_list_without_shell` |
| 5 | Input validation blocks option injection, traversal and bad IDs | Pydantic `Field(pattern=…)` in every input model | `test_unsafe_revisions_are_invalid_requests`, `test_malformed_*`, `test_invalid_chat_requests_are_rejected` |
| 6 | Timeouts on every external call | `execute_tool` + per-client timeouts (`subprocess timeout`, `httpx.Timeout`, `_request_timeout`, MLflow env, `HttpOptions.timeout`) | `test_timeout_reports_unavailable_without_data` |
| 7 | Unavailable tools are surfaced, never filled in | `ToolResult.data=None` for unavailable; `_response` notice; prompt rule 2 | `test_unavailable_tool_is_surfaced_even_if_model_text_omits_it` |
| 8 | No retraining as a side effect | `trigger_dag` is state-changing; prompt rule 4; `policy.auto_trigger=False` | `test_integration_scenario_uses_all_four_systems_without_retraining` |
| 9 | Failed validation or gate blocks promotion | `train.apply_quality_gate` (only registrar), `AirflowFailException` in the DAG | `test_quality_gate_failure_blocks_promotion`, `test_deterministic_failures_fail_fast_without_retry` |
| 10 | Secrets never in files, logs or LLM context | `.gitignore` (`.env`), `redact()` in formatter and `_result` | `test_structured_log_has_required_fields_and_no_secrets`, `test_rejected_credentials_are_an_error_without_leaking_password` |
| 11 | Untrusted model files cannot execute code | skops format; only `sklearn.tree._tree.Tree` trusted (`train.SKOPS_TRUSTED_TYPES`, `inference/app.TRUSTED_TYPES`) | verified live (an RF save was refused until trusted) |
| 12 | Pipeline can't modify the repo | Airflow mounts the repo `:ro` (`docker-compose.yml`) | by configuration |

## 4. Design decisions and rejected alternatives

| Decision | Rejected alternative | Why |
|---|---|---|
| Hand-written function-calling loop | LangChain / agent framework | Every control (confirmation, trace, unavailable list) is explicit and unit-testable in one file |
| Confirmation enforced by the orchestrator | A `confirm: bool` tool parameter | The LLM fills tool parameters itself; only the user can call `/confirm` |
| `ToolResult` with three statuses | Raise exceptions to the LLM | The model sees a uniform, machine-readable signal and can say "unavailable" instead of guessing |
| Deterministic `evaluate_retraining_need` | Let the LLM decide freshness | Reproducible and testable; the LLM only gathers evidence |
| `candidate` = latest gate-passing model | Alias the highest-F1 run | Promotion follows a rule (the gate); "best" is an explained, separate ranking |
| Provenance = md5 of the file actually used vs `dvc.lock` at the nearest `data-v*` tag | Trust the tag name | A modified file is recorded as `untagged`, never mislabelled |
| Model baked into the image | Pods pull from MLflow at start-up | No cluster→host network dependency; the image tag pins the model |
| skops serialization (MLflow 3.18 default) | pickle | Loading cannot run arbitrary code |
| LocalExecutor + Postgres | Celery + Redis | Fits ~8 GB of WSL memory; intermediate files can live in `/tmp` |
| Airflow 3.3.2 | 3.4.0 | 3.4.0 was 9 days old; 3.3.2 is the last patch of the previous minor |
| DAG tests inside the Airflow image | Install Airflow in the agent's venv | `apache-airflow-core` pins `fastapi<0.137`; the agent API uses 0.163 |
| `gemini-3.5-flash` default, overridable via `AGENT_MODEL` | `gemini-2.5-flash` / `3.7` / `3.8` | 2.5 is closed to new users; 3.7/3.8 timed out on this key |

## 5. How the injected failures were diagnosed

**Failure A: bad image tag** ([evidence](evidence/failure_a_image_pull.md))
- The question "Why is my prediction API unavailable?" led the model to call:
  - `list_services` → 2 ready / 1 not-ready endpoint;
  - `find_unhealthy_pods` → pod `…h9sbn` waiting `ImagePullBackOff`, Warning event "pull access denied";
  - `get_deployed_version` → Deployment now references `mlops-inference:model-v99`.
- Conclusion: the new image doesn't exist, so the rollout is stuck. The old pods keep serving because the
  rolling update has maxUnavailable 0 with 2 replicas.
- Fix: operator `kubectl rollout undo`; the agent then confirmed 2/2 on `model-v6`.
- Defense point: the agent cannot change images; that is deliberately outside its write permissions.

**Failure B: corrupted data** ([evidence](evidence/failure_b_validation.md))
- Blank values in 5 rows → `validate_dataset` raised `AirflowFailException`. It was not retried (a
  deterministic failure), downstream tasks were `upstream_failed`, so the gate never ran and nothing was
  registered.
- The first diagnosis exposed a tool bug: Airflow 3 puts the exception in `error_detail`, which
  `get_task_logs` ignored. After the `_log_line` fix the model reported: `get_failed_tasks` →
  `validate_dataset` failed; `get_task_logs` → "AirflowFailException: data validation failed: 5 missing
  values"; `check_data_changes` → `data/breast_cancer.csv` modified versus `dvc.lock`;
  `get_model_registry_info` → `@candidate` still v7, so nothing was promoted.
- Fix: `dvc checkout --force data/breast_cancer.csv` restored md5 `b154f6a7…` (data-v2).

**Unplanned failure: LLM quota.** Gemini `429 RESOURCE_EXHAUSTED` made `/chat` return HTTP 500. Root
cause: `llm.generate` errors weren't handled. Fix: the handler in `_chat` (rolls back the turn and answers
honestly), plus a test.
