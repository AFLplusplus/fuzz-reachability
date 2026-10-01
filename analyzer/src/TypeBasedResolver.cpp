#include "TypeBasedResolver.h"
#include "llvm/IR/InstIterator.h"
#include <vector>

using namespace llvm;

namespace reach {

void TypeBasedResolver::prepare(Module &m, const EscapeIndex &idx,
                                FunctionSets &sets) {
  DenseMap<FunctionType *, std::vector<Function *>> buckets;
  std::vector<Function *> addressTaken;
  for (Function &f : m)
    if (f.hasAddressTaken()) {
      buckets[f.getFunctionType()].push_back(&f);
      addressTaken.push_back(&f);
    }
  for (auto &kv : buckets)
    Buckets[kv.first] = sets.add(std::move(kv.second));
  if (!addressTaken.empty())
    AddressTaken = sets.add(std::move(addressTaken));

  std::vector<Value *> roots;
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f))
      if (auto *cb = dyn_cast<CallBase>(&i))
        if (isIndirectCall(*cb))
          roots.push_back(cb->getCalledOperand());
  }
  computeValueFlow(roots, idx, sets, Flow, true, true);
}

void TypeBasedResolver::resolve(CallBase &cb, SmallVectorImpl<SetId> &out) {
  auto it = Buckets.find(cb.getFunctionType());
  if (it != Buckets.end())
    out.push_back(it->second);
  Value *callee = cb.getCalledOperand();
  auto flow = Flow.sccOf.find(callee->stripPointerCasts());
  if (flow == Flow.sccOf.end())
    return;
  if (Flow.sccSet[flow->second] != NoSet)
    out.push_back(Flow.sccSet[flow->second]);
  bool hasCast = isEscapeCast(callee) || Flow.sccCasts[flow->second];
  if (hasCast && Flow.sccUnresolved[flow->second] && AddressTaken != NoSet)
    out.push_back(AddressTaken);
}

void AnyResolver::prepare(Module &m, const EscapeIndex &, FunctionSets &sets) {
  std::vector<Function *> addressTaken;
  for (Function &f : m)
    if (f.hasAddressTaken())
      addressTaken.push_back(&f);
  if (!addressTaken.empty())
    AddressTaken = sets.add(std::move(addressTaken));
}

void AnyResolver::resolve(CallBase &, SmallVectorImpl<SetId> &out) {
  if (AddressTaken != NoSet)
    out.push_back(AddressTaken);
}

} // namespace reach
