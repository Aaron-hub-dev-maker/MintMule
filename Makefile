# Linux / macOS convenience targets (Windows: use the PowerShell commands in README.md).
PY ?= .venv/bin/python

.PHONY: venv starter full test validate repro sqlite er

venv:
	python3.11 -m venv .venv && $(PY) -m pip install -r requirements.txt

starter:
	$(PY) -m generator --profile starter

full:
	$(PY) -m generator

test:
	$(PY) -m pytest

validate:
	$(PY) -m generator.validator output/full

repro:
	$(PY) -m generator.reproducibility --profile full

sqlite:
	$(PY) -m generator.load_sqlite output/full

er:
	$(PY) -m generator.schema
