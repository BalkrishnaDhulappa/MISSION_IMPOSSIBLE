"""Telegram notifications — fire_shop compatible."""

from __future__ import annotations

import os
from enum import Enum

import requests


class Level(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"
    ERROR = "ERROR"


def _prefix(level: Level) -> str:
    return f"[MTF][{level.value}]"


def send_telegram(
    message: str,
    *,
    level: Level = Level.INFO,
    bot_token: str | None = None,
    chat_id: str | None = None,
    timeout: int = 10,
) -> bool:
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    text = f"{_prefix(level)} {message}"
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text},
            timeout=timeout,
        )
        return resp.status_code == 200
    except requests.RequestException:
        return False


def format_emi_alert(symbol: str, amount: float, installment_no: int, status: str) -> str:
    return (
        f"EMI {status.upper()}: {symbol} #{installment_no} ₹{amount:.2f}\n"
        f"→ Kite → Funds → Repay MTF → partial ₹{amount:.2f}"
    )
