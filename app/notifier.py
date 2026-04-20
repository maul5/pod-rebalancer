from __future__ import annotations

import requests

from app.config import settings
from app.scheduler import RebalanceResult


def send_telegram(result: RebalanceResult) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return

    moved_lines = [f"- {item.pod_name} ({item.deployment_name}): {item.message}" for item in result.moved] or ["- none"]
    skipped_lines = [f"- {item.pod_name} ({item.deployment_name}): {item.message}" for item in result.skipped] or ["- none"]
    message = "\n".join(
        [
            "[pod-rebalancer]",
            f"namespace: {settings.namespace}",
            f"worst node: {result.worst_node or 'n/a'}",
            f"max move: {result.max_move}",
            "moved:",
            *moved_lines,
            "skipped:",
            *skipped_lines,
        ]
    )

    requests.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": settings.telegram_chat_id, "text": message},
        timeout=10,
    )

