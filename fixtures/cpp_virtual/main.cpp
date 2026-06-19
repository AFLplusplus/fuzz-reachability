#include <cstddef>
#include <cstdint>

struct Base {
    virtual int run(int) = 0;
    virtual ~Base() {}
};
struct A : Base {
    int run(int x) override { return x + 1; }
};
struct B : Base {
    int run(int x) override { return x * 2; }
};

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *d, size_t n) {
    Base *b = (n & 1) ? static_cast<Base *>(new A()) : static_cast<Base *>(new B());
    int r = b->run(n ? d[0] : 0); // virtual dispatch -> indirect call
    delete b;
    return r;
}
