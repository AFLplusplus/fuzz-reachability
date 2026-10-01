#include "Module.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/Dominators.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IRReader/IRReader.h"
#include "llvm/Support/SourceMgr.h"
#include "llvm/Transforms/Utils/PromoteMemToReg.h"

namespace reach {
std::unique_ptr<llvm::Module> loadModule(llvm::LLVMContext &ctx,
                                         llvm::StringRef path, std::string &err) {
  llvm::SMDiagnostic d;
  auto m = llvm::parseIRFile(path, d, ctx);
  if (!m) {
    err = d.getMessage().str();
    return nullptr;
  }
  return m;
}

void promoteMemoryToRegisters(llvm::Module &m) {
  llvm::SmallVector<llvm::AllocaInst *, 16> allocas;
  for (llvm::Function &f : m) {
    if (f.isDeclaration())
      continue;
    allocas.clear();
    for (llvm::Instruction &i : f.getEntryBlock())
      if (auto *alloca = llvm::dyn_cast<llvm::AllocaInst>(&i))
        if (llvm::isAllocaPromotable(alloca))
          allocas.push_back(alloca);
    if (allocas.empty())
      continue;
    llvm::DominatorTree dt(f);
    llvm::PromoteMemToReg(allocas, dt);
  }
}
}
