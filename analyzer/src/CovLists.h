#pragma once

#include "Reachability.h"
#include "llvm/IR/Module.h"
#include "llvm/Support/raw_ostream.h"

namespace reach {

// SanitizerCoverage special-case-list output, consumable by clang's
// -fsanitize-coverage-allowlist= / -fsanitize-coverage-ignorelist= and by
// AFL++'s AFL_LLVM_ALLOWLIST / AFL_LLVM_DENYLIST. Entries are the LLVM symbol
// (mangled) names, which is what both match `fun:` against, except the Rust
// '17h<hash>' mangling disambiguator is replaced by a '*' glob so an entry
// matches the same instance across builds (the disambiguator is codegen
// dependent and differs between the bitcode snapshot and the fuzz binary).
// Only defined functions are listed (declarations are external).

// Allowlist of reachable functions. A coverage allowlist instruments a function
// only when BOTH a src: and a fun: entry match, so we emit `src:*` plus one
// `fun:<pattern>` per distinct reachable defined function.
void writeAllowlist(llvm::raw_ostream &os, llvm::Module &m, const ReachResult &res);

// Ignorelist of unreachable functions: one `fun:<pattern>` per unreachable
// defined function (no src: line -- that would exclude everything). A pattern
// that also matches a reachable instance is omitted so reachable code is never
// excluded.
void writeIgnorelist(llvm::raw_ostream &os, llvm::Module &m, const ReachResult &res);

} // namespace reach
