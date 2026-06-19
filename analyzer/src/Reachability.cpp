#include "Reachability.h"
#include <queue>

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
    if (!f || f->isDeclaration()) {
      res.missingNames.push_back(name);
      continue;
    }
    if (!res.reached.count(f)) {
      res.reached[f] = Via::Direct; // roots count as directly reached
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
    for (const auto &[callee, kind] : it->second) {
      Via vk = fromKind(kind);
      auto found = res.reached.find(callee);
      if (found == res.reached.end()) {
        res.reached[callee] = vk;
        work.push(callee);
      } else {
        found->second = mergeVia(found->second, vk);
      }
    }
  }
  return res;
}

} // namespace reach
