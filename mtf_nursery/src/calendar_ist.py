"""IST market calendar — reuse fire_shop JSON format."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class MarketCalendar:
    holidays: frozenset[date]
    market_open_minutes: int
    market_close_minutes: int
    calendar_path: str


def load_market_calendar(calendar_dir: str | Path, for_year: int | None = None) -> MarketCalendar:
    calendar_dir = Path(calendar_dir)
    year = for_year or datetime.now(IST).year
    path = calendar_dir / f"market_calendar_{year}.json"
    if not path.exists():
        raise FileNotFoundError(f"Market calendar not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    holidays = frozenset(date.fromisoformat(d) for d in data["holidays"])
    oh, om = (int(x) for x in data["market_open"].split(":"))
    ch, cm = (int(x) for x in data["market_close"].split(":"))
    return MarketCalendar(
        holidays=holidays,
        market_open_minutes=oh * 60 + om,
        market_close_minutes=ch * 60 + cm,
        calendar_path=str(path),
    )


def is_trading_day(cal: MarketCalendar, on: date | None = None) -> bool:
    on = on or datetime.now(IST).date()
    if on.weekday() >= 5:
        return False
    return on not in cal.holidays


def should_run_scan(cfg: dict, on: date | None = None) -> bool:
    """Skip scan on weekends/holidays (uses fire_shop market calendar)."""
    cal_dir = cfg.get("market_calendar_dir", "/home/ubuntu/fire_shop")
    try:
        return is_trading_day(load_market_calendar(cal_dir), on)
    except FileNotFoundError:
        return True
