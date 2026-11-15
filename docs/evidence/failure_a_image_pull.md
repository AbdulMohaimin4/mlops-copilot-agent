# Injected failure A: bad image tag (ImagePullBackOff)

Recorded 2026-11-15T17:08Z. Model: **gemini-3.5-flash-lite** (see scenario 5 note). All commands below were run for real; outputs are verbatim.

## 1. Injection (operator)

```
$ kubectl -n mlops set image deploy/inference-api inference-api=mlops-inference:model-v99
deployment.apps/inference-api image updated
```

State after 45 s:

```
$ kubectl -n mlops get pods
NAME                             READY   STATUS             RESTARTS   AGE
inference-api-6d4df994b-gwn9p    1/1     Running            0          4m37s
inference-api-6d4df994b-q72ql    1/1     Running            0          4m30s
inference-api-756b9ff476-h9sbn   0/1     ImagePullBackOff   0          45s

$ kubectl -n mlops get events --field-selector type=Warning
LAST SEEN   TYPE      REASON   OBJECT                               MESSAGE
0s          Warning   Failed   pod/inference-api-756b9ff476-h9sbn   Failed to pull image "mlops-inference:model-v99": failed to pull and unpack image "docker.io/library/mlops-inference:model-v99": failed to resolve reference "docker.io/library/mlops-inference:model-v99": pull access denied, repository does not exist or may require authorization: server message: insufficient_scope: authorization failed
0s          Warning   Failed   pod/inference-api-756b9ff476-h9sbn   Error: ErrImagePull
13s         Warning   Failed   pod/inference-api-756b9ff476-h9sbn   Error: ImagePullBackOff
```

## 2. Agent diagnosis

### User

Why is my prediction API unavailable? We just tried to roll out a new version.

### Tool calls (request `4c42a1324f7d`)

1. `list_services({})` → **ok** (42.5 ms)

```json
{
  "namespace": "mlops",
  "services": [
    {
      "name": "inference-api",
      "type": "ClusterIP",
      "cluster_ip": "10.96.72.129",
      "ports": [
        "80->http/TCP"
      ],
      "selector": {
        "app.kubernetes.io/name": "inference-api"
      },
      "ready_endpoints": 2,
      "not_ready_endpoints": 1
    }
  ]
}
```

2. `find_unhealthy_pods({})` → **ok** (23.6 ms)

```json
{
  "namespace": "mlops",
  "total_pods": 3,
  "unhealthy_pods": [
    {
      "name": "inference-api-756b9ff476-h9sbn",
      "phase": "Pending",
      "ready": false,
      "restarts": 0,
      "waiting_reasons": [
        "ImagePullBackOff"
      ],
      "node": "mlops-control-plane",
      "age": "0m"
    }
  ],
  "warning_events": [
    {
      "type": "Warning",
      "reason": "Failed",
      "object": "Pod/inference-api-756b9ff476-h9sbn",
      "message": "Error: ImagePullBackOff",
      "count": 2
    },
    {
      "type": "Warning",
      "reason": "Failed",
      "object": "Pod/inference-api-756b9ff476-h9sbn",
      "message": "Failed to pull image \"mlops-inference:model-v99\": failed to pull and unpack image \"docker.io/library/mlops-inference:model-v99\": failed to resolve reference \"docker.io/library/mlops-inference:model-v99\": pull access denied, repository does not exist or may require authorization: server message: insufficient_scope: authorization failed",
      "count": 3
    },
    {
      "type": "Warning",
      "reason": "Failed",
      "object": "Pod/inference-api-756b9ff476-h9sbn",
      "message": "Error: ErrImagePull",
      "count": 3
    }
  ]
}
```

3. `get_deployed_version({})` → **ok** (108.0 ms)

```json
{
  "deployment": "inference-api",
  "images": [
    "mlops-inference:model-v99"
  ],
  "version_label": "model-v6",
  "revision": "3",
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
* **Inference API Service Status**: The `inference-api` service has 2 ready endpoints and 1 not ready endpoint (`list_services`).
* **Unhealthy Pods / Pod Status**: Pod `inference-api-756b9ff476-h9sbn` is in phase `Pending` with reason `ImagePullBackOff` (`find_unhealthy_pods`).
* **Pull Error**: The pod failed to pull image `mlops-inference:model-v99` with error `pull access denied, repository does not exist or may require authorization` (`find_unhealthy_pods`).
* **Deployed Version Configuration**: The deployment specifies container image `mlops-inference:model-v99` and version label `model-v6` (`get_deployed_version`).

**Uncertainties**
* Whether the container image `mlops-inference:model-v99` was successfully built and pushed to the container registry, or if the tag name/credentials are incorrect.

**Recommended actions**
* Verify that the container image `mlops-inference:model-v99` exists in the container registry and that the cluster has proper pull access/credentials.
* If necessary, roll back the deployment to the previous working version (`model-v6` / `model-v5`) until a valid container image is available.

---

## 3. Resolution (operator)

Root cause: the Deployment was updated to image tag `model-v99`, which was never built or loaded into the kind node, so the
new ReplicaSet pod could not pull it (`ErrImagePull` → `ImagePullBackOff`). The rolling-update strategy (maxUnavailable 0 for
2 replicas) kept both old `model-v6` pods serving, so the Service still had 2 ready endpoints: the rollout was stuck, not the
whole API down.

```
$ kubectl -n mlops rollout undo deploy/inference-api
Warning: resource deployments/inference-api was previously managed with 'kubectl apply'. Rolling back will not update the kubectl.kubernetes.io/last-applied-configuration annotation, which may cause unexpected behavior on future 'kubectl apply' operations. Consider using 'kubectl apply' with your previous configuration file instead.
deployment.apps/inference-api rolled back
$ kubectl -n mlops rollout status deploy/inference-api
deployment "inference-api" successfully rolled out
$ kubectl -n mlops get pods
NAME                             READY   STATUS        RESTARTS   AGE
inference-api-6d4df994b-gwn9p    1/1     Running       0          5m3s
inference-api-6d4df994b-q72ql    1/1     Running       0          4m56s
inference-api-756b9ff476-h9sbn   0/1     Terminating   0          71s
```

## 4. Agent verification after the fix

### User

I rolled the deployment back. Is the prediction API healthy now, and which image and model version is running?

### Tool calls (request `06a2c7cce05e`)

1. `list_services({})` → **ok** (34.2 ms)

```json
{
  "namespace": "mlops",
  "services": [
    {
      "name": "inference-api",
      "type": "ClusterIP",
      "cluster_ip": "10.96.72.129",
      "ports": [
        "80->http/TCP"
      ],
      "selector": {
        "app.kubernetes.io/name": "inference-api"
      },
      "ready_endpoints": 2,
      "not_ready_endpoints": 0
    }
  ]
}
```

2. `find_unhealthy_pods({})` → **ok** (18.2 ms)

```json
{
  "namespace": "mlops",
  "total_pods": 2,
  "unhealthy_pods": [],
  "warning_events": [
    {
      "type": "Warning",
      "reason": "Failed",
      "object": "Pod/inference-api-756b9ff476-h9sbn",
      "message": "Failed to pull image \"mlops-inference:model-v99\": failed to pull and unpack image \"docker.io/library/mlops-inference:model-v99\": failed to resolve reference \"docker.io/library/mlops-inference:model-v99\": pull access denied, repository does not exist or may require authorization: server message: insufficient_scope: authorization failed",
      "count": 3
    },
    {
      "type": "Warning",
      "reason": "Failed",
      "object": "Pod/inference-api-756b9ff476-h9sbn",
      "message": "Error: ErrImagePull",
      "count": 3
    },
    {
      "type": "Warning",
      "reason": "Failed",
      "object": "Pod/inference-api-756b9ff476-h9sbn",
      "message": "Error: ImagePullBackOff",
      "count": 4
    }
  ]
}
```

3. `get_deployed_version({})` → **ok** (94.4 ms)

```json
{
  "deployment": "inference-api",
  "images": [
    "mlops-inference:model-v6"
  ],
  "version_label": "model-v6",
  "revision": "4",
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
* **Inference API Health**: The service `inference-api` has 2 ready endpoints and 0 not-ready endpoints (`list_services`), and `find_unhealthy_pods` reports 0 unhealthy pods in namespace `mlops` (although recent historical warning events show past image pull failures for `model-v99`).
* **Container Image**: `mlops-inference:model-v6` (`get_deployed_version`).
* **Live Model Version**: Version `6` (`model-v6`), with model name `breast-cancer-classifier`, run ID `0344b16e77de48f59d683fa1567eb3fa`, data version `data-v2`, and alias `candidate` (`get_deployed_version`).

**Uncertainties**
* None regarding current health or deployed versions; the rollback successfully restored a running and healthy state.

**Recommended actions**
* None required; the prediction API is currently healthy and running the expected `model-v6` version.

---

## 5. Review

- **Diagnosis correct and evidence-based:** the agent found the `ImagePullBackOff` pod, quoted the registry pull error, and
  showed the Deployment referencing `model-v99` (tools: `list_services`, `find_unhealthy_pods`, `get_deployed_version`).
- **Weaknesses (flash-lite):** it reported "2 ready endpoints" but did not state plainly that the API was still being served
  by the old pods; "model-v5" in its rollback suggestion is not backed by any tool output; after the fix it called v6
  `candidate`, which is the alias *at export time* (`alias_at_export` in the image), while the registry alias had since moved
  to v7. These are reasoning lapses of the smaller model, visible because every claim is traceable to the tool trace above.
- **Resolution** was performed by the operator (`kubectl rollout undo`); the agent has no image-changing tool by design (only
  a confirmed rollout restart in `mlops`).
