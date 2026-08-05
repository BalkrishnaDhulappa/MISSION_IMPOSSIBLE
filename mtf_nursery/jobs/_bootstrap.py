"""Shared job bootstrap: path, env, config."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def load_env_file(path: str | Path) -> None:
    """Load KEY=VALUE lines into os.environ (does not override existing)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def bootstrap(env_file: str | Path | None = None) -> Path:
    """Add src to path; optionally load env file."""
    if env_file:
        load_env_file(env_file)
    else:
        default = os.environ.get("MTF_ENV_FILE", "/home/ubuntu/.env_fire_shop")
        load_env_file(default)
    return ROOT
