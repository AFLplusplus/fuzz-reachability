# Build the reachability analyzer against a chosen LLVM (>= 21), no CMake.
#
#   make                              # llvm-config-21 (default)
#   make LLVM_CONFIG=llvm-config-23   # build against LLVM 23
#   make BUILD=build/23 LLVM_CONFIG=llvm-config-23   # out-of-place build (used by the matrix)
#   make clean
#
# Linking uses the LLVM shared library (llvm-config --link-shared), which keeps
# the command short and avoids the system-static-lib zstd packaging quirk.

LLVM_CONFIG ?= llvm-config-21
BUILD       ?= build

LLVM_CONFIG_PATH := $(shell command -v '$(LLVM_CONFIG)' 2>/dev/null)
LLVM_VERSION     := $(if $(LLVM_CONFIG_PATH),$(shell '$(LLVM_CONFIG_PATH)' --version 2>/dev/null))
LLVM_MAJOR       := $(word 1,$(subst ., ,$(LLVM_VERSION)))
LLVM_BINDIR      := $(if $(LLVM_CONFIG_PATH),$(shell '$(LLVM_CONFIG_PATH)' --bindir 2>/dev/null))
LLVM_CPPFLAGS    := $(if $(LLVM_CONFIG_PATH),$(shell '$(LLVM_CONFIG_PATH)' --cppflags 2>/dev/null))
LLVM_LDFLAGS     := $(if $(LLVM_CONFIG_PATH),$(shell '$(LLVM_CONFIG_PATH)' --ldflags 2>/dev/null))
LLVM_LDLIBS      := $(if $(LLVM_CONFIG_PATH),$(shell '$(LLVM_CONFIG_PATH)' --link-shared --libs 2>/dev/null))

# Use the clang++ that ships with the chosen LLVM (Make pre-defines CXX=g++, so
# assign unconditionally; `make CXX=...` on the command line still overrides).
CXX  := $(if $(LLVM_BINDIR),$(LLVM_BINDIR)/clang++,clang++)
SCAN_BUILD := $(if $(wildcard $(LLVM_BINDIR)/scan-build),$(LLVM_BINDIR)/scan-build,scan-build-$(LLVM_MAJOR))
BIN  := $(BUILD)/reachability-analyzer
SRC  := $(wildcard src/*.cpp)
OBJ  := $(patsubst src/%.cpp,$(BUILD)/%.o,$(SRC))
DEP  := $(OBJ:.o=.d)

DANGEROUS := ../dangerous_functions.txt
GEN_INC   := $(BUILD)/DangerousFunctions.inc

# Rebuild objects when LLVM_CONFIG changes, not only when sources change --
# otherwise switching LLVM_MAJOR silently relinks stale objects from the old LLVM.
STAMP := $(BUILD)/.llvm-config-stamp
STAMP_VALUE := $(LLVM_CONFIG_PATH)|$(LLVM_VERSION)|$(LLVM_BINDIR)

# -Wno-error: never treat warnings as errors, even if a flag source adds -Werror
# (keeps builds resilient across LLVM versions).
CXXFLAGS := -std=c++17 -O2 -fPIC -Wno-error -MMD -MP -I$(BUILD) $(LLVM_CPPFLAGS)
LDFLAGS  := $(LLVM_LDFLAGS)
LDLIBS   := $(LLVM_LDLIBS)

.PHONY: all check-llvm clean print-config force compdb cppcheck scan-build
all: $(BIN)

check-llvm:
	@if [ -z '$(LLVM_CONFIG_PATH)' ]; then echo 'error: LLVM_CONFIG=$(LLVM_CONFIG) was not found on PATH' >&2; exit 1; fi
	@if ! printf '%s' '$(LLVM_MAJOR)' | grep -Eq '^[0-9]+$$' || [ '$(LLVM_MAJOR)' -lt 21 ]; then echo 'error: LLVM >= 21 is required; $(LLVM_CONFIG_PATH) reports $(LLVM_VERSION)' >&2; exit 1; fi
	@if [ ! -x '$(LLVM_BINDIR)/clang++' ]; then echo 'error: clang++ not found in LLVM bindir $(LLVM_BINDIR)' >&2; exit 1; fi

$(BIN): $(OBJ) | $(BUILD)
	$(CXX) $(OBJ) -o $@ $(LDFLAGS) $(LDLIBS)

$(BUILD)/%.o: src/%.cpp $(STAMP) | $(BUILD)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/Metrics.o: $(GEN_INC)

$(GEN_INC): $(DANGEROUS) ../scripts/gen_dangerous_list.sh | $(BUILD)
	bash ../scripts/gen_dangerous_list.sh $(DANGEROUS) $@

$(STAMP): force check-llvm | $(BUILD)
	@printf '%s\n' '$(STAMP_VALUE)' | cmp -s - $@ 2>/dev/null || printf '%s\n' '$(STAMP_VALUE)' > $@

force:

$(BUILD):
	mkdir -p $(BUILD)

print-config:
	@echo "LLVM_CONFIG=$(LLVM_CONFIG_PATH) version=$(LLVM_VERSION) BUILD=$(BUILD)"

compdb: check-llvm $(GEN_INC) | $(BUILD)
	@rm -rf $(BUILD)/compdb
	@mkdir -p $(BUILD)/compdb
	@set -e; for src in $(SRC); do name=$$(basename "$$src" .cpp); $(CXX) $(filter-out -MMD -MP,$(CXXFLAGS)) -MJ "$(BUILD)/compdb/$$name.json" -fsyntax-only "$$src"; done
	@set -e; tmp=compile_commands.json.tmp; printf '[\n' > "$$tmp"; first=1; for part in $(BUILD)/compdb/*.json; do if [ $$first -eq 0 ]; then printf ',\n' >> "$$tmp"; fi; sed '$$s/,$$//' "$$part" >> "$$tmp"; first=0; done; printf '\n]\n' >> "$$tmp"; mv "$$tmp" compile_commands.json

cppcheck:
	cppcheck --enable=warning,performance,portability --std=c++17 --error-exitcode=1 --suppress=missingIncludeSystem src

scan-build: check-llvm
	$(SCAN_BUILD) --status-bugs $(MAKE) LLVM_CONFIG=$(LLVM_CONFIG_PATH) BUILD=$(BUILD)/scan-build clean all

clean:
	rm -rf $(BUILD) compile_commands.json

-include $(DEP)
