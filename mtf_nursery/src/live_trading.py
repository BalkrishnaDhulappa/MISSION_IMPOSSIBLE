"""Live trading safety gates (C6)."""

from __future__ import annotations

import os


class LiveTradingBlocked(RuntimeError):
    pass


def live_confirm_ok() -> bool:
    return os.environ.get("LIVE_CONFIRM") == "YES"


def mtf_live_enabled(cfg: dict) -> bool:
    return cfg.get("mode") == "live" and bool(cfg.get("live_mtf_enabled", False))


def liquid_live_enabled(cfg: dict) -> bool:
    return bool(cfg.get("live_liquid_topup", False))


def assert_live_allowed(*, config_live_flag: bool, kind: str = "order") -> None:
    if not live_confirm_ok():
        raise LiveTradingBlocked(
            f"Live {kind} blocked: set LIVE_CONFIRM=YES in environment."
        )
    if not config_live_flag:
        raise LiveTradingBlocked(
            f"Live {kind} blocked: enable the matching config flag "
            f"(live_mtf_enabled or live_liquid_topup) and mode=live for MTF."
        )


def execution_mode_for_mtf(cfg: dict) -> str:
    if mtf_live_enabled(cfg):
        return "live"
    return cfg.get("mode", "dry_run")


def execution_mode_for_liquid(cfg: dict) -> str:
    if liquid_live_enabled(cfg):
        return "live"
    return "dry_run"


def config_live_flag_for_intent(cfg: dict, product: str) -> bool:
    if product.upper() == "MTF":
        return mtf_live_enabled(cfg)
    if product.upper() == "CNC":
        return liquid_live_enabled(cfg)
    return False
