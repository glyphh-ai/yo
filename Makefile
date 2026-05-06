.PHONY: help install dev test typecheck lint build binary publish-test publish clean

help:
	@echo "yo-term — common tasks"
	@echo ""
	@echo "  make install      - sync dev environment (uv)"
	@echo "  make dev          - run yo --help in dev mode"
	@echo "  make typecheck    - mypy"
	@echo "  make lint         - ruff"
	@echo "  make test         - run smoke imports"
	@echo "  make build        - build wheel + sdist (uv build)"
	@echo "  make binary       - build single-file binary via PyInstaller"
	@echo "  make publish-test - upload to TestPyPI"
	@echo "  make publish      - upload to PyPI (prefer the GH release workflow)"
	@echo "  make clean        - remove build artifacts"

install:
	uv sync --all-extras

dev:
	uv run yo --help

typecheck:
	uv run mypy src/dotyo/ --ignore-missing-imports

lint:
	uv run ruff check src/

test:
	uv run python -c "import dotyo.cli, dotyo.banner, dotyo.commands.doctor, dotyo.commands.login, dotyo.commands.wallet, dotyo.commands.send, dotyo.commands.worker, dotyo.commands.cypher, dotyo.commands.watch, dotyo.mcp.yo_mcp; print('imports ok')"
	uv run yo --help > /dev/null
	uv run yo version

build:
	rm -rf dist/
	uv build
	@echo
	@ls -lh dist/

binary:
	./scripts/build-binary.sh

publish-test: build
	uv publish --publish-url https://test.pypi.org/legacy/

publish: build
	@echo "Prefer 'git tag v0.x.y && git push --tags' — the GH workflow does OIDC publish."
	@echo "If you really mean to publish from your machine, set UV_PUBLISH_TOKEN and run uv publish."

clean:
	rm -rf build/ dist/ src/*.egg-info .ruff_cache .mypy_cache .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
