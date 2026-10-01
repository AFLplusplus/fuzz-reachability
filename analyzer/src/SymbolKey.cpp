#include "SymbolKey.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/ADT/Twine.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include <algorithm>

using namespace llvm;

namespace reach {

namespace {

class V0Symbol {
public:
  explicit V0Symbol(StringRef name) : s(name) {}

  bool instantiatedStem(size_t &stemLen) {
    if (!eat('_') || !eat('R'))
      return false;
    while (isDigit(peek()))
      ++pos;
    if (!path())
      return false;
    stemLen = pos;
    return pos < s.size() && path() && pos == s.size();
  }

private:
  static constexpr unsigned MaxDepth = 256;

  struct Nested {
    unsigned &depth;
    explicit Nested(unsigned &d) : depth(d) { ++depth; }
    ~Nested() { --depth; }
  };

  StringRef s;
  size_t pos = 0;
  unsigned depth = 0;

  char peek() const { return pos < s.size() ? s[pos] : '\0'; }

  bool eat(char c) {
    if (peek() != c)
      return false;
    ++pos;
    return true;
  }

  bool base62() {
    while (isAlnum(peek()))
      ++pos;
    return eat('_');
  }

  bool hexData() {
    while (isDigit(peek()) || (peek() >= 'a' && peek() <= 'f'))
      ++pos;
    return eat('_');
  }

  bool undisambiguatedIdentifier() {
    eat('u');
    if (!isDigit(peek()))
      return false;
    size_t length = 0;
    if (!eat('0'))
      while (isDigit(peek())) {
        length = length * 10 + (s[pos++] - '0');
        if (length > s.size())
          return false;
      }
    eat('_');
    if (length > s.size() - pos)
      return false;
    pos += length;
    return true;
  }

  bool identifier() {
    if (eat('s') && !base62())
      return false;
    return undisambiguatedIdentifier();
  }

  bool implPath() {
    if (eat('s') && !base62())
      return false;
    return path();
  }

  bool path() {
    Nested nested(depth);
    if (depth > MaxDepth)
      return false;
    switch (peek()) {
    case 'C':
      ++pos;
      return identifier();
    case 'M':
      ++pos;
      return implPath() && type();
    case 'X':
      ++pos;
      return implPath() && type() && path();
    case 'Y':
      ++pos;
      return type() && path();
    case 'N':
      ++pos;
      if (!isAlpha(peek()))
        return false;
      ++pos;
      return path() && identifier();
    case 'I':
      ++pos;
      if (!path())
        return false;
      while (!eat('E'))
        if (!genericArg())
          return false;
      return true;
    case 'B':
      ++pos;
      return base62();
    default:
      return false;
    }
  }

  bool genericArg() {
    if (eat('L'))
      return base62();
    if (eat('K'))
      return constant();
    return type();
  }

  bool type() {
    Nested nested(depth);
    if (depth > MaxDepth)
      return false;
    char tag = peek();
    if (isLower(tag)) {
      ++pos;
      return true;
    }
    switch (tag) {
    case 'A':
      ++pos;
      return type() && constant();
    case 'S':
    case 'P':
    case 'O':
      ++pos;
      return type();
    case 'T':
      ++pos;
      while (!eat('E'))
        if (!type())
          return false;
      return true;
    case 'R':
    case 'Q':
      ++pos;
      if (eat('L') && !base62())
        return false;
      return type();
    case 'F':
      ++pos;
      return fnSig();
    case 'D':
      ++pos;
      return dynBounds() && eat('L') && base62();
    case 'W':
      ++pos;
      return type() && pattern();
    default:
      return path();
    }
  }

  bool fnSig() {
    if (eat('G') && !base62())
      return false;
    eat('U');
    if (eat('K') && !eat('C') && !undisambiguatedIdentifier())
      return false;
    while (!eat('E'))
      if (!type())
        return false;
    return type();
  }

  bool dynBounds() {
    if (eat('G') && !base62())
      return false;
    while (!eat('E')) {
      if (!path())
        return false;
      while (eat('p'))
        if (!undisambiguatedIdentifier() || !(eat('K') ? constant() : type()))
          return false;
    }
    return true;
  }

  bool pattern() {
    Nested nested(depth);
    if (depth > MaxDepth)
      return false;
    if (eat('R'))
      return constant() && constant();
    if (!eat('O'))
      return false;
    while (!eat('E'))
      if (!pattern())
        return false;
    return true;
  }

  bool constants() {
    while (!eat('E'))
      if (!constant())
        return false;
    return true;
  }

  bool constant() {
    Nested nested(depth);
    if (depth > MaxDepth || pos >= s.size())
      return false;
    char tag = s[pos++];
    switch (tag) {
    case 'p':
      return true;
    case 'B':
      return base62();
    case 'a':
    case 's':
    case 'l':
    case 'x':
    case 'n':
    case 'i':
      eat('n');
      return hexData();
    case 'h':
    case 't':
    case 'm':
    case 'y':
    case 'o':
    case 'j':
    case 'b':
    case 'c':
    case 'e':
      return hexData();
    case 'R':
    case 'Q':
      if (tag == 'R' && eat('e'))
        return hexData();
      return constant();
    case 'A':
    case 'T':
      return constants();
    case 'V':
      if (!path())
        return false;
      if (eat('U'))
        return true;
      if (eat('T'))
        return constants();
      if (!eat('S'))
        return false;
      while (!eat('E'))
        if (!identifier() || !constant())
          return false;
      return true;
    default:
      return false;
    }
  }
};

} // namespace

static bool v0Stem(StringRef name, size_t &stemLen) {
  return name.starts_with("_R") && V0Symbol(name).instantiatedStem(stemLen);
}

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
  if (legacyStem(mangled, stemLen) || v0Stem(mangled, stemLen))
    return mangled.substr(0, stemLen).str();
  return mangled.str();
}

std::string toPattern(StringRef mangled) {
  size_t stemLen;
  if (legacyStem(mangled, stemLen) || v0Stem(mangled, stemLen))
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
