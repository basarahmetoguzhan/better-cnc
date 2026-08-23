# better-cnc — task runner
#
# One command runs everything:
#
#     make test
#
# No third-party packages are required. The LinuxCNC live USB has no pip
# packages installed and we do not want `pip install` standing between a bug and
# a diagnosis, so the whole suite is stdlib unittest.

PYTHON ?= python3

# ui/ holds the bettercnc package; it is not installed, so every invocation
# needs it on the import path.
export PYTHONPATH := ui:$(PYTHONPATH)

.DEFAULT_GOAL := help
.PHONY: help test test-ui test-scripts coverage ui ui-marble dump lint clean

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

test:  ## run EVERY test in the repo (ui/tests + scripts/test_grbl2ini.py)
	$(PYTHON) ui/tests/run_all.py

test-v:  ## same, verbose
	$(PYTHON) ui/tests/run_all.py -v

test-ui:  ## only the UI tests
	$(PYTHON) -m unittest discover -s ui/tests -t ui/tests

test-scripts:  ## only the pre-existing script tests
	$(PYTHON) scripts/test_grbl2ini.py

coverage:  ## line coverage for ui/bettercnc (stdlib tracer, no `coverage` pkg)
	$(PYTHON) ui/tools/covreport.py

coverage-missing:  ## coverage plus the uncovered line numbers
	$(PYTHON) ui/tools/covreport.py --missing

ui:  ## serve the operator UI for the test rig on :8080 (mock, not connected)
	$(PYTHON) -m bettercnc.app --profile test-rig --port 8080

ui-marble:  ## serve the marble profile, to see what is still TODO
	$(PYTHON) -m bettercnc.app --profile marble --port 8080

dump:  ## render one screen to stdout: make dump SCREEN=makine
	$(PYTHON) -m bettercnc.app --dump $(or $(SCREEN),makine)

lint:  ## byte-compile everything as a cheap syntax check
	$(PYTHON) -m compileall -q ui/bettercnc ui/tests ui/tools scripts && echo "syntax OK"

clean:  ## drop __pycache__
	find ui scripts -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo cleaned
