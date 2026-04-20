from __future__ import annotations

import sys

from app.k8s import KubectlError
from app.notifier import send_telegram
from app.scheduler import RebalanceResult, run_rebalancer


def main() -> int:
    result = RebalanceResult(worst_node="", max_move=0, moved=[], skipped=[])
    try:
        result = run_rebalancer()
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
