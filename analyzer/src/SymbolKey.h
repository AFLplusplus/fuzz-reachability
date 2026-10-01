#pragma once

#include "llvm/ADT/StringRef.h"
#include "llvm/IR/Function.h"
#include <string>
#include <vector>

namespace reach {

std::string canonicalKey(llvm::StringRef mangled);
std::string toPattern(llvm::StringRef mangled);
std::string symbolName(const llvm::Function &f);
std::vector<std::string> symbolVariants(const llvm::Function &f);

} // namespace reach
