#pragma once

#include "llvm/IR/LLVMContext.h"
#include "llvm/IR/Module.h"
#include <memory>
#include <string>

namespace reach {
// Parse a .ll or .bc file. Returns nullptr and sets `err` on failure.
std::unique_ptr<llvm::Module> loadModule(llvm::LLVMContext &ctx,
                                         llvm::StringRef path, std::string &err);
}
