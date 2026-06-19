#include <stddef.h>
#include <stdint.h>

typedef int (*fn)(int);

int handler_a(int x) { return x + 1; }
int handler_b(int x) { return x * 2; }
int unused_handler(int x) { return x - 1; } /* address-taken, never called directly */
int truly_dead(int x) { return 0; }         /* never address-taken, never called */

static fn table[3] = {handler_a, handler_b, unused_handler};

int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) {
    fn f = table[n % 3];
    return f(n ? d[0] : 0);
}
