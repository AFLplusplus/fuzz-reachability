#pragma once

#include "CallGraph.h"
#include "Reachability.h"
#include "llvm/Support/raw_ostream.h"

namespace reach {
// Emit a DOT digraph of the reachable subgraph. Indirect edges are dashed/red.
void writeDot(llvm::raw_ostream &os, const CallGraph &g, const ReachResult &res);
}
