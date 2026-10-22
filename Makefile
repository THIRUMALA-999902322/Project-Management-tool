# Fieldbook MVP. Settings come from the environment; copy .env.example to .env to set them.
-include .env
export

.PHONY: setup test lint demo serve seed verify

setup:            ## install Python deps into .venv
	uv sync

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

test: lint        ## ruff + pytest
	uv run pytest

seed:             ## (re)load the sample company from the design brief
	uv run fieldbook seed --reset

demo: seed        ## sample data + server; open the owner login link it prints
	uv run fieldbook serve

serve:
	uv run fieldbook serve

verify:           ## check the ledger's hash chain
	uv run fieldbook verify
