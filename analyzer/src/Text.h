#pragma once

#include "llvm/ADT/StringRef.h"
#include <string>

namespace reach {

inline std::string sanitizeUtf8(llvm::StringRef input) {
  std::string out;
  const auto *data = reinterpret_cast<const unsigned char *>(input.data());
  size_t i = 0;
  while (i < input.size()) {
    unsigned char lead = data[i];
    if (lead < 0x80) {
      out.push_back(static_cast<char>(lead));
      ++i;
      continue;
    }
    size_t length = 0;
    if (lead >= 0xc2 && lead <= 0xdf)
      length = 2;
    else if (lead >= 0xe0 && lead <= 0xef)
      length = 3;
    else if (lead >= 0xf0 && lead <= 0xf4)
      length = 4;
    bool valid = length != 0 && i + length <= input.size();
    for (size_t j = 1; valid && j < length; ++j)
      valid = data[i + j] >= 0x80 && data[i + j] <= 0xbf;
    if (valid && lead == 0xe0)
      valid = data[i + 1] >= 0xa0;
    if (valid && lead == 0xed)
      valid = data[i + 1] <= 0x9f;
    if (valid && lead == 0xf0)
      valid = data[i + 1] >= 0x90;
    if (valid && lead == 0xf4)
      valid = data[i + 1] <= 0x8f;
    if (valid) {
      out.append(input.data() + i, length);
      i += length;
    } else {
      out.append("\xef\xbf\xbd");
      ++i;
    }
  }
  return out;
}

}
