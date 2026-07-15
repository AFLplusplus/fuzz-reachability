#include "TypeBasedResolver.h"
#include "CallGraph.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/Instructions.h"

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
  for (Function *f : flow)
    if (seen.insert(f).second)
      Candidates.push_back(f);
  bool hasCast = false;
  SmallVector<Value *, 8> work = {cb.getCalledOperand()};
  DenseSet<Value *> visited;
  while (!work.empty()) {
    Value *v = work.pop_back_val();
    if (!v || !visited.insert(v).second)
      continue;
    if (isa<CastInst>(v) || (isa<ConstantExpr>(v) && cast<ConstantExpr>(v)->isCast()))
      hasCast = true;
    if (auto *u = dyn_cast<User>(v))
      for (Use &op : u->operands())
        work.push_back(op.get());
  }
  if (hasCast && flow.empty())
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
