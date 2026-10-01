#pragma once

#include "Reachability.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/Module.h"

namespace reach {

llvm::DenseSet<llvm::Function *> computeSlotTargets(llvm::Module &m,
                                                    const ReachResult &res);

} // namespace reach
