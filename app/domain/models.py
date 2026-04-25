"""Domain objects shared across adapters, services, and notification code.

The goal of this module is to keep the business vocabulary in one place so a
human can understand the rebalance flow without reading Kubernetes client code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeMetric:
    """Normalized node metrics used by the scheduling policy.

    This object is produced from the metrics adapter and intentionally hides
    whether the data came from `kubectl top` or another implementation.
    """

    name: str
    cpu_percent: int
    memory_percent: int

    @property
    def score(self) -> int:
        """Lower score means the node is busier and has less headroom."""
        return (100 - self.cpu_percent) + (100 - self.memory_percent)


@dataclass(frozen=True)
class PodCandidate:
    """One movable Deployment-backed Pod discovered on a busy node."""

    pod_name: str
    deployment_name: str
    node_name: str
    replicas: int


@dataclass(frozen=True)
class MoveResult:
    """Outcome for one candidate processed during a rebalance run."""

    pod_name: str
    deployment_name: str
    status: str
    message: str


@dataclass(frozen=True)
class RebalanceResult:
    """Top-level outcome returned from a single CronJob execution."""

    worst_node: str
    max_move: int
    moved: list[MoveResult]
    skipped: list[MoveResult]
