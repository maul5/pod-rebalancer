"""?? Kubernetes Python client ??????.

???? ???? ??? ??? ?? ? ??? ???.
? ??? ?????.
- `app/adapters/kubectl_metrics.py`? kubectl ?? ??? ??? ?????.
- ? ??? ??? Kubernetes ??? ??? Python client? ?????.
"""

from __future__ import annotations

import time

from kubernetes import client, config
from kubernetes.client import ApiException

from app.config import settings
from app.domain.models import PodCandidate


STATE_CONFIGMAP_NAME = 'pod-rebalancer-state'
SYSTEM_POD_PREFIXES = ('svclb-', 'local-path-provisioner', 'coredns', 'metrics-server')


class KubeApiGateway:
    """?? Kubernetes Python client? ?? ???? ????????."""

    def __init__(self) -> None:
        self._load_config()
        self.core_api = client.CoreV1Api()
        self.apps_api = client.AppsV1Api()

    def _load_config(self) -> None:
        """???? ???? incluster ???, ?? ?? ??? kubeconfig? ?????."""

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

    def get_node_count(self) -> int:
        return len(self.core_api.list_node().items)

    def get_pod_candidates(self, namespace: str, node_name: str) -> list[PodCandidate]:
        """?? ?? ?? ?? ?? ??? Deployment Pod ??? ?????.

        owner reference, replica ?, ?? ?? ???? Kubernetes ?? ???
        ???? ??? ??? ??? ?? ???.
        """

        candidates: list[PodCandidate] = []
        for pod in self.core_api.list_namespaced_pod(namespace=namespace).items:
            if not pod.spec or pod.spec.node_name != node_name:
                continue

            pod_name = pod.metadata.name if pod.metadata else ''
            if pod_name.startswith(SYSTEM_POD_PREFIXES):
                continue

            owner_refs = pod.metadata.owner_references if pod.metadata else None
            if not owner_refs:
                continue
            owner = owner_refs[0]
            if owner.kind != 'ReplicaSet':
                continue

            deployment_name = self._replicaset_to_deployment(owner.name or '')
            replicas = self.get_deployment_replicas(namespace, deployment_name)
            if replicas <= 1:
                continue

            candidates.append(
                PodCandidate(
                    pod_name=pod_name,
                    deployment_name=deployment_name,
                    node_name=node_name,
                    replicas=replicas,
                )
            )
        return candidates

    def get_deployment_replicas(self, namespace: str, deployment_name: str) -> int:
        try:
            deployment = self.apps_api.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        except ApiException as error:
            if error.status == 404:
                return 0
            raise
        return deployment.spec.replicas or 0

    def get_deployment_pod_names(self, namespace: str, deployment_name: str) -> set[str]:
        """Pod ??? ???? ?? Deployment? ?? Pod ??? ????? ?????.

        ? ???? ?? ? ?? sibling Pod? ?? ? ?? ?? replacement Pod?
        ???? ? ?????.
        """

        pod_names: set[str] = set()
        for pod in self.core_api.list_namespaced_pod(namespace=namespace).items:
            inferred_deployment = self._get_deployment_name_from_pod(pod)
            if inferred_deployment != deployment_name:
                continue
            if pod.metadata and pod.metadata.name:
                pod_names.add(pod.metadata.name)
        return pod_names

    def cordon_node(self, node_name: str) -> None:
        if settings.dry_run:
            return
        self.core_api.patch_node(node_name, {'spec': {'unschedulable': True}})

    def uncordon_node(self, node_name: str) -> None:
        if settings.dry_run:
            return
        self.core_api.patch_node(node_name, {'spec': {'unschedulable': False}})

    def delete_pod(self, namespace: str, pod_name: str) -> None:
        if settings.dry_run:
            return
        self.core_api.delete_namespaced_pod(name=pod_name, namespace=namespace, body=client.V1DeleteOptions())

    def wait_until_ready(
        self,
        namespace: str,
        deployment_name: str,
        deleted_pod_name: str,
        existing_pod_names: set[str],
        timeout_seconds: int,
    ) -> tuple[bool, str]:
        """?? ?? ?? ???? replacement Pod? Ready ? ??? ?????."""

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            replacement_name = self.find_ready_replacement(
                namespace=namespace,
                deployment_name=deployment_name,
                deleted_pod_name=deleted_pod_name,
                existing_pod_names=existing_pod_names,
            )
            if replacement_name:
                return True, replacement_name
            time.sleep(settings.loop_interval_seconds)
        return False, ''

    def find_ready_replacement(
        self,
        namespace: str,
        deployment_name: str,
        deleted_pod_name: str,
        existing_pod_names: set[str],
    ) -> str | None:
        """?? Deployment? ?? ?? ??? Ready Pod ??? ?????."""

        for pod in self.core_api.list_namespaced_pod(namespace=namespace).items:
            pod_name = pod.metadata.name if pod.metadata else None
            if not pod_name or pod_name == deleted_pod_name:
                continue
            if pod_name in existing_pod_names:
                continue
            inferred_deployment = self._get_deployment_name_from_pod(pod)
            if inferred_deployment != deployment_name:
                continue
            if self._is_pod_ready(pod):
                return pod_name
        return None

    def get_last_moved_deployments(self, namespace: str) -> set[str]:
        """ConfigMap ???? ?? 1? ??? ?? ??? ?? ???."""

        try:
            config_map = self.core_api.read_namespaced_config_map(name=STATE_CONFIGMAP_NAME, namespace=namespace)
        except ApiException as error:
            if error.status == 404:
                return set()
            raise
        value = (config_map.data or {}).get('lastMovedDeployments', '')
        return {item for item in value.split(',') if item}

    def save_last_moved_deployments(self, namespace: str, deployments: list[str]) -> None:
        """?? ???? ??? Deployment ??? ??? ?? ???? ? ? ?????."""

        if settings.dry_run:
            return
        body = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=STATE_CONFIGMAP_NAME, namespace=namespace),
            data={'lastMovedDeployments': ','.join(deployments)},
        )
        try:
            self.core_api.read_namespaced_config_map(name=STATE_CONFIGMAP_NAME, namespace=namespace)
            self.core_api.replace_namespaced_config_map(name=STATE_CONFIGMAP_NAME, namespace=namespace, body=body)
        except ApiException as error:
            if error.status == 404:
                self.core_api.create_namespaced_config_map(namespace=namespace, body=body)
                return
            raise

    def _get_deployment_name_from_pod(self, pod: client.V1Pod) -> str | None:
        owner_refs = pod.metadata.owner_references if pod.metadata else None
        if not owner_refs:
            return None
        owner = owner_refs[0]
        if owner.kind != 'ReplicaSet':
            return None
        return self._replicaset_to_deployment(owner.name or '')

    @staticmethod
    def _replicaset_to_deployment(replica_set_name: str) -> str:
        return replica_set_name.rsplit('-', 1)[0] if '-' in replica_set_name else replica_set_name

    @staticmethod
    def _is_pod_ready(pod: client.V1Pod) -> bool:
        conditions = pod.status.conditions if pod.status else None
        if not conditions:
            return False
        return any(condition.type == 'Ready' and condition.status == 'True' for condition in conditions)
