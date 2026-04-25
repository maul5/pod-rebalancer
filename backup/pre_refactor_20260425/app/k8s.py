from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from app.config import settings


class KubectlError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeMetric:
    name: str
    cpu_percent: int
    memory_percent: int

    @property
    def score(self) -> int:
        return (100 - self.cpu_percent) + (100 - self.memory_percent)


@dataclass(frozen=True)
class PodCandidate:
    pod_name: str
    deployment_name: str
    node_name: str
    replicas: int


STATE_CONFIGMAP_NAME = "pod-rebalancer-state"


def _run_kubectl(args: list[str], check: bool = True, input_text: str | None = None) -> str:
    command = [settings.kubectl_bin, *args]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        input=input_text,
        check=False,
    )
    if check and completed.returncode != 0:
        raise KubectlError(completed.stderr.strip() or f"kubectl command failed: {' '.join(command)}")
    return completed.stdout


def get_node_metrics() -> list[NodeMetric]:
    last_error: KubectlError | None = None
    for attempt in range(settings.metrics_retry_count):
        try:
            output = _run_kubectl(["top", "nodes", "--no-headers"])
            break
        except KubectlError as error:
            last_error = error
            if "Metrics API not available" not in str(error):
                raise
            if attempt == settings.metrics_retry_count - 1:
                raise
            time.sleep(settings.metrics_retry_delay_seconds)
    else:
        if last_error is not None:
            raise last_error
        raise KubectlError("Unable to load node metrics.")

    metrics: list[NodeMetric] = []
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 5:
            continue
        cpu_percent = _parse_percent(columns[2])
        memory_percent = _parse_percent(columns[4])
        if cpu_percent is None or memory_percent is None:
            print(f"metrics warning: skipped node line with unknown values: {line}")
            continue
        metrics.append(
            NodeMetric(
                name=columns[0],
                cpu_percent=cpu_percent,
                memory_percent=memory_percent,
            )
        )
    return metrics


def _parse_percent(raw: str) -> int | None:
    value = raw.rstrip("%")
    if value == "<unknown>":
        return None
    return int(value)


def get_worst_node(metrics: list[NodeMetric]) -> NodeMetric | None:
    if not metrics:
        return None
    return sorted(metrics, key=lambda item: (item.score, item.cpu_percent, item.memory_percent))[0]


def get_nodes_by_pressure(metrics: list[NodeMetric]) -> list[NodeMetric]:
    return sorted(metrics, key=lambda item: (item.score, item.cpu_percent, item.memory_percent))


def get_node_count() -> int:
    output = _run_kubectl(["get", "nodes", "-o", "name"])
    return len([line for line in output.splitlines() if line.strip()])


def calculate_max_move(node_count: int) -> int:
    if settings.max_move_override > 0:
        return settings.max_move_override
    return max(1, min(2, node_count // 3))


def get_namespace_pods(namespace: str) -> dict[str, Any]:
    output = _run_kubectl(["get", "pods", "-n", namespace, "-o", "json"])
    return json.loads(output)


def get_deployment_pod_names(namespace: str, deployment_name: str) -> set[str]:
    payload = get_namespace_pods(namespace)
    pod_names: set[str] = set()
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        owner_refs = metadata.get("ownerReferences", [])
        if not owner_refs:
            continue
        replica_set_name = owner_refs[0].get("name", "")
        inferred_deployment = replica_set_name.rsplit("-", 1)[0] if "-" in replica_set_name else replica_set_name
        if inferred_deployment == deployment_name:
            name = metadata.get("name")
            if name:
                pod_names.add(name)
    return pod_names


def get_pod_candidates(namespace: str, worst_node_name: str) -> list[PodCandidate]:
    payload = get_namespace_pods(namespace)
    candidates: list[PodCandidate] = []
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        owner_refs = metadata.get("ownerReferences", [])

        if spec.get("nodeName") != worst_node_name:
            continue
        if metadata.get("name", "").startswith(("svclb-", "local-path-provisioner", "coredns", "metrics-server")):
            continue
        if not owner_refs:
            continue
        if owner_refs[0].get("kind") != "ReplicaSet":
            continue

        replica_set_name = owner_refs[0].get("name", "")
        deployment_name = replica_set_name.rsplit("-", 1)[0] if "-" in replica_set_name else replica_set_name
        replicas = get_deployment_replicas(namespace, deployment_name)
        if replicas <= 1:
            continue

        candidates.append(
            PodCandidate(
                pod_name=metadata["name"],
                deployment_name=deployment_name,
                node_name=spec["nodeName"],
                replicas=replicas,
            )
        )
    return candidates


def get_deployment_replicas(namespace: str, deployment_name: str) -> int:
    output = _run_kubectl(
        ["get", "deployment", deployment_name, "-n", namespace, "-o", "jsonpath={.spec.replicas}"],
        check=False,
    ).strip()
    return int(output) if output.isdigit() else 0


def cordon_node(node_name: str) -> None:
    if settings.dry_run:
        return
    _run_kubectl(["cordon", node_name])


def uncordon_node(node_name: str) -> None:
    if settings.dry_run:
        return
    _run_kubectl(["uncordon", node_name], check=False)


def delete_pod(namespace: str, pod_name: str) -> None:
    if settings.dry_run:
        return
    _run_kubectl(["delete", "pod", pod_name, "-n", namespace, "--wait=false"])


def find_ready_replacement(
    namespace: str,
    deployment_name: str,
    excluded_pod_name: str,
    existing_pod_names: set[str],
) -> str | None:
    payload = get_namespace_pods(namespace)
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        pod_name = metadata.get("name")
        if pod_name == excluded_pod_name:
            continue
        owner_refs = metadata.get("ownerReferences", [])
        if not owner_refs:
            continue
        replica_set_name = owner_refs[0].get("name", "")
        inferred_deployment = replica_set_name.rsplit("-", 1)[0] if "-" in replica_set_name else replica_set_name
        if inferred_deployment != deployment_name:
            continue
        if pod_name in existing_pod_names:
            continue
        for condition in item.get("status", {}).get("conditions", []):
            if condition.get("type") == "Ready" and condition.get("status") == "True":
                return pod_name
    return None


def wait_until_ready(
    namespace: str,
    deployment_name: str,
    deleted_pod_name: str,
    existing_pod_names: set[str],
    timeout_seconds: int,
) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        replacement_pod_name = find_ready_replacement(
            namespace,
            deployment_name,
            deleted_pod_name,
            existing_pod_names,
        )
        if replacement_pod_name:
            return True, replacement_pod_name
        time.sleep(settings.loop_interval_seconds)
    return False, ""


def get_last_moved_deployments(namespace: str) -> set[str]:
    output = _run_kubectl(
        ["get", "configmap", STATE_CONFIGMAP_NAME, "-n", namespace, "-o", "json"],
        check=False,
    ).strip()
    if not output:
        return set()

    payload = json.loads(output)
    if payload.get("kind") == "Status" and payload.get("reason") == "NotFound":
        return set()

    value = payload.get("data", {}).get("lastMovedDeployments", "")
    return {item for item in value.split(",") if item}


def save_last_moved_deployments(namespace: str, deployments: list[str]) -> None:
    manifest = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": STATE_CONFIGMAP_NAME,
            "namespace": namespace,
        },
        "data": {
            "lastMovedDeployments": ",".join(deployments),
        },
    }
    _run_kubectl(["apply", "-f", "-"], input_text=json.dumps(manifest))
