"""kubectl-only metrics adapter.

This file is the explicit boundary for logic that still depends on the kubectl
CLI. We keep it separate from the Kubernetes Python client so maintainers can
see at a glance which parts of the system depend on text command output.
"""

from __future__ import annotations

import subprocess
import time

from app.config import settings
from app.domain.models import NodeMetric


class KubectlError(RuntimeError):
    """Raised when the kubectl-based metrics path cannot continue."""



def _run_kubectl(args: list[str]) -> str:
    """Run one kubectl command and return stdout as text.

    We intentionally keep the wrapper small because this project only needs
    kubectl for Metrics API access. All structured Kubernetes reads and writes
    belong in the Python client adapter.
    """

    command = [settings.kubectl_bin, *args]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise KubectlError(completed.stderr.strip() or f"kubectl command failed: {' '.join(command)}")
    return completed.stdout



def _parse_percent(raw: str) -> int | None:
    """Return a numeric percentage or None when metrics are temporarily unknown."""

    value = raw.rstrip('%')
    if value == '<unknown>':
        return None
    return int(value)


class KubectlMetricsGateway:
    """Collect node pressure metrics through `kubectl top nodes`.

    Metrics API access is the one place where a kubectl text interface remains
    practical for this project. By isolating it here, the rest of the codebase
    can work with typed domain objects instead of parsing command output.
    """

    def get_node_metrics(self) -> list[NodeMetric]:
        """Load node metrics with retry handling for transient Metrics API gaps."""

        last_error: KubectlError | None = None
        for attempt in range(settings.metrics_retry_count):
            try:
                output = _run_kubectl(['top', 'nodes', '--no-headers'])
                break
            except KubectlError as error:
                last_error = error
                if 'Metrics API not available' not in str(error):
                    raise
                if attempt == settings.metrics_retry_count - 1:
                    raise
                time.sleep(settings.metrics_retry_delay_seconds)
        else:
            if last_error is not None:
                raise last_error
            raise KubectlError('Unable to load node metrics.')

        metrics: list[NodeMetric] = []
        for line in output.splitlines():
            columns = line.split()
            if len(columns) < 5:
                continue

            # `kubectl top nodes` occasionally reports `<unknown>` during cluster
            # recovery. We skip that row instead of failing the whole CronJob.
            cpu_percent = _parse_percent(columns[2])
            memory_percent = _parse_percent(columns[4])
            if cpu_percent is None or memory_percent is None:
                print(f'metrics warning: skipped node line with unknown values: {line}')
                continue

            metrics.append(
                NodeMetric(
                    name=columns[0],
                    cpu_percent=cpu_percent,
                    memory_percent=memory_percent,
                )
            )
        return metrics
