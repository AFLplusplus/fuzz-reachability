#pragma once

#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Module.h"
#include <utility>

namespace reach {

enum class EdgeKind { Direct, Indirect };

// Call graph over llvm::Function*. Declarations are kept as opaque leaf nodes
// (they appear as edge targets but have no out-edges).
class CallGraph {
public:
  using Edge = std::pair<llvm::Function *, EdgeKind>;
  using EdgeMap = llvm::DenseMap<llvm::Function *, llvm::SmallVector<Edge, 4>>;

  void addEdge(llvm::Function *from, llvm::Function *to, EdgeKind kind);
  const EdgeMap &edges() const { return Edges; }

private:
  EdgeMap Edges;
};

struct IndirectResolver; // defined in IndirectResolver.h

// Add a Direct edge for every CallBase resolving to a concrete function.
void buildDirectEdges(llvm::Module &m, CallGraph &g);

// Add Indirect edges for every indirect CallBase, using the resolver.
void buildIndirectEdges(llvm::Module &m, CallGraph &g, IndirectResolver &r);

void buildEscapeEdges(llvm::Module &m, CallGraph &g);

} // namespace reach
