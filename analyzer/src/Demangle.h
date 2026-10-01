#pragma once

#include "llvm/ADT/StringRef.h"
#include <string>

namespace reach {
// Demangle Itanium (C++) and Rust legacy/v0 names. Returns the input unchanged
// if it is not a recognized mangled name.
std::string demangle(llvm::StringRef mangled);
// Rust legacy mangling appends a ::h<hex> disambiguator to the demangled path
// (e.g. "crate::main::hcc7e51cc..."). Strip it so demangled-name matching can use
// the human-readable path ("crate::main").
std::string demangleWithoutHash(llvm::StringRef mangled);
}
