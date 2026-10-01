#include "CallGraph.h"
#include "IndirectResolver.h"
#include "Reachability.h"
#include "SlotEvidence.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/Analysis/MemoryBuiltins.h"
#include "llvm/Analysis/TargetLibraryInfo.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/GlobalAlias.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/IntrinsicInst.h"
#include <set>
#include <utility>
#include <vector>

using namespace llvm;

namespace reach {

SetId FunctionSets::add(std::vector<Function *> members) {
  Sets.push_back(std::move(members));
  return Sets.size() - 1;
}

void CallGraph::addEdge(Function *from, Function *to, EdgeKind kind) {
  auto &seen = kind == EdgeKind::Direct ? SeenDirect : SeenIndirect;
  if (seen.insert({from, to}).second)
    Edges[from].push_back({to, kind});
}

void CallGraph::addSetEdge(Function *from, SetId set) {
  if (set != NoSet && SeenSet.insert({from, set}).second)
    SetEdges[from].push_back(set);
}

void CallGraph::addEscapeSet(Function *from, SetId set) {
  if (set == NoSet)
    return;
  if (SeenEscape.insert({from, set}).second)
    EscapeSets[from].push_back(set);
  addSetEdge(from, set);
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

std::optional<MemoryWrite> memoryWrite(Instruction &i) {
  if (auto *store = dyn_cast<StoreInst>(&i))
    return MemoryWrite{store->getPointerOperand(), store->getValueOperand()};
  if (auto *rmw = dyn_cast<AtomicRMWInst>(&i))
    if (rmw->getOperation() == AtomicRMWInst::Xchg)
      return MemoryWrite{rmw->getPointerOperand(), rmw->getValOperand()};
  if (auto *cmpxchg = dyn_cast<AtomicCmpXchgInst>(&i))
    return MemoryWrite{cmpxchg->getPointerOperand(), cmpxchg->getNewValOperand()};
  return std::nullopt;
}

bool isIndirectCall(CallBase &cb) {
  if (cb.isInlineAsm())
    return false;
  return directCallee(cb) == nullptr;
}

void buildIndirectEdges(Module &m, CallGraph &g, IndirectResolver &r,
                        const EscapeIndex &idx) {
  r.prepare(m, idx, g.sets());
  SmallVector<SetId, 4> targets;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f))
      if (auto *cb = dyn_cast<CallBase>(&i))
        if (isIndirectCall(*cb)) {
          targets.clear();
          r.resolve(*cb, targets);
          for (SetId id : targets)
            g.addSetEdge(&f, id);
        }
  }
}

namespace {

class CallbackValues {
public:
  CallbackValues(const EscapeIndex &idx, const std::set<SlotKey> &callableFields)
      : Idx(idx), CallableFields(callableFields) {}

  bool carries(Value *value) {
    SmallPtrSet<Value *, 8> visited;
    return carries(value, 0, visited);
  }

private:
  bool carries(Value *value, unsigned depth, SmallPtrSetImpl<Value *> &visited) {
    if (!value->getType()->isPointerTy())
      return false;
    if (resolveCallableValue(value))
      return true;
    Value *v = value->stripPointerCasts();
    if (isa<Argument>(v))
      return true;
    if (depth >= 4 || !visited.insert(v).second)
      return false;
    if (auto *select = dyn_cast<SelectInst>(v))
      return carries(select->getTrueValue(), depth + 1, visited) ||
             carries(select->getFalseValue(), depth + 1, visited);
    if (auto *phi = dyn_cast<PHINode>(v)) {
      for (Value *incoming : phi->incoming_values())
        if (carries(incoming, depth + 1, visited))
          return true;
      return false;
    }
    if (auto *load = dyn_cast<LoadInst>(v)) {
      auto key = fieldKey(load->getPointerOperand());
      return key && CallableFields.count(*key);
    }
    return holdsCallable(getUnderlyingObject(v));
  }

  bool holdsCallable(Value *object) {
    auto cached = Holds.find(object);
    if (cached != Holds.end())
      return cached->second;
    bool holds = false;
    if (auto *global = dyn_cast<GlobalVariable>(object)) {
      if (isDispatchTable(*global)) {
        Holds[object] = false;
        return false;
      }
      if (global->hasInitializer())
        holds = constantHoldsCallable(global->getInitializer());
    }
    if (!holds) {
      auto it = Idx.storedTo.find(object);
      if (it != Idx.storedTo.end())
        for (Value *stored : it->second)
          if (resolveCallableValue(stored)) {
            holds = true;
            break;
          }
    }
    Holds[object] = holds;
    return holds;
  }

  static bool constantHoldsCallable(Constant *c) {
    if (resolveCallableValue(c))
      return true;
    if (!isa<ConstantAggregate>(c))
      return false;
    for (Use &op : c->operands())
      if (constantHoldsCallable(cast<Constant>(op.get())))
        return true;
    return false;
  }

  const EscapeIndex &Idx;
  const std::set<SlotKey> &CallableFields;
  DenseMap<Value *, bool> Holds;
};

} // namespace

static void propagateArgumentStores(EscapeIndex &idx,
                                    const std::set<SlotKey> &callableFields) {
  CallbackValues callbacks(idx, callableFields);
  DenseMap<Argument *, SmallVector<Value *, 2>> callerObjects;
  for (auto &kv : idx.callSites) {
    Function *callee = kv.first;
    if (callee->isDeclaration())
      continue;
    for (CallBase *cb : kv.second)
      for (unsigned k = 0; k < cb->arg_size() && k < callee->arg_size(); ++k) {
        Value *operand = cb->getArgOperand(k);
        if (!operand->getType()->isPointerTy())
          continue;
        Argument *param = callee->getArg(k);
        Value *object = getUnderlyingObject(operand);
        if (object == param)
          continue;
        if (auto *global = dyn_cast<GlobalVariable>(object)) {
          if (global->isConstant())
            continue;
        } else if (isa<Constant>(object)) {
          continue;
        }
        callerObjects[param].push_back(object);
      }
  }

  auto storesThrough = [&](Value *object, SmallVectorImpl<Value *> &out,
                           size_t from) {
    size_t index = 0;
    for (const auto *map : {&idx.storedTo, &idx.parameterStores}) {
      auto it = map->find(object);
      if (it == map->end())
        continue;
      for (Value *value : it->second)
        if (index++ >= from)
          out.push_back(value);
    }
    return index;
  };

  DenseMap<Value *, DenseSet<Value *>> present;
  DenseMap<Argument *, size_t> propagated;
  std::vector<Argument *> work;
  DenseSet<Argument *> queued;
  for (auto &kv : callerObjects)
    if (idx.storedTo.count(kv.first) && queued.insert(kv.first).second)
      work.push_back(kv.first);
  SmallVector<Value *, 8> delta;
  SmallVector<Value *, 8> existing;
  while (!work.empty()) {
    Argument *param = work.back();
    work.pop_back();
    queued.erase(param);
    delta.clear();
    size_t &done = propagated[param];
    done = storesThrough(param, delta, done);
    for (Value *object : callerObjects.lookup(param)) {
      bool grew = false;
      auto inserted = present.try_emplace(object);
      DenseSet<Value *> &have = inserted.first->second;
      if (inserted.second) {
        existing.clear();
        storesThrough(object, existing, 0);
        have.insert(existing.begin(), existing.end());
      }
      for (Value *value : delta)
        if (callbacks.carries(value) && have.insert(value).second) {
          idx.parameterStores[object].push_back(value);
          grew = true;
        }
      if (auto *caller = dyn_cast<Argument>(object))
        if (grew && callerObjects.count(caller) && queued.insert(caller).second)
          work.push_back(caller);
    }
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
        if (auto *copy = dyn_cast<AnyMemTransferInst>(cb))
          idx.storedTo[getUnderlyingObject(copy->getRawDest())].push_back(
              copy->getRawSource());
      } else if (auto write = memoryWrite(i)) {
        idx.storedTo[getUnderlyingObject(write->pointer)].push_back(write->value);
      }
    }
  }
  propagateArgumentStores(idx, collectCallableFields(m));
}

static Value *stripEscape(Value *v) {
  return v ? v->stripPointerCasts() : nullptr;
}

bool isEscapeCast(Value *v) {
  if (isa<CastInst>(v))
    return true;
  auto *expr = dyn_cast_or_null<ConstantExpr>(v);
  return expr && expr->isCast();
}

static void appendStoredValues(Value *value, const EscapeIndex &idx,
                               SmallVectorImpl<Value *> &out,
                               bool parameterStores) {
  if (!value)
    return;
  Value *base = getUnderlyingObject(value);
  auto it = idx.storedTo.find(base);
  if (it != idx.storedTo.end())
    out.append(it->second.begin(), it->second.end());
  if (!parameterStores)
    return;
  auto passed = idx.parameterStores.find(base);
  if (passed != idx.parameterStores.end())
    out.append(passed->second.begin(), passed->second.end());
}

static void escapeSuccessors(Value *v, const EscapeIndex &idx,
                             SmallVectorImpl<Value *> &out,
                             bool parameterStores) {
  appendStoredValues(v, idx, out, parameterStores);
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
  Value *readFrom = nullptr;
  if (auto *load = dyn_cast<LoadInst>(v))
    readFrom = load->getPointerOperand();
  else if (auto *rmw = dyn_cast<AtomicRMWInst>(v))
    readFrom = rmw->getPointerOperand();
  else if (auto *cmpxchg = dyn_cast<AtomicCmpXchgInst>(v))
    readFrom = cmpxchg->getPointerOperand();
  if (readFrom) {
    out.push_back(getUnderlyingObject(readFrom));
    appendStoredValues(readFrom, idx, out, parameterStores);
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

void computeValueFlow(const std::vector<Value *> &roots, const EscapeIndex &idx,
                      FunctionSets &sets, ValueFlow &out, bool flags,
                      bool parameterStores) {
  struct Frame {
    unsigned node;
    unsigned next;
  };
  DenseMap<Value *, unsigned> index;
  std::vector<Value *> nodes;
  std::vector<unsigned> low;
  std::vector<SmallVector<Value *, 4>> succ;
  std::vector<bool> onStack;
  std::vector<unsigned> comp;
  std::vector<Frame> stack;

  auto visit = [&](Value *v) {
    unsigned n = nodes.size();
    index[v] = n;
    nodes.push_back(v);
    low.push_back(n);
    onStack.push_back(true);
    comp.push_back(n);
    succ.emplace_back();
    escapeSuccessors(v, idx, succ.back(), parameterStores);
    stack.push_back({n, 0});
  };

  for (Value *root : roots) {
    Value *r = stripEscape(root);
    if (!r || index.count(r))
      continue;
    visit(r);
    while (!stack.empty()) {
      unsigned n = stack.back().node;
      bool descended = false;
      while (stack.back().next < succ[n].size()) {
        Value *w = stripEscape(succ[n][stack.back().next++]);
        if (!w)
          continue;
        auto wi = index.find(w);
        if (wi == index.end()) {
          visit(w);
          descended = true;
          break;
        }
        if (onStack[wi->second] && wi->second < low[n])
          low[n] = wi->second;
      }
      if (descended)
        continue;
      if (low[n] == n) {
        unsigned id = out.sccSet.size();
        SmallVector<unsigned, 8> members;
        for (;;) {
          unsigned w = comp.back();
          comp.pop_back();
          onStack[w] = false;
          members.push_back(w);
          if (w == n)
            break;
        }
        for (unsigned w : members)
          out.sccOf[nodes[w]] = id;
        std::vector<Function *> own;
        DenseSet<Function *> ownSeen;
        SmallVector<SetId, 4> inherited;
        SmallDenseSet<SetId, 8> inheritedSeen;
        bool unresolved = false;
        bool hasCast = false;
        for (unsigned w : members) {
          Value *wv = nodes[w];
          if (auto *f = dyn_cast<Function>(wv))
            if (ownSeen.insert(f).second)
              own.push_back(f);
          if (flags && isEscapeCast(wv))
            hasCast = true;
          if (flags && succ[w].empty() && !isa<Function>(wv) &&
              !(isa<Constant>(wv) && !isa<GlobalValue>(wv)) &&
              !isa<AllocaInst>(wv))
            unresolved = true;
          for (Value *s : succ[w]) {
            if (flags && isEscapeCast(s))
              hasCast = true;
            s = stripEscape(s);
            if (!s)
              continue;
            if (auto *f = dyn_cast<Function>(s)) {
              if (ownSeen.insert(f).second)
                own.push_back(f);
              continue;
            }
            auto si = out.sccOf.find(s);
            if (si == out.sccOf.end() || si->second == id)
              continue;
            SetId inheritedSet = out.sccSet[si->second];
            if (inheritedSet != NoSet && inheritedSeen.insert(inheritedSet).second)
              inherited.push_back(inheritedSet);
            if (flags && out.sccUnresolved[si->second])
              unresolved = true;
            if (flags && out.sccCasts[si->second])
              hasCast = true;
          }
        }
        SetId result = NoSet;
        if (own.empty() && inherited.size() == 1) {
          result = inherited.front();
        } else if (inherited.empty()) {
          if (!own.empty())
            result = sets.add(std::move(own));
        } else {
          for (SetId sid : inherited)
            for (Function *f : sets.get(sid))
              if (ownSeen.insert(f).second)
                own.push_back(f);
          result = sets.add(std::move(own));
        }
        out.sccSet.push_back(result);
        if (flags) {
          out.sccUnresolved.push_back(unresolved);
          out.sccCasts.push_back(hasCast);
        }
      }
      unsigned vlow = low[n];
      stack.pop_back();
      if (!stack.empty()) {
        unsigned p = stack.back().node;
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

struct EscapeSite {
  Function *from;
  Value *value;
  bool handedOut;
};

static bool registersExitHandler(CallBase &cb) {
  Function *callee = directCallee(cb);
  if (!callee)
    return false;
  StringRef name = callee->getName();
  return name == "atexit" || name == "at_quick_exit" || name == "on_exit" ||
         name == "__cxa_atexit" || name == "__cxa_thread_atexit" ||
         name == "__cxa_thread_atexit_impl";
}

static void appendOperandBundleRoots(CallBase &cb, std::vector<Value *> &roots,
                                     std::vector<EscapeSite> *sites,
                                     Function *caller, bool handedOut) {
  for (unsigned i = 0; i < cb.getNumOperandBundles(); ++i)
    for (const Use &input : cb.getOperandBundleAt(i).Inputs) {
      roots.push_back(input.get());
      if (sites)
        sites->push_back({caller, input.get(), handedOut});
    }
}

static bool isExternalMemory(Value *pointer,
                             const DenseSet<Function *> &externallyCalled,
                             const TargetLibraryInfo &tli, unsigned budget = 8);

static bool returnsFreshMemory(CallBase &call, Function &callee,
                               const TargetLibraryInfo &tli) {
  if (call.hasRetAttr(Attribute::NoAlias) || isAllocationFn(&call, &tli))
    return true;
  StringRef name = callee.getName();
  if (name.starts_with("_Znw") || name.starts_with("_Zna"))
    return true;
  LibFunc lib;
  if (!tli.getLibFunc(callee, lib))
    return false;
  switch (lib) {
  case LibFunc_malloc:
  case LibFunc_calloc:
  case LibFunc_realloc:
  case LibFunc_reallocf:
  case LibFunc_reallocarray:
  case LibFunc_valloc:
  case LibFunc_pvalloc:
  case LibFunc_aligned_alloc:
  case LibFunc_memalign:
  case LibFunc_vec_malloc:
  case LibFunc_vec_calloc:
  case LibFunc_vec_realloc:
  case LibFunc_strdup:
  case LibFunc_strndup:
  case LibFunc_dunder_strdup:
  case LibFunc_dunder_strndup:
    return true;
  default:
    return false;
  }
}

static bool returnsExternalMemory(CallBase &call,
                                  const DenseSet<Function *> &externallyCalled,
                                  const TargetLibraryInfo &tli, unsigned budget) {
  Function *callee = directCallee(call);
  if (!callee || callee->isIntrinsic())
    return false;
  if (callee->isDeclaration())
    return !returnsFreshMemory(call, *callee, tli);
  for (Instruction &i : instructions(*callee)) {
    auto *ret = dyn_cast<ReturnInst>(&i);
    if (!ret || !ret->getReturnValue())
      continue;
    Value *source = getUnderlyingObject(ret->getReturnValue());
    if (auto *arg = dyn_cast<Argument>(source))
      if (arg->getParent() == callee && arg->getArgNo() < call.arg_size())
        source = call.getArgOperand(arg->getArgNo());
    if (isExternalMemory(source, externallyCalled, tli, budget))
      return true;
  }
  return false;
}

static bool isExternalMemory(Value *pointer,
                             const DenseSet<Function *> &externallyCalled,
                             const TargetLibraryInfo &tli, unsigned budget) {
  Value *base = getUnderlyingObject(pointer);
  while (budget-- > 0) {
    if (auto *global = dyn_cast<GlobalVariable>(base))
      return global->isDeclaration();
    if (auto *arg = dyn_cast<Argument>(base))
      return externallyCalled.count(arg->getParent());
    if (auto *call = dyn_cast<CallBase>(base))
      return returnsExternalMemory(*call, externallyCalled, tli, budget);
    auto *load = dyn_cast<LoadInst>(base);
    if (!load)
      return false;
    base = getUnderlyingObject(load->getPointerOperand());
  }
  return false;
}

static bool isDispatchTablePointer(Value *value) {
  auto *global = dyn_cast<GlobalVariable>(getUnderlyingObject(value));
  return global && isDispatchTable(*global);
}

void buildEscapeEdges(Module &m, CallGraph &g, const EscapeIndex &idx) {
  TargetLibraryInfoImpl tlii(m.getTargetTriple());
  TargetLibraryInfo tli(tlii);
  DenseSet<Function *> external;
  std::vector<Function *> externalOrder;
  std::vector<EscapeSite> sites;
  std::vector<Value *> roots;
  DenseSet<Instruction *> storeSites;
  auto addStoreSites = [&](Function &f) {
    for (Instruction &i : instructions(f)) {
      auto write = memoryWrite(i);
      if (!write || storeSites.count(&i) || isDispatchTablePointer(write->value) ||
          !isExternalMemory(write->pointer, external, tli))
        continue;
      storeSites.insert(&i);
      sites.push_back({&f, write->value, true});
      roots.push_back(write->value);
    }
  };
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (callsAnalyzableCallee(*cb))
          continue;
        Function *callee = cb->getCalledFunction();
        bool handedOut =
            !registersExitHandler(*cb) && !(callee && callee->isIntrinsic());
        for (const Use &argU : cb->args()) {
          sites.push_back({&f, argU.get(), handedOut});
          roots.push_back(argU.get());
        }
        appendOperandBundleRoots(*cb, roots, &sites, &f, handedOut);
      } else if (auto *ret = dyn_cast<ReturnInst>(&i)) {
        if (Value *rv = ret->getReturnValue()) {
          sites.push_back({&f, rv, false});
          roots.push_back(rv);
        }
      }
    }
    addStoreSites(f);
  }

  for (;;) {
    FunctionSets scratch;
    ValueFlow flow;
    computeValueFlow(roots, idx, scratch, flow, false, true);
    bool grew = false;
    for (const EscapeSite &site : sites) {
      if (!site.handedOut)
        continue;
      Value *r = stripEscape(site.value);
      auto it = r ? flow.sccOf.find(r) : flow.sccOf.end();
      if (it == flow.sccOf.end() || flow.sccSet[it->second] == NoSet)
        continue;
      for (Function *f : scratch.get(flow.sccSet[it->second]))
        if (!f->isDeclaration() && external.insert(f).second) {
          externalOrder.push_back(f);
          grew = true;
        }
    }
    if (!grew)
      break;
    for (Function *f : externalOrder)
      addStoreSites(*f);
  }

  ValueFlow flow;
  computeValueFlow(roots, idx, g.sets(), flow, false, true);

  for (const EscapeSite &site : sites) {
    Value *r = stripEscape(site.value);
    if (!r)
      continue;
    auto it = flow.sccOf.find(r);
    if (it == flow.sccOf.end())
      continue;
    if (site.handedOut)
      g.addEscapeSet(site.from, flow.sccSet[it->second]);
    else
      g.addSetEdge(site.from, flow.sccSet[it->second]);
  }
  for (Function *f : externalOrder)
    g.markExternallyCalled(f);
}

DenseSet<Function *> computeAddressFlowTargets(
    Module &m, const EscapeIndex &idx, const ReachResult &res,
    const DenseSet<Function *> &externallyCalled) {
  // Root the value-flow at every place an address could be consumed as a
  // callable: an indirect call's callee operand, the arguments/returns that
  // reach unanalyzable *code* that might call them, and stores into memory that
  // such code owns. Inline asm and intrinsics are excluded: an asm operand
  // (e.g. `std::hint::black_box`, lowered to empty `asm sideeffect ""`) and
  // intrinsics (`llvm.lifetime`, `memcpy`, `dbg`, ...)
  // observe or move a value but cannot invoke an arbitrary function, so an
  // address handed only to them is not evidence of being callable. A function
  // carried to a remaining root has concrete flow evidence, not just a type
  // match. (Confidence-only; reachability/escape edges are unaffected and stay
  // sound.)
  TargetLibraryInfoImpl tlii(m.getTargetTriple());
  TargetLibraryInfo tli(tlii);
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
        if (isIndirectCall(*cb))
          roots.push_back(cb->getCalledOperand());
        if (!callsAnalyzableCallee(*cb))
          for (const Use &argU : cb->args())
            roots.push_back(argU.get());
        if (!callsAnalyzableCallee(*cb))
          appendOperandBundleRoots(*cb, roots, nullptr, nullptr, false);
      } else if (auto write = memoryWrite(i)) {
        if (!isDispatchTablePointer(write->value) &&
            isExternalMemory(write->pointer, externallyCalled, tli))
          roots.push_back(write->value);
      } else if (auto *ret = dyn_cast<ReturnInst>(&i)) {
        if (Value *rv = ret->getReturnValue())
          roots.push_back(rv);
      }
    }
  }

  FunctionSets sets;
  ValueFlow flow;
  computeValueFlow(roots, idx, sets, flow, false, false);

  DenseSet<Function *> out;
  DenseSet<SetId> seen;
  for (SetId id : flow.sccSet)
    if (id != NoSet && seen.insert(id).second)
      for (Function *f : sets.get(id))
        out.insert(f);
  return out;
}

} // namespace reach
