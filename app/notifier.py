from __future__ import annotations

import requests
from requests import RequestException

from app.config import settings
from app.domain.models import RebalanceResult


def send_telegram(result: RebalanceResult) -> None:
    """Telegram ??? ?? ??? ???. ???? Job ??? ??? ??? ???."""

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    moved_lines = [f"- {item.pod_name} ({item.deployment_name}): {item.message}" for item in result.moved] or ['- none']
    skipped_lines = [f"- {item.pod_name} ({item.deployment_name}): {item.message}" for item in result.skipped] or ['- none']
    message = '\n'.join(
        [
            '[pod-rebalancer]',
            f'namespace: {settings.namespace}',
            f'worst node: {result.worst_node or "n/a"}',
            f'max move: {result.max_move}',
            'moved:',
            *moved_lines,
            'skipped:',
            *skipped_lines,
        ]
    )

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={'chat_id': settings.telegram_chat_id, 'text': message},
            timeout=10,
        )
        response.raise_for_status()
    except RequestException as error:
        print(f'telegram warning: {error}')
