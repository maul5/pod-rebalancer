from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.k8s import (
    PodCandidate,
    calculate_max_move,
    cordon_node,
    delete_pod,
    get_node_count,
    get_node_metrics,
    get_pod_candidates,
    get_worst_node,
    uncordon_node,
    wait_until_ready,
)


@dataclass(frozen=True)
class MoveResult:
    pod_name: str
    deployment_name: str
    status: str
    message: str


@dataclass(frozen=True)
class RebalanceResult:
    worst_node: str
    max_move: int
    moved: list[MoveResult]
    skipped: list[MoveResult]


def run_rebalancer() -> RebalanceResult:
    metrics = get_node_metrics()
    worst_node = get_worst_node(metrics)
    if worst_node is None:
        return RebalanceResult(worst_node="", max_move=0, moved=[], skipped=[])

    node_count = get_node_count()
    max_move = calculate_max_move(node_count)
    candidates = get_pod_candidates(settings.namespace, worst_node.name)

    moved: list[MoveResult] = []
    skipped: list[MoveResult] = []
    last_deployment_name = ""

    cordon_node(worst_node.name)
    try:
        for candidate in candidates:
            if len(moved) >= max_move:
                break
            if candidate.deployment_name == last_deployment_name:
                skipped.append(
                    MoveResult(
                        pod_name=candidate.pod_name,
                        deployment_name=candidate.deployment_name,
                        status="skipped",
                        message="Skipped to avoid consecutive moves from the same deployment.",
                    )
                )
                continue

            result = _move_one_candidate(candidate)
            if result.status == "moved":
                moved.append(result)
                last_deployment_name = candidate.deployment_name
            else:
                skipped.append(result)
    finally:
        uncordon_node(worst_node.name)

    return RebalanceResult(
        worst_node=worst_node.name,
        max_move=max_move,
        moved=moved,
        skipped=skipped,
    )


def _move_one_candidate(candidate: PodCandidate) -> MoveResult:
    delete_pod(settings.namespace, candidate.pod_name)
    ready, replacement_name = wait_until_ready(
        settings.namespace,
        candidate.deployment_name,
        candidate.pod_name,
        settings.wait_ready_timeout_seconds,
    )
    if ready:
        return MoveResult(
            pod_name=candidate.pod_name,
            deployment_name=candidate.deployment_name,
            status="moved",
            message=f"Replacement pod became ready: {replacement_name}",
        )
    return MoveResult(
        pod_name=candidate.pod_name,
        deployment_name=candidate.deployment_name,
        status="timeout",
        message=f"Timed out after {settings.wait_ready_timeout_seconds} seconds.",
    )
