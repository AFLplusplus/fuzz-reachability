#include "DotExport.h"
#include "Demangle.h"
#include "Text.h"
#include <algorithm>
#include <tuple>
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
  std::vector<std::tuple<Function *, Function *, EdgeKind>> edges;
  for (auto &kv : g.edges()) {
    Function *from = kv.first;
    if (from->isDeclaration() || !res.reached.count(from))
      continue;
    for (auto &[to, kind] : kv.second)
      if (!to->isDeclaration() && res.reached.count(to))
        edges.emplace_back(from, to, kind);
  }
  std::sort(edges.begin(), edges.end(), [](const auto &a, const auto &b) {
    if (std::get<0>(a)->getName() != std::get<0>(b)->getName())
      return std::get<0>(a)->getName() < std::get<0>(b)->getName();
    if (std::get<1>(a)->getName() != std::get<1>(b)->getName())
      return std::get<1>(a)->getName() < std::get<1>(b)->getName();
    return std::get<2>(a) < std::get<2>(b);
  });
  for (auto &[from, to, kind] : edges) {
    os << "  \"" << escape(from->getName()) << "\" -> \""
       << escape(to->getName()) << "\"";
    if (kind == EdgeKind::Indirect)
      os << " [style=dashed,color=red]";
    os << ";\n";
  }
  os << "}\n";
}

} // namespace reach
