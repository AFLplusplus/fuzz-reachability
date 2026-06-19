#include "TypeBasedResolver.h"
#include "llvm/IR/Instructions.h"

using namespace llvm;

namespace reach {

void TypeBasedResolver::prepare(Module &m) {
  for (Function &f : m)
    if (!f.isDeclaration() && f.hasAddressTaken())
      Buckets[f.getFunctionType()].push_back(&f);
}

std::vector<Function *> TypeBasedResolver::resolve(CallBase &cb) {
  auto it = Buckets.find(cb.getFunctionType());
  if (it == Buckets.end())
    return {};
  return std::vector<Function *>(it->second.begin(), it->second.end());
}

void AnyResolver::prepare(Module &m) {
  for (Function &f : m)
    if (!f.isDeclaration() && f.hasAddressTaken())
      AddressTaken.push_back(&f);
}

std::vector<Function *> AnyResolver::resolve(CallBase &) { return AddressTaken; }

} // namespace reach
