#include "Demangle.h"
#include "llvm/Demangle/Demangle.h"
#include <cctype>

namespace reach {
std::string demangle(llvm::StringRef mangled) {
  // llvm::demangle auto-detects the scheme (Itanium, Rust v0, Rust legacy).
  return llvm::demangle(std::string(mangled));
}
std::string demangleWithoutHash(llvm::StringRef mangled) {
  std::string s = demangle(mangled);
  std::string::size_type pos = s.rfind("::h");
  if (pos == std::string::npos)
    return s;
  llvm::StringRef tail = llvm::StringRef(s).substr(pos + 3);
  if (tail.empty())
    return s;
  for (char c : tail)
    if (!std::isxdigit(static_cast<unsigned char>(c)))
      return s;
  return s.substr(0, pos);
}
}
