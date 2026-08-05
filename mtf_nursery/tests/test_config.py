"""Tests for config loading."""

import json
from pathlib import Path

from config import DEFAULT_CONFIG, load_config


def test_load_config_defaults_without_file():
    cfg = load_config()
    assert cfg["ticket_start"] == 15000
    assert cfg["scanner"]["max_dist_200_pct"] == 10.0


def test_load_config_merges_user_file(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ticket_start": 20000, "scanner": {"car_rising_days": 12}}))
    cfg = load_config(path)
    assert cfg["ticket_start"] == 20000
    assert cfg["scanner"]["car_rising_days"] == 12
    assert cfg["scanner"]["max_dist_200_pct"] == DEFAULT_CONFIG["scanner"]["max_dist_200_pct"]
