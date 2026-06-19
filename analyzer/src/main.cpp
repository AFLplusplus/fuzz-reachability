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
#include <functional>
#include <memory>

using namespace llvm;

static cl::opt<std::string> InputIR(cl::Positional, cl::desc("<input .ll/.bc>"),
                                    cl::init(""));
static cl::list<std::string> EntryList("entry",
                                       cl::desc("entry symbol (repeatable; default "
                                                "LLVMFuzzerTestOneInput)"));
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

  std::vector<std::string> entries(EntryList.begin(), EntryList.end());
  if (entries.empty())
    entries.push_back("LLVMFuzzerTestOneInput");

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

  if (DumpEdges) {
    for (auto &kv : graph.edges())
      for (auto &[to, kind] : kv.second)
        outs() << kv.first->getName() << " -> " << to->getName() << " ["
               << (kind == reach::EdgeKind::Direct ? "direct" : "indirect") << "]\n";
    return 0;
  }

  reach::ReachResult res = reach::computeReachability(*mod, graph, entries);

  if (res.reached.empty()) {
    suggestEntries(*mod, entries);
    return 1;
  }
  if (!res.missingNames.empty()) {
    errs() << "warning: unresolved entry symbols:";
    for (auto &n : res.missingNames)
      errs() << " " << n;
    errs() << "\n";
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
