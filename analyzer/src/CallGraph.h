#pragma once

#include "llvm/ADT/ArrayRef.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/MapVector.h"
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/Analysis/TargetLibraryInfo.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/InstrTypes.h"
#include "llvm/IR/Module.h"
#include <cstdint>
#include <deque>
#include <optional>
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

struct MemoryWrite {
  llvm::Value *pointer;
  llvm::Value *value;
};

std::optional<MemoryWrite> memoryWrite(llvm::Instruction &i);

// Add a Direct edge for every CallBase resolving to a concrete function.
void buildDirectEdges(llvm::Module &m, CallGraph &g);

// Add Indirect edges for every indirect CallBase, using the resolver.
void buildIndirectEdges(llvm::Module &m, CallGraph &g, IndirectResolver &r,
                        const EscapeIndex &idx);

struct EscapeIndex {
  llvm::MapVector<llvm::Function *, llvm::SmallVector<llvm::CallBase *, 4>> callSites;
  llvm::DenseMap<llvm::Value *, llvm::SmallVector<llvm::Value *, 4>> storedTo;
  llvm::DenseMap<llvm::Value *, llvm::SmallVector<llvm::Value *, 4>> parameterStores;
};

void buildEscapeIndex(llvm::Module &m, EscapeIndex &idx);

class ExternalMemory {
public:
  ExternalMemory(llvm::Module &m, const EscapeIndex &idx);
  ExternalMemory(const ExternalMemory &) = delete;
  ExternalMemory &operator=(const ExternalMemory &) = delete;

  void addCalledFunction(llvm::Function *f);
  std::vector<llvm::Function *> takeChanged();
  bool writesExternalMemory(const llvm::Instruction &write) const {
    return ExternalWrites.count(&write);
  }

private:
  struct Sources {
    uint64_t external = 0;
    uint64_t holds = 0;
    uint64_t nested = 0;
    bool always = false;

    bool empty() const { return !always && !external && !holds && !nested; }
    bool merge(const Sources &other);
  };

  struct Facts {
    Sources external;
    Sources holds;
    Sources nested;

    bool empty() const {
      return external.empty() && holds.empty() && nested.empty();
    }
    bool merge(const Facts &other);
  };

  struct Writes {
    Sources holds;
    Sources nested;
  };

  struct Summary {
    Facts returned;
    llvm::SmallVector<Writes, 0> writes;
  };

  using Slot = std::pair<const llvm::Value *, unsigned>;

  static bool evaluate(const Sources &sources, const Sources &params);
  void pend(llvm::Function *f);
  void propagate();
  void process(llvm::Function &f);
  void analyze(llvm::Function &f);
  void visit(llvm::Instruction &i);
  void updateArgument(llvm::Argument &arg);
  void schedule(llvm::Value *v);
  void scheduleUsers(llvm::Value *v);
  Facts factsOf(llvm::Value *v) const;
  Facts computeFacts(llvm::Instruction &i);
  Facts argumentFacts(llvm::Argument &arg) const;
  Sources substitute(const Sources &sources, llvm::CallBase &cb) const;
  Facts substitute(const Facts &facts, llvm::CallBase &cb) const;
  Sources localWrite(const llvm::Value *object, unsigned depth) const;
  void applyWrites(llvm::Instruction &i);
  void addWrite(const llvm::Value *pointer, unsigned depth,
                const Sources &sources);
  void markGlobal(const Slot &slot);

  const EscapeIndex &Idx;
  llvm::TargetLibraryInfoImpl TLII;
  llvm::TargetLibraryInfo TLI;
  llvm::DenseMap<llvm::Function *, llvm::SmallVector<llvm::CallBase *, 4>> Calls;
  llvm::DenseSet<const llvm::Function *> Fillers;
  llvm::DenseMap<const llvm::Function *, Summary> Summaries;
  llvm::DenseMap<const llvm::Function *, Sources> Params;
  llvm::DenseSet<Slot> GlobalWritten;
  std::deque<llvm::Function *> Pending;
  llvm::DenseSet<llvm::Function *> PendingSet;
  std::vector<llvm::Function *> Changed;
  llvm::DenseSet<llvm::Function *> ChangedSet;
  llvm::DenseSet<const llvm::Instruction *> ExternalWrites;
  llvm::DenseMap<const llvm::Value *, Facts> ValueFacts;
  llvm::DenseMap<Slot, Sources> LocalWrites;
  llvm::DenseMap<Slot, Sources> GlobalWrites;
  llvm::SmallPtrSet<const llvm::BasicBlock *, 32> Visited;
  llvm::Instruction *Current = nullptr;
  std::vector<llvm::Value *> Work;
  llvm::DenseSet<llvm::Value *> Queued;
  std::vector<llvm::Instruction *> WriteSites;
};

void buildEscapeEdges(llvm::Module &m, CallGraph &g, const EscapeIndex &idx,
                      ExternalMemory &memory);

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
    llvm::Module &m, const EscapeIndex &idx, const ReachResult &res,
    const ExternalMemory &memory);

} // namespace reach
