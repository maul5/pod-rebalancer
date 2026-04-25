"""???? 1? ??? ?? ???? ??? ?? ?????.

? ??? Kubernetes? kubectl? ??? ????? ?? ????.
????? ??? ?? ?? ??? ?????, ?? ???? ?? ??
?? ???? ?????.
"""

from __future__ import annotations

from app.config import settings
from app.domain.models import MoveResult, NodeMetric, PodCandidate, RebalanceResult


class RebalanceService:
    """??? ???? ?? ?? ???? ?? ????? ?????."""

    def __init__(self, metrics_gateway, kube_gateway) -> None:
        self.metrics_gateway = metrics_gateway
        self.kube_gateway = kube_gateway

    def run(self) -> RebalanceResult:
        """?? ? ?? ???? ??? ? ? ?????."""

        metrics = self.metrics_gateway.get_node_metrics()
        candidate_nodes = self._sort_nodes_by_pressure(metrics)
        if not candidate_nodes:
            return RebalanceResult(worst_node='', max_move=0, moved=[], skipped=[])

        node_count = self.kube_gateway.get_node_count()
        max_move = self._calculate_max_move(node_count)
        last_moved_deployments = self.kube_gateway.get_last_moved_deployments(settings.namespace)

        selected_node_name = ''
        candidates: list[PodCandidate] = []

        # ? ?? ???? worst node ??? ???? ????.
        # ???? ??? ??? ?? ??? ????, ?? ???? ???
        # ?? ?? ??? Deployment Pod? ?? ? ??? ?????.
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
            # ??? timeout?? ??? ???? uncordon? ?? ??? ???
            # ???? ?? ??? ?? ??? ???? ????.
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
        """Pod ??? ????, ?? ?? ?? replacement Pod? ?????."""

        # ?? ?? Pod ???? ??? ??, ?? ? ?? sibling replica?
        # ? replacement Pod? ???? ??? ???.
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
        """?? ?? ???? ?? ?? ?? ??? ?????."""

        return sorted(metrics, key=lambda item: (item.score, item.cpu_percent, item.memory_percent))

    @staticmethod
    def _calculate_max_move(node_count: int) -> int:
        if settings.max_move_override > 0:
            return settings.max_move_override
        return max(1, min(2, node_count // 3))
