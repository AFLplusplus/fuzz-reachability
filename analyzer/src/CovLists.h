#pragma once

#include "Reachability.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/raw_ostream.h"

namespace reach {

// SanitizerCoverage special-case-list output, consumable by clang's
// -fsanitize-coverage-allowlist= / -fsanitize-coverage-ignorelist=.
// Names are the LLVM symbol (mangled) names, which is what clang matches `fun:`
// against. Only defined functions are listed (declarations are external).

// Allowlist of reachable functions. A coverage allowlist instruments a function
// only when BOTH a src: and a fun: entry match, so we emit `src:*` plus one
// `fun:<mangled>` per reachable defined function.
void writeAllowlist(llvm::raw_ostream &os, llvm::Module &m, const ReachResult &res);

// Ignorelist of unreachable functions: one `fun:<mangled>` per unreachable
// defined function (no src: line -- that would exclude everything).
void writeIgnorelist(llvm::raw_ostream &os, llvm::Module &m, const ReachResult &res);

} // namespace reach
