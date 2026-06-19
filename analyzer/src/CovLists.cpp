#include "CovLists.h"
#include <algorithm>
#include <vector>

using namespace llvm;

namespace reach {

// Mangled names of defined functions, partitioned by reachability and sorted.
static std::vector<StringRef> definedNames(Module &m, const ReachResult &res,
                                           bool reached) {
  std::vector<StringRef> out;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    if (static_cast<bool>(res.reached.count(&f)) == reached)
      out.push_back(f.getName());
  }
  std::sort(out.begin(), out.end());
  return out;
}

void writeAllowlist(raw_ostream &os, Module &m, const ReachResult &res) {
  os << "# SanitizerCoverage allowlist: statically-reachable functions.\n"
     << "# Use with: clang -fsanitize-coverage=<...> "
        "-fsanitize-coverage-allowlist=reached.txt\n"
     << "# A coverage allowlist matches a function only when BOTH a src: and a\n"
     << "# fun: entry match, hence the src:* line below.\n"
     << "src:*\n";
  for (StringRef n : definedNames(m, res, /*reached=*/true))
    os << "fun:" << n << "\n";
}

void writeIgnorelist(raw_ostream &os, Module &m, const ReachResult &res) {
  os << "# SanitizerCoverage ignorelist: statically-unreachable functions.\n"
     << "# Use with: clang -fsanitize-coverage=<...> "
        "-fsanitize-coverage-ignorelist=not_reached.txt\n";
  for (StringRef n : definedNames(m, res, /*reached=*/false))
    os << "fun:" << n << "\n";
}

} // namespace reach
