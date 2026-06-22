#include "CallGraph.h"
#include "CovLists.h"
#include "Demangle.h"
#include "DotExport.h"
#include "JsonReport.h"
#include "Module.h"
#include "Reachability.h"
#include "SVFResolver.h"
#include "Toolchain.h"
#include "TypeBasedResolver.h"

#include "llvm/IR/LLVMContext.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/raw_ostream.h"
#include <cctype>
#include <functional>
#include <memory>
#include <set>
#include <utility>

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
                                    cl::desc("indirect-call backend: type-based|svf"));
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

} // namespace

int main(int argc, char **argv) {
  cl::SetVersionPrinter([](raw_ostream &os) {
    os << "reachability-analyzer (LLVM " << reach::linkedLLVMMajor() << ")\n";
  });
  cl::ParseCommandLineOptions(argc, argv, "static fuzz-reachability analyzer\n");

  if (!SelfTestDemangle.empty()) {
    outs() << reach::demangle(SelfTestDemangle) << "\n";
    return 0;
  }

  if (Backend == "svf") {
#ifndef REACHABILITY_ENABLE_SVF
    errs() << "error: SVF backend not available (built without "
              "REACHABILITY_ENABLE_SVF)\n";
    return 2;
#endif
  } else if (Backend != "type-based") {
    errs() << "error: unknown backend '" << Backend << "'\n";
    return 2;
  }

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

  std::unique_ptr<reach::IndirectResolver> resolver;
  if (IndirectAny) {
    resolver = std::make_unique<reach::AnyResolver>();
  } else if (Backend == "svf") {
#ifdef REACHABILITY_ENABLE_SVF
    resolver = std::make_unique<reach::SVFResolver>();
#else
    errs() << "error: SVF backend not available (built without "
              "REACHABILITY_ENABLE_SVF)\n";
    return 2;
#endif
  } else {
    resolver = std::make_unique<reach::TypeBasedResolver>();
  }
  reach::buildIndirectEdges(*mod, graph, *resolver);
  reach::buildEscapeEdges(*mod, graph);

  if (DumpEdges) {
    for (auto &kv : graph.edges())
      for (auto &[to, kind] : kv.second)
        outs() << kv.first->getName() << " -> " << to->getName() << " ["
               << (kind == reach::EdgeKind::Direct ? "direct" : "indirect") << "]\n";
    return 0;
  }

  if (entries.empty()) {
    suggestEntries(*mod, requested);
    return 1;
  }
  if (!unresolved.empty()) {
    errs() << "warning: unresolved entry symbols:";
    for (auto &n : unresolved)
      errs() << " " << n;
    errs() << "\n";
  }

  reach::ReachResult res = reach::computeReachability(*mod, graph, entries);
  if (res.reached.empty()) {
    suggestEntries(*mod, requested);
    return 1;
  }

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

  const char *backendName = IndirectAny ? "indirect-any" : Backend.c_str();
  if (OutFile.empty()) {
    reach::writeJson(outs(), *mod, graph, res, backendName, entries);
  } else {
    std::error_code ec;
    raw_fd_ostream out(OutFile, ec, sys::fs::OF_Text);
    if (ec) {
      errs() << "error: cannot write JSON to " << OutFile << ": " << ec.message()
             << "\n";
      return 1;
    }
    reach::writeJson(out, *mod, graph, res, backendName, entries);
  }
  return 0;
}
