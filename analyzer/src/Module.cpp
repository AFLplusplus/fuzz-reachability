#include "Module.h"
#include "llvm/IRReader/IRReader.h"
#include "llvm/Support/SourceMgr.h"

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
}
