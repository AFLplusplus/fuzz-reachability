#include "DotExport.h"
#include "Demangle.h"

using namespace llvm;

namespace reach {

static std::string escape(StringRef s) {
  std::string out;
  for (char c : s) {
    if (c == '"' || c == '\\')
      out.push_back('\\');
    out.push_back(c);
  }
  return out;
}

void writeDot(raw_ostream &os, const CallGraph &g, const ReachResult &res) {
  os << "digraph reachable {\n";
  os << "  node [shape=box];\n";
  // Node declarations (label = demangled) for reached functions.
  for (auto &kv : res.reached) {
    Function *f = kv.first;
    os << "  \"" << escape(f->getName()) << "\" [label=\""
       << escape(demangle(f->getName())) << "\"];\n";
  }
  // Edges where both endpoints are reached.
  for (auto &kv : g.edges()) {
    Function *from = kv.first;
    if (!res.reached.count(from))
      continue;
    for (auto &[to, kind] : kv.second) {
      if (!res.reached.count(to))
        continue;
      os << "  \"" << escape(from->getName()) << "\" -> \"" << escape(to->getName())
         << "\"";
      if (kind == EdgeKind::Indirect)
        os << " [style=dashed,color=red]";
      os << ";\n";
    }
  }
  os << "}\n";
}

} // namespace reach
