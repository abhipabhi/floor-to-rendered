VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: install dev test demo clean

install:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]"

dev:
	$(VENV)/bin/uvicorn app.main:app --reload --port 8020

test:
	$(VENV)/bin/pytest -q

# Runs the whole pipeline on example/ from the command line and writes
# build/ with model.glb, model.obj, model.mtl and blender_import.py
demo:
	$(PY) -m app.cli example --out build
	@echo
	@echo "Wrote build/. Open build/model.glb in Blender, or run:"
	@echo "  blender --python build/blender_import.py"

clean:
	rm -rf build data/jobs .pytest_cache

release:
	$(VENV)/bin/pytest -q
	$(PY) -m app.cli example --out build
	@echo "built and tested"
