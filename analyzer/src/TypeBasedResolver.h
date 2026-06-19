#pragma once

#include "IndirectResolver.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/IR/DerivedTypes.h"

namespace reach {

// Default backend: an indirect call of function type T may reach any
// address-taken function whose type is structurally identical to T.
// LLVM uniques FunctionType per context, so pointer identity == type identity.
class TypeBasedResolver : public IndirectResolver {
public:
  void prepare(llvm::Module &m) override;
  std::vector<llvm::Function *> resolve(llvm::CallBase &cb) override;

private:
  llvm::DenseMap<llvm::FunctionType *, llvm::SmallVector<llvm::Function *, 4>>
      Buckets;
};

// Debug backend (--indirect-any): an indirect call may reach ANY address-taken
// function regardless of type. Maximal (coarsest) sound over-approximation.
class AnyResolver : public IndirectResolver {
public:
  void prepare(llvm::Module &m) override;
  std::vector<llvm::Function *> resolve(llvm::CallBase &cb) override;

private:
  std::vector<llvm::Function *> AddressTaken;
};

} // namespace reach
