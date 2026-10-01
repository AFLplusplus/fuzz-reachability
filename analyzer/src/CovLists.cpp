#include "CovLists.h"
#include "SymbolKey.h"
#include <algorithm>
#include <set>
#include <string>
#include <vector>

using namespace llvm;

namespace reach {

static std::set<std::string> reachedPatterns(Module &m, const ReachResult &res) {
  std::set<std::string> out;
  for (Function &f : m) {
    if (f.isDeclaration() || !res.reached.count(&f))
      continue;
    for (const std::string &name : symbolVariants(f))
      out.insert(toPattern(name));
  }
  return out;
}

static std::set<std::string> unreachedPatterns(Module &m, const ReachResult &res) {
  std::set<std::string> out;
  for (Function &f : m) {
    if (f.isDeclaration() || res.reached.count(&f))
      continue;
    out.insert(toPattern(f.getName()));
    out.insert(toPattern(symbolName(f)));
  }
  return out;
}

void writeAllowlist(raw_ostream &os, Module &m, const ReachResult &res) {
  os << "# SanitizerCoverage allowlist: statically-reachable functions.\n"
     << "# Use with: clang -fsanitize-coverage=<...> "
        "-fsanitize-coverage-allowlist=reached.txt\n"
     << "# A coverage allowlist matches a function only when BOTH a src: and a\n"
     << "# fun: entry match, hence the src:* line below.\n"
     << "# Rust generic instances carry a codegen-dependent '17h<hash>' mangling\n"
     << "# disambiguator; it is replaced by '*' so an entry matches the same\n"
     << "# instance regardless of which build emitted it (clang sancov and AFL++\n"
     << "# both treat '*' as a glob in fun: entries).\n"
     << "src:*\n";
  for (const std::string &p : reachedPatterns(m, res))
    os << "fun:" << p << "\n";
}

static std::vector<std::string> reachedNames(Module &m, const ReachResult &res) {
  std::vector<std::string> out;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    if (res.reached.count(&f))
      for (std::string &name : symbolVariants(f))
        out.push_back(std::move(name));
  }
  std::sort(out.begin(), out.end());
  return out;
}

static bool matchesReached(StringRef pattern,
                           const std::vector<std::string> &reached) {
  if (pattern.ends_with("*")) {
    std::string prefix = pattern.drop_back().str();
    auto it = std::lower_bound(reached.begin(), reached.end(), prefix);
    return it != reached.end() && StringRef(*it).starts_with(prefix);
  }
  return std::binary_search(reached.begin(), reached.end(), pattern.str());
}

void writeIgnorelist(raw_ostream &os, Module &m, const ReachResult &res) {
  os << "# SanitizerCoverage ignorelist: statically-unreachable functions.\n"
     << "# Use with: clang -fsanitize-coverage=<...> "
        "-fsanitize-coverage-ignorelist=not_reached.txt\n"
     << "# The Rust '17h<hash>' mangling disambiguator is replaced by '*' (see\n"
     << "# the allowlist header). A pattern that also matches a reachable\n"
     << "# function's name as a glob is omitted, so excluding an unreachable\n"
     << "# instance never excludes a reachable one that shares its name or prefix.\n";
  std::vector<std::string> reached = reachedNames(m, res);
  for (const std::string &p : unreachedPatterns(m, res))
    if (!matchesReached(p, reached))
      os << "fun:" << p << "\n";
}

} // namespace reach
