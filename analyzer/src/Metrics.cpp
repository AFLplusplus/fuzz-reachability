#include "Metrics.h"
#include "Demangle.h"

#include "llvm/ADT/DenseSet.h"
#include "llvm/Analysis/LoopInfo.h"
#include "llvm/IR/CFG.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/DebugProgramInstruction.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/IntrinsicInst.h"
#include <cstdint>
#include <queue>
#include <vector>

using namespace llvm;

namespace reach {

namespace {

struct DangerousEntry {
  const char *name;
  bool prefix;
};

bool isDangerousName(StringRef n) {
  static const DangerousEntry entries[] = {
#include "DangerousFunctions.inc"
  };
  for (const DangerousEntry &e : entries)
    if (e.prefix ? n.starts_with(e.name) : n == e.name)
      return true;
  return false;
}

unsigned countLocals(Function &f) {
  DenseSet<DILocalVariable *> vars;
  for (BasicBlock &bb : f)
    for (Instruction &i : bb) {
      for (DbgVariableRecord &dvr : filterDbgVars(i.getDbgRecordRange()))
        if (DILocalVariable *v = dvr.getVariable())
          if (v->getArg() == 0)
            vars.insert(v);
      if (auto *dii = dyn_cast<DbgVariableIntrinsic>(&i))
        if (DILocalVariable *v = dii->getVariable())
          if (v->getArg() == 0)
            vars.insert(v);
    }
  if (!vars.empty())
    return vars.size();
  if (f.getSubprogram())
    return 0;
  unsigned allocas = 0;
  for (Instruction &i : instructions(f))
    if (isa<AllocaInst>(&i))
      ++allocas;
  return allocas;
}

void computeLocal(Function &f, FuncMetrics &fm) {
  fm.basicBlocks = f.size();
  for (Argument &a : f.args())
    if (a.getType()->isPointerTy()) {
      fm.ptrArg = true;
      break;
    }

  unsigned edges = 0;
  for (BasicBlock &bb : f)
    edges += bb.getTerminator()->getNumSuccessors();
  int cyc = (int)edges - (int)f.size() + 2;
  fm.cyclomatic = cyc < 1 ? 1u : (unsigned)cyc;

  for (Instruction &i : instructions(f))
    if (auto *cb = dyn_cast<CallBase>(&i))
      if (Function *callee = directCallee(*cb))
        if (isDangerousName(callee->getName()))
          ++fm.dangerousCalls;

  DominatorTree dt(f);
  LoopInfo li(dt);
  fm.loops = li.getLoopsInPreorder().size();
}

void computeInteresting(Module &m, const CallGraph &g,
                        const std::vector<std::string> &roots,
                        DenseMap<Function *, FuncMetrics> &out) {
  std::queue<Function *> work;
  std::vector<std::string> scopes;
  auto mark = [&](Function *f) {
    auto it = out.find(f);
    if (it != out.end() && it->second.ptrArg && !it->second.interesting) {
      it->second.interesting = true;
      work.push(f);
    }
  };
  for (const std::string &name : roots) {
    Function *f = m.getFunction(name);
    if (!f)
      continue;
    mark(f);
    scopes.push_back(demangleWithoutHash(f->getName()) + "::");
  }
  for (auto &kv : out) {
    if (!kv.second.ptrArg || kv.second.interesting)
      continue;
    std::string name = demangleWithoutHash(kv.first->getName());
    for (const std::string &scope : scopes) {
      StringRef rest = StringRef(name);
      if (!rest.consume_front(scope))
        continue;
      if (rest.starts_with("{{closure}}") || rest.starts_with("{closure#") ||
          rest.starts_with("_$u7b$$u7b$closure$u7d$$u7d$") ||
          rest.starts_with("{lambda") || rest.starts_with("$_")) {
        mark(kv.first);
        break;
      }
    }
  }
  const auto &E = g.edges();
  const auto &S = g.setEdges();
  std::vector<bool> expanded(g.sets().size(), false);
  while (!work.empty()) {
    Function *cur = work.front();
    work.pop();
    auto it = E.find(cur);
    if (it != E.end())
      for (const auto &[to, kind] : it->second)
        mark(to);
    auto st = S.find(cur);
    if (st == S.end())
      continue;
    for (SetId id : st->second) {
      if (expanded[id])
        continue;
      expanded[id] = true;
      for (Function *to : g.sets().get(id))
        mark(to);
    }
  }
}

void computeDeadEnd(const CallGraph &g,
                    DenseMap<Function *, FuncMetrics> &out) {
  const auto &E = g.edges();
  const auto &S = g.setEdges();
  auto interesting = [&](Function *to) {
    auto mi = out.find(to);
    return mi != out.end() && mi->second.interesting;
  };
  std::vector<int8_t> setInteresting(g.sets().size(), -1);
  for (auto &kv : out) {
    bool leadsToInteresting = false;
    auto it = E.find(kv.first);
    if (it != E.end())
      for (const auto &[to, kind] : it->second)
        if (interesting(to)) {
          leadsToInteresting = true;
          break;
        }
    auto st = S.find(kv.first);
    if (!leadsToInteresting && st != S.end())
      for (SetId id : st->second) {
        if (setInteresting[id] < 0) {
          setInteresting[id] = 0;
          for (Function *to : g.sets().get(id))
            if (interesting(to)) {
              setInteresting[id] = 1;
              break;
            }
        }
        if (setInteresting[id] == 1) {
          leadsToInteresting = true;
          break;
        }
      }
    kv.second.deadEnd = !leadsToInteresting;
  }
}

void computeBottleneck(Module &m, const CallGraph &g,
                       const std::vector<std::string> &roots,
                       DenseMap<Function *, FuncMetrics> &out) {
  std::vector<Function *> nodes;
  DenseMap<Function *, unsigned> idx;
  for (auto &kv : out) {
    idx[kv.first] = nodes.size();
    nodes.push_back(kv.first);
  }
  unsigned N = nodes.size();
  if (N == 0)
    return;

  const auto &E = g.edges();
  const auto &S = g.setEdges();
  DenseMap<SetId, unsigned> setNode;
  std::vector<SetId> setOfNode;
  for (unsigned i = 0; i < N; ++i) {
    auto st = S.find(nodes[i]);
    if (st == S.end())
      continue;
    for (SetId id : st->second)
      if (setNode.try_emplace(id, N + setOfNode.size()).second)
        setOfNode.push_back(id);
  }
  unsigned V = N + setOfNode.size();
  unsigned R = V;

  std::vector<std::vector<unsigned>> succ(V + 1), pred(V + 1);
  for (unsigned i = 0; i < N; ++i) {
    DenseSet<unsigned> seen;
    auto it = E.find(nodes[i]);
    if (it != E.end())
      for (const auto &[to, kind] : it->second) {
        auto ti = idx.find(to);
        if (ti == idx.end() || ti->second == i || !seen.insert(ti->second).second)
          continue;
        succ[i].push_back(ti->second);
        pred[ti->second].push_back(i);
      }
    auto st = S.find(nodes[i]);
    if (st != S.end())
      for (SetId id : st->second) {
        unsigned v = setNode.lookup(id);
        succ[i].push_back(v);
        pred[v].push_back(i);
      }
  }
  for (unsigned k = 0; k < setOfNode.size(); ++k) {
    unsigned v = N + k;
    DenseSet<unsigned> seen;
    for (Function *to : g.sets().get(setOfNode[k])) {
      auto ti = idx.find(to);
      if (ti == idx.end() || !seen.insert(ti->second).second)
        continue;
      succ[v].push_back(ti->second);
      pred[ti->second].push_back(v);
    }
  }
  for (const std::string &name : roots) {
    Function *f = m.getFunction(name);
    if (!f)
      continue;
    auto ri = idx.find(f);
    if (ri != idx.end()) {
      succ[R].push_back(ri->second);
      pred[ri->second].push_back(R);
    }
  }

  std::vector<int> postnum(V + 1, -1);
  std::vector<unsigned> order;
  std::vector<std::pair<unsigned, unsigned>> stack;
  std::vector<bool> visited(V + 1, false);
  stack.push_back({R, 0});
  visited[R] = true;
  while (!stack.empty()) {
    auto &fr = stack.back();
    if (fr.second < succ[fr.first].size()) {
      unsigned w = succ[fr.first][fr.second++];
      if (!visited[w]) {
        visited[w] = true;
        stack.push_back({w, 0});
      }
    } else {
      order.push_back(fr.first);
      stack.pop_back();
    }
  }
  for (unsigned k = 0; k < order.size(); ++k)
    postnum[order[k]] = (int)k;

  std::vector<int> idom(V + 1, -1);
  idom[R] = (int)R;
  auto intersect = [&](int a, int b) {
    while (a != b) {
      while (postnum[a] < postnum[b])
        a = idom[a];
      while (postnum[b] < postnum[a])
        b = idom[b];
    }
    return a;
  };
  bool changed = true;
  while (changed) {
    changed = false;
    for (auto rit = order.rbegin(); rit != order.rend(); ++rit) {
      unsigned b = *rit;
      if (b == R || postnum[b] < 0)
        continue;
      int newIdom = -1;
      for (unsigned p : pred[b]) {
        if (idom[p] == -1)
          continue;
        newIdom = newIdom == -1 ? (int)p : intersect((int)p, newIdom);
      }
      if (newIdom != -1 && idom[b] != newIdom) {
        idom[b] = newIdom;
        changed = true;
      }
    }
  }

  for (unsigned b = 0; b < N; ++b) {
    int d = idom[b];
    while (d != -1 && (unsigned)d >= N && (unsigned)d != R)
      d = idom[d];
    if (d != -1 && (unsigned)d != R)
      out[nodes[d]].bottleneck = true;
  }
}

} // namespace

DenseMap<Function *, unsigned> countLocalVariables(Module &m) {
  DenseMap<Function *, unsigned> out;
  for (Function &f : m)
    if (!f.isDeclaration())
      out[&f] = countLocals(f);
  return out;
}

DenseMap<Function *, FuncMetrics>
computeMetrics(Module &m, const CallGraph &g, const ReachResult &res,
               const std::vector<std::string> &roots,
               const DenseMap<Function *, unsigned> &localVars) {
  DenseMap<Function *, FuncMetrics> out;
  for (Function &f : m) {
    if (f.isDeclaration() || !res.reached.count(&f))
      continue;
    FuncMetrics fm;
    computeLocal(f, fm);
    fm.localVars = localVars.lookup(&f);
    out[&f] = fm;
  }
  computeInteresting(m, g, roots, out);
  computeDeadEnd(g, out);
  computeBottleneck(m, g, roots, out);
  return out;
}

} // namespace reach
