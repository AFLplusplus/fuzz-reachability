#include "Demangle.h"
#include "llvm/Demangle/Demangle.h"

namespace reach {
std::string demangle(llvm::StringRef mangled) {
  // llvm::demangle auto-detects the scheme (Itanium, Rust v0, Rust legacy).
  return llvm::demangle(std::string(mangled));
}
}
