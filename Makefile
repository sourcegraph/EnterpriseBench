# EnterpriseBench — top-level Makefile
#
# Common targets:
#   make help              — list targets with one-line descriptions
#   make verify            — run task validation (mix, CRNT, expected_solution)
#   make verify-tasks      — schema/preflight validation for every task TOML
#   make analyze           — run analysis pipeline → results/analysis/
#   make charts            — regenerate PNG charts from analysis JSON
#   make report            — regenerate the analysis report (markdown)
#   make paper-figures     — full pipeline + copy figures into paper/figures/
#   make paper             — alias for paper-figures (paper.md is hand-written)
#   make test              — run the test suite (tests/)
#   make clean             — remove generated analysis artifacts (NOT raw runs)
#
# Phase 8 deliverable: paper-figures is the single command that regenerates
# every figure used in paper/paper.md from the raw run data under results/.

PYTHON ?= python3

# Where the tests live, and how CI runs them (.github/workflows/ci.yml keeps the
# same marker filter). Override either on the command line:
#   make test PYTEST_PATHS=tests/integrity PYTEST_ARGS='-v'
PYTEST_PATHS ?= tests/
PYTEST_ARGS  ?= -q -m 'not network and not docker'

ANALYSIS_JSON := results/analysis/score_analysis.json
CHARTS_DIR    := results/analysis/charts
PAPER_FIGS    := paper/figures
REPORT_MD     := results/analysis/report.md
COST_REPORT   := results/analysis/cost_report.json
REPRO_REPORT  := results/analysis/reproducibility_report.json

.DEFAULT_GOAL := help

.PHONY: help
help:
	@awk 'BEGIN { FS = ":.*## " } /^[a-zA-Z0-9_-]+:.*## / { printf "  %-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: verify
verify: verify-mix verify-tasks verify-crnt ## Run all task validation checks

.PHONY: verify-mix
verify-mix: ## Validate task mix against PRD targets
	$(PYTHON) scripts/validation/task_mix_validator.py

.PHONY: verify-tasks
verify-tasks: ## Run preflight schema validation for every task
	$(PYTHON) scripts/validate_tasks_preflight.py

.PHONY: verify-crnt
verify-crnt: ## Validate Cross-Repo Necessity Test for multi-repo tasks
	$(PYTHON) scripts/validation/crnt_validator.py

.PHONY: verify-expected-solutions
verify-expected-solutions: ## Validate expected_solution.json files
	$(PYTHON) scripts/validation/validate_expected_solutions.py

.PHONY: analyze
analyze: ## Run the score analysis engine (always re-scans raw runs)
	@mkdir -p $(dir $(ANALYSIS_JSON))
	$(PYTHON) scripts/analyze_scores.py --output $(ANALYSIS_JSON)

.PHONY: charts
charts: analyze ## Regenerate PNG charts from analysis JSON
	@mkdir -p $(CHARTS_DIR)
	$(PYTHON) scripts/generate_charts.py \
		--analysis $(ANALYSIS_JSON) \
		--output-dir $(CHARTS_DIR) \
		$(if $(wildcard $(COST_REPORT)),--cost-report $(COST_REPORT),)

.PHONY: report
report: analyze ## Regenerate the analysis report (markdown)
	$(PYTHON) scripts/generate_report.py \
		--analysis $(ANALYSIS_JSON) \
		--charts-dir $(CHARTS_DIR) \
		--output $(REPORT_MD) \
		$(if $(wildcard $(COST_REPORT)),--cost-report $(COST_REPORT),) \
		$(if $(wildcard $(REPRO_REPORT)),--reproducibility-report $(REPRO_REPORT),)

.PHONY: paper-figures
paper-figures: charts report ## Regenerate paper figures (full pipeline)
	@mkdir -p $(PAPER_FIGS)
	@cp -f $(CHARTS_DIR)/*.png $(PAPER_FIGS)/
	@echo "[paper-figures] copied $$(ls $(PAPER_FIGS)/*.png | wc -l) figures into $(PAPER_FIGS)/"

.PHONY: paper
paper: paper-figures ## Alias: regenerate paper figures (paper.md is hand-written)

.PHONY: test
test: ## Run the test suite (override PYTEST_PATHS / PYTEST_ARGS)
	$(if $(strip $(PYTEST_PATHS)),,$(error PYTEST_PATHS is empty; with no paths pytest walks the whole repo root. Name the test directories explicitly))
	@PYTHONPATH=lib$${PYTHONPATH:+:$$PYTHONPATH} $(PYTHON) -m pytest $(PYTEST_PATHS) $(PYTEST_ARGS); \
	status=$$?; \
	if [ $$status -eq 5 ]; then \
		echo "[test] ERROR: collected 0 tests from '$(strip $(PYTEST_PATHS))'."; \
		echo "[test] A suite that runs nothing is a failure, not a pass. Check PYTEST_PATHS."; \
		exit 1; \
	fi; \
	exit $$status

.PHONY: clean
clean: ## Remove generated analysis artifacts (does NOT touch raw runs)
	rm -f $(ANALYSIS_JSON) $(REPORT_MD)
	rm -rf $(CHARTS_DIR)
