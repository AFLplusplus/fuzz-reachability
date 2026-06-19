#pragma once

#include "CallGraph.h"
#include "Reachability.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/raw_ostream.h"
#include <string>
#include <vector>

namespace reach {
// Emit the machine-readable reachability report (spec section 5).
void writeJson(llvm::raw_ostream &os, llvm::Module &m, const CallGraph &g,
               const ReachResult &res, llvm::StringRef backend,
               const std::vector<std::string> &entries);
}
