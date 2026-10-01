#include "Reachability.h"
#include <queue>
#include <vector>

using namespace llvm;

namespace reach {

static Via fromKind(EdgeKind k) {
  return k == EdgeKind::Direct ? Via::Direct : Via::Indirect;
}

static Via mergeVia(Via a, Via b) { return a == b ? a : Via::Both; }

ReachResult computeReachability(Module &m, const CallGraph &g,
                                const std::vector<std::string> &entries) {
  ReachResult res;
  std::queue<Function *> work;

  for (const auto &name : entries) {
    Function *f = m.getFunction(name);
    if (!f || f->isDeclaration())
      continue;
    if (!res.reached.count(f)) {
      res.reached[f] = Via::Direct; // roots count as directly reached
      res.depth[f] = 0;
      work.push(f);
    }
  }

  const auto &E = g.edges();
  const auto &S = g.setEdges();
  std::vector<bool> expanded(g.sets().size(), false);
  auto visit = [&](Function *cur, Function *callee, EdgeKind kind) {
    Via vk = fromKind(kind);
    auto found = res.reached.find(callee);
    if (found == res.reached.end()) {
      res.reached[callee] = vk;
      res.depth[callee] = res.depth[cur] + 1;
      work.push(callee);
    } else {
      found->second = mergeVia(found->second, vk);
    }
  };
  while (!work.empty()) {
    Function *cur = work.front();
    work.pop();
    auto it = E.find(cur);
    if (it != E.end())
      for (const auto &[callee, kind] : it->second)
        visit(cur, callee, kind);
    auto st = S.find(cur);
    if (st == S.end())
      continue;
    for (SetId id : st->second) {
      if (expanded[id])
        continue;
      expanded[id] = true;
      for (Function *callee : g.sets().get(id))
        visit(cur, callee, EdgeKind::Indirect);
    }
  }
  return res;
}

} // namespace reach
