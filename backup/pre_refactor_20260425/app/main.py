from __future__ import annotations

import json
import sys

from app.k8s import KubectlError
from app.notifier import send_telegram
from app.scheduler import RebalanceResult, run_rebalancer


def _print_result(result: RebalanceResult) -> None:
    payload = {
        "worst_node": result.worst_node,
        "max_move": result.max_move,
        "moved": [
            {
                "pod_name": item.pod_name,
                "deployment_name": item.deployment_name,
                "status": item.status,
                "message": item.message,
            }
            for item in result.moved
        ],
        "skipped": [
            {
                "pod_name": item.pod_name,
                "deployment_name": item.deployment_name,
                "status": item.status,
                "message": item.message,
            }
            for item in result.skipped
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    result = RebalanceResult(worst_node="", max_move=0, moved=[], skipped=[])
    try:
        result = run_rebalancer()
        _print_result(result)
        send_telegram(result)
        return 0
    except KubectlError as error:
        result = RebalanceResult(worst_node="", max_move=0, moved=[], skipped=[])
        print(f"kubectl error: {error}", file=sys.stderr)
        send_telegram(result)
        return 1
    except Exception as error:  # pragma: no cover
        print(f"unexpected error: {error}", file=sys.stderr)
        send_telegram(result)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
