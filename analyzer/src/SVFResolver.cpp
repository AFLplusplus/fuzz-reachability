#include "SVFResolver.h"

#ifdef REACHABILITY_ENABLE_SVF

#include "SVF-LLVM/LLVMModule.h"
#include "SVF-LLVM/SVFIRBuilder.h"
#include "Util/ExtAPI.h"
#include "Util/Options.h"
#include "WPA/Andersen.h"
#include "llvm/IR/Instructions.h"

using namespace llvm;

namespace reach {

struct SVFState {
  SVF::PointerAnalysis *pta = nullptr;
};

SVFResolver::SVFResolver() : State(std::make_unique<SVFState>()) {}

void SVFResolver::prepare(Module &m) {
  Mod = &m;
  Fallback.prepare(m); // soundness net for callsites SVF leaves unresolved

  // Silence SVF's statistics dump so it never pollutes our JSON on stdout.
  const_cast<::Option<bool> &>(SVF::Options::PStat).setValue(false);

#ifdef REACHABILITY_SVF_EXTAPI
  SVF::ExtAPI::setExtBcPath(REACHABILITY_SVF_EXTAPI);
#endif

  // Build SVF over our in-memory module so getCallICFGNode maps our calls.
  SVF::LLVMModuleSet::buildSVFModule(m);
  SVF::SVFIRBuilder builder;
  SVF::SVFIR *pag = builder.build();
  State->pta = SVF::AndersenWaveDiff::createAndersenWaveDiff(pag);
}

std::vector<Function *> SVFResolver::resolve(CallBase &cb) {
  SVF::PointerAnalysis *pta = State->pta;
  auto *ms = SVF::LLVMModuleSet::getLLVMModuleSet();
  SVF::CallICFGNode *cs = ms->getCallICFGNode(&cb);
  if (cs && pta->getCallGraph()->hasIndCSCallees(cs)) {
    const auto &callees = pta->getIndCSCallees(cs);
    std::vector<Function *> out;
    for (const SVF::FunObjVar *f : callees)
      if (Function *lf = Mod->getFunction(f->getName()))
        out.push_back(lf);
    if (!out.empty())
      return out;
  }
  // SVF could not resolve this site -> stay sound via type-based.
  return Fallback.resolve(cb);
}

SVFResolver::~SVFResolver() { SVF::LLVMModuleSet::releaseLLVMModuleSet(); }

} // namespace reach

#endif // REACHABILITY_ENABLE_SVF
