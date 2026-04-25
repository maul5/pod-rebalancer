from __future__ import annotations

"""Legacy compatibility wrapper.

Historically this project mixed every Kubernetes-related concern into one file.
We now keep the boundaries explicit:
- `app.adapters.kube_api` contains official Kubernetes Python client logic.
- `app.adapters.kubectl_metrics` contains kubectl-only metrics collection.

This wrapper remains so older imports do not break while maintainers transition
fully to the new module layout.
"""

from app.adapters.kube_api import KubeApiGateway
from app.adapters.kubectl_metrics import KubectlError, KubectlMetricsGateway
from app.domain.models import NodeMetric, PodCandidate

__all__ = [
    'KubeApiGateway',
    'KubectlError',
    'KubectlMetricsGateway',
    'NodeMetric',
    'PodCandidate',
]
