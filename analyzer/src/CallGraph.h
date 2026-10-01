#pragma once

#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InstrTypes.h"
#include "llvm/IR/Module.h"
#include <utility>
#include <vector>

namespace reach {

enum class EdgeKind { Direct, Indirect };

using SetId = unsigned;
constexpr SetId NoSet = ~0u;

class FunctionSets {
public:
  SetId add(std::vector<llvm::Function *> members);
  llvm::ArrayRef<llvm::Function *> get(SetId id) const { return Sets[id]; }
  size_t size() const { return Sets.size(); }

private:
  std::vector<std::vector<llvm::Function *>> Sets;
};

// Call graph over llvm::Function*. Declarations are kept as opaque leaf nodes
// (they appear as edge targets but have no out-edges).
class CallGraph {
public:
  using Edge = std::pair<llvm::Function *, EdgeKind>;
  using EdgeMap = llvm::DenseMap<llvm::Function *, llvm::SmallVector<Edge, 4>>;
  using SetMap = llvm::DenseMap<llvm::Function *, llvm::SmallVector<SetId, 2>>;

  void addEdge(llvm::Function *from, llvm::Function *to, EdgeKind kind);
  void addSetEdge(llvm::Function *from, SetId set);
  void addEscapeSet(llvm::Function *from, SetId set);
  const EdgeMap &edges() const { return Edges; }
  const SetMap &setEdges() const { return SetEdges; }
  const SetMap &escapeSets() const { return EscapeSets; }
  FunctionSets &sets() { return Sets; }
  const FunctionSets &sets() const { return Sets; }

  template <typename Fn> void forEachCallee(llvm::Function *from, Fn &&fn) const {
    auto it = Edges.find(from);
    if (it != Edges.end())
      for (const auto &[to, kind] : it->second)
        fn(to, kind);
    auto st = SetEdges.find(from);
    if (st != SetEdges.end())
      for (SetId id : st->second)
        for (llvm::Function *to : Sets.get(id))
          fn(to, EdgeKind::Indirect);
  }

private:
  EdgeMap Edges;
  SetMap SetEdges;
  SetMap EscapeSets;
  FunctionSets Sets;
  llvm::DenseSet<std::pair<llvm::Function *, llvm::Function *>> SeenDirect;
  llvm::DenseSet<std::pair<llvm::Function *, llvm::Function *>> SeenIndirect;
  llvm::DenseSet<std::pair<llvm::Function *, SetId>> SeenSet;
  llvm::DenseSet<std::pair<llvm::Function *, SetId>> SeenEscape;
};

struct IndirectResolver; // defined in IndirectResolver.h
struct EscapeIndex;
struct ReachResult;

// Resolve a CallBase to a concrete callee, seeing through bitcasts and aliases.
// Returns nullptr for genuinely indirect calls and inline asm.
llvm::Function *directCallee(llvm::CallBase &cb);
llvm::Function *resolveCallableValue(llvm::Value *value);
bool isIndirectCall(llvm::CallBase &cb);
bool isEscapeCast(llvm::Value *v);

// Add a Direct edge for every CallBase resolving to a concrete function.
void buildDirectEdges(llvm::Module &m, CallGraph &g);

// Add Indirect edges for every indirect CallBase, using the resolver.
void buildIndirectEdges(llvm::Module &m, CallGraph &g, IndirectResolver &r,
                        const EscapeIndex &idx);

struct EscapeIndex {
  llvm::DenseMap<llvm::Function *, llvm::SmallVector<llvm::CallBase *, 4>> callSites;
  llvm::DenseMap<llvm::Value *, llvm::SmallVector<llvm::Value *, 4>> storedTo;
  llvm::DenseMap<llvm::Value *, llvm::SmallVector<llvm::Value *, 4>> parameterStores;
};

void buildEscapeIndex(llvm::Module &m, EscapeIndex &idx);

void buildEscapeEdges(llvm::Module &m, CallGraph &g, const EscapeIndex &idx);

struct ValueFlow {
  llvm::DenseMap<llvm::Value *, unsigned> sccOf;
  std::vector<SetId> sccSet;
  std::vector<bool> sccUnresolved;
  std::vector<bool> sccCasts;
};

void computeValueFlow(const std::vector<llvm::Value *> &roots,
                      const EscapeIndex &idx, FunctionSets &sets, ValueFlow &out,
                      bool flags, bool parameterStores);

// Functions for which there is concrete value-flow evidence that the address is
// used as a callable -- it reaches the callee operand of an indirect call, or
// escapes as an argument/return to unanalyzable code -- beyond a bare type
// match. Used only to annotate reachability *confidence*; it never prunes the
// (sound) reachable set. A function reached purely by type matching but absent
// here is a low-confidence (likely-spurious) indirect target.
llvm::DenseSet<llvm::Function *> computeAddressFlowTargets(
    llvm::Module &m, const EscapeIndex &idx, const ReachResult &res);

} // namespace reach
