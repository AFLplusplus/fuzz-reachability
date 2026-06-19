#include "JsonReport.h"
#include "Demangle.h"
#include "Toolchain.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/Support/JSON.h"
#include <algorithm>

using namespace llvm;

namespace reach {

namespace {
const char *viaStr(Via v) {
  switch (v) {
  case Via::Direct:
    return "direct";
  case Via::Indirect:
    return "indirect";
  case Via::Both:
    return "both";
  }
  return "direct";
}

// Emit one function object. `via` is null for unreachable functions.
void emitFn(json::OStream &J, Function *f, const Via *via) {
  J.object([&] {
    J.attribute("mangled", f->getName());
    J.attribute("demangled", demangle(f->getName()));
    if (DISubprogram *sp = f->getSubprogram()) {
      J.attribute("file", sp->getFilename());
      J.attribute("line", (int64_t)sp->getLine());
    } else {
      J.attribute("file", nullptr);
      J.attribute("line", nullptr);
    }
    if (via) {
      J.attribute("via", viaStr(*via));
      J.attribute("indirect_only", *via == Via::Indirect);
    }
  });
}
} // namespace

void writeJson(raw_ostream &os, Module &m, const CallGraph &, const ReachResult &res,
               StringRef backend, const std::vector<std::string> &entries) {
  // Defined functions, partitioned into reachable / unreachable, sorted by name.
  std::vector<std::pair<Function *, Via>> reachable;
  std::vector<Function *> unreachable;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    auto it = res.reached.find(&f);
    if (it != res.reached.end())
      reachable.push_back({&f, it->second});
    else
      unreachable.push_back(&f);
  }
  auto byName = [](Function *a, Function *b) { return a->getName() < b->getName(); };
  std::sort(reachable.begin(), reachable.end(),
            [&](auto &a, auto &b) { return byName(a.first, b.first); });
  std::sort(unreachable.begin(), unreachable.end(), byName);

  int64_t indirectOnly = 0;
  for (auto &[f, via] : reachable)
    if (via == Via::Indirect)
      ++indirectOnly;

  json::OStream J(os, 2);
  J.object([&] {
    J.attribute("llvm_version", std::to_string(linkedLLVMMajor()));
    J.attribute("backend", backend);
    J.attributeArray("entries", [&] {
      for (const auto &e : entries)
        J.value(e);
    });
    J.attributeObject("summary", [&] {
      J.attribute("defined", (int64_t)(reachable.size() + unreachable.size()));
      J.attribute("reachable", (int64_t)reachable.size());
      J.attribute("indirect_only", indirectOnly);
      J.attribute("unreachable", (int64_t)unreachable.size());
    });
    J.attributeArray("reachable", [&] {
      for (auto &[f, via] : reachable)
        emitFn(J, f, &via);
    });
    J.attributeArray("unreachable_defined", [&] {
      for (Function *f : unreachable)
        emitFn(J, f, nullptr);
    });
  });
  os << "\n";
}

} // namespace reach
