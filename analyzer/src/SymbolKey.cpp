#include "SymbolKey.h"
#include "llvm/ADT/Twine.h"

using namespace llvm;

namespace reach {

static bool legacyStem(StringRef name, size_t &stemLen) {
  const size_t tail = 20;
  if (name.size() > tail && name.ends_with("E")) {
    StringRef t = name.substr(name.size() - tail);
    if (t.starts_with("17h")) {
      bool hex = true;
      for (size_t i = 3; i < 19; ++i) {
        char ch = t[i];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) {
          hex = false;
          break;
        }
      }
      if (hex) {
        stemLen = name.size() - tail;
        return true;
      }
    }
  }
  return false;
}

std::string canonicalKey(StringRef mangled) {
  size_t stemLen;
  if (legacyStem(mangled, stemLen))
    return mangled.substr(0, stemLen).str();
  return mangled.str();
}

std::string toPattern(StringRef mangled) {
  size_t stemLen;
  if (legacyStem(mangled, stemLen))
    return (mangled.substr(0, stemLen) + "*").str();
  return mangled.str();
}

} // namespace reach
