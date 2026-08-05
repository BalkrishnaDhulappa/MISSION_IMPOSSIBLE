"""Kite Connect client — read-only for dry_run."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class KiteConfigError(RuntimeError):
    pass


def get_kite(
    *,
    api_key: str | None = None,
    token_path: str | Path | None = None,
):
    """
    Return authenticated KiteConnect instance.

    Reads KITE_API_KEY from env and access token from JSON token file
  (same format as fire_shop `.kite_token`).
    """
    api_key = api_key or os.environ.get("KITE_API_KEY", "")
    if not api_key:
        raise KiteConfigError("KITE_API_KEY not set")

    token_path = Path(token_path or os.environ.get("KITE_TOKEN_PATH", ".kite_token"))
    if not token_path.exists():
        raise KiteConfigError(f"Kite token file missing: {token_path}")

    data = json.loads(token_path.read_text(encoding="utf-8"))
    access_token = data.get("access_token")
    if not access_token:
        raise KiteConfigError(f"No access_token in {token_path}")

    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def fetch_holdings(kite: Any) -> list[dict]:
    return kite.holdings()


def fetch_margins_equity(kite: Any) -> dict:
    margins = kite.margins()
    if isinstance(margins, dict) and "equity" in margins:
        return margins["equity"]
    return margins
