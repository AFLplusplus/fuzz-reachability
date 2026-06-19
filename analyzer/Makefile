# Build the reachability analyzer against a chosen LLVM (>= 21), no CMake.
#
#   make                              # llvm-config-21 (default), type-based backend only
#   make LLVM_CONFIG=llvm-config-23   # build against LLVM 23
#   make SVF=1                        # also build the SVF backend
#                                     #   (run scripts/build_svf.sh <major> first)
#   make BUILD=build/23 LLVM_CONFIG=llvm-config-23   # out-of-place build (used by the matrix)
#   make clean
#
# Linking uses the LLVM shared library (llvm-config --link-shared), which keeps
# the command short and avoids the system-static-lib zstd packaging quirk.

LLVM_CONFIG ?= llvm-config-21
SVF         ?= 0
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

# -Wno-error: never treat warnings as errors, even if a flag source adds -Werror
# (keeps builds resilient across LLVM versions). Same policy applied to SVF in
# scripts/build_svf.sh (-DSVF_WARN_AS_ERROR=OFF).
CXXFLAGS := -std=c++17 -O2 -fPIC -Wno-error -MMD -MP $(shell $(LLVM_CONFIG) --cppflags)
LDFLAGS  := $(shell $(LLVM_CONFIG) --ldflags)
LDLIBS   := $(shell $(LLVM_CONFIG) --link-shared --libs)

ifeq ($(SVF),1)
  SVF_PREFIX ?= $(CURDIR)/../third_party/SVF/install-$(LLVM_MAJOR)
  Z3_LIB     ?= $(CURDIR)/../third_party/z3.obj/bin/libz3.so
  EXTAPI     := $(SVF_PREFIX)/lib/extapi.bc
  CXXFLAGS += -DREACHABILITY_ENABLE_SVF=1 -DREACHABILITY_SVF_EXTAPI=\"$(EXTAPI)\" \
              -I$(SVF_PREFIX)/include
  # SVF static archives must precede the LLVM dylib they reference; z3 last.
  LDLIBS := $(SVF_PREFIX)/lib/libSvfLLVM.a $(SVF_PREFIX)/lib/libSvfCore.a $(LDLIBS) $(Z3_LIB)
endif

.PHONY: all clean print-config
all: $(BIN)

$(BIN): $(OBJ) | $(BUILD)
	$(CXX) $(OBJ) -o $@ $(LDFLAGS) $(LDLIBS)

$(BUILD)/%.o: src/%.cpp | $(BUILD)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(BUILD):
	mkdir -p $(BUILD)

print-config:
	@echo "LLVM_CONFIG=$(LLVM_CONFIG) major=$(LLVM_MAJOR) SVF=$(SVF) BUILD=$(BUILD)"

clean:
	rm -rf $(BUILD)

-include $(DEP)
