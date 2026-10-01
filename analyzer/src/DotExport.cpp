#include "DotExport.h"
#include "Demangle.h"
#include "Text.h"
#include "llvm/ADT/DenseSet.h"
#include <algorithm>
#include <vector>

using namespace llvm;

namespace reach {

static std::string escape(StringRef s) {
  std::string out;
  for (char c : sanitizeUtf8(s)) {
    if (c == '"' || c == '\\')
      out.push_back('\\');
    out.push_back(c);
  }
  return out;
}

void writeDot(raw_ostream &os, const CallGraph &g, const ReachResult &res) {
  os << "digraph reachable {\n";
  os << "  node [shape=box];\n";
  std::vector<Function *> nodes;
  for (auto &kv : res.reached) {
    Function *f = kv.first;
    if (!f->isDeclaration())
      nodes.push_back(f);
  }
  std::sort(nodes.begin(), nodes.end(), [](Function *a, Function *b) {
    return a->getName() < b->getName();
  });
  for (Function *f : nodes) {
    os << "  \"" << escape(f->getName()) << "\" [label=\""
       << escape(demangle(f->getName())) << "\"];\n";
  }
  std::vector<std::pair<Function *, EdgeKind>> targets;
  DenseSet<std::pair<Function *, unsigned>> seen;
  for (Function *from : nodes) {
    targets.clear();
    seen.clear();
    g.forEachCallee(from, [&](Function *to, EdgeKind kind) {
      if (!to->isDeclaration() && res.reached.count(to) &&
          seen.insert({to, (unsigned)kind}).second)
        targets.emplace_back(to, kind);
    });
    std::sort(targets.begin(), targets.end(), [](const auto &a, const auto &b) {
      if (a.first->getName() != b.first->getName())
        return a.first->getName() < b.first->getName();
      return a.second < b.second;
    });
    for (auto &[to, kind] : targets) {
      os << "  \"" << escape(from->getName()) << "\" -> \""
         << escape(to->getName()) << "\"";
      if (kind == EdgeKind::Indirect)
        os << " [style=dashed,color=red]";
      os << ";\n";
    }
  }
  os << "}\n";
}

} // namespace reach
