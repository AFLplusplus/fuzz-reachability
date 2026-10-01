#pragma once

#include "CallGraph.h"
#include "Metrics.h"
#include "Reachability.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/raw_ostream.h"
#include <string>
#include <vector>

namespace reach {
constexpr int64_t SchemaVersion = 2;

// Emit the machine-readable reachability report (spec section 5).
// flowTargets carries the evidence used to annotate per-function confidence
// (see computeAddressFlowTargets). metrics carries the per-function structural
// and graph-derived signals (see computeMetrics). escapedFromUnreached lists the
// unreachable functions that only unreached code hands to external code. run,
// when set, is copied into the report verbatim; includeEdges controls the edges
// array.
void writeJson(llvm::raw_ostream &os, llvm::Module &m, const CallGraph &g,
               const ReachResult &res, llvm::StringRef backend,
               const std::vector<std::string> &entries,
               const llvm::DenseSet<llvm::Function *> &flowTargets,
               const llvm::DenseMap<llvm::Function *, FuncMetrics> &metrics,
               const std::vector<llvm::Function *> &escapedFromUnreached,
               const llvm::json::Object *run, bool includeEdges);
}
