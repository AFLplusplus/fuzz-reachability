#include "CallGraph.h"
#include "IndirectResolver.h"
#include "Reachability.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/GlobalAlias.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include <utility>
#include <vector>

using namespace llvm;

namespace reach {

void CallGraph::addEdge(Function *from, Function *to, EdgeKind kind) {
  auto &seen = kind == EdgeKind::Direct ? SeenDirect : SeenIndirect;
  if (seen.insert({from, to}).second)
    Edges[from].push_back({to, kind});
}

Function *directCallee(CallBase &cb) {
  if (cb.isInlineAsm())
    return nullptr;
  return resolveCallableValue(cb.getCalledOperand());
}

Function *resolveCallableValue(Value *value) {
  DenseSet<Value *> seen;
  while (value) {
    value = value->stripPointerCasts();
    if (!seen.insert(value).second)
      return nullptr;
    if (auto *f = dyn_cast<Function>(value))
      return f;
    auto *alias = dyn_cast<GlobalAlias>(value);
    if (!alias)
      return nullptr;
    value = alias->getAliasee();
  }
  return nullptr;
}

void buildDirectEdges(Module &m, CallGraph &g) {
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    if (f.hasPersonalityFn())
      if (Function *personality = resolveCallableValue(f.getPersonalityFn()))
        g.addEdge(&f, personality, EdgeKind::Direct);
    for (Instruction &i : instructions(f))
      if (auto *cb = dyn_cast<CallBase>(&i))
        if (Function *callee = directCallee(*cb))
          g.addEdge(&f, callee, EdgeKind::Direct);
  }
}

static bool isIndirect(CallBase &cb) {
  if (cb.isInlineAsm())
    return false;
  return directCallee(cb) == nullptr;
}

void buildIndirectEdges(Module &m, CallGraph &g, IndirectResolver &r,
                        const EscapeIndex &idx) {
  r.prepare(m, idx);
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

void buildEscapeIndex(Module &m, EscapeIndex &idx) {
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (Function *callee = directCallee(*cb))
          idx.callSites[callee].push_back(cb);
      } else if (auto *store = dyn_cast<StoreInst>(&i)) {
        Value *base = getUnderlyingObject(store->getPointerOperand());
        idx.storedTo[base].push_back(store->getValueOperand());
      }
    }
  }
}

static Value *stripEscape(Value *v) {
  return v ? v->stripPointerCasts() : nullptr;
}

static bool isEscapeCast(Value *v) {
  if (isa<CastInst>(v))
    return true;
  auto *expr = dyn_cast_or_null<ConstantExpr>(v);
  return expr && expr->isCast();
}

static void appendStoredValues(Value *value, const EscapeIndex &idx,
                               SmallVectorImpl<Value *> &out) {
  if (!value)
    return;
  Value *base = getUnderlyingObject(value);
  auto it = idx.storedTo.find(base);
  if (it != idx.storedTo.end())
    out.append(it->second.begin(), it->second.end());
}

static void escapeSuccessors(Value *v, const EscapeIndex &idx,
                             SmallVectorImpl<Value *> &out) {
  appendStoredValues(v, idx, out);
  if (auto *ga = dyn_cast<GlobalAlias>(v)) {
    out.push_back(ga->getAliasee());
    return;
  }
  if (auto *gv = dyn_cast<GlobalVariable>(v)) {
    if (gv->hasInitializer())
      out.push_back(gv->getInitializer());
    return;
  }
  if (auto *arg = dyn_cast<Argument>(v)) {
    auto it = idx.callSites.find(arg->getParent());
    if (it != idx.callSites.end())
      for (CallBase *cb : it->second)
        if (arg->getArgNo() < cb->arg_size())
          out.push_back(cb->getArgOperand(arg->getArgNo()));
    return;
  }
  if (auto *load = dyn_cast<LoadInst>(v)) {
    Value *base = getUnderlyingObject(load->getPointerOperand());
    out.push_back(base);
    appendStoredValues(load->getPointerOperand(), idx, out);
    return;
  }
  if (auto *call = dyn_cast<CallBase>(v)) {
    if (Function *callee = directCallee(*call))
      if (!callee->isDeclaration())
        for (Instruction &i : instructions(*callee))
          if (auto *ret = dyn_cast<ReturnInst>(&i))
            if (Value *rv = ret->getReturnValue())
              out.push_back(rv);
    return;
  }
  if (auto *ce = dyn_cast<ConstantExpr>(v)) {
    for (Use &u : ce->operands())
      out.push_back(u.get());
    return;
  }
  if (auto *ca = dyn_cast<ConstantAggregate>(v)) {
    for (Use &u : ca->operands())
      out.push_back(u.get());
    return;
  }
  if (auto *phi = dyn_cast<PHINode>(v)) {
    for (Value *incoming : phi->incoming_values())
      out.push_back(incoming);
    return;
  }
  if (auto *select = dyn_cast<SelectInst>(v)) {
    out.push_back(select->getTrueValue());
    out.push_back(select->getFalseValue());
    return;
  }
  if (auto *freeze = dyn_cast<FreezeInst>(v)) {
    out.push_back(freeze->getOperand(0));
    return;
  }
  if (auto *cast = dyn_cast<CastInst>(v)) {
    out.push_back(cast->getOperand(0));
    return;
  }
  if (auto *gep = dyn_cast<GetElementPtrInst>(v)) {
    out.push_back(gep->getPointerOperand());
    return;
  }
  if (auto *extract = dyn_cast<ExtractValueInst>(v)) {
    out.push_back(extract->getAggregateOperand());
    return;
  }
  if (auto *insert = dyn_cast<InsertValueInst>(v)) {
    out.push_back(insert->getAggregateOperand());
    out.push_back(insert->getInsertedValueOperand());
    return;
  }
  if (isa<AllocaInst>(v))
    return;
}

static void computeEscapeSets(const std::vector<Value *> &roots,
                              const EscapeIndex &idx,
                              DenseMap<Value *, unsigned> &sccOf,
                              std::vector<SmallVector<Function *, 4>> &sccSinks,
                              std::vector<bool> *sccUnresolved = nullptr,
                              std::vector<bool> *sccCasts = nullptr) {
  struct Frame {
    Value *v;
    SmallVector<Value *, 8> succ;
    unsigned next;
  };
  DenseMap<Value *, unsigned> index;
  DenseMap<Value *, unsigned> low;
  DenseSet<Value *> onStack;
  std::vector<Value *> comp;
  std::vector<Frame> stack;
  unsigned counter = 0;

  for (Value *root : roots) {
    Value *r = stripEscape(root);
    if (!r || index.count(r))
      continue;
    stack.push_back({r, {}, 0});
    while (!stack.empty()) {
      Frame &fr = stack.back();
      Value *v = fr.v;
      if (fr.next == 0) {
        ++counter;
        index[v] = counter;
        low[v] = counter;
        comp.push_back(v);
        onStack.insert(v);
        escapeSuccessors(v, idx, fr.succ);
      }
      bool descended = false;
      while (fr.next < fr.succ.size()) {
        Value *w = stripEscape(fr.succ[fr.next++]);
        if (!w)
          continue;
        auto wi = index.find(w);
        if (wi == index.end()) {
          stack.push_back({w, {}, 0});
          descended = true;
          break;
        }
        if (onStack.count(w) && wi->second < low[v])
          low[v] = wi->second;
      }
      if (descended)
        continue;
      if (low[v] == index[v]) {
        unsigned id = sccSinks.size();
        SmallVector<Value *, 8> members;
        for (;;) {
          Value *w = comp.back();
          comp.pop_back();
          onStack.erase(w);
          members.push_back(w);
          if (w == v)
            break;
        }
        for (Value *w : members)
          sccOf[w] = id;
        DenseSet<Function *> funcs;
        bool unresolved = false;
        bool hasCast = false;
        SmallVector<Value *, 8> succ;
        for (Value *w : members) {
          if (auto *f = dyn_cast<Function>(w))
            funcs.insert(f);
          if (sccCasts && isEscapeCast(w))
            hasCast = true;
          succ.clear();
          escapeSuccessors(w, idx, succ);
          if (sccUnresolved && succ.empty() && !isa<Function>(w) &&
              !(isa<Constant>(w) && !isa<GlobalValue>(w)) &&
              !isa<AllocaInst>(w))
            unresolved = true;
          for (Value *s : succ) {
            if (sccCasts && isEscapeCast(s))
              hasCast = true;
            s = stripEscape(s);
            if (!s)
              continue;
            auto si = sccOf.find(s);
            if (si != sccOf.end() && si->second != id) {
              for (Function *f : sccSinks[si->second])
                funcs.insert(f);
              if (sccUnresolved && (*sccUnresolved)[si->second])
                unresolved = true;
              if (sccCasts && (*sccCasts)[si->second])
                hasCast = true;
            }
          }
        }
        sccSinks.emplace_back(funcs.begin(), funcs.end());
        if (sccUnresolved)
          sccUnresolved->push_back(unresolved);
        if (sccCasts)
          sccCasts->push_back(hasCast);
      }
      unsigned vlow = low[v];
      stack.pop_back();
      if (!stack.empty()) {
        Value *p = stack.back().v;
        if (vlow < low[p])
          low[p] = vlow;
      }
    }
  }
}

static bool callsAnalyzableCallee(CallBase &cb) {
  if (cb.isInlineAsm())
    return false;
  Function *callee = directCallee(cb);
  return callee && !callee->isDeclaration();
}

static void appendOperandBundleRoots(CallBase &cb,
                                     std::vector<Value *> &roots,
                                     std::vector<std::pair<Function *, Value *>> *sites,
                                     Function *caller) {
  for (unsigned i = 0; i < cb.getNumOperandBundles(); ++i)
    for (const Use &input : cb.getOperandBundleAt(i).Inputs) {
      roots.push_back(input.get());
      if (sites)
        sites->push_back({caller, input.get()});
    }
}

void buildEscapeEdges(Module &m, CallGraph &g, const EscapeIndex &idx) {
  std::vector<std::pair<Function *, Value *>> sites;
  std::vector<Value *> roots;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (callsAnalyzableCallee(*cb))
          continue;
        for (const Use &argU : cb->args()) {
          sites.push_back({&f, argU.get()});
          roots.push_back(argU.get());
        }
        appendOperandBundleRoots(*cb, roots, &sites, &f);
      } else if (auto *ret = dyn_cast<ReturnInst>(&i)) {
        if (Value *rv = ret->getReturnValue()) {
          sites.push_back({&f, rv});
          roots.push_back(rv);
        }
      }
    }
  }

  DenseMap<Value *, unsigned> sccOf;
  std::vector<SmallVector<Function *, 4>> sccSinks;
  computeEscapeSets(roots, idx, sccOf, sccSinks);

  for (const auto &site : sites) {
    Value *r = stripEscape(site.second);
    if (!r)
      continue;
    auto it = sccOf.find(r);
    if (it == sccOf.end())
      continue;
    for (Function *callee : sccSinks[it->second])
      g.addEdge(site.first, callee, EdgeKind::Indirect);
  }
}

ValueFlowResult computeValueFlowTargets(Value *root, const EscapeIndex &idx) {
  std::vector<Value *> roots = {root};
  DenseMap<Value *, unsigned> sccOf;
  std::vector<SmallVector<Function *, 4>> sccSinks;
  std::vector<bool> sccUnresolved;
  std::vector<bool> sccCasts;
  computeEscapeSets(roots, idx, sccOf, sccSinks, &sccUnresolved, &sccCasts);
  ValueFlowResult out;
  Value *value = stripEscape(root);
  auto it = sccOf.find(value);
  if (it != sccOf.end()) {
    for (Function *f : sccSinks[it->second])
      out.targets.insert(f);
    out.hasCast = isEscapeCast(root) || sccCasts[it->second];
    out.unresolved = sccUnresolved[it->second];
  }
  return out;
}

DenseSet<Function *> computeAddressFlowTargets(Module &m, const EscapeIndex &idx,
                                               const ReachResult &res) {
  // Root the value-flow at every place an address could be consumed as a
  // callable: an indirect call's callee operand, and the arguments/returns that
  // reach unanalyzable *code* that might call them. Inline asm and intrinsics
  // are excluded: an asm operand (e.g. `std::hint::black_box`, lowered to empty
  // `asm sideeffect ""`) and intrinsics (`llvm.lifetime`, `memcpy`, `dbg`, ...)
  // observe or move a value but cannot invoke an arbitrary function, so an
  // address handed only to them is not evidence of being callable. A function
  // carried to a remaining root has concrete flow evidence, not just a type
  // match. (Confidence-only; reachability/escape edges are unaffected and stay
  // sound.)
  std::vector<Value *> roots;
  for (Function &f : m) {
    if (f.isDeclaration() || !res.reached.count(&f))
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (cb->isInlineAsm())
          continue;
        Function *callee = cb->getCalledFunction();
        if (callee && callee->isIntrinsic())
          continue;
        if (isIndirect(*cb))
          roots.push_back(cb->getCalledOperand());
        if (!callsAnalyzableCallee(*cb))
          for (const Use &argU : cb->args())
            roots.push_back(argU.get());
        if (!callsAnalyzableCallee(*cb))
          appendOperandBundleRoots(*cb, roots, nullptr, nullptr);
      } else if (auto *ret = dyn_cast<ReturnInst>(&i)) {
        if (Value *rv = ret->getReturnValue())
          roots.push_back(rv);
      }
    }
  }

  DenseMap<Value *, unsigned> sccOf;
  std::vector<SmallVector<Function *, 4>> sccSinks;
  computeEscapeSets(roots, idx, sccOf, sccSinks);

  DenseSet<Function *> out;
  for (const auto &sink : sccSinks)
    for (Function *f : sink)
      out.insert(f);
  return out;
}

} // namespace reach
