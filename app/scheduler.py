from __future__ import annotations

"""Legacy compatibility wrapper for the rebalance service.

The real orchestration now lives in `app.services.rebalancer`, which keeps the
business rules separate from adapter details. This file exists only to keep
older imports working while humans learn the new structure.
"""

from app.adapters.kube_api import KubeApiGateway
from app.adapters.kubectl_metrics import KubectlMetricsGateway
from app.services.rebalancer import RebalanceService


def run_rebalancer():
    """Backward-compatible entrypoint used by older code paths."""

    return RebalanceService(KubectlMetricsGateway(), KubeApiGateway()).run()
