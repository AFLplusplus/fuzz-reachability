#pragma once

#include "CallGraph.h"
#include "IndirectResolver.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/IR/DerivedTypes.h"

namespace reach {

// Default backend: exact type matches plus address-flow targets, with a
// conservative address-taken fallback for unresolved cast flows.
class TypeBasedResolver : public IndirectResolver {
public:
  void prepare(llvm::Module &m, const EscapeIndex &idx,
               FunctionSets &sets) override;
  void resolve(llvm::CallBase &cb, llvm::SmallVectorImpl<SetId> &out) override;

private:
  llvm::DenseMap<llvm::FunctionType *, SetId> Buckets;
  SetId AddressTaken = NoSet;
  ValueFlow Flow;
};

// Debug backend (--indirect-any): an indirect call may reach ANY address-taken
// function regardless of type. Maximal (coarsest) sound over-approximation.
class AnyResolver : public IndirectResolver {
public:
  void prepare(llvm::Module &m, const EscapeIndex &idx,
               FunctionSets &sets) override;
  void resolve(llvm::CallBase &cb, llvm::SmallVectorImpl<SetId> &out) override;

private:
  SetId AddressTaken = NoSet;
};

} // namespace reach
