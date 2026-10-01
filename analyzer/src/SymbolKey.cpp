#include "SymbolKey.h"
#include "llvm/ADT/Twine.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include <algorithm>

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

static size_t numericSuffixStart(StringRef name) {
  size_t end = name.size();
  size_t digits = end;
  while (digits > 0 && name[digits - 1] >= '0' && name[digits - 1] <= '9')
    --digits;
  if (digits == end || digits == 0 || name[digits - 1] != '.')
    return StringRef::npos;
  return digits - 1;
}

static bool isRenamedFrom(StringRef name, StringRef original) {
  if (original.empty() || original.size() >= name.size() ||
      !name.starts_with(original))
    return false;
  StringRef rest = name.drop_front(original.size());
  while (!rest.empty()) {
    size_t start = numericSuffixStart(rest);
    if (start == StringRef::npos)
      return false;
    rest = rest.take_front(start);
  }
  return true;
}

std::string symbolName(const Function &f) {
  StringRef name = f.getName();
  if (!f.hasLocalLinkage() || numericSuffixStart(name) == StringRef::npos)
    return name.str();
  if (const DISubprogram *sp = f.getSubprogram()) {
    StringRef original = sp->getLinkageName();
    if (original.empty())
      original = sp->getName();
    if (isRenamedFrom(name, original))
      return original.str();
  }
  return name.str();
}

std::vector<std::string> symbolVariants(const Function &f) {
  std::vector<std::string> out = {f.getName().str()};
  std::string symbol = symbolName(f);
  if (symbol != out.front())
    out.push_back(symbol);
  if (f.hasLocalLinkage() && !f.getSubprogram()) {
    StringRef stem = f.getName();
    for (size_t start = numericSuffixStart(stem); start != StringRef::npos;
         start = numericSuffixStart(stem)) {
      stem = stem.take_front(start);
      if (std::find(out.begin(), out.end(), stem) == out.end())
        out.push_back(stem.str());
    }
  }
  return out;
}

} // namespace reach
