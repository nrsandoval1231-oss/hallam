# Sabra Job Engine — operator commands.
#
# Note for anyone adding a package under packages/ or apps/: the project is
# installed in editable mode, and hatchling's editable mapping is written at
# install time. A NEW package directory is therefore invisible to imports until
# the install is refreshed, and the failure looks like
# `ModuleNotFoundError: No module named 'sabra_x'` even though the directory
# exists. `make sync` after adding a package.
SHELL := bash
VENV := .venv

.PHONY: sync test test-live lint fmt run-api clean reset

sync:
	uv sync --all-extras --reinstall-package sabra-job-engine

# `addopts` in pyproject already sets -q; passing it again made pytest quiet enough
# to omit its own verdict line, so the gate gave an exit code and nothing else.
test:
	uv run pytest tests/

# Tests that reach the network. Excluded from the default suite because they
# depend on third-party availability, not on this repository's correctness.
test-live:
	uv run pytest tests/ -q -m live

lint:
	uv run ruff check packages apps tests

fmt:
	uv run ruff format packages apps tests

init:
	uv run sabra init

discover:
	uv run sabra discover

rank:
	uv run sabra rank

status:
	uv run sabra status

run-api:
	uv run uvicorn sabra_api.main:app --host 127.0.0.1 --port 8787

# Deletes the local pilot database. Only ever points at var/.
reset:
	rm -f var/sabra.db var/sabra.db-wal var/sabra.db-shm

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__
