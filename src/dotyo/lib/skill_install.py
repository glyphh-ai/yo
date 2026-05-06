"""Install the dotyo-network skill into the user's `~/.claude/skills/`
directory so Claude Code can discover it on every yo session.

Idempotent: only writes if missing or out-of-date (compared by the
`name` + content hash). Runs at REPL startup; failures are non-fatal
(logged + skipped).
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from importlib import resources
from pathlib import Path


SKILL_NAME = "dotyo-network"


def _claude_skills_dir() -> Path:
    """Where Claude Code looks for user-global skills."""
    return Path.home() / ".claude" / "skills"


def _bundled_skill_path() -> Path | None:
    """Locate the SKILL.md that ships in our wheel.

    Uses importlib.resources so it works whether installed via pipx, uv tool,
    or run from a source checkout via `uv run`.
    """
    try:
        # importlib.resources.files returns a Traversable — convert to Path.
        # On a regular install, this is a real path on disk; on a zipped
        # install we'd need a different path, but our package is plain.
        files = resources.files("dotyo.skills")
        skill = files / SKILL_NAME / "SKILL.md"
        path = Path(str(skill))
        if path.is_file():
            return path
    except Exception:
        pass

    # Fallback: relative to this file (works in dev / `uv run`)
    here = Path(__file__).resolve().parent.parent
    candidate = here / "skills" / SKILL_NAME / "SKILL.md"
    if candidate.is_file():
        return candidate

    return None


def _hash(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def install_skill(verbose: bool = False) -> bool:
    """Copy our SKILL.md into ~/.claude/skills/dotyo-network/. Returns
    True if a write happened, False if no-op (already up to date or
    something went wrong)."""
    src = _bundled_skill_path()
    if src is None:
        if verbose:
            print(f"[skill] bundled SKILL.md not found", file=sys.stderr)
        return False

    target_dir = _claude_skills_dir() / SKILL_NAME
    target = target_dir / "SKILL.md"

    try:
        if target.is_file() and _hash(target) == _hash(src):
            return False  # no-op
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        if verbose:
            print(f"[skill] installed {SKILL_NAME} → {target}", file=sys.stderr)
        return True
    except Exception as e:
        if verbose:
            print(f"[skill] install failed (non-fatal): {e}", file=sys.stderr)
        return False
