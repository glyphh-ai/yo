"""Persistent config at ~/.dotyo/config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir
from pydantic import BaseModel


class Config(BaseModel):
    server_url: str = "https://api.yosup.dev"
    access_token: str | None = None
    refresh_token: str | None = None
    user_id: str | None = None
    email: str | None = None

    # Worker identity
    worker_id: str | None = None  # stable id for this machine's worker
    worker_name: str | None = None  # display name

    # Worker behavior — overridable from CLI flags
    worker_max_concurrent: int = 1
    worker_capabilities: list[str] = []  # e.g. ["sonnet", "code", "research"]
    worker_max_daily_yos: int | None = None  # earn cap; None = unlimited
    worker_prompt_allowlist: list[str] = []  # regex patterns; empty = allow all
    worker_prompt_denylist: list[str] = []  # regex patterns
    worker_log_file: str | None = None  # default ~/.dotyo/logs/worker.log when None


def _config_dir() -> Path:
    # ~/.dotyo/  on every platform — easier to find than platform-specific dirs.
    # One-time migration: if a legacy ~/.yo-term/ exists and ~/.dotyo/ doesn't,
    # move the directory. After that we exclusively use ~/.dotyo/.
    home = Path.home()
    new_dir = home / ".dotyo"
    legacy = home / ".yo-term"
    if legacy.exists() and not new_dir.exists():
        try:
            legacy.rename(new_dir)
        except OSError:
            pass
    return new_dir


def config_path() -> Path:
    return _config_dir() / "config.json"


def _from_env() -> dict[str, Any]:
    out: dict[str, Any] = {}
    if v := os.environ.get("YO_SERVER_URL"):
        out["server_url"] = v
    if v := os.environ.get("YO_TOKEN"):
        out["access_token"] = v
    return out


def load_config() -> Config:
    path = config_path()
    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            raw = {}
    raw.update(_from_env())  # env overrides file
    return Config(**raw)


def save_config(updates: dict[str, Any]) -> Config:
    current = load_config().model_dump()
    current.update(updates)
    next_cfg = Config(**current)

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(next_cfg.model_dump(), indent=2))
    path.chmod(0o600)
    return next_cfg


def clear_auth() -> Config:
    """Wipe credentials but keep server_url + worker_id."""
    return save_config({"access_token": None, "refresh_token": None, "user_id": None, "email": None})
