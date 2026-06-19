#include "Toolchain.h"
#include "llvm/Config/llvm-config.h"

namespace reach {
int linkedLLVMMajor() { return LLVM_VERSION_MAJOR; }
}
