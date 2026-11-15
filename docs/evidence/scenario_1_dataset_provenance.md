# Scenario 1: Dataset provenance

Recorded 2026-11-15T17:01Z with `uv run --env-file .env python -m agent.main --save-transcript docs/evidence/scenario_1_dataset_provenance.md -m ...`
(model: gemini-3.5-flash, live DVC/MLflow/Airflow/Kubernetes). JSON logs: `logs/scenario_1.jsonl`.

---

### User

Which dataset version was used to train the model that is currently aliased as candidate in the MLflow registry? Give me the git commit and the data hash, and show me how to reproduce that exact dataset.

### Tool calls (request `a9aab93d9b94`)

1. `get_model_registry_info({})` → **ok** (291.3 ms)

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

2. `reproduce_dataset_version({"rev": "data-v2"})` → **ok** (989.8 ms)

```json
{
  "rev": "data-v2",
  "git_commit": "3cce5da5b838f9c79f1bc98b7f418eee42ef8d36",
  "files": [
    {
      "path": "data/breast_cancer.csv",
      "reproduced_to": "/home/a/Projects/university/mlops-copilot-agent/scratch/data-v2/data/breast_cancer.csv",
      "expected_md5": "b154f6a78a166306105a372ea2344899",
      "actual_md5": "b154f6a78a166306105a372ea2344899",
      "verified": true
    }
  ],
  "all_verified": true,
  "restore_steps": [
    "git checkout data-v2  # or: git worktree add ../repro-data-v2 data-v2",
    "dvc pull  # fetch the exact files recorded in dvc.lock from the DVC remote",
    "dvc status  # must report no changes",
    "dvc repro  # optional: rebuild from params.yaml + code; same md5 expected"
  ]
}
```

### Agent

### **Facts**
* **Candidate Model Alias**: Points to version `6` of model `breast-cancer-classifier` (Run ID: `0344b16e77de48f59d683fa1567eb3fa`). [Source: `get_model_registry_info`]
* **Dataset Version**: `data-v2` [Source: `get_model_registry_info`]
* **Git Commit**: `3cce5da5b838f9c79f1bc98b7f418eee42ef8d36` [Source: `get_model_registry_info`, `reproduce_dataset_version`]
* **Data Hash (MD5)**: `b154f6a78a166306105a372ea2344899` (for `data/breast_cancer.csv`) [Source: `get_model_registry_info`, `reproduce_dataset_version`]

---

### **Reproduction Steps** [Source: `reproduce_dataset_version`]
To reproduce this exact dataset version locally:

1. Checkout the git revision:
   ```bash
   git checkout data-v2
   # Or using a worktree:
   git worktree add ../repro-data-v2 data-v2
   ```
2. Fetch the dataset tracked by DVC:
   ```bash
   dvc pull
   ```
3. Verify local data state against `dvc.lock`:
   ```bash
   dvc status
   ```
4. *(Optional)* Re-execute the pipeline stage:
   ```bash
   dvc repro
   ```

---

### **Uncertainties**
* None. All requested attributes were successfully retrieved and verified against DVC.

---

### **Recommended Actions**
* Execute `git checkout data-v2` followed by `dvc pull` to bring your workspace to the exact data state used by candidate model version 6.

---

