#pragma once

#include "IndirectResolver.h"
#include "TypeBasedResolver.h"
#include <memory>

namespace reach {

struct SVFState; // pimpl: hides all SVF types from the rest of the analyzer

// Optional backend (--backend=svf): SVF Andersen points-to gives per-callsite
// callee sets. For any indirect call SVF does not resolve, falls back to the
// type-based resolver so the result is never less sound than type-based.
class SVFResolver : public IndirectResolver {
public:
  SVFResolver();
  ~SVFResolver() override;
  void prepare(llvm::Module &m) override;
  std::vector<llvm::Function *> resolve(llvm::CallBase &cb) override;

private:
  llvm::Module *Mod = nullptr;
  TypeBasedResolver Fallback;
  std::unique_ptr<SVFState> State;
};

} // namespace reach
