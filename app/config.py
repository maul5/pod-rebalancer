from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    namespace: str = os.getenv("NAMESPACE", "default")
    wait_ready_timeout_seconds: int = _int_env("WAIT_READY_TIMEOUT_SECONDS", 60)
    loop_interval_seconds: int = _int_env("LOOP_INTERVAL_SECONDS", 5)
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    kubectl_bin: str = os.getenv("KUBECTL_BIN", "kubectl")
    max_move_override: int = _int_env("MAX_MOVE_OVERRIDE", 0)
    dry_run: bool = os.getenv("DRY_RUN", "false").lower() == "true"


settings = Settings()

