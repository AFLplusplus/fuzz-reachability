#include "CallGraph.h"
#include "IndirectResolver.h"
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/GlobalAlias.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"

using namespace llvm;

namespace reach {

void CallGraph::addEdge(Function *from, Function *to, EdgeKind kind) {
  auto &v = Edges[from];
  for (auto &e : v)
    if (e.first == to && e.second == kind)
      return;
  v.push_back({to, kind});
}

// Resolve a CallBase to a concrete callee, seeing through bitcasts and aliases.
// Returns nullptr for genuinely indirect calls and inline asm.
static Function *directCallee(CallBase &cb) {
  if (Function *f = cb.getCalledFunction())
    return f;
  if (cb.isInlineAsm())
    return nullptr;
  Value *v = cb.getCalledOperand()->stripPointerCasts();
  if (auto *f = dyn_cast<Function>(v))
    return f;
  if (auto *ga = dyn_cast<GlobalAlias>(v))
    if (auto *f = dyn_cast<Function>(ga->getAliasee()->stripPointerCasts()))
      return f;
  return nullptr;
}

void buildDirectEdges(Module &m, CallGraph &g) {
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f))
      if (auto *cb = dyn_cast<CallBase>(&i))
        if (Function *callee = directCallee(*cb))
          g.addEdge(&f, callee, EdgeKind::Direct);
  }
}

static bool isIndirect(CallBase &cb) {
  if (cb.isInlineAsm())
    return false;
  if (cb.getCalledFunction())
    return false;
  Value *v = cb.getCalledOperand()->stripPointerCasts();
  return !isa<Function>(v) && !isa<GlobalAlias>(v);
}

void buildIndirectEdges(Module &m, CallGraph &g, IndirectResolver &r) {
  r.prepare(m);
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f))
      if (auto *cb = dyn_cast<CallBase>(&i))
        if (isIndirect(*cb))
          for (Function *callee : r.resolve(*cb))
            g.addEdge(&f, callee, EdgeKind::Indirect);
  }
}

static void collectEscapedFunctions(Value *v,
                                    SmallPtrSetImpl<Function *> &out) {
  if (!v)
    return;
  v = v->stripPointerCasts();
  if (auto *f = dyn_cast<Function>(v)) {
    out.insert(f);
    return;
  }
  if (auto *ga = dyn_cast<GlobalAlias>(v)) {
    collectEscapedFunctions(ga->getAliasee(), out);
    return;
  }
  if (auto *ce = dyn_cast<ConstantExpr>(v)) {
    for (Use &u : ce->operands())
      collectEscapedFunctions(u.get(), out);
    return;
  }
  if (auto *ca = dyn_cast<ConstantAggregate>(v)) {
    for (Use &u : ca->operands())
      collectEscapedFunctions(u.get(), out);
    return;
  }
}

static bool callsAnalyzableCallee(CallBase &cb) {
  if (cb.isInlineAsm())
    return false;
  Function *callee = cb.getCalledFunction();
  if (!callee) {
    Value *v = cb.getCalledOperand()->stripPointerCasts();
    callee = dyn_cast<Function>(v);
    if (!callee)
      if (auto *ga = dyn_cast<GlobalAlias>(v))
        callee = dyn_cast<Function>(ga->getAliasee()->stripPointerCasts());
  }
  return callee && !callee->isDeclaration();
}

void buildEscapeEdges(Module &m, CallGraph &g) {
  SmallPtrSet<Function *, 4> fns;
  auto addAll = [&](Function *from) {
    for (Function *callee : fns)
      g.addEdge(from, callee, EdgeKind::Indirect);
  };
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (callsAnalyzableCallee(*cb))
          continue;
        for (const Use &argU : cb->args()) {
          fns.clear();
          collectEscapedFunctions(argU.get(), fns);
          addAll(&f);
        }
      } else if (auto *ret = dyn_cast<ReturnInst>(&i)) {
        fns.clear();
        collectEscapedFunctions(ret->getReturnValue(), fns);
        addAll(&f);
      }
    }
  }
}

} // namespace reach
