#include "TypeBasedResolver.h"
#include "CallGraph.h"
#include "llvm/ADT/DenseSet.h"

using namespace llvm;

namespace reach {

void TypeBasedResolver::prepare(Module &m, const EscapeIndex &idx) {
  Index = &idx;
  for (Function &f : m)
    if (f.hasAddressTaken()) {
      Buckets[f.getFunctionType()].push_back(&f);
      AddressTaken.push_back(&f);
    }
}

ArrayRef<Function *> TypeBasedResolver::resolve(CallBase &cb) {
  Candidates.clear();
  DenseSet<Function *> seen;
  auto it = Buckets.find(cb.getFunctionType());
  if (it != Buckets.end())
    for (Function *f : it->second)
      if (seen.insert(f).second)
        Candidates.push_back(f);
  auto flow = computeValueFlowTargets(cb.getCalledOperand(), *Index);
  for (Function *f : flow.targets)
    if (seen.insert(f).second)
      Candidates.push_back(f);
  if (flow.hasCast && flow.unresolved)
    for (Function *f : AddressTaken)
      if (seen.insert(f).second)
        Candidates.push_back(f);
  return Candidates;
}

void AnyResolver::prepare(Module &m, const EscapeIndex &) {
  for (Function &f : m)
    if (f.hasAddressTaken())
      AddressTaken.push_back(&f);
}

ArrayRef<Function *> AnyResolver::resolve(CallBase &) { return AddressTaken; }

} // namespace reach
