// A small C++ fuzz target built with CMake. Exercises virtual dispatch so the
// analyzer's indirect-call resolution has something to chew on.
#include <cstddef>
#include <cstdint>

struct Codec {
    virtual int decode(const uint8_t *d, size_t n) = 0;
    virtual ~Codec() {}
};
struct Raw : Codec {
    int decode(const uint8_t *d, size_t n) override { return n ? d[0] : 0; }
};
struct Xor : Codec {
    int decode(const uint8_t *d, size_t n) override { return n ? (d[0] ^ 0x5a) : 0; }
};

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    Codec *c = (size & 1) ? static_cast<Codec *>(new Raw())
                          : static_cast<Codec *>(new Xor());
    int r = c->decode(data, size); // virtual dispatch -> indirect call
    delete c;
    return r;
}

// A trivial driver so the project links as an executable (real fuzz targets get
// their main() from libFuzzer). Reachability is rooted at the fuzz entry, so
// main() itself is reported unreachable-from-entry.
int main() {
    const uint8_t b[1] = {0};
    return LLVMFuzzerTestOneInput(b, sizeof b);
}
