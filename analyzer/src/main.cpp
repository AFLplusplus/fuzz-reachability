#include "CallGraph.h"
#include "CovLists.h"
#include "Demangle.h"
#include "DotExport.h"
#include "JsonReport.h"
#include "Metrics.h"
#include "Module.h"
#include "Reachability.h"
#include "Toolchain.h"
#include "TypeBasedResolver.h"

#include "llvm/ADT/DenseSet.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringSet.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/GlobalIFunc.h"
#include "llvm/IR/GlobalVariable.h"
#include "llvm/IR/LLVMContext.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/raw_ostream.h"
#include <cctype>
#include <functional>
#include <memory>
#include <set>
#include <utility>
#include <vector>

using namespace llvm;

static cl::opt<std::string> InputIR(cl::Positional, cl::desc("<input .ll/.bc>"),
                                    cl::init(""));
static cl::list<std::string> EntryList("entry",
                                       cl::desc("entry function (repeatable; default "
                                                "LLVMFuzzerTestOneInput). Matches a "
                                                "mangled symbol, a demangled name, a "
                                                "'::name' suffix (e.g. 'main'), or the "
                                                "alias 'fuzz_target!'."));
static cl::opt<std::string> Backend("backend", cl::init("type-based"),
                                    cl::desc("deprecated and ignored; the type-based "
                                             "backend is always used"));
static cl::opt<bool> IndirectAny("indirect-any",
                                 cl::desc("indirect call may reach ANY address-taken "
                                          "function (debug, maximal over-approx)"));
static cl::opt<std::string> DotFile("dot", cl::init(""),
                                    cl::desc("write reachable-subgraph DOT to FILE"));
static cl::opt<std::string> OutFile("out", cl::init(""),
                                    cl::desc("write JSON report to FILE (default stdout)"));
static cl::opt<std::string> ReachedOut("reached-out", cl::init(""),
                                       cl::desc("write a sancov allowlist of reachable "
                                                "functions to FILE"));
static cl::opt<std::string> NotReachedOut("not-reached-out", cl::init(""),
                                          cl::desc("write a sancov ignorelist of "
                                                   "unreachable functions to FILE"));
static cl::opt<std::string> SelfTestDemangle("selftest-demangle", cl::init(""),
                                             cl::desc("print demangle(SYMBOL) and exit"));
static cl::opt<bool> DumpEdges("dump-edges", cl::desc("debug: print call-graph edges"));
static cl::opt<bool> NoNameRoots("no-name-roots",
                                 cl::desc("disable treating a defined, externally-visible "
                                          "function whose symbol name appears as a string "
                                          "constant as an extra root. That heuristic recovers "
                                          "functions reached only by runtime name lookup "
                                          "(dlsym/dlopen-by-name); it is on by default and "
                                          "active only when the module performs such a lookup."));
static cl::opt<bool> IncludeProcessLifecycleRoots(
    "include-process-lifecycle-roots",
    cl::desc("include constructors, destructors, ifunc resolvers, and a defined "
             "LLVMFuzzerInitialize as roots (default off)"));

namespace {

// Print suggestions when no requested entry resolved.
void suggestEntries(Module &m, const std::vector<std::string> &requested) {
  errs() << "error: no entry symbol resolved. Requested:";
  for (auto &e : requested)
    errs() << " " << e;
  errs() << "\n";
  static const char *known[] = {"LLVMFuzzerTestOneInput", "rust_fuzzer_test_input",
                                "_RNvCs"};
  std::vector<std::string> hits;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    StringRef n = f.getName();
    bool match = false;
    for (auto &e : requested)
      if (!e.empty() && n.contains(e))
        match = true;
    for (auto *k : known)
      if (n.contains(k))
        match = true;
    if (match)
      hits.push_back(n.str());
  }
  if (!hits.empty()) {
    errs() << "  did you mean one of these defined symbols?\n";
    for (auto &h : hits)
      errs() << "    " << h << "\n";
  }
}

// Rust legacy mangling appends a ::h<hex> disambiguator to the demangled path
// (e.g. "crate::main::hcc7e51cc..."). Strip it so demangled-name matching can use
// the human-readable path ("crate::main").
std::string stripLegacyHash(const std::string &s) {
  std::string::size_type pos = s.rfind("::h");
  if (pos == std::string::npos)
    return s;
  StringRef tail = StringRef(s).substr(pos + 3);
  if (tail.empty())
    return s;
  for (char c : tail)
    if (!std::isxdigit(static_cast<unsigned char>(c)))
      return s;
  return s.substr(0, pos);
}

// Resolve each requested entry token to the mangled names of defined functions,
// so callers never have to spell out a mangled symbol. A token matches by, in
// order and unioned: exact mangled symbol; exact demangled name; demangled
// "<path>::<token>" suffix (e.g. "main" -> "crate::main"). The alias
// "fuzz_target!" expands to the cargo-fuzz/libFuzzer entries. Unioning roots is
// sound: extra roots only widen the over-approximation. A token matching nothing
// is reported in `unresolved`.
void resolveEntries(Module &m, const std::vector<std::string> &requested,
                    std::vector<std::string> &resolved,
                    std::vector<std::string> &unresolved) {
  std::vector<std::pair<std::string, std::string>> defs;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    std::string name = f.getName().str();
    defs.emplace_back(name, stripLegacyHash(reach::demangle(name)));
  }
  std::set<std::string> seen;
  for (const auto &req : requested) {
    std::vector<std::string> tokens;
    if (req == "fuzz_target!" || req == "fuzz_target")
      tokens = {"LLVMFuzzerTestOneInput", "rust_fuzzer_test_input"};
    else
      tokens = {req};
    std::vector<std::string> matches;
    for (const auto &tok : tokens) {
      if (Function *f = m.getFunction(tok))
        if (!f->isDeclaration())
          matches.push_back(f->getName().str());
      std::string suffix = "::" + tok;
      for (const auto &[mn, dem] : defs)
        if (dem == tok || StringRef(dem).ends_with(suffix))
          matches.push_back(mn);
    }
    if (matches.empty()) {
      unresolved.push_back(req);
      continue;
    }
    for (const auto &mn : matches)
      if (seen.insert(mn).second)
        resolved.push_back(mn);
  }
}

// Functions reached only through a runtime symbol lookup (dlsym/dlopen-family):
// a defined, externally-visible function whose linkage name appears verbatim as
// a string constant. Such a function has no in-IR caller and no taken address,
// so neither the direct, indirect, nor escape edge builders can see it -- yet
//   let f = dlsym(handle, c"name"); f(x);
// calls it at runtime. We recover it by matching the name and adding it as a
// root. Sound: extra roots only widen the over-approximation (see resolveEntries).
// Gated on the module actually performing a dynamic lookup, so projects that do
// not use dlsym are unaffected. Returns the mangled names of the added roots.
std::vector<std::string> collectNameReferencedRoots(Module &m) {
  std::vector<std::string> added;
  static const char *dynLookup[] = {"dlsym", "dlvsym", "dlopen", "dlmopen",
                                     "GetProcAddress"};
  bool hasDyn = false;
  for (const char *n : dynLookup)
    if (m.getFunction(n)) {
      hasDyn = true;
      break;
    }
  if (!hasDyn)
    return added;

  // Collect NUL-separated tokens from every byte-string constant, descending
  // through constant aggregates/expressions (e.g. string tables, struct fields).
  StringSet<> strings;
  SmallVector<Constant *, 16> work;
  DenseSet<Constant *> visited;
  for (GlobalVariable &g : m.globals())
    if (g.hasInitializer())
      work.push_back(g.getInitializer());
  while (!work.empty()) {
    Constant *c = work.pop_back_val();
    if (!c || !visited.insert(c).second)
      continue;
    if (auto *cds = dyn_cast<ConstantDataSequential>(c)) {
      if (cds->isString()) {
        StringRef raw = cds->getRawDataValues();
        size_t pos = 0;
        while (pos < raw.size()) {
          size_t nul = raw.find('\0', pos);
          if (nul == StringRef::npos)
            nul = raw.size();
          StringRef tok = raw.substr(pos, nul - pos);
          if (!tok.empty())
            strings.insert(tok);
          pos = nul + 1;
        }
      }
      continue;
    }
    if (isa<ConstantAggregate>(c) || isa<ConstantExpr>(c))
      for (const Use &op : c->operands())
        if (auto *oc = dyn_cast<Constant>(op.get()))
          work.push_back(oc);
  }

  for (Function &f : m)
    if (!f.isDeclaration() && !f.hasLocalLinkage() && strings.count(f.getName()))
      added.push_back(f.getName().str());
  return added;
}

void addLifecycleRoot(Function *f, std::set<std::string> &have,
                      std::vector<std::string> &roots,
                      std::vector<std::string> &added) {
  if (!f || f->isDeclaration())
    return;
  std::string name = f->getName().str();
  if (have.insert(name).second) {
    roots.push_back(name);
    added.push_back(name);
  }
}

void collectLifecycleGlobal(Module &m, StringRef name, std::set<std::string> &have,
                            std::vector<std::string> &roots,
                            std::vector<std::string> &added) {
  GlobalVariable *global = m.getNamedGlobal(name);
  if (!global)
    return;
  auto *array = global->hasInitializer()
                    ? dyn_cast<ConstantArray>(global->getInitializer())
                    : nullptr;
  if (!array) {
    errs() << "warning: malformed " << name << " lifecycle records\n";
    return;
  }
  for (unsigned i = 0; i < array->getNumOperands(); ++i) {
    auto *record = dyn_cast<Constant>(array->getOperand(i));
    Constant *target = record ? record->getAggregateElement(1u) : nullptr;
    Function *function = reach::resolveCallableValue(target);
    if (!function || function->isDeclaration()) {
      errs() << "warning: malformed " << name << " lifecycle record " << i << "\n";
      continue;
    }
    addLifecycleRoot(function, have, roots, added);
  }
}

std::vector<std::string> collectProcessLifecycleRoots(
    Module &m, std::set<std::string> &have, std::vector<std::string> &roots) {
  std::vector<std::string> added;
  collectLifecycleGlobal(m, "llvm.global_ctors", have, roots, added);
  collectLifecycleGlobal(m, "llvm.global_dtors", have, roots, added);
  for (GlobalIFunc &ifunc : m.ifuncs()) {
    Function *resolver = ifunc.getResolverFunction();
    if (!resolver || resolver->isDeclaration()) {
      errs() << "warning: malformed ifunc resolver for " << ifunc.getName() << "\n";
      continue;
    }
    addLifecycleRoot(resolver, have, roots, added);
  }
  addLifecycleRoot(m.getFunction("LLVMFuzzerInitialize"), have, roots, added);
  return added;
}

void disableDebugInfoAutoUpgrade() {
  auto &opts = cl::getRegisteredOptions();
  auto it = opts.find("disable-auto-upgrade-debug-info");
  if (it == opts.end())
    return;
  auto *o = dynamic_cast<cl::opt<bool> *>(it->second);
  if (!o) {
    errs() << "warning: LLVM option disable-auto-upgrade-debug-info has an "
              "unexpected type\n";
    return;
  }
  if (o->getNumOccurrences() == 0)
    *o = true;
}

} // namespace

int main(int argc, char **argv) {
  cl::SetVersionPrinter([](raw_ostream &os) {
    os << "reachability-analyzer (LLVM " << reach::linkedLLVMMajor() << ")\n";
  });
  cl::ParseCommandLineOptions(argc, argv, "static fuzz-reachability analyzer\n");
  disableDebugInfoAutoUpgrade();

  if (!SelfTestDemangle.empty()) {
    outs() << reach::demangle(SelfTestDemangle) << "\n";
    return 0;
  }

  if (Backend.getNumOccurrences() > 0)
    errs() << "warning: --backend is deprecated and ignored; using the "
              "type-based backend\n";

  if (InputIR.empty()) {
    errs() << "error: no input .ll/.bc file given\n";
    return 1;
  }

  LLVMContext ctx;
  std::string err;
  auto mod = reach::loadModule(ctx, InputIR, err);
  if (!mod) {
    errs() << "error: failed to load " << InputIR << ": " << err << "\n";
    return 1;
  }

  std::vector<std::string> requested(EntryList.begin(), EntryList.end());
  if (requested.empty())
    requested.push_back("LLVMFuzzerTestOneInput");
  std::vector<std::string> entries, unresolved;
  resolveEntries(*mod, requested, entries, unresolved);

  reach::CallGraph graph;
  reach::buildDirectEdges(*mod, graph);
  reach::EscapeIndex escapeIdx;
  reach::buildEscapeIndex(*mod, escapeIdx);
  reach::buildEscapeEdges(*mod, graph, escapeIdx);

  std::unique_ptr<reach::IndirectResolver> resolver;
  if (IndirectAny)
    resolver = std::make_unique<reach::AnyResolver>();
  else
    resolver = std::make_unique<reach::TypeBasedResolver>();
  reach::buildIndirectEdges(*mod, graph, *resolver, escapeIdx);

  if (DumpEdges) {
    for (auto &kv : graph.edges())
      for (auto &[to, kind] : kv.second)
        outs() << kv.first->getName() << " -> " << to->getName() << " ["
               << (kind == reach::EdgeKind::Direct ? "direct" : "indirect") << "]\n";
    return 0;
  }

  if (!unresolved.empty()) {
    errs() << "warning: unresolved entry symbols:";
    for (auto &n : unresolved)
      errs() << " " << n;
    errs() << "\n";
  }

  std::vector<std::string> roots = entries;
  std::set<std::string> have(roots.begin(), roots.end());
  if (!NoNameRoots) {
    std::vector<std::string> added;
    for (const std::string &n : collectNameReferencedRoots(*mod))
      if (have.insert(n).second) {
        roots.push_back(n);
        added.push_back(n);
      }
    if (!added.empty()) {
      errs() << "note: added " << added.size()
             << " root(s) referenced by name (dlsym/dlopen-by-name reachability):";
      for (const std::string &n : added)
        errs() << " " << n;
      errs() << "\n";
    }
  }
  if (IncludeProcessLifecycleRoots) {
    std::vector<std::string> added =
        collectProcessLifecycleRoots(*mod, have, roots);
    if (!added.empty()) {
      errs() << "note: added " << added.size() << " process lifecycle root(s):";
      for (const std::string &n : added)
        errs() << " " << n;
      errs() << "\n";
    }
  }
  if (roots.empty()) {
    suggestEntries(*mod, requested);
    return 1;
  }

  reach::ReachResult res = reach::computeReachability(*mod, graph, roots);
  if (res.reached.empty()) {
    suggestEntries(*mod, requested);
    return 1;
  }

  auto flowTargets = reach::computeAddressFlowTargets(*mod, escapeIdx, res);
  auto metrics = reach::computeMetrics(*mod, graph, res, roots);

  auto writeFile = [&](const std::string &path, const char *what,
                       const std::function<void(raw_ostream &)> &fn) -> bool {
    std::error_code ec;
    raw_fd_ostream os(path, ec, sys::fs::OF_Text);
    if (ec) {
      errs() << "error: cannot write " << what << " to " << path << ": "
             << ec.message() << "\n";
      return false;
    }
    fn(os);
    return true;
  };

  if (!DotFile.empty() &&
      !writeFile(DotFile, "DOT", [&](raw_ostream &o) { reach::writeDot(o, graph, res); }))
    return 1;
  if (!ReachedOut.empty() &&
      !writeFile(ReachedOut, "allowlist",
                 [&](raw_ostream &o) { reach::writeAllowlist(o, *mod, res); }))
    return 1;
  if (!NotReachedOut.empty() &&
      !writeFile(NotReachedOut, "ignorelist",
                 [&](raw_ostream &o) { reach::writeIgnorelist(o, *mod, res); }))
    return 1;

  const char *backendName = IndirectAny ? "indirect-any" : "type-based";
  if (OutFile.empty()) {
    reach::writeJson(outs(), *mod, graph, res, backendName, roots, flowTargets,
                     metrics);
  } else {
    std::error_code ec;
    raw_fd_ostream out(OutFile, ec, sys::fs::OF_Text);
    if (ec) {
      errs() << "error: cannot write JSON to " << OutFile << ": " << ec.message()
             << "\n";
      return 1;
    }
    reach::writeJson(out, *mod, graph, res, backendName, roots, flowTargets,
                     metrics);
  }
  return 0;
}
