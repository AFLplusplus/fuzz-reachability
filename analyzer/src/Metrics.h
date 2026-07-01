#pragma once

#include "CallGraph.h"
#include "Reachability.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Module.h"
#include <string>
#include <vector>

namespace reach {

struct FuncMetrics {
  unsigned basicBlocks = 0;
  unsigned dangerousCalls = 0;
  unsigned localVars = 0;
  unsigned cyclomatic = 0;
  unsigned loops = 0;
  bool ptrArg = false;
  bool interesting = false;
  bool bottleneck = false;
  bool deadEnd = false;
};

llvm::DenseMap<llvm::Function *, FuncMetrics>
computeMetrics(llvm::Module &m, const CallGraph &g, const ReachResult &res,
               const std::vector<std::string> &roots);

} // namespace reach
