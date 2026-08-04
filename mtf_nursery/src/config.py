"""Load and validate configuration."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "dry_run",
    "ticket_start": 15000,
    "max_buys_per_day": 1,
    "max_sells_per_day": 1,
    "max_mtf_buys_per_month": 2,
    "profit_target_pct": 0.0628,
    "buffer_pct": 0.10,
    "emi_weeks": 16,
    "interest_daily": 0.0004,
    "brokerage_rate": 0.003,
    "brokerage_cap": 20,
    "pledge_per_side": 15,
    "gst": 1.18,
    "tax_rate_on_net": 0.2,
    "tax_surcharge_multiplier": 1.04,
    "fire_shop_daily_reserve": 6000,
    "liquid_etf_symbol": "LIQUIDCASE",
    "live_liquid_topup": False,
    "liquid_etf_min_reserve": 10000,
    "liquid_etf_max_sell_per_event": 25000,
    "liquid_sell_cushion_pct": 0.02,
    "rms_loss_warn_pct": 0.15,
    "rms_loss_critical_pct": 0.20,
    "scanner": {
        "require_dma30_gt_dma50": True,
        "max_dist_200_pct": 10.0,
        "car_rising_days": 10,
    },
}


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load JSON config merged over defaults."""
    cfg = deepcopy(DEFAULT_CONFIG)
    if path is None:
        return cfg
    with open(path, encoding="utf-8") as fh:
        user_cfg = json.load(fh)
    _deep_merge(cfg, user_cfg)
    return cfg


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
