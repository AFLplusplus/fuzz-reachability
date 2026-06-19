#include <stddef.h>
#include <stdint.h>

int used_b(int x) { return x + 1; }
int used_a(int x) { return used_b(x) * 2; }
int dead_fn(int x) { return x - 100; } /* never called */

int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) {
    if (n)
        return used_a((int)d[0]);
    return 0;
}
