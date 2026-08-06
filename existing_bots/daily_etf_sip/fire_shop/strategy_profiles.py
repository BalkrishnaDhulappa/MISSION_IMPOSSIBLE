#!/usr/bin/env python3
"""Formal strategy profiles for the two-engine setup.

This file is intentionally separate from the live order engine.
It defines two capital buckets with distinct state files and exit rules:

1. core_accumulator
   - new-buy biased
   - profit-only exits
   - never sells at a loss
   - intended for steady accumulation

2. growth_rotator
   - the highest-CAGR variant from the backtests
   - monthly 6-month relative momentum rotation
   - top 10 names by default
   - intended for the capital-ready growth bucket

The goal is to keep both strategies explicit so they do not share state,
logic, or assumptions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List

BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class EngineProfile:
    name: str
    description: str
    state_file: str
    universe_file: str
    mode: str
    rebalance_frequency: str
    entry_policy: str
    exit_policy: str
    buy_bias: str
    score_weights: Dict[str, float]
    risk_notes: List[str]


ENGINE_PROFILES: Dict[str, EngineProfile] = {
    "core_accumulator": EngineProfile(
        name="core_accumulator",
        description="New-buy-biased accumulation engine with profit-only exits.",
        state_file="positions_state_core.json",
        universe_file="etf_universe.json",
        mode="daily",
        rebalance_frequency="daily",
        entry_policy=(
            "Rank new ETFs ahead of bids; bids only compete when price has fallen enough."
        ),
        exit_policy=(
            "Sell only when the position is in profit; do not realize losses."
        ),
        buy_bias="new_buy_first",
        score_weights={
            "new_weight": 1.8,
            "bid_last_weight": 1.2,
            "bid_dma_weight": 0.30,
            "bid_penalty_per_level": 0.025,
            "profit_target_base": 0.08,
        },
        risk_notes=[
            "Can retain underwater positions for long periods.",
            "Capital efficiency depends on new names continuing to appear.",
            "This is not a rotation engine."
        ],
    ),
    "growth_rotator": EngineProfile(
        name="growth_rotator",
        description="Higher-CAGR growth bucket using monthly 6-month momentum rotation.",
        state_file="positions_state_growth.json",
        universe_file="etf_universe.json",
        mode="monthly_rotation",
        rebalance_frequency="monthly",
        entry_policy=(
            "Buy the strongest ETFs by 6-month relative momentum, rebalanced monthly."
        ),
        exit_policy=(
            "Rotate out of names that fall out of the ranked basket at rebalance."
        ),
        buy_bias="rotation_top_k",
        score_weights={
            "lookback_days": 126,
            "top_k": 10,
            "skip_recent_days": 0,
            "trend_filter_days": 0,
        },
        risk_notes=[
            "Higher turnover than the accumulator bucket.",
            "Uses a separate state file so it never collides with core positions.",
            "Best treated as a distinct capital bucket, not a replacement for the core engine."
        ],
    ),
}


def list_profiles() -> List[str]:
    return sorted(ENGINE_PROFILES.keys())


def get_profile(name: str) -> EngineProfile:
    try:
        return ENGINE_PROFILES[name]
    except KeyError as exc:
        raise KeyError(f"Unknown profile: {name}") from exc


def profile_to_dict(name: str) -> Dict[str, object]:
    return asdict(get_profile(name))


def dump_profiles() -> str:
    payload = {name: asdict(profile) for name, profile in ENGINE_PROFILES.items()}
    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    print(dump_profiles())
