# Exported evidence from DVC, MLflow, Airflow and Kubernetes

Generated 2026-11-15T17:12Z by running the commands shown (outputs verbatim, secrets never printed).

## DVC

```
$ git tag -l "data-v*" -n1
data-v1         feat(data): DVC prepare stage and dataset v1 (70% sample)
data-v2         feat(data): dataset v2 (full 569 rows)
$ git log --oneline -- dvc.lock
3cce5da feat(data): dataset v2 (full 569 rows)
deb5869 feat(data): DVC prepare stage and dataset v1 (70% sample)
$ dvc list . --dvc-only -R
data/breast_cancer.csv
$ dvc remote list
local   /home/a/Projects/university/mlops-dvc-remote    (default)
$ dvc params diff data-v1 data-v2
Path         Param             data-v1    data-v2
params.yaml  data.sample_frac  0.7        1.0
$ dvc diff --show-hash data-v1 data-v2
Modified:
    e1c1c472..b154f6a7  data/breast_cancer.csv

files summary: 1 modified
$ dvc status && dvc status --cloud
Data and pipelines are up to date.
Cache and remote 'local' are in sync.
```

## MLflow (via MlflowClient)

```
experiment 2 'breast-cancer-pipeline': 3 runs
  ded4b2fc FINISHED logreg-data-v2  f1=0.9861 gate=passed data=data-v2 commit=3cce5da dirty=true trigger=airflow
  0344b16e FINISHED rf-data-v2      f1=0.9583 gate=passed data=data-v2 commit=3cce5da dirty=true trigger=airflow
  c1dca733 FINISHED logreg-data-v2  f1=0.9861 gate=passed data=data-v2 commit=3cce5da dirty=true trigger=airflow
experiment 1 'breast-cancer-classification': 8 runs
  1d7cb656 FAILED   logreg-data-v1  f1=0.9811 gate=pending data=data-v1 commit=10fea22 dirty=true trigger=cli
  b2bab0ae FAILED   rf-data-v1      f1=0.9720 gate=pending data=data-v1 commit=10fea22 dirty=true trigger=cli
  c5bf4b86 FINISHED logreg-data-v1  f1=0.9811 gate=passed data=data-v1 commit=10fea22 dirty=true trigger=cli
  da108a0f FAILED   rf-data-v1      f1=0.9720 gate=pending data=data-v1 commit=10fea22 dirty=true trigger=cli
  174e46bb FINISHED rf-data-v1      f1=0.9720 gate=passed data=data-v1 commit=10fea22 dirty=true trigger=cli
  95f960db FINISHED logreg-data-v1  f1=0.9811 gate=failed data=data-v1 commit=10fea22 dirty=true trigger=cli
  974fb6cc FINISHED logreg-data-v2  f1=0.9861 gate=passed data=data-v2 commit=3cce5da dirty=false trigger=cli
  25606340 FINISHED rf-data-v2      f1=0.9583 gate=passed data=data-v2 commit=3cce5da dirty=false trigger=cli
experiment 0 'Default': 0 runs
registered model breast-cancer-classifier aliases: {'candidate': '7'}
  v1 run=c5bf4b86 data=data-v1 commit=10fea22
  v2 run=174e46bb data=data-v1 commit=10fea22
  v3 run=974fb6cc data=data-v2 commit=3cce5da
  v4 run=25606340 data=data-v2 commit=3cce5da
  v5 run=ded4b2fc data=data-v2 commit=3cce5da
  v6 run=0344b16e data=data-v2 commit=3cce5da
  v7 run=c1dca733 data=data-v2 commit=3cce5da
```

## Airflow 3 (REST API /api/v2, JWT auth)

```
$ GET /api/v2/dags/training_pipeline/dagRuns?order_by=-start_date
manual__2026-11-15T17:10:08.165848+00:00 failed {'model_type': 'logreg'} triggered by MLOps agent after explicit 
manual__2026-11-15T17:02:39.131296+00:00 success {'model_type': 'logreg'} triggered by MLOps agent after explicit 
manual__2026-11-15T16:24:03.786094+00:00 success {'model_type': 'rf'} triggered by MLOps agent after explicit 
manual__2026-11-15T16:19:52.802014+00:00 success {'model_type': 'logreg'} verification run (item 3.2)
$ GET .../taskInstances for each run
manual__2026-11-15T17:10:08.165848+00:00 validate_dataset=failed(try 1) preprocess=upstream_failed(try 0) train_model=upstream_failed(try 0) evaluate_and_log=upstream_failed(try 0) quality_gate=upstream_failed(try 0)
manual__2026-11-15T17:02:39.131296+00:00 validate_dataset=success(try 1) preprocess=success(try 1) train_model=success(try 1) evaluate_and_log=success(try 1) quality_gate=success(try 1)
manual__2026-11-15T16:24:03.786094+00:00 validate_dataset=success(try 1) preprocess=success(try 1) train_model=success(try 1) evaluate_and_log=success(try 1) quality_gate=success(try 1)
manual__2026-11-15T16:19:52.802014+00:00 validate_dataset=success(try 1) preprocess=success(try 1) train_model=success(try 1) evaluate_and_log=success(try 1) quality_gate=success(try 1)
```

## Kubernetes (kind cluster `mlops`)

```
$ kubectl -n mlops get deploy,rs,pods,svc,endpointslices,cm -o wide
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE   CONTAINERS      IMAGES                     SELECTOR
deployment.apps/inference-api   2/2     2            2           12m   inference-api   mlops-inference:model-v6   app.kubernetes.io/name=inference-api

NAME                                       DESIRED   CURRENT   READY   AGE     CONTAINERS      IMAGES                      SELECTOR
replicaset.apps/inference-api-68fc554f96   0         0         0       12m     inference-api   mlops-inference:model-v6    app.kubernetes.io/name=inference-api,pod-template-hash=68fc554f96
replicaset.apps/inference-api-6d4df994b    2         2         2       8m11s   inference-api   mlops-inference:model-v6    app.kubernetes.io/name=inference-api,pod-template-hash=6d4df994b
replicaset.apps/inference-api-756b9ff476   0         0         0       4m19s   inference-api   mlops-inference:model-v99   app.kubernetes.io/name=inference-api,pod-template-hash=756b9ff476

NAME                                READY   STATUS    RESTARTS   AGE     IP           NODE                  NOMINATED NODE   READINESS GATES
pod/inference-api-6d4df994b-gwn9p   1/1     Running   0          8m11s   10.244.0.7   mlops-control-plane   <none>           <none>
pod/inference-api-6d4df994b-q72ql   1/1     Running   0          8m4s    10.244.0.8   mlops-control-plane   <none>           <none>

NAME                    TYPE        CLUSTER-IP     EXTERNAL-IP   PORT(S)   AGE   SELECTOR
service/inference-api   ClusterIP   10.96.72.129   <none>        80/TCP    12m   app.kubernetes.io/name=inference-api

NAME                                                 ADDRESSTYPE   PORTS   ENDPOINTS               AGE
endpointslice.discovery.k8s.io/inference-api-h7xxf   IPv4          8000    10.244.0.7,10.244.0.8   12m

NAME                         DATA   AGE
configmap/inference-config   4      12m
configmap/kube-root-ca.crt   1      12m

$ kubectl -n mlops rollout history deploy/inference-api
deployment.apps/inference-api 
REVISION  CHANGE-CAUSE
1         <none>
3         <none>
4         <none>


$ kubectl -n mlops describe deploy inference-api  (excerpt)
Replicas:               2 desired | 2 updated | 2 total | 2 available | 0 unavailable
    Image:      mlops-inference:model-v6
    Limits:
      cpu:     500m
      memory:  512Mi
    Requests:
      cpu:      100m
      memory:   256Mi
    Liveness:   http-get http://:http/health delay=15s timeout=1s period=10s successThreshold=1 failureThreshold=3
    Readiness:  http-get http://:http/ready delay=5s timeout=1s period=5s successThreshold=1 failureThreshold=3
    Environment Variables from:
      inference-config  ConfigMap  Optional: false
```

## MLflow run artifacts via the agent tool `get_run_details`

Added during final verification (MLflow 3 keeps models as separate logged models, so the run artifact list alone was empty):

```
$ get_run_details(run_id="c1dca733b9ec43fda201534f9f6f44d3")  -> artifacts / logged_models / registry
{
  "artifacts": [],
  "logged_models": [
    {
      "model_id": "m-345ba499da734122959363fef5ec0147",
      "name": "model",
      "files": [
        "MLmodel",
        "conda.yaml",
        "input_example.json",
        "model.skops",
        "python_env.yaml",
        "requirements.txt",
        "serving_input_example.json"
      ]
    }
  ],
  "registry": [
    {
      "model": "breast-cancer-classifier",
      "version": "7",
      "aliases": [
        "candidate"
      ]
    }
  ],
  "params": {
    "C": "1.0",
    "max_iter": "1000",
    "model_type": "logreg"
  }
}
```
