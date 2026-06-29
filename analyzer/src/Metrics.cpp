#include "Metrics.h"

#include "llvm/ADT/DenseSet.h"
#include "llvm/Analysis/LoopInfo.h"
#include "llvm/IR/CFG.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/DebugProgramInstruction.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/GlobalAlias.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/IntrinsicInst.h"
#include <queue>

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

Function *directCallee(CallBase &cb) {
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

  fm.localVars = countLocals(f);

  DominatorTree dt(f);
  LoopInfo li(dt);
  fm.loops = li.getLoopsInPreorder().size();
}

void computeInteresting(Module &m, const CallGraph &g,
                        const std::vector<std::string> &roots,
                        DenseMap<Function *, FuncMetrics> &out) {
  std::queue<Function *> work;
  for (const std::string &name : roots) {
    Function *f = m.getFunction(name);
    if (!f)
      continue;
    auto it = out.find(f);
    if (it != out.end() && it->second.ptrArg && !it->second.interesting) {
      it->second.interesting = true;
      work.push(f);
    }
  }
  const auto &E = g.edges();
  while (!work.empty()) {
    Function *cur = work.front();
    work.pop();
    auto it = E.find(cur);
    if (it == E.end())
      continue;
    for (const auto &[to, kind] : it->second) {
      auto mi = out.find(to);
      if (mi != out.end() && mi->second.ptrArg && !mi->second.interesting) {
        mi->second.interesting = true;
        work.push(to);
      }
    }
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
  unsigned R = N;

  std::vector<std::vector<unsigned>> succ(N + 1), pred(N + 1);
  const auto &E = g.edges();
  for (unsigned i = 0; i < N; ++i) {
    auto it = E.find(nodes[i]);
    if (it == E.end())
      continue;
    DenseSet<unsigned> seen;
    for (const auto &[to, kind] : it->second) {
      auto ti = idx.find(to);
      if (ti == idx.end() || ti->second == i || !seen.insert(ti->second).second)
        continue;
      succ[i].push_back(ti->second);
      pred[ti->second].push_back(i);
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

  std::vector<int> postnum(N + 1, -1);
  std::vector<unsigned> order;
  std::vector<std::pair<unsigned, unsigned>> stack;
  std::vector<bool> visited(N + 1, false);
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

  std::vector<int> idom(N + 1, -1);
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
    if (d != -1 && (unsigned)d != R)
      out[nodes[d]].bottleneck = true;
  }
}

} // namespace

DenseMap<Function *, FuncMetrics>
computeMetrics(Module &m, const CallGraph &g, const ReachResult &res,
               const std::vector<std::string> &roots) {
  DenseMap<Function *, FuncMetrics> out;
  for (Function &f : m) {
    if (f.isDeclaration() || !res.reached.count(&f))
      continue;
    FuncMetrics fm;
    computeLocal(f, fm);
    out[&f] = fm;
  }
  computeInteresting(m, g, roots, out);
  computeBottleneck(m, g, roots, out);
  return out;
}

} // namespace reach
