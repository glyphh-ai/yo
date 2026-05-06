#!/usr/bin/env bash
# Build a single-file `yo` binary for the current platform via PyInstaller.
# Mirrors the matrix in .github/workflows/release.yml so local builds match CI.
#
# Output: dist/yo  (or dist/yo.exe on Windows)
#
# Prerequisites:
#   uv sync --all-extras   # installs pyinstaller from the dev group

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "→ cleaning previous build/"
rm -rf build/ dist/yo dist/yo.exe

echo "→ building single-file binary as 'yo'"
uv run pyinstaller \
    --onefile \
    --name yo \
    --collect-all claude_agent_sdk \
    --hidden-import dotyo.commands.doctor \
    --hidden-import dotyo.commands.login \
    --hidden-import dotyo.commands.wallet \
    --hidden-import dotyo.commands.send \
    --hidden-import dotyo.commands.worker \
    --hidden-import dotyo.commands.cypher \
    --hidden-import dotyo.commands.watch \
    --hidden-import dotyo.mcp.yo_mcp \
    src/dotyo/__main__.py

echo
echo "✓ built: dist/yo"
ls -lh dist/yo* 2>/dev/null || true
echo
echo "smoke test:"
./dist/yo version || true
echo
echo "to install: cp dist/yo ~/.local/bin/  (or wherever)"
