"""?????? ???.

? ??? ?? ?? ??? ?? ??? ? ?? ??? ? ???
????? ?? ?????.

1. ??? ??
2. ???? ??? ??
3. ??? ?? ?? JSON ?? ??
4. ??? ?? ?? Telegram ??? ??
"""

from __future__ import annotations

import json
import sys

from app.adapters.kube_api import KubeApiGateway
from app.adapters.kubectl_metrics import KubectlError, KubectlMetricsGateway
from app.domain.models import RebalanceResult
from app.notifier import send_telegram
from app.services.rebalancer import RebalanceService


def _print_result(result: RebalanceResult) -> None:
    """`kubectl logs`? ?? ??? ?? ?? ??? JSON? ?????."""

    payload = {
        'worst_node': result.worst_node,
        'max_move': result.max_move,
        'moved': [
            {
                'pod_name': item.pod_name,
                'deployment_name': item.deployment_name,
                'status': item.status,
                'message': item.message,
            }
            for item in result.moved
        ],
        'skipped': [
            {
                'pod_name': item.pod_name,
                'deployment_name': item.deployment_name,
                'status': item.status,
                'message': item.message,
            }
            for item in result.skipped
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    """????? ? ? ???? ??? ?? ??? ?????."""

    result = RebalanceResult(worst_node='', max_move=0, moved=[], skipped=[])

    # ??? ??? ?????? ??? ??? ??? ?? ????
    # ????? ???.
    service = RebalanceService(KubectlMetricsGateway(), KubeApiGateway())
    try:
        result = service.run()
        _print_result(result)
        send_telegram(result)
        return 0
    except KubectlError as error:
        print(f'kubectl error: {error}', file=sys.stderr)
        send_telegram(result)
        return 1
    except Exception as error:  # pragma: no cover
        print(f'unexpected error: {error}', file=sys.stderr)
        send_telegram(result)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
