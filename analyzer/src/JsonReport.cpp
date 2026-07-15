#include "JsonReport.h"
#include "Demangle.h"
#include "SymbolKey.h"
#include "Text.h"
#include "Toolchain.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/Support/JSON.h"
#include <algorithm>
#include <set>
#include <tuple>
#include <vector>

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

// Reachability confidence. `high`: reached by a concrete direct edge (or a
// root). `medium`: reached only indirectly, but the address has value-flow
// evidence of being callable (reaches an indirect callee, or escapes to
// unanalyzable code). `low`: reached only by a type match, with no flow
// evidence -- the likely-spurious surface of the over-approximation. This is a
// triage hint, not a verdict: it never removes a function from the reachable
// set, and a target whose address is laundered through integer arithmetic
// (ptrtoint/inttoptr) can legitimately rate `low`.
const char *confidenceStr(Via via, bool hasFlow) {
  if (via != Via::Indirect)
    return "high";
  return hasFlow ? "medium" : "low";
}

const char *manglingStr(Module &m) {
  for (Function &f : m)
    if (!f.isDeclaration() && f.getName().starts_with("_R"))
      return "v0";
  return "legacy";
}

// Emit one function object. `via` is null for unreachable functions.
void emitFn(json::OStream &J, Function *f, const Via *via,
            const DenseSet<Function *> &flow,
            const DenseMap<Function *, unsigned> &depth,
            const DenseMap<Function *, FuncMetrics> &metrics) {
  std::string name = sanitizeUtf8(f->getName());
  J.object([&] {
    J.attribute("mangled", name);
    J.attribute("demangled", demangle(name));
    J.attribute("key", canonicalKey(name));
    if (DISubprogram *sp = f->getSubprogram()) {
      J.attribute("file", sanitizeUtf8(sp->getFilename()));
      J.attribute("line", (int64_t)sp->getLine());
    } else {
      J.attribute("file", nullptr);
      J.attribute("line", nullptr);
    }
    if (via) {
      J.attribute("via", viaStr(*via));
      J.attribute("indirect_only", *via == Via::Indirect);
      J.attribute("confidence", confidenceStr(*via, flow.count(f)));
      J.attribute("depth", (int64_t)depth.lookup(f));
      const FuncMetrics &fm = metrics.lookup(f);
      J.attribute("basic_blocks", (int64_t)fm.basicBlocks);
      J.attribute("dangerous_calls", (int64_t)fm.dangerousCalls);
      J.attribute("C11", (int64_t)fm.localVars);
      J.attribute("cyclomatic", (int64_t)fm.cyclomatic);
      J.attribute("loops", (int64_t)fm.loops);
      J.attribute("interesting", fm.interesting);
      J.attribute("bottleneck", fm.bottleneck);
      J.attribute("dead_end", fm.deadEnd);
    }
  });
}
} // namespace

void writeJson(raw_ostream &os, Module &m, const CallGraph &g, const ReachResult &res,
               StringRef backend, const std::vector<std::string> &entries,
               const DenseSet<Function *> &flowTargets,
               const DenseMap<Function *, FuncMetrics> &metrics) {
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
  int64_t lowConfidence = 0;
  for (auto &[f, via] : reachable) {
    if (via == Via::Indirect) {
      ++indirectOnly;
      if (!flowTargets.count(f))
        ++lowConfidence;
    }
  }

  std::set<std::string> externalCallees;
  for (auto &kv : g.edges()) {
    Function *from = kv.first;
    if (from->isDeclaration() || !res.reached.count(from))
      continue;
    for (auto &[to, kind] : kv.second)
      if (to->isDeclaration() && !to->isIntrinsic())
        externalCallees.insert(sanitizeUtf8(to->getName()));
  }

  json::OStream J(os, 2);
  J.object([&] {
    J.attribute("llvm_version", std::to_string(linkedLLVMMajor()));
    J.attribute("backend", backend);
    J.attribute("mangling", manglingStr(m));
    J.attributeArray("entries", [&] {
      for (const auto &e : entries)
        J.value(sanitizeUtf8(e));
    });
    J.attributeObject("summary", [&] {
      J.attribute("defined", (int64_t)(reachable.size() + unreachable.size()));
      J.attribute("reachable", (int64_t)reachable.size());
      J.attribute("indirect_only", indirectOnly);
      J.attribute("low_confidence", lowConfidence);
      J.attribute("unreachable", (int64_t)unreachable.size());
      J.attribute("external_declarations", (int64_t)externalCallees.size());
    });
    J.attributeArray("reachable", [&] {
      for (auto &[f, via] : reachable)
        emitFn(J, f, &via, flowTargets, res.depth, metrics);
    });
    J.attributeArray("unreachable_defined", [&] {
      for (Function *f : unreachable)
        emitFn(J, f, nullptr, flowTargets, res.depth, metrics);
    });
    J.attributeArray("external_declarations", [&] {
      for (const std::string &n : externalCallees)
        J.value(n);
    });
    J.attributeArray("edges", [&] {
      std::vector<std::tuple<std::string, std::string, EdgeKind>> edges;
      for (auto &kv : g.edges()) {
        Function *from = kv.first;
        if (from->isDeclaration() || !res.reached.count(from))
          continue;
        for (auto &[to, kind] : kv.second)
          if (!to->isDeclaration() && res.reached.count(to))
            edges.emplace_back(sanitizeUtf8(from->getName()),
                               sanitizeUtf8(to->getName()), kind);
      }
      std::sort(edges.begin(), edges.end(), [](const auto &a, const auto &b) {
        if (std::get<0>(a) != std::get<0>(b))
          return std::get<0>(a) < std::get<0>(b);
        if (std::get<1>(a) != std::get<1>(b))
          return std::get<1>(a) < std::get<1>(b);
        return std::get<2>(a) < std::get<2>(b);
      });
      for (auto &e : edges) {
        const std::string &from = std::get<0>(e);
        const std::string &to = std::get<1>(e);
        EdgeKind kind = std::get<2>(e);
        J.object([&] {
          J.attribute("from", from);
          J.attribute("to", to);
          J.attribute("kind", kind == EdgeKind::Direct ? "direct" : "indirect");
        });
      }
    });
  });
  os << "\n";
}

} // namespace reach
