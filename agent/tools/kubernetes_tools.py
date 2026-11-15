"""Kubernetes operations tools (official Python client) for the inference application.

Read tools inspect workloads, pods, logs, events, services and versions. The only
state-changing tool, `restart_deployment`, is refused outside the approved namespace
and is executed by the orchestrator only after explicit user confirmation.
"""

import json
from datetime import UTC, datetime
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from pydantic import BaseModel, Field
from urllib3.exceptions import HTTPError

from agent.config import load_settings
from agent.tools.common import ActionRejectedError, ServiceUnavailableError, Tool

# RFC 1123 names, as Kubernetes requires for namespaces, pods and deployments.
NAME_PATTERN = r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$"
APP_PORT = 8000


class NamespaceInput(BaseModel):
    namespace: str = Field(default="mlops", pattern=NAME_PATTERN)


class PodInput(NamespaceInput):
    pod_name: str = Field(pattern=NAME_PATTERN)
    tail_lines: int = Field(default=50, ge=1, le=200)
    previous: bool = Field(default=False, description="Logs of the previous (crashed) container.")


class DeploymentInput(NamespaceInput):
    deployment: str = Field(default="inference-api", pattern=NAME_PATTERN)


def _api_client() -> client.ApiClient:
    try:
        # The kubernetes.config module ships without type hints.
        api: client.ApiClient = config.new_client_from_config(  # type: ignore[no-untyped-call]
            context=load_settings().kube_context
        )
        return api
    except (ConfigException, FileNotFoundError) as exc:
        raise ServiceUnavailableError(f"no usable kubeconfig: {exc}") from exc


def _call(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Call the Kubernetes API with a timeout, mapping failures to agent errors."""
    try:
        return method(*args, _request_timeout=load_settings().tool_timeout_seconds / 2, **kwargs)
    except ApiException as exc:
        if exc.status == 404:
            raise LookupError(f"not found: {exc.reason}") from exc
        if exc.status in (401, 403):
            raise PermissionError(f"Kubernetes API denied access ({exc.status})") from exc
        raise
    except HTTPError as exc:
        raise ServiceUnavailableError(f"Kubernetes API unreachable: {exc}") from exc


def _age(timestamp: datetime | None) -> str | None:
    if timestamp is None:
        return None
    return f"{int((datetime.now(UTC) - timestamp).total_seconds() // 60)}m"


def _container_states(pod: Any) -> list[dict[str, Any]]:
    states = []
    for status in pod.status.container_statuses or []:
        waiting, terminated = status.state.waiting, status.state.terminated
        last = status.last_state.terminated if status.last_state else None
        states.append(
            {
                "container": status.name,
                "ready": status.ready,
                "restart_count": status.restart_count,
                "image": status.image,
                "waiting_reason": waiting.reason if waiting else None,
                "waiting_message": waiting.message if waiting else None,
                "terminated_reason": terminated.reason if terminated else None,
                "last_termination": (
                    {"reason": last.reason, "exit_code": last.exit_code} if last else None
                ),
            }
        )
    return states


def _pod_summary(pod: Any) -> dict[str, Any]:
    containers = _container_states(pod)
    return {
        "name": pod.metadata.name,
        "phase": pod.status.phase,
        "ready": bool(containers) and all(c["ready"] for c in containers),
        "restarts": sum(c["restart_count"] for c in containers),
        "waiting_reasons": [c["waiting_reason"] for c in containers if c["waiting_reason"]],
        "node": pod.spec.node_name,
        "age": _age(pod.metadata.creation_timestamp),
    }


def _events(core: client.CoreV1Api, namespace: str, selector: str | None = None) -> list[Any]:
    events = _call(core.list_namespaced_event, namespace, field_selector=selector).items
    events.sort(key=lambda e: e.last_timestamp or e.event_time or e.metadata.creation_timestamp)
    return [
        {
            "type": e.type,
            "reason": e.reason,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "message": e.message,
            "count": e.count,
        }
        for e in events[-20:]
    ]


def list_workloads(args: NamespaceInput) -> dict[str, Any]:
    """List Deployments (desired/ready/available replicas, image, version label) and Pods (phase,
    readiness, restarts, waiting reasons) in a namespace."""
    with _api_client() as api:
        apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
        deployments = _call(apps.list_namespaced_deployment, args.namespace).items
        pods = _call(core.list_namespaced_pod, args.namespace).items
    return {
        "namespace": args.namespace,
        "deployments": [
            {
                "name": d.metadata.name,
                "desired": d.spec.replicas,
                "ready": d.status.ready_replicas or 0,
                "available": d.status.available_replicas or 0,
                "updated": d.status.updated_replicas or 0,
                "images": [c.image for c in d.spec.template.spec.containers],
                "version_label": (d.metadata.labels or {}).get("app.kubernetes.io/version"),
            }
            for d in deployments
        ],
        "pods": [_pod_summary(p) for p in pods],
    }


def get_pod_status_and_logs(args: PodInput) -> dict[str, Any]:
    """Get one Pod's detailed status (conditions, container states, restart counts, last
    termination), its recent events, and the tail of its logs."""
    with _api_client() as api:
        core = client.CoreV1Api(api)
        pod = _call(core.read_namespaced_pod, args.pod_name, args.namespace)
        selector = f"involvedObject.name={args.pod_name}"
        events = _events(core, args.namespace, selector)
        try:
            logs = _call(
                core.read_namespaced_pod_log,
                args.pod_name,
                args.namespace,
                tail_lines=args.tail_lines,
                previous=args.previous,
            )
            log_error = None
        except ApiException as exc:  # e.g. container never started: no logs exist
            logs, log_error = "", f"logs unavailable: {exc.reason} {(exc.body or '')[:200]}"
    return {
        **_pod_summary(pod),
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason}
            for c in pod.status.conditions or []
        ],
        "containers": _container_states(pod),
        "events": events,
        "logs": logs.splitlines()[-args.tail_lines :],
        "log_error": log_error,
    }


def list_services(args: NamespaceInput) -> dict[str, Any]:
    """List Services with type, cluster IP, ports, selector, and how many ready endpoints back
    each one (zero ready endpoints means the service cannot answer requests)."""
    with _api_client() as api:
        core, discovery = client.CoreV1Api(api), client.DiscoveryV1Api(api)
        services = _call(core.list_namespaced_service, args.namespace).items
        result = []
        for svc in services:
            slices = _call(
                discovery.list_namespaced_endpoint_slice,
                args.namespace,
                label_selector=f"kubernetes.io/service-name={svc.metadata.name}",
            ).items
            endpoints = [ep for s in slices for ep in s.endpoints or []]
            ready = sum(1 for ep in endpoints if ep.conditions and ep.conditions.ready)
            result.append(
                {
                    "name": svc.metadata.name,
                    "type": svc.spec.type,
                    "cluster_ip": svc.spec.cluster_ip,
                    "ports": [f"{p.port}->{p.target_port}/{p.protocol}" for p in svc.spec.ports],
                    "selector": svc.spec.selector,
                    "ready_endpoints": ready,
                    "not_ready_endpoints": len(endpoints) - ready,
                }
            )
    return {"namespace": args.namespace, "services": result}


def find_unhealthy_pods(args: NamespaceInput) -> dict[str, Any]:
    """Find Pods that are not ready, are waiting (e.g. ImagePullBackOff, CrashLoopBackOff) or have
    restarted, together with the namespace's recent Warning events."""
    with _api_client() as api:
        core = client.CoreV1Api(api)
        pods = [_pod_summary(p) for p in _call(core.list_namespaced_pod, args.namespace).items]
        warnings = _events(core, args.namespace, "type=Warning")
    unhealthy = [p for p in pods if not p["ready"] or p["restarts"] > 0 or p["waiting_reasons"]]
    return {
        "namespace": args.namespace,
        "total_pods": len(pods),
        "unhealthy_pods": unhealthy,
        "warning_events": warnings,
    }


def get_deployed_version(args: DeploymentInput) -> dict[str, Any]:
    """Report the currently deployed application version: container image, version label,
    rollout revision, ConfigMap APP_VERSION, and the model version reported live by a ready Pod."""
    with _api_client() as api:
        apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
        deployment = _call(apps.read_namespaced_deployment, args.deployment, args.namespace)
        selector = ",".join(f"{k}={v}" for k, v in deployment.spec.selector.match_labels.items())
        pods = _call(core.list_namespaced_pod, args.namespace, label_selector=selector).items
        ready = [p for p in pods if _pod_summary(p)["ready"]]
        live = None
        if ready:
            # Raw body: the generated client would otherwise turn the JSON into a str repr.
            raw = _call(
                core.connect_get_namespaced_pod_proxy_with_path,
                f"{ready[0].metadata.name}:{APP_PORT}",
                args.namespace,
                "version",
                _preload_content=False,
            )
            live = json.loads(raw.data)
        config_maps = [
            source.config_map_ref.name
            for container in deployment.spec.template.spec.containers
            for source in container.env_from or []
            if source.config_map_ref
        ]
        app_versions = {
            name: _call(core.read_namespaced_config_map, name, args.namespace).data.get(
                "APP_VERSION"
            )
            for name in config_maps
        }
    return {
        "deployment": args.deployment,
        "images": [c.image for c in deployment.spec.template.spec.containers],
        "version_label": (deployment.metadata.labels or {}).get("app.kubernetes.io/version"),
        "revision": (deployment.metadata.annotations or {}).get(
            "deployment.kubernetes.io/revision"
        ),
        "configmap_app_version": app_versions,
        "ready_pods": len(ready),
        "live_version_from_pod": live,
    }


def restart_deployment(args: DeploymentInput) -> dict[str, Any]:
    """Rolling-restart a Deployment. State-changing: allowed only in the approved namespace and
    executed only after the user explicitly confirms."""
    approved = load_settings().k8s_approved_namespace
    if args.namespace != approved:
        raise ActionRejectedError(
            f"namespace '{args.namespace}' is not approved for changes (approved: '{approved}')"
        )
    restarted_at = datetime.now(UTC).isoformat()
    patch = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": restarted_at}}
            }
        }
    }
    with _api_client() as api:
        apps = client.AppsV1Api(api)
        deployment = _call(apps.patch_namespaced_deployment, args.deployment, args.namespace, patch)
    return {
        "deployment": args.deployment,
        "namespace": args.namespace,
        "restarted_at": restarted_at,
        "generation": deployment.metadata.generation,
    }


TOOLS = [
    Tool.from_function(list_workloads),
    Tool.from_function(get_pod_status_and_logs),
    Tool.from_function(list_services),
    Tool.from_function(find_unhealthy_pods),
    Tool.from_function(get_deployed_version),
    Tool.from_function(restart_deployment, state_changing=True),
]
