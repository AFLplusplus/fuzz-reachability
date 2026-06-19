#pragma once

#include "CallGraph.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/IR/Module.h"
#include <string>
#include <vector>

namespace reach {

// How a function was reached. A node reached by both a direct and an indirect
// edge is Both; reached only through indirect edges is Indirect (indirect-only,
// the over-approximation surface to audit).
enum class Via { Direct, Indirect, Both };

struct ReachResult {
  llvm::DenseMap<llvm::Function *, Via> reached;
  std::vector<std::string> missingNames; // entry symbols that did not resolve
};

// BFS from the union of entry symbols. Entry roots are marked Direct.
ReachResult computeReachability(llvm::Module &m, const CallGraph &g,
                                const std::vector<std::string> &entries);

} // namespace reach
