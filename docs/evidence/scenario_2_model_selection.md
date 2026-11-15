# Scenario 2: Model selection

Recorded 2026-11-15T17:01Z with `uv run --env-file .env python -m agent.main --save-transcript docs/evidence/scenario_2_model_selection.md -m ...`
(model: gemini-3.5-flash, live MLflow). JSON logs: `logs/scenario_2.jsonl`.

---

### User

Compare the last five training runs and recommend the best candidate based on F1 score.

### Tool calls (request `1c28514d9d85`)

1. `list_runs({"max_results": 5})` → **ok** (338.6 ms)

```json
{
  "experiment_id": "1",
  "runs": [
    {
      "run_id": "2560634072c64931a240e537798a2734",
      "run_name": "rf-data-v2",
      "status": "FINISHED",
      "start_time": "2026-11-15T16:05:36.663000+00:00",
      "model_type": "rf",
      "metrics": {
        "f1": 0.9583,
        "accuracy": 0.9474,
        "precision": 0.9583,
        "recall": 0.9583,
        "roc_auc": 0.994,
        "test_rows": 114.0
      },
      "provenance": {
        "data_version": "data-v2",
        "data_md5": "b154f6a78a166306105a372ea2344899",
        "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
        "git_dirty": "false",
        "quality_gate": "passed",
        "trigger": "cli",
        "airflow_run_id": null
      }
    },
    {
      "run_id": "974fb6ccd65b478c9748fc5e3eedc95f",
      "run_name": "logreg-data-v2",
      "status": "FINISHED",
      "start_time": "2026-11-15T16:05:32.432000+00:00",
      "model_type": "logreg",
      "metrics": {
        "f1": 0.9861,
        "accuracy": 0.9825,
        "precision": 0.9861,
        "recall": 0.9861,
        "roc_auc": 0.9954,
        "test_rows": 114.0
      },
      "provenance": {
        "data_version": "data-v2",
        "data_md5": "b154f6a78a166306105a372ea2344899",
        "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
        "git_dirty": "false",
        "quality_gate": "passed",
        "trigger": "cli",
        "airflow_run_id": null
      }
    },
    {
      "run_id": "95f960dbb7b4432fa460d14497df667e",
      "run_name": "logreg-data-v1",
      "status": "FINISHED",
      "start_time": "2026-11-15T15:58:33.537000+00:00",
      "model_type": "logreg",
      "metrics": {
        "f1": 0.9811,
        "accuracy": 0.975,
        "precision": 0.963,
        "recall": 1.0,
        "roc_auc": 0.9973,
        "test_rows": 80.0
      },
      "provenance": {
        "data_version": "data-v1",
        "data_md5": "e1c1c4721fa52fd770840aee20006c89",
        "git_commit": "10fea221e21c1fea1ec1f75867c70a931ba2b279",
        "git_dirty": "true",
        "quality_gate": "failed",
        "trigger": "cli",
        "airflow_run_id": null
      }
    },
    {
      "run_id": "174e46bb92204333ba990bc5831bbd2d",
      "run_name": "rf-data-v1",
      "status": "FINISHED",
      "start_time": "2026-11-15T15:58:19.595000+00:00",
      "model_type": "rf",
      "metrics": {
        "f1": 0.972,
        "accuracy": 0.9625,
        "precision": 0.9455,
        "recall": 1.0,
        "roc_auc": 0.9973,
        "test_rows": 80.0
      },
      "provenance": {
        "data_version": "data-v1",
        "data_md5": "e1c1c4721fa52fd770840aee20006c89",
        "git_commit": "10fea221e21c1fea1ec1f75867c70a931ba2b279",
        "git_dirty": "true",
        "quality_gate": "passed",
        "trigger": "cli",
        "airflow_run_id": null
      }
    },
    {
      "run_id": "da108a0f03034307a40b19c79f5bcbc1",
      "run_name": "rf-data-v1",
      "status": "FAILED",
      "start_time": "2026-11-15T15:58:07.161000+00:00",
      "model_type": "rf",
      "metrics": {
        "f1": 0.972,
        "accuracy": 0.9625,
        "precision": 0.9455,
        "recall": 1.0,
        "roc_auc": 0.9973,
        "test_rows": 80.0
      },
      "provenance": {
        "data_version": "data-v1",
        "data_md5": "e1c1c4721fa52fd770840aee20006c89",
        "git_commit": "10fea221e21c1fea1ec1f75867c70a931ba2b279",
        "git_dirty": "true",
        "quality_gate": "pending",
        "trigger": "cli",
        "airflow_run_id": null
      }
    }
  ]
}
```

2. `find_best_run({"metric": "f1", "max_results": 5})` → **ok** (198.1 ms)

```json
{
  "best_run": {
    "run_id": "974fb6ccd65b478c9748fc5e3eedc95f",
    "run_name": "logreg-data-v2",
    "status": "FINISHED",
    "start_time": "2026-11-15T16:05:32.432000+00:00",
    "model_type": "logreg",
    "metrics": {
      "f1": 0.9861,
      "accuracy": 0.9825,
      "precision": 0.9861,
      "recall": 0.9861,
      "roc_auc": 0.9954,
      "test_rows": 114.0
    },
    "provenance": {
      "data_version": "data-v2",
      "data_md5": "b154f6a78a166306105a372ea2344899",
      "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
      "git_dirty": "false",
      "quality_gate": "passed",
      "trigger": "cli",
      "airflow_run_id": null
    }
  },
  "ranking": [
    {
      "run_id": "974fb6ccd65b478c9748fc5e3eedc95f",
      "run_name": "logreg-data-v2",
      "f1": 0.9861,
      "quality_gate": "passed",
      "data_version": "data-v2"
    },
    {
      "run_id": "95f960dbb7b4432fa460d14497df667e",
      "run_name": "logreg-data-v1",
      "f1": 0.9811,
      "quality_gate": "failed",
      "data_version": "data-v1"
    },
    {
      "run_id": "174e46bb92204333ba990bc5831bbd2d",
      "run_name": "rf-data-v1",
      "f1": 0.972,
      "quality_gate": "passed",
      "data_version": "data-v1"
    },
    {
      "run_id": "2560634072c64931a240e537798a2734",
      "run_name": "rf-data-v2",
      "f1": 0.9583,
      "quality_gate": "passed",
      "data_version": "data-v2"
    }
  ],
  "rationale": {
    "selected_by": "highest f1 among 4 finished runs of the 5 most recent",
    "margin_over_runner_up": 0.005,
    "tied_with": [],
    "small_test_set_warning": "margins below ~0.01 F1 are within noise for a test set of 114 rows"
  },
  "production_readiness": {
    "verdict": "not established: a top metric alone does not make a model production-ready",
    "evidence": {
      "quality_gate": "passed",
      "registry": [
        {
          "model": "breast-cancer-classifier",
          "version": "3",
          "aliases": []
        }
      ],
      "data_version": "data-v2",
      "code_committed": true
    },
    "missing_evidence": [
      "deployment health of this model version (check Kubernetes)",
      "evaluation on fresh or production data",
      "human review / approval"
    ]
  },
  "excluded": [
    {
      "run_id": "da108a0f03034307a40b19c79f5bcbc1",
      "reason": "status FAILED"
    }
  ]
}
```

3. `get_model_registry_info({})` → **ok** (54.5 ms)

```json
{
  "model": "breast-cancer-classifier",
  "aliases": {
    "candidate": "6"
  },
  "versions": [
    {
      "version": "6",
      "run_id": "0344b16e77de48f59d683fa1567eb3fa",
      "status": "READY",
      "created": "2026-11-15T16:24:26.249000+00:00",
      "tags": {
        "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
        "data_version": "data-v2",
        "data_md5": "b154f6a78a166306105a372ea2344899",
        "run_id": "0344b16e77de48f59d683fa1567eb3fa"
      }
    },
    {
      "version": "5",
      "run_id": "ded4b2fc76714095bbf18087e52050ba",
      "status": "READY",
      "created": "2026-11-15T16:20:24.773000+00:00",
      "tags": {
        "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
        "data_version": "data-v2",
        "data_md5": "b154f6a78a166306105a372ea2344899",
        "run_id": "ded4b2fc76714095bbf18087e52050ba"
      }
    },
    {
      "version": "4",
      "run_id": "2560634072c64931a240e537798a2734",
      "status": "READY",
      "created": "2026-11-15T16:05:38.655000+00:00",
      "tags": {
        "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
        "data_version": "data-v2",
        "data_md5": "b154f6a78a166306105a372ea2344899",
        "run_id": "2560634072c64931a240e537798a2734"
      }
    },
    {
      "version": "3",
      "run_id": "974fb6ccd65b478c9748fc5e3eedc95f",
      "status": "READY",
      "created": "2026-11-15T16:05:33.999000+00:00",
      "tags": {
        "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
        "data_version": "data-v2",
        "data_md5": "b154f6a78a166306105a372ea2344899",
        "run_id": "974fb6ccd65b478c9748fc5e3eedc95f"
      }
    },
    {
      "version": "2",
      "run_id": "174e46bb92204333ba990bc5831bbd2d",
      "status": "READY",
      "created": "2026-11-15T15:58:21.629000+00:00",
      "tags": {
        "git_commit": "10fea221e21c1fea1ec1f75867c70a931ba2b279",
        "data_version": "data-v1",
        "data_md5": "e1c1c4721fa52fd770840aee20006c89",
        "run_id": "174e46bb92204333ba990bc5831bbd2d"
      }
    },
    {
      "version": "1",
      "run_id": "c5bf4b86056443e0abb0231a80aa5b1e",
      "status": "READY",
      "created": "2026-11-15T15:58:04.451000+00:00",
      "tags": {
        "git_commit": "10fea221e21c1fea1ec1f75867c70a931ba2b279",
        "data_version": "data-v1",
        "data_md5": "e1c1c4721fa52fd770840aee20006c89",
        "run_id": "c5bf4b86056443e0abb0231a80aa5b1e"
      }
    }
  ]
}
```

4. `get_deployed_version({})` → **ok** (113.1 ms)

```json
{
  "deployment": "inference-api",
  "images": [
    "mlops-inference:model-v6"
  ],
  "version_label": "model-v6",
  "revision": "1",
  "configmap_app_version": {
    "inference-config": "model-v6"
  },
  "ready_pods": 2,
  "live_version_from_pod": {
    "app_version": "model-v6",
    "model_name": "breast-cancer-classifier",
    "model_version": "6",
    "alias_at_export": "candidate",
    "run_id": "0344b16e77de48f59d683fa1567eb3fa",
    "data_version": "data-v2",
    "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
    "exported_at": "2026-11-15T16:30:22.919232+00:00"
  }
}
```

### Agent

**Facts**
- `list_runs`: The 5 most recent training runs are:
  1. **`2560634072c64931a240e537798a2734`** (`rf-data-v2`): `FINISHED`, F1 = `0.9583`, Accuracy = `0.9474`, Recall = `0.9583`, Precision = `0.9583`, ROC AUC = `0.9940`, Data Version = `data-v2` (md5: `b154f6a78a166306105a372ea2344899`), Quality Gate = `passed`.
  2. **`974fb6ccd65b478c9748fc5e3eedc95f`** (`logreg-data-v2`): `FINISHED`, F1 = `0.9861`, Accuracy = `0.9825`, Recall = `0.9861`, Precision = `0.9861`, ROC AUC = `0.9954`, Data Version = `data-v2` (md5: `b154f6a78a166306105a372ea2344899`), Quality Gate = `passed`.
  3. **`95f960dbb7b4432fa460d14497df667e`** (`logreg-data-v1`): `FINISHED`, F1 = `0.9811`, Accuracy = `0.9750`, Recall = `1.0000`, Precision = `0.9630`, ROC AUC = `0.9973`, Data Version = `data-v1` (md5: `e1c1c4721fa52fd770840aee20006c89`), Quality Gate = `failed`.
  4. **`174e46bb92204333ba990bc5831bbd2d`** (`rf-data-v1`): `FINISHED`, F1 = `0.9720`, Accuracy = `0.9625`, Recall = `1.0000`, Precision = `0.9455`, ROC AUC = `0.9973`, Data Version = `data-v1` (md5: `e1c1c4721fa52fd770840aee20006c89`), Quality Gate = `passed`.
  5. **`da108a0f03034307a40b19c79f5bcbc1`** (`rf-data-v1`): `FAILED`, F1 = `0.9720`, Accuracy = `0.9625`, Recall = `1.0000`, Precision = `0.9455`, ROC AUC = `0.9973`, Data Version = `data-v1` (md5: `e1c1c4721fa52fd770840aee20006c89`), Quality Gate = `pending`.
- `find_best_run`: Run `974fb6ccd65b478c9748fc5e3eedc95f` (`logreg-data-v2`) achieved the highest F1 score (`0.9861`), leading runner-up `95f960dbb7b4432fa460d14497df667e` by a margin of `0.0050`.
- `get_model_registry_info`: Run `974fb6ccd65b478c9748fc5e3eedc95f` is registered as Model Version `3` under `breast-cancer-classifier`, but currently has no active registry alias. The `candidate` alias is currently assigned to Model Version `6` (run `0344b16e77de48f59d683fa1567eb3fa`).
- `get_deployed_version`: Kubernetes deployment `inference-api` is running image version `model-v6` (Model Version `6`, run `0344b16e77de48f59d683fa1567eb3fa`).

**Uncertainties**
- The test set size for run `974fb6ccd65b478c9748fc5e3eedc95f` is 114 rows; F1 margins under ~0.01 fall within statistical noise.
- Model Version `3` has passed its quality gate, but lacks human approval and is not promoted or aliased in the registry.

**Recommended actions**
- Evaluate candidate Model Version `3` (run `974fb6ccd65b478c9748fc5e3eedc95f`) against currently deployed Model Version `6` on fresh data.
- If performance is verified and approved, assign appropriate registry aliases prior to deployment consideration.

---

