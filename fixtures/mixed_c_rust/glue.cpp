// Mimics the libFuzzer driver glue that cargo-fuzz generates: a C++
// LLVMFuzzerTestOneInput that forwards into the Rust entry by C ABI symbol name.
#include <cstddef>
#include <cstdint>

extern "C" int rust_fuzzer_test_input(const uint8_t *data, size_t len);

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t len) {
    return rust_fuzzer_test_input(data, len);
}
