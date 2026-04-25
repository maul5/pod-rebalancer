"""Business rules for one rebalance run.

This module intentionally does not know how Kubernetes or kubectl are called.
It only coordinates policy decisions by talking to gateway objects. That keeps
business logic readable for humans and easier to test in isolation.
"""

from __future__ import annotations

from app.config import settings
from app.domain.models import MoveResult, NodeMetric, PodCandidate, RebalanceResult


class RebalanceService:
    """Coordinates a full rebalance run from metrics lookup to final result."""

    def __init__(self, metrics_gateway, kube_gateway) -> None:
        self.metrics_gateway = metrics_gateway
        self.kube_gateway = kube_gateway

    def run(self) -> RebalanceResult:
        """Execute the end-to-end rebalance policy once."""

        metrics = self.metrics_gateway.get_node_metrics()
        candidate_nodes = self._sort_nodes_by_pressure(metrics)
        if not candidate_nodes:
            return RebalanceResult(worst_node='', max_move=0, moved=[], skipped=[])

        node_count = self.kube_gateway.get_node_count()
        max_move = self._calculate_max_move(node_count)
        last_moved_deployments = self.kube_gateway.get_last_moved_deployments(settings.namespace)

        selected_node_name = ''
        candidates: list[PodCandidate] = []

        # We no longer lock onto one absolute worst node if it has no safe move
        # candidates. Instead, we walk nodes from busiest to least busy until we
        # find a node that actually has movable Deployment pods.
        for node in candidate_nodes:
            node_candidates = self.kube_gateway.get_pod_candidates(settings.namespace, node.name)
            if node_candidates:
                selected_node_name = node.name
                candidates = node_candidates
                break

        if not selected_node_name:
            return RebalanceResult(worst_node=candidate_nodes[0].name, max_move=max_move, moved=[], skipped=[])

        moved: list[MoveResult] = []
        skipped: list[MoveResult] = []
        last_deployment_name = ''

        self.kube_gateway.cordon_node(selected_node_name)
        try:
            for candidate in candidates:
                if len(moved) >= max_move:
                    break

                if candidate.deployment_name in last_moved_deployments:
                    skipped.append(
                        MoveResult(
                            pod_name=candidate.pod_name,
                            deployment_name=candidate.deployment_name,
                            status='skipped',
                            message='Skipped because this deployment was moved in the previous run.',
                        )
                    )
                    continue

                if candidate.deployment_name == last_deployment_name:
                    skipped.append(
                        MoveResult(
                            pod_name=candidate.pod_name,
                            deployment_name=candidate.deployment_name,
                            status='skipped',
                            message='Skipped to avoid consecutive moves from the same deployment.',
                        )
                    )
                    continue

                result = self._move_one_candidate(candidate)
                if result.status == 'moved':
                    moved.append(result)
                    last_deployment_name = candidate.deployment_name
                else:
                    skipped.append(result)
        finally:
            # Uncordon and state persistence must happen even if one candidate
            # times out or a later step raises, otherwise the next run can be
            # left with a partially mutated cluster state.
            self.kube_gateway.uncordon_node(selected_node_name)
            self.kube_gateway.save_last_moved_deployments(
                settings.namespace,
                [item.deployment_name for item in moved],
            )

        return RebalanceResult(
            worst_node=selected_node_name,
            max_move=max_move,
            moved=moved,
            skipped=skipped,
        )

    def _move_one_candidate(self, candidate: PodCandidate) -> MoveResult:
        """Delete one pod and wait for a truly new ready replacement pod."""

        # Snapshot existing pod names first so we do not mistake an already
        # running sibling replica for the new replacement pod.
        existing_pod_names = self.kube_gateway.get_deployment_pod_names(settings.namespace, candidate.deployment_name)
        self.kube_gateway.delete_pod(settings.namespace, candidate.pod_name)
        ready, replacement_name = self.kube_gateway.wait_until_ready(
            namespace=settings.namespace,
            deployment_name=candidate.deployment_name,
            deleted_pod_name=candidate.pod_name,
            existing_pod_names=existing_pod_names,
            timeout_seconds=settings.wait_ready_timeout_seconds,
        )
        if ready:
            return MoveResult(
                pod_name=candidate.pod_name,
                deployment_name=candidate.deployment_name,
                status='moved',
                message=f'Replacement pod became ready: {replacement_name}',
            )
        return MoveResult(
            pod_name=candidate.pod_name,
            deployment_name=candidate.deployment_name,
            status='timeout',
            message=f'Timed out after {settings.wait_ready_timeout_seconds} seconds.',
        )

    @staticmethod
    def _sort_nodes_by_pressure(metrics: list[NodeMetric]) -> list[NodeMetric]:
        """Sort from busiest node to least busy node."""

        return sorted(metrics, key=lambda item: (item.score, item.cpu_percent, item.memory_percent))

    @staticmethod
    def _calculate_max_move(node_count: int) -> int:
        if settings.max_move_override > 0:
            return settings.max_move_override
        return max(1, min(2, node_count // 3))
