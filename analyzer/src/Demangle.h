#pragma once

#include "llvm/ADT/StringRef.h"
#include <string>

namespace reach {
// Demangle Itanium (C++) and Rust legacy/v0 names. Returns the input unchanged
// if it is not a recognized mangled name.
std::string demangle(llvm::StringRef mangled);
}
