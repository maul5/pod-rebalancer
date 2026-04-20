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
    get_nodes_by_pressure,
    get_pod_candidates,
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
    candidate_nodes = get_nodes_by_pressure(metrics)
    if not candidate_nodes:
        return RebalanceResult(worst_node="", max_move=0, moved=[], skipped=[])

    node_count = get_node_count()
    max_move = calculate_max_move(node_count)
    selected_node_name = ""
    candidates: list[PodCandidate] = []
    for node in candidate_nodes:
        node_candidates = get_pod_candidates(settings.namespace, node.name)
        if node_candidates:
            selected_node_name = node.name
            candidates = node_candidates
            break

    if not selected_node_name:
        return RebalanceResult(worst_node=candidate_nodes[0].name, max_move=max_move, moved=[], skipped=[])

    moved: list[MoveResult] = []
    skipped: list[MoveResult] = []
    last_deployment_name = ""

    cordon_node(selected_node_name)
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
        uncordon_node(selected_node_name)

    return RebalanceResult(
        worst_node=selected_node_name,
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
