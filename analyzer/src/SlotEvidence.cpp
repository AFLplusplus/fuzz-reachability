#include "SlotEvidence.h"
#include "CallGraph.h"
#include "SymbolKey.h"
#include "llvm/BinaryFormat/Dwarf.h"
#include "llvm/Config/llvm-config.h"
#include "llvm/IR/Constants.h"
#include "llvm/IR/DataLayout.h"
#include "llvm/IR/DebugInfoMetadata.h"
#include "llvm/IR/InstIterator.h"
#include "llvm/IR/Instructions.h"
#include "llvm/IR/Operator.h"
#include <map>
#include <optional>
#include <set>
#include <utility>
#include <vector>

using namespace llvm;

namespace reach {

std::optional<SlotKey> fieldKey(Value *pointer) {
  auto *gep = dyn_cast<GEPOperator>(pointer);
  if (!gep || !gep->getSourceElementType()->isStructTy() ||
      gep->getNumIndices() < 2)
    return std::nullopt;
  Type *owner = gep->getSourceElementType();
  Type *current = owner;
  std::vector<uint64_t> path;
  for (auto it = std::next(gep->idx_begin()); it != gep->idx_end(); ++it) {
    auto *index = dyn_cast<ConstantInt>(*it);
    if (!index)
      return std::nullopt;
    uint64_t k = index->getZExtValue();
    if (auto *st = dyn_cast<StructType>(current)) {
      if (k >= st->getNumElements())
        return std::nullopt;
      current = st->getElementType(k);
    } else if (auto *at = dyn_cast<ArrayType>(current)) {
      current = at->getElementType();
    } else {
      return std::nullopt;
    }
    path.push_back(k);
    if (current->isStructTy() && std::next(it) != gep->idx_end()) {
      owner = current;
      path.clear();
    }
  }
  if (path.empty())
    return std::nullopt;
  return SlotKey{owner, std::move(path)};
}

bool isDispatchTable(const GlobalVariable &g) {
  StringRef name = g.getName();
  return name.starts_with("_ZTV") || name.starts_with("_ZTC") ||
         name.starts_with("_ZTT") || name.starts_with("vtable.");
}

namespace {

enum class Language { Unknown, Cxx, Rust, Other };

enum DispatchKind : uint64_t { ItaniumTable, RustTable };

Language languageOfUnit(const DICompileUnit &cu) {
#if LLVM_VERSION_MAJOR >= 22
  DISourceLanguageName lang = cu.getSourceLanguage();
  unsigned name = lang.getName();
  if (lang.hasVersionedName()) {
    if (name == dwarf::DW_LNAME_C_plus_plus || name == dwarf::DW_LNAME_ObjC_plus_plus)
      return Language::Cxx;
    return name == dwarf::DW_LNAME_Rust ? Language::Rust : Language::Other;
  }
#else
  unsigned name = cu.getSourceLanguage();
#endif
  switch (name) {
  case dwarf::DW_LANG_C_plus_plus:
  case dwarf::DW_LANG_C_plus_plus_03:
  case dwarf::DW_LANG_C_plus_plus_11:
  case dwarf::DW_LANG_C_plus_plus_14:
  case dwarf::DW_LANG_C_plus_plus_17:
  case dwarf::DW_LANG_C_plus_plus_20:
  case dwarf::DW_LANG_ObjC_plus_plus:
    return Language::Cxx;
  case dwarf::DW_LANG_Rust:
    return Language::Rust;
  default:
    return Language::Other;
  }
}

Language languageOf(const Function &f) {
  if (DISubprogram *sp = f.getSubprogram())
    if (DICompileUnit *cu = sp->getUnit())
      return languageOfUnit(*cu);
  StringRef name = f.getName();
  if (name.starts_with("_R") || canonicalKey(name) != name)
    return Language::Rust;
  if (name.starts_with("_Z"))
    return Language::Cxx;
  return Language::Unknown;
}

void recordInitializer(Constant *c, Type *owner, std::vector<uint64_t> path,
                       std::map<SlotKey, std::vector<Function *>> &stored) {
  if (Function *f = resolveCallableValue(c)) {
    if (owner && !path.empty())
      stored[{owner, path}].push_back(f);
    return;
  }
  if (auto *cs = dyn_cast<ConstantStruct>(c)) {
    for (unsigned i = 0; i < cs->getNumOperands(); ++i)
      recordInitializer(cs->getOperand(i), cs->getType(), {i}, stored);
    return;
  }
  if (auto *ca = dyn_cast<ConstantArray>(c)) {
    for (unsigned i = 0; i < ca->getNumOperands(); ++i) {
      std::vector<uint64_t> element = path;
      element.push_back(i);
      recordInitializer(ca->getOperand(i), owner, owner ? element : path, stored);
    }
  }
}

void collectOffsets(Constant *c, uint64_t base, const DataLayout &dl,
                    std::vector<std::pair<uint64_t, Function *>> &out) {
  if (Function *f = resolveCallableValue(c)) {
    out.push_back({base, f});
    return;
  }
  if (auto *cs = dyn_cast<ConstantStruct>(c)) {
    const StructLayout *layout = dl.getStructLayout(cs->getType());
    for (unsigned i = 0; i < cs->getNumOperands(); ++i)
      collectOffsets(cs->getOperand(i), base + layout->getElementOffset(i), dl,
                     out);
    return;
  }
  if (auto *ca = dyn_cast<ConstantArray>(c)) {
    uint64_t size = dl.getTypeAllocSize(ca->getType()->getElementType());
    for (unsigned i = 0; i < ca->getNumOperands(); ++i)
      collectOffsets(ca->getOperand(i), base + i * size, dl, out);
  }
}

unsigned topLevelElement(GlobalVariable &g, uint64_t offset,
                         const DataLayout &dl) {
  auto *st = dyn_cast<StructType>(g.getValueType());
  if (!st)
    return 0;
  const StructLayout *layout = dl.getStructLayout(st);
  if (offset >= layout->getSizeInBytes())
    return st->getNumElements();
  return layout->getElementContainingOffset(offset);
}

void recordDispatchTable(GlobalVariable &g, const DataLayout &dl,
                         std::map<SlotKey, std::vector<Function *>> &stored) {
  StringRef name = g.getName();
  std::set<uint64_t> addressPoints;
  bool itanium = name.starts_with("_ZTV") || name.starts_with("_ZTC");
  DispatchKind kind = itanium ? ItaniumTable : RustTable;
  if (itanium) {
    for (User *user : g.users()) {
      auto *gep = dyn_cast<GEPOperator>(user);
      if (!gep || gep->getPointerOperand() != &g)
        continue;
      APInt offset(dl.getIndexTypeSizeInBits(gep->getType()), 0);
      if (gep->accumulateConstantOffset(dl, offset) && !offset.isNegative())
        addressPoints.insert(offset.getZExtValue());
    }
  } else if (name.starts_with("vtable.")) {
    addressPoints.insert(0);
  }
  if (addressPoints.empty())
    return;
  std::vector<std::pair<uint64_t, Function *>> entries;
  collectOffsets(g.getInitializer(), 0, dl, entries);
  for (const auto &[offset, f] : entries)
    for (uint64_t point : addressPoints) {
      if (point > offset)
        continue;
      if (itanium && topLevelElement(g, point, dl) != topLevelElement(g, offset, dl))
        continue;
      stored[{nullptr, {kind, offset - point}}].push_back(f);
    }
}

Value *objectArgument(CallBase &call) {
  for (unsigned i = 0; i < call.arg_size(); ++i)
    if (!call.paramHasAttr(i, Attribute::StructRet))
      return call.getArgOperand(i)->stripPointerCasts();
  return nullptr;
}

std::optional<SlotKey> dispatchKey(Value *pointer, CallBase &call,
                                   const DataLayout &dl, Language caller) {
  APInt offset(dl.getIndexTypeSizeInBits(pointer->getType()), 0);
  Value *base =
      pointer->stripAndAccumulateConstantOffsets(dl, offset, /*AllowNonInbounds=*/true);
  if (offset.isNegative() || isa<GlobalValue>(base) || isa<AllocaInst>(base))
    return std::nullopt;
  if (caller == Language::Rust)
    return SlotKey{nullptr, {RustTable, offset.getZExtValue()}};
  auto *vptr = dyn_cast<LoadInst>(base);
  if ((caller == Language::Cxx || caller == Language::Unknown) && vptr &&
      vptr->getPointerOperand()->stripPointerCasts() == objectArgument(call))
    return SlotKey{nullptr, {ItaniumTable, offset.getZExtValue()}};
  return std::nullopt;
}

} // namespace

std::set<SlotKey> collectCallableFields(Module &m) {
  std::map<SlotKey, std::vector<Function *>> stored;
  for (GlobalVariable &g : m.globals())
    if (g.hasInitializer())
      recordInitializer(g.getInitializer(), nullptr, {}, stored);
  std::set<SlotKey> out;
  for (const auto &kv : stored)
    out.insert(kv.first);
  for (Function &f : m) {
    if (f.isDeclaration())
      continue;
    for (Instruction &i : instructions(f)) {
      if (auto write = memoryWrite(i)) {
        if (resolveCallableValue(write->value))
          if (auto key = fieldKey(write->pointer))
            out.insert(std::move(*key));
        continue;
      }
      auto *cb = dyn_cast<CallBase>(&i);
      if (!cb || !isIndirectCall(*cb))
        continue;
      if (auto *load = dyn_cast<LoadInst>(cb->getCalledOperand()->stripPointerCasts()))
        if (auto key = fieldKey(load->getPointerOperand()))
          out.insert(std::move(*key));
    }
  }
  return out;
}

DenseSet<Function *> computeSlotTargets(Module &m, const ReachResult &res) {
  const DataLayout &dl = m.getDataLayout();
  std::map<SlotKey, std::vector<Function *>> stored;
  std::set<SlotKey> called;

  for (GlobalVariable &g : m.globals()) {
    if (!g.hasInitializer())
      continue;
    recordInitializer(g.getInitializer(), nullptr, {}, stored);
    recordDispatchTable(g, dl, stored);
  }

  for (Function &f : m) {
    if (f.isDeclaration() || !res.reached.count(&f))
      continue;
    Language language = languageOf(f);
    for (Instruction &i : instructions(f)) {
      if (auto write = memoryWrite(i)) {
        if (Function *target = resolveCallableValue(write->value))
          if (auto key = fieldKey(write->pointer))
            stored[*key].push_back(target);
        continue;
      }
      auto *cb = dyn_cast<CallBase>(&i);
      if (!cb || !isIndirectCall(*cb))
        continue;
      auto *load = dyn_cast<LoadInst>(cb->getCalledOperand()->stripPointerCasts());
      if (!load)
        continue;
      Value *slot = load->getPointerOperand();
      if (auto field = fieldKey(slot))
        called.insert(*field);
      else if (auto dispatch = dispatchKey(slot, *cb, dl, language))
        called.insert(*dispatch);
    }
  }

  DenseSet<Function *> out;
  for (const SlotKey &key : called) {
    auto it = stored.find(key);
    if (it != stored.end())
      out.insert(it->second.begin(), it->second.end());
  }
  return out;
}

} // namespace reach
