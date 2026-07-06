#pragma once

#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InstrTypes.h"
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
  llvm::DenseSet<std::pair<llvm::Function *, llvm::Function *>> SeenDirect;
  llvm::DenseSet<std::pair<llvm::Function *, llvm::Function *>> SeenIndirect;
};

struct IndirectResolver; // defined in IndirectResolver.h

// Resolve a CallBase to a concrete callee, seeing through bitcasts and aliases.
// Returns nullptr for genuinely indirect calls and inline asm.
llvm::Function *directCallee(llvm::CallBase &cb);

// Add a Direct edge for every CallBase resolving to a concrete function.
void buildDirectEdges(llvm::Module &m, CallGraph &g);

// Add Indirect edges for every indirect CallBase, using the resolver.
void buildIndirectEdges(llvm::Module &m, CallGraph &g, IndirectResolver &r);

struct EscapeIndex {
  llvm::DenseMap<llvm::Function *, llvm::SmallVector<llvm::CallBase *, 4>> callSites;
  llvm::DenseMap<llvm::Value *, llvm::SmallVector<llvm::Value *, 4>> storedTo;
};

void buildEscapeIndex(llvm::Module &m, EscapeIndex &idx);

void buildEscapeEdges(llvm::Module &m, CallGraph &g, const EscapeIndex &idx);

// Functions for which there is concrete value-flow evidence that the address is
// used as a callable -- it reaches the callee operand of an indirect call, or
// escapes as an argument/return to unanalyzable code -- beyond a bare type
// match. Used only to annotate reachability *confidence*; it never prunes the
// (sound) reachable set. A function reached purely by type matching but absent
// here is a low-confidence (likely-spurious) indirect target.
llvm::DenseSet<llvm::Function *> computeAddressFlowTargets(llvm::Module &m,
                                                           const EscapeIndex &idx);

} // namespace reach
