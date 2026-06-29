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

LLVM_MAJOR  := $(shell $(LLVM_CONFIG) --version 2>/dev/null | cut -d. -f1)
LLVM_BINDIR := $(shell $(LLVM_CONFIG) --bindir 2>/dev/null)

# Use the clang++ that ships with the chosen LLVM (Make pre-defines CXX=g++, so
# assign unconditionally; `make CXX=...` on the command line still overrides).
CXX  := $(LLVM_BINDIR)/clang++
BIN  := $(BUILD)/reachability-analyzer
SRC  := $(wildcard src/*.cpp)
OBJ  := $(patsubst src/%.cpp,$(BUILD)/%.o,$(SRC))
DEP  := $(OBJ:.o=.d)

DANGEROUS := ../dangerous_functions.txt
GEN_INC   := $(BUILD)/DangerousFunctions.inc

# Rebuild objects when LLVM_CONFIG changes, not only when sources change --
# otherwise switching LLVM_MAJOR silently relinks stale objects from the old LLVM.
STAMP := $(BUILD)/.llvm-config-stamp

# -Wno-error: never treat warnings as errors, even if a flag source adds -Werror
# (keeps builds resilient across LLVM versions).
CXXFLAGS := -std=c++17 -O2 -fPIC -Wno-error -MMD -MP -I$(BUILD) $(shell $(LLVM_CONFIG) --cppflags)
LDFLAGS  := $(shell $(LLVM_CONFIG) --ldflags)
LDLIBS   := $(shell $(LLVM_CONFIG) --link-shared --libs)

.PHONY: all clean print-config force
all: $(BIN)

$(BIN): $(OBJ) | $(BUILD)
	$(CXX) $(OBJ) -o $@ $(LDFLAGS) $(LDLIBS)

$(BUILD)/%.o: src/%.cpp $(STAMP) | $(BUILD)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD)/Metrics.o: $(GEN_INC)

$(GEN_INC): $(DANGEROUS) ../scripts/gen_dangerous_list.sh | $(BUILD)
	bash ../scripts/gen_dangerous_list.sh $(DANGEROUS) $@

$(STAMP): force | $(BUILD)
	@printf '%s\n' '$(LLVM_CONFIG)' | cmp -s - $@ 2>/dev/null || printf '%s\n' '$(LLVM_CONFIG)' > $@

force:

$(BUILD):
	mkdir -p $(BUILD)

print-config:
	@echo "LLVM_CONFIG=$(LLVM_CONFIG) major=$(LLVM_MAJOR) BUILD=$(BUILD)"

clean:
	rm -rf $(BUILD)

-include $(DEP)
