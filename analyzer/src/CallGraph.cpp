#include "CallGraph.h"
#include "IndirectResolver.h"
#include "Reachability.h"
#include "SlotEvidence.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/PostOrderIterator.h"
#include "llvm/ADT/SmallPtrSet.h"
#include "llvm/Analysis/MemoryBuiltins.h"
#include "llvm/Analysis/TargetLibraryInfo.h"
#include "llvm/Analysis/ValueTracking.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/GlobalAlias.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/IntrinsicInst.h"
#include <algorithm>
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
    if (auto *f = dyn_cast<Function>(value))
      return f;
    auto *alias = dyn_cast<GlobalAlias>(value);
    if (!alias || !seen.insert(value).second)
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
  CallbackValues(const EscapeIndex &idx, const std::set<SlotKey> &callableFields,
                 const DenseSet<Value *> &holders)
      : Idx(idx), CallableFields(callableFields), Holders(holders) {}

  bool carries(Value *value) {
    auto cached = Carries.find(value);
    if (cached != Carries.end())
      return cached->second;
    SmallPtrSet<Value *, 8> visited;
    bool result = carries(value, 0, visited);
    Carries[value] = result;
    return result;
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
      if (auto key = fieldKey(load->getPointerOperand()))
        return CallableFields.count(*key);
      v = load->getPointerOperand();
    }
    return pointsToCallable(v, depth + 1, visited);
  }

  bool pointsToCallable(Value *pointer, unsigned depth,
                        SmallPtrSetImpl<Value *> &visited) {
    SmallVector<const Value *, 4> objects;
    getUnderlyingObjects(pointer, objects);
    for (const Value *o : objects) {
      Value *object = const_cast<Value *>(o);
      if (Holders.count(object))
        return true;
      if (depth >= 4 || !visited.insert(object).second)
        continue;
      if (auto *arg = dyn_cast<Argument>(object)) {
        auto it = Idx.callSites.find(arg->getParent());
        if (it == Idx.callSites.end())
          continue;
        for (CallBase *cb : it->second)
          if (arg->getArgNo() < cb->arg_size() &&
              pointsToCallable(cb->getArgOperand(arg->getArgNo()), depth + 1,
                               visited))
            return true;
      } else if (auto *load = dyn_cast<LoadInst>(object)) {
        auto it = Idx.storedTo.find(getUnderlyingObject(load->getPointerOperand()));
        if (it == Idx.storedTo.end())
          continue;
        for (Value *stored : it->second)
          if (stored->getType()->isPointerTy() &&
              pointsToCallable(stored, depth + 1, visited))
            return true;
      }
    }
    return false;
  }

  const EscapeIndex &Idx;
  const std::set<SlotKey> &CallableFields;
  const DenseSet<Value *> &Holders;
  DenseMap<Value *, bool> Carries;
};

} // namespace

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

static DenseSet<Argument *> collectCallableArguments(const EscapeIndex &idx) {
  DenseSet<Argument *> callable;
  std::vector<Argument *> work;
  auto add = [&](Argument *arg) {
    if (callable.insert(arg).second)
      work.push_back(arg);
  };
  for (auto &kv : idx.callSites) {
    Function *callee = kv.first;
    if (callee->isDeclaration())
      continue;
    for (CallBase *cb : kv.second)
      for (unsigned k = 0; k < cb->arg_size() && k < callee->arg_size(); ++k)
        if (resolveCallableValue(cb->getArgOperand(k)))
          add(callee->getArg(k));
  }
  while (!work.empty()) {
    Argument *arg = work.back();
    work.pop_back();
    for (Use &use : arg->uses()) {
      auto *cb = dyn_cast<CallBase>(use.getUser());
      if (!cb || !cb->isArgOperand(&use))
        continue;
      Function *callee = directCallee(*cb);
      unsigned k = cb->getArgOperandNo(&use);
      if (callee && !callee->isDeclaration() && k < callee->arg_size())
        add(callee->getArg(k));
    }
  }
  return callable;
}

static DenseSet<Value *> collectHolders(
    Module &m, const EscapeIndex &idx,
    const MapVector<Argument *, SmallVector<Value *, 2>> &callerObjects,
    const DenseMap<Value *, SmallVector<Value *, 2>> &copiedTo) {
  DenseSet<Argument *> callableArgs = collectCallableArguments(idx);
  DenseSet<Value *> holders;
  std::vector<Value *> work;
  auto add = [&](Value *object) {
    if (auto *global = dyn_cast<GlobalVariable>(object))
      if (isDispatchTable(*global))
        return;
    if (holders.insert(object).second)
      work.push_back(object);
  };
  for (GlobalVariable &global : m.globals())
    if (global.hasInitializer() && constantHoldsCallable(global.getInitializer()))
      add(&global);
  for (auto &kv : idx.storedTo)
    for (Value *stored : kv.second) {
      auto *arg = dyn_cast<Argument>(stored->stripPointerCasts());
      if ((arg && callableArgs.count(arg)) || resolveCallableValue(stored)) {
        add(kv.first);
        break;
      }
    }
  while (!work.empty()) {
    Value *object = work.back();
    work.pop_back();
    auto copies = copiedTo.find(object);
    if (copies != copiedTo.end())
      for (Value *copy : copies->second)
        add(copy);
    if (auto *param = dyn_cast<Argument>(object))
      for (Value *caller : callerObjects.lookup(param))
        add(caller);
  }
  return holders;
}

static void propagateArgumentStores(
    Module &m, EscapeIndex &idx, const std::set<SlotKey> &callableFields,
    const DenseMap<Value *, SmallVector<Value *, 2>> &copiedTo) {
  MapVector<Argument *, SmallVector<Value *, 2>> callerObjects;
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
  DenseSet<Value *> holders = collectHolders(m, idx, callerObjects, copiedTo);
  CallbackValues callbacks(idx, callableFields, holders);

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
  DenseMap<Value *, SmallVector<Value *, 2>> copiedTo;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (Function *callee = directCallee(*cb))
          idx.callSites[callee].push_back(cb);
        if (auto *copy = dyn_cast<AnyMemTransferInst>(cb)) {
          Value *dest = getUnderlyingObject(copy->getRawDest());
          idx.storedTo[dest].push_back(copy->getRawSource());
          copiedTo[getUnderlyingObject(copy->getRawSource())].push_back(dest);
        }
      } else if (auto write = memoryWrite(i)) {
        idx.storedTo[getUnderlyingObject(write->pointer)].push_back(write->value);
      }
    }
  }
  propagateArgumentStores(m, idx, collectCallableFields(m), copiedTo);
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

static bool isExitHandlerRegistration(StringRef name) {
  return name == "atexit" || name == "at_quick_exit" || name == "on_exit" ||
         name == "__cxa_atexit" || name == "__cxa_thread_atexit" ||
         name == "__cxa_thread_atexit_impl";
}

static bool registersExitHandler(CallBase &cb) {
  Function *callee = directCallee(cb);
  return callee && isExitHandlerRegistration(callee->getName());
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

static uint64_t argumentBit(unsigned index) {
  return uint64_t(1) << std::min(index, 63u);
}

static Function *calleeOf(CallBase &cb) {
  if (Function *f = cb.getCalledFunction())
    return f;
  return directCallee(cb);
}

bool ExternalMemory::Sources::merge(const Sources &other) {
  Sources before = *this;
  always |= other.always;
  external |= other.external;
  holds |= other.holds;
  nested |= other.nested;
  return always != before.always || external != before.external ||
         holds != before.holds || nested != before.nested;
}

bool ExternalMemory::Facts::merge(const Facts &other) {
  bool changed = external.merge(other.external);
  changed |= holds.merge(other.holds);
  changed |= nested.merge(other.nested);
  return changed;
}

bool ExternalMemory::evaluate(const Sources &sources, const Sources &params) {
  return sources.always || (sources.external & params.external) ||
         (sources.holds & params.holds) || (sources.nested & params.nested);
}

ExternalMemory::ExternalMemory(Module &m, const EscapeIndex &idx)
    : Idx(idx), TLII(m.getTargetTriple()), TLI(TLII) {
  for (Function &f : m) {
    if (f.isDeclaration()) {
      LibFunc lib;
      if (!f.isIntrinsic() && !isExitHandlerRegistration(f.getName()) &&
          !TLI.getLibFunc(f, lib))
        Fillers.insert(&f);
      continue;
    }
    for (Instruction &i : instructions(f))
      if (auto *cb = dyn_cast<CallBase>(&i))
        if (callsAnalyzableCallee(*cb))
          Calls[&f].push_back(cb);
  }
  DenseSet<Function *> visited;
  for (Function &root : m) {
    if (root.isDeclaration() || !visited.insert(&root).second)
      continue;
    SmallVector<std::pair<Function *, unsigned>, 16> stack{{&root, 0}};
    while (!stack.empty()) {
      Function *f = stack.back().first;
      unsigned next = stack.back().second++;
      auto calls = Calls.find(f);
      if (calls != Calls.end() && next < calls->second.size()) {
        Function *callee = calleeOf(*calls->second[next]);
        if (visited.insert(callee).second)
          stack.push_back({callee, 0});
        continue;
      }
      pend(f);
      stack.pop_back();
    }
  }
  propagate();
  Changed.clear();
  ChangedSet.clear();
}

void ExternalMemory::pend(Function *f) {
  if (PendingSet.insert(f).second)
    Pending.push_back(f);
}

void ExternalMemory::addCalledFunction(Function *f) {
  Sources received;
  for (Argument &arg : f->args())
    if (arg.getType()->isPointerTy())
      received.external |= argumentBit(arg.getArgNo());
  if (Params[f].merge(received))
    pend(f);
}

std::vector<Function *> ExternalMemory::takeChanged() {
  propagate();
  std::vector<Function *> out;
  out.swap(Changed);
  ChangedSet.clear();
  return out;
}

void ExternalMemory::propagate() {
  while (!Pending.empty()) {
    Function *f = Pending.front();
    Pending.pop_front();
    PendingSet.erase(f);
    process(*f);
  }
}

void ExternalMemory::process(Function &f) {
  analyze(f);
  Summary &summary = Summaries[&f];
  bool grew = false;
  for (BasicBlock &bb : f)
    if (auto *ret = dyn_cast<ReturnInst>(bb.getTerminator()))
      if (Value *rv = ret->getReturnValue())
        if (rv->getType()->isPointerTy())
          grew |= summary.returned.merge(factsOf(rv));
  for (Argument &arg : f.args()) {
    Writes writes{localWrite(&arg, 0), localWrite(&arg, 1)};
    if (writes.holds.empty() && writes.nested.empty())
      continue;
    if (summary.writes.size() <= arg.getArgNo())
      summary.writes.resize(arg.getArgNo() + 1);
    grew |= summary.writes[arg.getArgNo()].holds.merge(writes.holds);
    grew |= summary.writes[arg.getArgNo()].nested.merge(writes.nested);
  }
  if (grew) {
    auto callers = Idx.callSites.find(&f);
    if (callers != Idx.callSites.end())
      for (CallBase *cb : callers->second)
        pend(cb->getFunction());
  }

  Sources params = Params.lookup(&f);
  auto calls = Calls.find(&f);
  if (calls != Calls.end())
    for (CallBase *cb : calls->second) {
      Function *callee = calleeOf(*cb);
      Sources passed;
      for (unsigned k = 0; k < cb->arg_size() && k < callee->arg_size(); ++k) {
        Value *operand = cb->getArgOperand(k);
        if (!operand->getType()->isPointerTy())
          continue;
        Facts facts = factsOf(operand);
        if (evaluate(facts.external, params))
          passed.external |= argumentBit(k);
        if (evaluate(facts.holds, params))
          passed.holds |= argumentBit(k);
        if (evaluate(facts.nested, params))
          passed.nested |= argumentBit(k);
      }
      if (!passed.empty() && Params[callee].merge(passed))
        pend(callee);
    }
  SmallVector<Slot, 4> written;
  for (auto &kv : GlobalWrites)
    if (evaluate(kv.second, params))
      written.push_back(kv.first);
  for (const Slot &slot : written)
    markGlobal(slot);
  for (Instruction *i : WriteSites) {
    Value *pointer = isa<AnyMemTransferInst>(i)
                         ? cast<AnyMemTransferInst>(i)->getRawDest()
                         : memoryWrite(*i)->pointer;
    if (evaluate(factsOf(pointer).external, params))
      ExternalWrites.insert(i);
  }
  if (ChangedSet.insert(&f).second)
    Changed.push_back(&f);
}

void ExternalMemory::markGlobal(const Slot &slot) {
  if (!GlobalWritten.insert(slot).second)
    return;
  SmallVector<const Value *, 8> work{slot.first};
  SmallPtrSet<const Value *, 8> seen;
  while (!work.empty()) {
    const Value *v = work.pop_back_val();
    for (const User *user : v->users()) {
      if (auto *i = dyn_cast<Instruction>(user))
        pend(const_cast<Function *>(i->getFunction()));
      else if ((isa<ConstantExpr>(user) || isa<GlobalAlias>(user)) &&
               seen.insert(user).second)
        work.push_back(user);
    }
  }
}

void ExternalMemory::analyze(Function &f) {
  ValueFacts.clear();
  LocalWrites.clear();
  GlobalWrites.clear();
  WriteSites.clear();
  Visited.clear();
  for (Argument &arg : f.args())
    if (arg.getType()->isPointerTy())
      updateArgument(arg);
  ReversePostOrderTraversal<Function *> order(&f);
  for (BasicBlock *bb : order) {
    for (Instruction &i : *bb) {
      Current = &i;
      if (memoryWrite(i) || isa<AnyMemTransferInst>(i))
        WriteSites.push_back(&i);
      visit(i);
      while (!Work.empty()) {
        Value *v = Work.back();
        Work.pop_back();
        Queued.erase(v);
        if (auto *arg = dyn_cast<Argument>(v))
          updateArgument(*arg);
        else
          visit(*cast<Instruction>(v));
      }
    }
    Visited.insert(bb);
  }
  Current = nullptr;
}

void ExternalMemory::visit(Instruction &i) {
  if (i.getType()->isPointerTy()) {
    Facts facts = computeFacts(i);
    if (!facts.empty() && ValueFacts[&i].merge(facts))
      scheduleUsers(&i);
  }
  applyWrites(i);
}

void ExternalMemory::updateArgument(Argument &arg) {
  if (ValueFacts[&arg].merge(argumentFacts(arg)))
    scheduleUsers(&arg);
}

void ExternalMemory::schedule(Value *v) {
  if (auto *i = dyn_cast<Instruction>(v)) {
    BasicBlock *bb = i->getParent();
    if (!Visited.count(bb) &&
        (!Current || bb != Current->getParent() || Current->comesBefore(i)))
      return;
  }
  if (Queued.insert(v).second)
    Work.push_back(v);
}

void ExternalMemory::scheduleUsers(Value *v) {
  for (User *user : v->users())
    if (auto *i = dyn_cast<Instruction>(user))
      schedule(i);
}

ExternalMemory::Facts ExternalMemory::factsOf(Value *v) const {
  if (isa<Instruction>(v) || isa<Argument>(v)) {
    auto it = ValueFacts.find(v);
    return it == ValueFacts.end() ? Facts() : it->second;
  }
  Facts out;
  if (!v->getType()->isPointerTy())
    return out;
  auto *global = dyn_cast<GlobalVariable>(getUnderlyingObject(v));
  if (!global)
    return out;
  out.external.always = global->isDeclaration();
  out.holds.always = GlobalWritten.count({global, 0});
  out.nested.always = GlobalWritten.count({global, 1});
  return out;
}

ExternalMemory::Facts ExternalMemory::argumentFacts(Argument &arg) const {
  Facts out;
  uint64_t bit = argumentBit(arg.getArgNo());
  out.external.external = bit;
  out.holds.holds = bit;
  out.nested.nested = bit;
  out.holds.merge(localWrite(&arg, 0));
  out.nested.merge(localWrite(&arg, 1));
  return out;
}

ExternalMemory::Facts ExternalMemory::computeFacts(Instruction &i) {
  if (auto *gep = dyn_cast<GetElementPtrInst>(&i))
    return factsOf(gep->getPointerOperand());
  if (isa<BitCastInst>(i) || isa<AddrSpaceCastInst>(i))
    return factsOf(i.getOperand(0));
  Facts out;
  if (auto *phi = dyn_cast<PHINode>(&i)) {
    for (Value *incoming : phi->incoming_values())
      out.merge(factsOf(incoming));
    return out;
  }
  if (auto *select = dyn_cast<SelectInst>(&i)) {
    out = factsOf(select->getTrueValue());
    out.merge(factsOf(select->getFalseValue()));
    return out;
  }
  if (auto *load = dyn_cast<LoadInst>(&i)) {
    Facts slot = factsOf(load->getPointerOperand());
    out.external = slot.external;
    out.external.merge(slot.holds);
    out.holds = slot.nested;
  } else if (auto *cb = dyn_cast<CallBase>(&i)) {
    if (Value *arg = getArgumentAliasingToReturnedPointer(cb, false))
      return factsOf(arg);
    Function *callee = calleeOf(*cb);
    if (callee && !callee->isIntrinsic()) {
      if (callee->isDeclaration()) {
        out.external.always = !returnsFreshMemory(*cb, *callee, TLI);
      } else {
        auto summary = Summaries.find(callee);
        if (summary != Summaries.end())
          out = substitute(summary->second.returned, *cb);
      }
    }
  }
  out.holds.merge(localWrite(&i, 0));
  out.nested.merge(localWrite(&i, 1));
  return out;
}

ExternalMemory::Sources ExternalMemory::substitute(const Sources &sources,
                                                   CallBase &cb) const {
  Sources out;
  out.always = sources.always;
  uint64_t used = sources.external | sources.holds | sources.nested;
  if (!used)
    return out;
  for (unsigned k = 0; k < cb.arg_size(); ++k) {
    uint64_t bit = argumentBit(k);
    Value *operand = cb.getArgOperand(k);
    if (!(used & bit) || !operand->getType()->isPointerTy())
      continue;
    Facts facts = factsOf(operand);
    if (sources.external & bit)
      out.merge(facts.external);
    if (sources.holds & bit)
      out.merge(facts.holds);
    if (sources.nested & bit)
      out.merge(facts.nested);
  }
  return out;
}

ExternalMemory::Facts ExternalMemory::substitute(const Facts &facts,
                                                 CallBase &cb) const {
  Facts out;
  out.external = substitute(facts.external, cb);
  out.holds = substitute(facts.holds, cb);
  out.nested = substitute(facts.nested, cb);
  return out;
}

ExternalMemory::Sources ExternalMemory::localWrite(const Value *object,
                                                   unsigned depth) const {
  auto it = LocalWrites.find({object, depth});
  return it == LocalWrites.end() ? Sources() : it->second;
}

void ExternalMemory::applyWrites(Instruction &i) {
  if (auto write = memoryWrite(i)) {
    if (!write->value->getType()->isPointerTy())
      return;
    Facts value = factsOf(write->value);
    addWrite(write->pointer, 0, value.external);
    addWrite(write->pointer, 1, value.holds);
    return;
  }
  auto *cb = dyn_cast<CallBase>(&i);
  if (!cb)
    return;
  if (auto *copy = dyn_cast<AnyMemTransferInst>(cb)) {
    Facts source = factsOf(copy->getRawSource());
    Sources contents = source.external;
    contents.merge(source.holds);
    addWrite(copy->getRawDest(), 0, contents);
    addWrite(copy->getRawDest(), 1, source.nested);
    return;
  }
  Function *callee = calleeOf(*cb);
  if (!callee)
    return;
  if (Fillers.count(callee)) {
    Sources filled;
    filled.always = true;
    for (Value *arg : cb->args())
      if (arg->getType()->isPointerTy())
        addWrite(arg, 0, filled);
    return;
  }
  auto summary = Summaries.find(callee);
  if (summary == Summaries.end())
    return;
  const SmallVector<Writes, 0> &writes = summary->second.writes;
  for (unsigned k = 0; k < writes.size() && k < cb->arg_size(); ++k) {
    addWrite(cb->getArgOperand(k), 0, substitute(writes[k].holds, *cb));
    addWrite(cb->getArgOperand(k), 1, substitute(writes[k].nested, *cb));
  }
}

void ExternalMemory::addWrite(const Value *pointer, unsigned depth,
                              const Sources &sources) {
  if (sources.empty())
    return;
  SmallVector<const Value *, 4> objects;
  getUnderlyingObjects(pointer, objects, nullptr, 0);
  for (const Value *object : objects) {
    if (auto *global = dyn_cast<GlobalVariable>(object)) {
      if (!global->isConstant())
        GlobalWrites[{global, depth}].merge(sources);
      continue;
    }
    if (isa<Constant>(object))
      continue;
    if (LocalWrites[{object, depth}].merge(sources))
      schedule(const_cast<Value *>(object));
    if (depth == 0)
      if (auto *load = dyn_cast<LoadInst>(object))
        addWrite(load->getPointerOperand(), 1, sources);
  }
}

static bool passesFunction(CallBase &cb) {
  for (const Use &argU : cb.args())
    if (resolveCallableValue(argU.get()))
      return true;
  return false;
}

static std::optional<MemoryWrite> externalCopy(Instruction &i) {
  if (auto *copy = dyn_cast<AnyMemTransferInst>(&i))
    return MemoryWrite{copy->getRawDest(), copy->getRawSource()};
  return std::nullopt;
}

static bool isDispatchTablePointer(Value *value) {
  auto *global = dyn_cast<GlobalVariable>(getUnderlyingObject(value));
  return global && isDispatchTable(*global);
}

void buildEscapeEdges(Module &m, CallGraph &g, const EscapeIndex &idx,
                      ExternalMemory &memory) {
  DenseSet<Function *> external;
  std::vector<EscapeSite> sites;
  std::vector<Value *> roots;
  DenseSet<Instruction *> storeSites;
  auto addStoreSites = [&](Function &f) {
    for (Instruction &i : instructions(f)) {
      auto write = memoryWrite(i);
      if (!write)
        write = externalCopy(i);
      if (!write || storeSites.count(&i) || isDispatchTablePointer(write->value) ||
          !memory.writesExternalMemory(i))
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
        bool intrinsic = callee && callee->isIntrinsic();
        if (intrinsic && !passesFunction(*cb))
          continue;
        bool handedOut = !registersExitHandler(*cb) && !intrinsic;
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
          memory.addCalledFunction(f);
          grew = true;
        }
    }
    if (!grew)
      break;
    for (Function *f : memory.takeChanged())
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
}

DenseSet<Function *> computeAddressFlowTargets(
    Module &m, const EscapeIndex &idx, const ReachResult &res,
    const ExternalMemory &memory) {
  // Root the value-flow at every place an address could be consumed as a
  // callable: an indirect call's callee operand, the arguments/returns that
  // reach unanalyzable *code* that might call them, and stores or `memcpy`
  // copies into memory that such code owns. Inline asm and intrinsics are
  // otherwise excluded: an asm operand (e.g. `std::hint::black_box`, lowered
  // to empty `asm sideeffect ""`) and intrinsics (`llvm.lifetime`, `memcpy`,
  // `dbg`, ...) observe or move a value but cannot invoke an arbitrary
  // function, so an address handed only to them is not evidence of being
  // callable. A function carried to a remaining root has concrete flow
  // evidence, not just a type match. (Confidence-only; reachability/escape
  // edges are unaffected and stay sound.)
  std::vector<Value *> roots;
  for (Function &f : m) {
    if (f.isDeclaration() || !res.reached.count(&f))
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto *cb = dyn_cast<CallBase>(&i)) {
        if (cb->isInlineAsm())
          continue;
        if (auto copy = externalCopy(i))
          if (memory.writesExternalMemory(i))
            roots.push_back(copy->value);
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
        if (!isDispatchTablePointer(write->value) && memory.writesExternalMemory(i))
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
