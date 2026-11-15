# CLAUDE.md

Production-ready MLOps AI agent (university Assignment 03). Spec: `Assignment_03_MLOps_AI_Agent.md` (single source of truth).
**Read `PLAN.md` and continue from the first unchecked item.** Append to its Progress Log after every item.

## Commands
- Setup: `uv sync` · copy `.env.example` → `.env` and fill secrets locally (never commit)
- Lint/format: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run mypy agent api`
- Tests: `uv run pytest -q`
- Standard gate (after every item): all four of the above chained with `&&`
- DAG tests (need Airflow; run in its image): `docker run --rm -v "$PWD:/opt/airflow/project:ro" -w /opt/airflow/project
  -e PYTHONPATH=/opt/airflow/project --entrypoint bash mlops-airflow:3.3.2 -c "airflow db migrate >/dev/null &&
  python -m pip install -q pytest==9.2.0 && python -m pytest -q -p no:cacheprovider tests/test_dag.py"`
- MLflow + Airflow: `docker compose up -d` (MLflow :5000, Airflow API :8080)
- Data: `uv run dvc repro && uv run dvc push`
- Train locally: `uv run python -m training.train --model logreg|rf`
- K8s: `kind create cluster --name mlops` · build/load image · `kubectl apply -f kubernetes/`
- Agent CLI: `uv run python -m agent.main` · Agent API: `uv run uvicorn api.main:app --port 8000`

## Hard rules
- Git: the user makes every commit. Never `commit`, `push`, `tag`, `merge`, `rebase`, `reset`, `stash`, `checkout <branch>`,
  create branches/PRs. Read-only git is fine. `git add` only when asked.
- Commit checkpoint protocol: at a 🔒 item, STOP and print:
  `🔒 COMMIT CHECKPOINT <n>: <reason>` / `Run:` / `git add <exact paths>` / `git commit -m "<conventional msg>"` /
  optional `git tag <tag>` / `Reply "committed" when done.` Then wait; afterwards verify with `git log -1 --stat`
  (and `git tag`). Dataset versions must be committed + tagged (`data-v1`, `data-v2`) before dependent training runs.
- No fabrication: no invented outputs, metrics, run IDs, hashes, logs, screenshots. Unrunnable → say so, mark unverified.
- No secrets in files. Env vars only; `.env` is gitignored; logs redact secrets.
- Never weaken tests or requirements to pass; fix the code or stop and report.
- Verify library APIs (Airflow 3 REST/auth, MlflowClient, DVC CLI, kubernetes client, google-genai) against docs or
  installed source, not memory.

## Conventions
- Python 3.12, uv, everything in `pyproject.toml`, pinned versions, lockfile committed.
- ruff lint + format; full type hints; mypy clean on `agent/` and `api/`.
- Every tool: Pydantic v2 input model + `ToolResult` (`status` ok/error/unavailable, `data`, `error`) in `agent/schemas.py`.
- Follow spec §7 layout (+ `inference/` for the K8s service). No speculative abstractions, no dead code.
- Small single-purpose functions, clear names. PEP 8/257 docstrings on modules, public functions, every tool
  (the LLM reads them). Inline comments only for non-obvious intent.
- JSON logging via stdlib `logging`: tool, status, duration_ms, request_id; never secrets.
- Timeouts on every external call; config only via env vars with sane defaults.
- Safety: DVC/git subprocess allowlist (argv, no shell); Airflow DAG allowlist; state-changing ops become pending
  actions that run only after explicit user confirmation; K8s writes only in namespace `mlops`.
- Approved namespace: `mlops`. LLM: Gemini via `google-genai`, model from `AGENT_MODEL` (default gemini-3.5-flash;
  free tier = 20 requests/day/model, gemini-3.5-flash-lite used when exhausted).
- Never use `pkill -f <pattern>` in a command that contains the pattern (it kills its own shell).
