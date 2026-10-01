#pragma once

#include "Reachability.h"
#include "llvm/ADT/DenseSet.h"
#include "llvm/IR/Function.h"
#include "llvm/IR/GlobalVariable.h"
#include "llvm/IR/Module.h"
#include <cstdint>
#include <optional>
#include <set>
#include <utility>
#include <vector>

namespace reach {

using SlotKey = std::pair<llvm::Type *, std::vector<uint64_t>>;

std::optional<SlotKey> fieldKey(llvm::Value *pointer);
bool isDispatchTable(const llvm::GlobalVariable &g);
std::set<SlotKey> collectCallableFields(llvm::Module &m);

llvm::DenseSet<llvm::Function *> computeSlotTargets(llvm::Module &m,
                                                    const ReachResult &res);

} // namespace reach
