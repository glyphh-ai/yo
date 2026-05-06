"""Detect Claude Code OAuth credentials.

Claude Code stores credentials in different places per platform:
  • macOS: Keychain entry "Claude Code-credentials"
  • Linux/older: file at ~/.claude/.credentials.json
  • Some Windows installs: %APPDATA%\\Claude\\credentials.json

We just need to verify they exist for the doctor command — the SDK
auto-detects when it actually fires.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CcCredsResult:
    found: bool
    location: str | None = None  # human-readable description
    kind: str | None = None  # "file" | "keychain"


def _file_candidates() -> list[Path]:
    home = Path.home()
    paths = [
        home / ".claude" / ".credentials.json",
        home / ".claude" / "credentials.json",
        home / ".config" / "claude" / "credentials.json",
    ]
    if sys.platform == "darwin":
        paths.append(home / "Library" / "Application Support" / "Claude" / "credentials.json")
    elif sys.platform == "win32":
        import os
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "Claude" / "credentials.json")
    return paths


def _check_macos_keychain() -> bool:
    """Check macOS Keychain for the Claude Code credentials entry."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def find_cc_credentials() -> CcCredsResult:
    # First, check files (cross-platform)
    for p in _file_candidates():
        if p.exists():
            return CcCredsResult(found=True, location=str(p), kind="file")

    # Then, on macOS, check Keychain
    if sys.platform == "darwin" and _check_macos_keychain():
        return CcCredsResult(found=True, location="macOS Keychain (Claude Code-credentials)", kind="keychain")

    return CcCredsResult(found=False)
