# Top-level convenience targets. The analyzer build lives in analyzer/Makefile.
#
#   make build                 # build the analyzer; default LLVM
#                              #   auto-selected by scripts/select_llvm.sh (>= 21)
#   make build LLVM_MAJOR=23    # ... against LLVM 23
#   make test                  # run the full test suite
#   make matrix                # LLVM 21/22/23(+) compatibility matrix
#   make clean

# Default LLVM major: the newest installed llvm-config-N with N >= 21 (see
# scripts/select_llvm.sh). Override with e.g. `make build LLVM_MAJOR=21`.
LLVM_MAJOR  ?= $(shell bash $(CURDIR)/scripts/select_llvm.sh)
LLVM_CONFIG ?= llvm-config-$(LLVM_MAJOR)

GOBIN       := $(shell go env GOPATH 2>/dev/null)/bin
PY          := $(CURDIR)/.venv/bin/python
ANALYZER     := $(CURDIR)/analyzer/build/reachability-analyzer

.PHONY: help venv build test matrix ci clean

help:
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

venv: ## create the Python venv (.venv) with the driver + test deps
	bash scripts/setup_venv.sh

# Order-only prereq: create the venv if it doesn't exist yet.
$(PY):
	bash scripts/setup_venv.sh

build: ## build the analyzer
	$(MAKE) -C analyzer LLVM_CONFIG=$(LLVM_CONFIG)

test: build | $(PY) ## run the full test suite
	cd driver && PATH="$(GOBIN):$$PATH" \
	  REACHABILITY_ANALYZER="$(ANALYZER)" \
	  "$(PY)" -m pytest tests/ -q

matrix: ## build + test against every installed llvm-config-NN (NN >= 21)
	bash scripts/test_matrix.sh

ci: ## run this repo's suite + cov-analysis's suite (cross-repo key contract)
	bash scripts/ci_cross_repo.sh

clean: ## remove analyzer build outputs
	$(MAKE) -C analyzer clean BUILD=build
	rm -rf analyzer/build/2[0-9]
