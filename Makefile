.DEFAULT_GOAL := help

PYTHON ?= python
POETRY ?= poetry
export POETRY_VIRTUALENVS_IN_PROJECT := true
export POETRY_VIRTUALENVS_CREATE := true
export RELEASE_TAG

.PHONY: help setup test build-exe build-deb

help:
	@echo "make setup     - create .venv and install locked dependencies"
	@echo "make test      - run automated tests"
	@echo "make build-exe - build versioned Windows executable and installer"
	@echo "make build-deb - build versioned Debian package and installer"

setup:
	$(POETRY) env use "$(PYTHON)"
	$(POETRY) install --no-interaction --no-ansi

test:
	$(POETRY) run python -m unittest discover -s tests -v

build-exe:
	$(POETRY) run python scripts/build.py exe

build-deb:
	$(POETRY) run python scripts/build.py deb
