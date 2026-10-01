#pragma once

#include "CallGraph.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InstrTypes.h"
#include "llvm/IR/Module.h"
#include <vector>

namespace reach {

// Pluggable backend for resolving indirect call targets. The type-based
// resolver is the default; AnyResolver (--indirect-any) is a debug variant.
struct IndirectResolver {
  virtual ~IndirectResolver() = default;
  // Precompute over the whole module once before resolve() calls.
  virtual void prepare(llvm::Module &m, const EscapeIndex &idx,
                       FunctionSets &sets) = 0;
  // Candidate callees for one indirect call site (over-approximation).
  virtual void resolve(llvm::CallBase &cb, llvm::SmallVectorImpl<SetId> &out) = 0;
};

} // namespace reach
