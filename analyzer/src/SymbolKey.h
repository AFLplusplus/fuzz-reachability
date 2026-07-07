#pragma once

#include "llvm/ADT/StringRef.h"
#include <string>

namespace reach {

std::string canonicalKey(llvm::StringRef mangled);
std::string toPattern(llvm::StringRef mangled);

} // namespace reach
