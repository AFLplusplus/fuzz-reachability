#include <algorithm>
#include <array>
#include <atomic>
#include <csetjmp>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <coroutine>
#include <exception>
#include <functional>
#include <future>
#include <iterator>
#include <memory>
#include <memory_resource>
#include <new>
#include <numeric>
#include <stdexcept>
#include <string>
#include <thread>
#include <tuple>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#if defined(__GNUC__) || defined(__clang__)
#define CFG_NOINLINE __attribute__((noinline))
#define CFG_USED __attribute__((used))
#else
#define CFG_NOINLINE __declspec(noinline)
#define CFG_USED
#endif

#if defined(__GNUC__) || defined(__clang__)
#define CFG_MAYBE_UNUSED __attribute__((unused))
#else
#define CFG_MAYBE_UNUSED
#endif

static volatile std::uint64_t call_type_sink = 0;
static volatile std::uintptr_t call_type_pointer_sink = 0;

CFG_NOINLINE static void sink_add(std::uint64_t v) {
    call_type_sink = (call_type_sink * 1315423911ULL) ^ (v + 0x9e3779b97f4a7c15ULL);
}

CFG_NOINLINE static void sink_ptr(const void *p) {
    call_type_pointer_sink = reinterpret_cast<std::uintptr_t>(p);
}

struct fuzz_stream {
    const std::uint8_t *data;
    std::size_t size;
    std::size_t pos;

    std::uint8_t byte() {
        if (size == 0) {
            ++pos;
            return static_cast<std::uint8_t>(pos * 17U + 3U);
        }
        std::uint8_t v = data[pos % size];
        ++pos;
        return v;
    }

    int small_int() {
        return static_cast<int>(byte());
    }

    std::size_t index(std::size_t modulo) {
        return modulo == 0 ? 0 : static_cast<std::size_t>(byte()) % modulo;
    }
};

extern "C" CFG_USED CFG_NOINLINE int redherings_never_selected_function_pointer(int x) {
    sink_add(0xA0000001ULL + static_cast<unsigned>(x));
    return x ^ 0x1111;
}

extern "C" CFG_USED CFG_NOINLINE int redherings_never_selected_virtual_target(int x) {
    sink_add(0xA0000002ULL + static_cast<unsigned>(x));
    return x ^ 0x2222;
}

extern "C" CFG_USED CFG_NOINLINE int redherings_opaque_false_branch(int x) {
    sink_add(0xA0000003ULL + static_cast<unsigned>(x));
    return x ^ 0x3333;
}

extern "C" CFG_USED CFG_NOINLINE int redherings_uncalled_dlsym_shape(int x) {
    sink_add(0xA0000004ULL + static_cast<unsigned>(x));
    return x ^ 0x4444;
}

extern "C" CFG_USED CFG_NOINLINE int unreachable_plain_leaf(int x) {
    sink_add(0xB0000001ULL + static_cast<unsigned>(x));
    return x + 1001;
}

extern "C" CFG_USED CFG_NOINLINE int unreachable_function_pointer_target(int x) {
    sink_add(0xB0000002ULL + static_cast<unsigned>(x));
    return x + 1002;
}

extern "C" CFG_USED CFG_NOINLINE int unreachable_recursive_cycle_b(int);
extern "C" CFG_USED CFG_NOINLINE int unreachable_recursive_cycle_a(int x) {
    return x <= 0 ? 7 : unreachable_recursive_cycle_b(x - 1);
}
extern "C" CFG_USED CFG_NOINLINE int unreachable_recursive_cycle_b(int x) {
    return x <= 0 ? 11 : unreachable_recursive_cycle_a(x - 1);
}

extern "C" CFG_USED CFG_NOINLINE int unreachable_cold_virtual_anchor(int x) {
    sink_add(0xB0000003ULL + static_cast<unsigned>(x));
    return x + 1003;
}

CFG_NOINLINE static int function_pointer_add(int x) {
    sink_add(0x10100000ULL + static_cast<unsigned>(x));
    return x + 1;
}

CFG_NOINLINE static int function_pointer_xor(int x) {
    sink_add(0x10100010ULL + static_cast<unsigned>(x));
    return x ^ 0x55;
}

CFG_NOINLINE static int function_pointer_mul(int x) {
    sink_add(0x10100020ULL + static_cast<unsigned>(x));
    return x * 3 + 1;
}

CFG_NOINLINE static int function_pointer_returned_target(int x) {
    sink_add(0x10100030ULL + static_cast<unsigned>(x));
    return x - 9;
}

using function_pointer_sig = int (*)(int);

CFG_NOINLINE static function_pointer_sig function_pointer_returner(std::uint8_t selector) {
    if ((selector & 1U) != 0U) {
        return function_pointer_returned_target;
    }
    return function_pointer_xor;
}

CFG_NOINLINE static int function_pointer_entry(fuzz_stream &in) {
    function_pointer_sig fp = (in.byte() & 1U) ? function_pointer_add : function_pointer_mul;
    int a = fp(in.small_int());
    function_pointer_sig returned = function_pointer_returner(in.byte());
    int b = returned(a);
    return b;
}

CFG_NOINLINE static int function_pointer_table_entry(fuzz_stream &in) {
    static function_pointer_sig table[] = {
        function_pointer_add,
        function_pointer_xor,
        function_pointer_mul,
        redherings_never_selected_function_pointer
    };
    std::size_t idx = in.index(3);
    return table[idx](in.small_int());
}

struct struct_function_pointer_object {
    int base;
};

CFG_NOINLINE static int struct_function_pointer_increment(void *self, int x) {
    auto *obj = static_cast<struct_function_pointer_object *>(self);
    sink_add(0x10200000ULL + static_cast<unsigned>(x));
    return obj->base + x + 1;
}

CFG_NOINLINE static int struct_function_pointer_decrement(void *self, int x) {
    auto *obj = static_cast<struct_function_pointer_object *>(self);
    sink_add(0x10200010ULL + static_cast<unsigned>(x));
    return obj->base - x - 1;
}

CFG_NOINLINE static void struct_function_pointer_destroy(void *self) {
    sink_add(0x10200020ULL);
    delete static_cast<struct_function_pointer_object *>(self);
}

struct struct_function_pointer_interface {
    void *self;
    int (*call)(void *, int);
    void (*destroy)(void *);
};

CFG_NOINLINE static int struct_function_pointer_entry(fuzz_stream &in) {
    struct_function_pointer_interface iface{
        new struct_function_pointer_object{in.small_int()},
        (in.byte() & 1U) ? struct_function_pointer_increment : struct_function_pointer_decrement,
        struct_function_pointer_destroy
    };
    int out = iface.call(iface.self, in.small_int());
    iface.destroy(iface.self);
    return out;
}

CFG_NOINLINE static int c_callback_qsort_compare(const void *a, const void *b) {
    int ai = *static_cast<const int *>(a);
    int bi = *static_cast<const int *>(b);
    sink_add(0x10300000ULL + static_cast<unsigned>(ai ^ bi));
    return (ai > bi) - (ai < bi);
}

CFG_NOINLINE static int c_callback_bsearch_compare(const void *key, const void *elem) {
    int k = *static_cast<const int *>(key);
    int e = *static_cast<const int *>(elem);
    sink_add(0x10300010ULL + static_cast<unsigned>(k ^ e));
    return (k > e) - (k < e);
}

CFG_NOINLINE static int c_callback_entry(fuzz_stream &in) {
    int values[6] = {in.small_int(), in.small_int(), in.small_int(), in.small_int(), in.small_int(), in.small_int()};
    std::qsort(values, 6, sizeof(values[0]), c_callback_qsort_compare);
    int key = values[in.index(6)];
    void *found = std::bsearch(&key, values, 6, sizeof(values[0]), c_callback_bsearch_compare);
    sink_ptr(found);
    return found ? *static_cast<int *>(found) : -1;
}

struct virtual_dispatch_base {
    virtual ~virtual_dispatch_base() = default;
    virtual int virtual_dispatch_call(int x) = 0;
};

struct virtual_dispatch_left final : virtual_dispatch_base {
    CFG_NOINLINE int virtual_dispatch_call(int x) override {
        sink_add(0x10400000ULL + static_cast<unsigned>(x));
        return x + 13;
    }
};

struct virtual_dispatch_right final : virtual_dispatch_base {
    CFG_NOINLINE int virtual_dispatch_call(int x) override {
        sink_add(0x10400010ULL + static_cast<unsigned>(x));
        return x - 13;
    }
};

CFG_NOINLINE static std::unique_ptr<virtual_dispatch_base> virtual_dispatch_factory(std::uint8_t selector) {
    if ((selector & 1U) != 0U) {
        return std::unique_ptr<virtual_dispatch_base>(new virtual_dispatch_left());
    }
    return std::unique_ptr<virtual_dispatch_base>(new virtual_dispatch_right());
}

CFG_NOINLINE static int virtual_dispatch_entry(fuzz_stream &in) {
    std::unique_ptr<virtual_dispatch_base> obj = virtual_dispatch_factory(in.byte());
    return obj->virtual_dispatch_call(in.small_int());
}

struct virtual_destructor_base {
    CFG_NOINLINE virtual ~virtual_destructor_base() {
        sink_add(0x10500000ULL);
    }
    virtual int virtual_destructor_touch(int x) = 0;
};

struct virtual_destructor_derived final : virtual_destructor_base {
    CFG_NOINLINE ~virtual_destructor_derived() override {
        sink_add(0x10500010ULL);
    }
    CFG_NOINLINE int virtual_destructor_touch(int x) override {
        sink_add(0x10500020ULL + static_cast<unsigned>(x));
        return x + 21;
    }
};

CFG_NOINLINE static int virtual_destructor_entry(fuzz_stream &in) {
    virtual_destructor_base *obj = new virtual_destructor_derived();
    int out = obj->virtual_destructor_touch(in.small_int());
    delete obj;
    return out;
}

struct multiple_inheritance_left {
    virtual ~multiple_inheritance_left() = default;
    virtual int multiple_inheritance_left_call(int x) = 0;
};

struct multiple_inheritance_right {
    virtual ~multiple_inheritance_right() = default;
    virtual int multiple_inheritance_right_call(int x) = 0;
};

struct multiple_inheritance_derived final : multiple_inheritance_left, multiple_inheritance_right {
    CFG_NOINLINE int multiple_inheritance_left_call(int x) override {
        sink_add(0x10600000ULL + static_cast<unsigned>(x));
        return x + 31;
    }
    CFG_NOINLINE int multiple_inheritance_right_call(int x) override {
        sink_add(0x10600010ULL + static_cast<unsigned>(x));
        return x + 41;
    }
};

CFG_NOINLINE static int multiple_inheritance_virtual_entry(fuzz_stream &in) {
    multiple_inheritance_derived obj;
    multiple_inheritance_left *left = &obj;
    multiple_inheritance_right *right = &obj;
    return left->multiple_inheritance_left_call(in.small_int()) ^ right->multiple_inheritance_right_call(in.small_int());
}

struct covariant_return_base {
    virtual ~covariant_return_base() = default;
    CFG_NOINLINE virtual covariant_return_base *covariant_return_clone_like() {
        sink_add(0x10700000ULL);
        return this;
    }
    CFG_NOINLINE virtual int covariant_return_value(int x) {
        sink_add(0x10700010ULL + static_cast<unsigned>(x));
        return x;
    }
};

struct covariant_return_derived final : covariant_return_base {
    CFG_NOINLINE covariant_return_derived *covariant_return_clone_like() override {
        sink_add(0x10700020ULL);
        return this;
    }
    CFG_NOINLINE int covariant_return_value(int x) override {
        sink_add(0x10700030ULL + static_cast<unsigned>(x));
        return x + 51;
    }
};

CFG_NOINLINE static int covariant_return_entry(fuzz_stream &in) {
    covariant_return_derived d;
    covariant_return_base *b = &d;
    covariant_return_base *c = b->covariant_return_clone_like();
    return c->covariant_return_value(in.small_int());
}

struct pointer_to_member_object {
    int bias;

    CFG_NOINLINE int pointer_to_member_nonvirtual(int x) {
        sink_add(0x10800000ULL + static_cast<unsigned>(x));
        return bias + x + 61;
    }

    CFG_NOINLINE virtual int pointer_to_member_virtual(int x) {
        sink_add(0x10800010ULL + static_cast<unsigned>(x));
        return bias - x - 61;
    }

    virtual ~pointer_to_member_object() = default;
};

struct pointer_to_member_derived final : pointer_to_member_object {
    CFG_NOINLINE int pointer_to_member_virtual(int x) override {
        sink_add(0x10800020ULL + static_cast<unsigned>(x));
        return bias ^ x ^ 0x61;
    }
};

CFG_NOINLINE static int pointer_to_member_entry(fuzz_stream &in) {
    pointer_to_member_derived obj;
    obj.bias = in.small_int();
    int (pointer_to_member_object::*pmf_nonvirtual)(int) = &pointer_to_member_object::pointer_to_member_nonvirtual;
    int (pointer_to_member_object::*pmf_virtual)(int) = &pointer_to_member_object::pointer_to_member_virtual;
    pointer_to_member_object *base = &obj;
    int a = (obj.*pmf_nonvirtual)(in.small_int());
    int b = (base->*pmf_virtual)(in.small_int());
    return a ^ b;
}

CFG_NOINLINE static int std_function_target_body(int captured, int x) {
    sink_add(0x10900000ULL + static_cast<unsigned>(captured ^ x));
    return captured + x + 71;
}

CFG_NOINLINE static int std_function_entry(fuzz_stream &in) {
    int captured = in.small_int();
    std::function<int(int)> fn = [captured](int x) CFG_NOINLINE {
        return std_function_target_body(captured, x);
    };
    return fn(in.small_int());
}

CFG_NOINLINE static int std_bind_target(int a, int b, int c) {
    sink_add(0x10A00000ULL + static_cast<unsigned>(a ^ b ^ c));
    return (a * 3) + (b * 5) + c;
}

CFG_NOINLINE static int std_bind_entry(fuzz_stream &in) {
    auto bound = std::bind(std_bind_target, in.small_int(), std::placeholders::_1, in.small_int());
    return bound(in.small_int());
}

struct std_mem_fn_object {
    int base;
    CFG_NOINLINE int std_mem_fn_method(int x) {
        sink_add(0x10B00000ULL + static_cast<unsigned>(x));
        return base + x + 81;
    }
};

CFG_NOINLINE static int std_mem_fn_entry(fuzz_stream &in) {
    std_mem_fn_object obj{in.small_int()};
    auto mf = std::mem_fn(&std_mem_fn_object::std_mem_fn_method);
    return mf(obj, in.small_int());
}

CFG_NOINLINE static int std_invoke_free_function(int x) {
    sink_add(0x10C00000ULL + static_cast<unsigned>(x));
    return x + 91;
}

struct std_invoke_object {
    int base;
    CFG_NOINLINE int std_invoke_member(int x) {
        sink_add(0x10C00010ULL + static_cast<unsigned>(x));
        return base ^ x;
    }
};

CFG_NOINLINE static int std_invoke_entry(fuzz_stream &in) {
    std_invoke_object obj{in.small_int()};
    int a = std::invoke(std_invoke_free_function, in.small_int());
    int b = std::invoke(&std_invoke_object::std_invoke_member, obj, in.small_int());
    return a ^ b;
}

struct lambda_conversion_context {
    static CFG_NOINLINE int lambda_conversion_body(int x) {
        sink_add(0x10D00000ULL + static_cast<unsigned>(x));
        return x + 101;
    }
};

CFG_NOINLINE static int lambda_conversion_entry(fuzz_stream &in) {
    using lambda_conversion_sig = int (*)(int);
    lambda_conversion_sig fp = +[](int x) -> int {
        return lambda_conversion_context::lambda_conversion_body(x);
    };
    return fp(in.small_int());
}

struct function_object_call_operator {
    int captured;
    CFG_NOINLINE int operator()(int x) const {
        sink_add(0x10E00000ULL + static_cast<unsigned>(x));
        return captured + x + 111;
    }
};

CFG_NOINLINE static int function_object_operator_entry(fuzz_stream &in) {
    function_object_call_operator f{in.small_int()};
    return f(in.small_int());
}

struct overloaded_operator_box {
    int value;
};

CFG_NOINLINE static overloaded_operator_box operator+(overloaded_operator_box a, overloaded_operator_box b) {
    sink_add(0x10F00000ULL + static_cast<unsigned>(a.value ^ b.value));
    return overloaded_operator_box{a.value + b.value + 121};
}

CFG_NOINLINE static bool operator<(overloaded_operator_box a, overloaded_operator_box b) {
    sink_add(0x10F00010ULL + static_cast<unsigned>(a.value ^ b.value));
    return a.value < b.value;
}

CFG_NOINLINE static int overloaded_operator_entry(fuzz_stream &in) {
    overloaded_operator_box a{in.small_int()};
    overloaded_operator_box b{in.small_int()};
    overloaded_operator_box c = a + b;
    return (a < b) ? c.value : -c.value;
}

struct conversion_operator_box {
    int value;
    CFG_NOINLINE explicit operator int() const {
        sink_add(0x11000000ULL + static_cast<unsigned>(value));
        return value + 131;
    }
};

CFG_NOINLINE static int conversion_operator_entry(fuzz_stream &in) {
    conversion_operator_box box{in.small_int()};
    return static_cast<int>(box);
}

struct constructor_destructor_box {
    int value;

    CFG_NOINLINE explicit constructor_destructor_box(int v) : value(v) {
        sink_add(0x11100000ULL + static_cast<unsigned>(value));
    }

    CFG_NOINLINE constructor_destructor_box(const constructor_destructor_box &other) : value(other.value + 1) {
        sink_add(0x11100010ULL + static_cast<unsigned>(value));
    }

    CFG_NOINLINE constructor_destructor_box(constructor_destructor_box &&other) noexcept : value(other.value + 2) {
        sink_add(0x11100020ULL + static_cast<unsigned>(value));
        other.value = 0;
    }

    CFG_NOINLINE ~constructor_destructor_box() {
        sink_add(0x11100030ULL + static_cast<unsigned>(value));
    }
};

CFG_NOINLINE static constructor_destructor_box constructor_destructor_make(int x) {
    constructor_destructor_box local(x);
    constructor_destructor_box copied(local);
    return copied;
}

CFG_NOINLINE static int constructor_destructor_entry(fuzz_stream &in) {
    constructor_destructor_box made = constructor_destructor_make(in.small_int());
    constructor_destructor_box moved(std::move(made));
    return moved.value;
}

struct overloaded_new_delete_box {
    int value;

    CFG_NOINLINE explicit overloaded_new_delete_box(int v) : value(v) {
        sink_add(0x11200000ULL + static_cast<unsigned>(value));
    }

    CFG_NOINLINE ~overloaded_new_delete_box() {
        sink_add(0x11200010ULL + static_cast<unsigned>(value));
    }

    CFG_NOINLINE static void *operator new(std::size_t n) {
        sink_add(0x11200020ULL + static_cast<unsigned>(n));
        return ::operator new(n);
    }

    CFG_NOINLINE static void operator delete(void *p) noexcept {
        sink_add(0x11200030ULL);
        ::operator delete(p);
    }

    CFG_NOINLINE static void operator delete(void *p, std::size_t) noexcept {
        sink_add(0x11200040ULL);
        ::operator delete(p);
    }
};

CFG_NOINLINE static int overloaded_new_delete_entry(fuzz_stream &in) {
    overloaded_new_delete_box *box = new overloaded_new_delete_box(in.small_int());
    int out = box->value;
    delete box;
    return out;
}

CFG_NOINLINE static int placement_new_entry(fuzz_stream &in) {
    alignas(constructor_destructor_box) unsigned char storage[sizeof(constructor_destructor_box)];
    auto *box = new (storage) constructor_destructor_box(in.small_int());
    int out = box->value;
    box->~constructor_destructor_box();
    return out;
}

CFG_NOINLINE static int user_defined_literal_body(unsigned long long v) {
    sink_add(0x11300000ULL + static_cast<unsigned>(v));
    return static_cast<int>((v * 7ULL) & 0xffU);
}

CFG_NOINLINE static int operator"" _cfgcall(unsigned long long v) {
    return user_defined_literal_body(v);
}

CFG_NOINLINE static int user_defined_literal_entry(fuzz_stream &in) {
    return 123_cfgcall ^ in.small_int();
}

struct range_for_iterator {
    int current;
    int limit;

    CFG_NOINLINE int operator*() const {
        sink_add(0x11400000ULL + static_cast<unsigned>(current));
        return current;
    }

    CFG_NOINLINE range_for_iterator &operator++() {
        sink_add(0x11400010ULL + static_cast<unsigned>(current));
        ++current;
        return *this;
    }

    CFG_NOINLINE bool operator!=(const range_for_iterator &other) const {
        sink_add(0x11400020ULL + static_cast<unsigned>(current ^ other.current));
        return current != other.current;
    }
};

struct range_for_object {
    int first;
    int last;

    CFG_NOINLINE range_for_iterator begin() {
        sink_add(0x11400030ULL + static_cast<unsigned>(first));
        return range_for_iterator{first, last};
    }

    CFG_NOINLINE range_for_iterator end() {
        sink_add(0x11400040ULL + static_cast<unsigned>(last));
        return range_for_iterator{last, last};
    }
};

CFG_NOINLINE static int range_for_entry(fuzz_stream &in) {
    int start = in.small_int() & 3;
    range_for_object range{start, start + 3};
    int total = 0;
    for (int v : range) {
        total += v;
    }
    return total;
}

namespace adl_dispatch_namespace {
struct adl_dispatch_token {
    int value;
};

CFG_NOINLINE int adl_dispatch_hook(adl_dispatch_token t, int x) {
    sink_add(0x11500000ULL + static_cast<unsigned>(t.value ^ x));
    return t.value + x + 141;
}
}

template <typename T>
CFG_NOINLINE static int adl_dispatch_template_call(T t, int x) {
    return adl_dispatch_hook(t, x);
}

CFG_NOINLINE static int adl_dispatch_entry(fuzz_stream &in) {
    adl_dispatch_namespace::adl_dispatch_token t{in.small_int()};
    return adl_dispatch_template_call(t, in.small_int());
}

struct tag_invoke_cpo {
    template <typename T>
    CFG_NOINLINE auto operator()(T &&t, int x) const -> decltype(tag_invoke(*this, std::forward<T>(t), x)) {
        return tag_invoke(*this, std::forward<T>(t), x);
    }
};

struct tag_invoke_target {
    int value;
};

CFG_NOINLINE static int tag_invoke(tag_invoke_cpo, tag_invoke_target t, int x) {
    sink_add(0x11600000ULL + static_cast<unsigned>(t.value ^ x));
    return t.value - x + 151;
}

CFG_NOINLINE static int tag_invoke_entry(fuzz_stream &in) {
    tag_invoke_cpo cpo;
    tag_invoke_target target{in.small_int()};
    return cpo(target, in.small_int());
}

namespace adl_begin_end_namespace {
struct adl_begin_end_iterator {
    int value;
    CFG_NOINLINE int operator*() const {
        sink_add(0x11700000ULL + static_cast<unsigned>(value));
        return value;
    }
    CFG_NOINLINE adl_begin_end_iterator &operator++() {
        sink_add(0x11700010ULL + static_cast<unsigned>(value));
        ++value;
        return *this;
    }
};

CFG_NOINLINE bool operator!=(adl_begin_end_iterator a, adl_begin_end_iterator b) {
    sink_add(0x11700020ULL + static_cast<unsigned>(a.value ^ b.value));
    return a.value != b.value;
}

struct adl_begin_end_range {
    int first;
    int last;
};

CFG_NOINLINE adl_begin_end_iterator begin(adl_begin_end_range &r) {
    sink_add(0x11700030ULL + static_cast<unsigned>(r.first));
    return adl_begin_end_iterator{r.first};
}

CFG_NOINLINE adl_begin_end_iterator end(adl_begin_end_range &r) {
    sink_add(0x11700040ULL + static_cast<unsigned>(r.last));
    return adl_begin_end_iterator{r.last};
}
}

CFG_NOINLINE static int adl_begin_end_entry(fuzz_stream &in) {
    int start = in.small_int() & 3;
    adl_begin_end_namespace::adl_begin_end_range range{start, start + 2};
    using std::begin;
    using std::end;
    auto it = begin(range);
    auto last = end(range);
    int total = 0;
    for (; it != last; ++it) {
        total += *it;
    }
    return total;
}

struct variant_visit_visitor {
    CFG_NOINLINE int operator()(int v) const {
        sink_add(0x11800000ULL + static_cast<unsigned>(v));
        return v + 161;
    }

    CFG_NOINLINE int operator()(const std::string &s) const {
        sink_add(0x11800010ULL + static_cast<unsigned>(s.size()));
        return static_cast<int>(s.size()) + 162;
    }

    CFG_NOINLINE int operator()(double d) const {
        int v = static_cast<int>(d);
        sink_add(0x11800020ULL + static_cast<unsigned>(v));
        return v + 163;
    }
};

CFG_NOINLINE static int variant_visit_entry(fuzz_stream &in) {
    std::variant<int, std::string, double> v;
    switch (in.index(3)) {
        case 0: v = in.small_int(); break;
        case 1: v = std::string(static_cast<std::size_t>((in.byte() & 3U) + 1U), 'x'); break;
        default: v = static_cast<double>(in.small_int()) + 0.5; break;
    }
    return std::visit(variant_visit_visitor{}, v);
}

struct smart_pointer_unique_deleter {
    CFG_NOINLINE void operator()(int *p) const noexcept {
        sink_add(0x11900000ULL + static_cast<unsigned>(p ? *p : 0));
        delete p;
    }
};

CFG_NOINLINE static void smart_pointer_shared_deleter(int *p) noexcept {
    sink_add(0x11900010ULL + static_cast<unsigned>(p ? *p : 0));
    delete p;
}

CFG_NOINLINE static int smart_pointer_deleter_entry(fuzz_stream &in) {
    int a = in.small_int();
    int b = in.small_int();
    std::unique_ptr<int, smart_pointer_unique_deleter> up(new int(a));
    std::shared_ptr<int> sp(new int(b), smart_pointer_shared_deleter);
    int out = *up ^ *sp;
    up.reset();
    sp.reset();
    return out;
}

class pmr_virtual_resource final : public std::pmr::memory_resource {
    std::pmr::memory_resource *upstream_;

public:
    explicit pmr_virtual_resource(std::pmr::memory_resource *upstream) : upstream_(upstream) {}

private:
    CFG_NOINLINE void *do_allocate(std::size_t bytes, std::size_t alignment) override {
        sink_add(0x11A00000ULL + static_cast<unsigned>(bytes ^ alignment));
        return upstream_->allocate(bytes, alignment);
    }

    CFG_NOINLINE void do_deallocate(void *p, std::size_t bytes, std::size_t alignment) override {
        sink_add(0x11A00010ULL + static_cast<unsigned>(bytes ^ alignment));
        upstream_->deallocate(p, bytes, alignment);
    }

    CFG_NOINLINE bool do_is_equal(const std::pmr::memory_resource &other) const noexcept override {
        sink_add(0x11A00020ULL);
        return this == &other;
    }
};

CFG_NOINLINE static int pmr_virtual_resource_entry(fuzz_stream &in) {
    pmr_virtual_resource resource(std::pmr::new_delete_resource());
    std::pmr::vector<int> values(&resource);
    values.reserve(4);
    values.push_back(in.small_int());
    values.push_back(in.small_int());
    values.push_back(in.small_int());
    return std::accumulate(values.begin(), values.end(), 0);
}

struct exception_unwind_guard {
    int value;
    CFG_NOINLINE explicit exception_unwind_guard(int v) : value(v) {
        sink_add(0x11B00000ULL + static_cast<unsigned>(value));
    }
    CFG_NOINLINE ~exception_unwind_guard() {
        sink_add(0x11B00010ULL + static_cast<unsigned>(value));
    }
};

CFG_NOINLINE static void exception_unwind_thrower(int x) {
    exception_unwind_guard guard(x);
    sink_add(0x11B00020ULL + static_cast<unsigned>(x));
    throw std::runtime_error("cfg exception path");
}

CFG_NOINLINE static int exception_unwind_entry(fuzz_stream &in) {
    try {
        exception_unwind_thrower(in.small_int());
    } catch (const std::exception &e) {
        sink_add(0x11B00030ULL + static_cast<unsigned>(e.what()[0]));
        return 171;
    }
    return 0;
}

static std::jmp_buf setjmp_longjmp_buffer;

CFG_NOINLINE static void setjmp_longjmp_jump(int x) {
    sink_add(0x11C00000ULL + static_cast<unsigned>(x));
    std::longjmp(setjmp_longjmp_buffer, (x & 7) + 1);
}

CFG_NOINLINE static int setjmp_longjmp_entry(fuzz_stream &in) {
    int value = setjmp(setjmp_longjmp_buffer);
    if (value == 0) {
        setjmp_longjmp_jump(in.small_int());
    }
    sink_add(0x11C00010ULL + static_cast<unsigned>(value));
    return value;
}

CFG_NOINLINE static int computed_goto_fallback(int x) {
    sink_add(0x11D00000ULL + static_cast<unsigned>(x));
    return x + 181;
}

CFG_NOINLINE static int computed_goto_entry(fuzz_stream &in) {
#if defined(__GNUC__) || defined(__clang__)
    static void *labels[] = {&&computed_goto_l0, &&computed_goto_l1, &&computed_goto_l2, &&computed_goto_redherring};
    goto *labels[in.index(3)];
computed_goto_l0:
    sink_add(0x11D00010ULL);
    return in.small_int() + 191;
computed_goto_l1:
    sink_add(0x11D00020ULL);
    return in.small_int() + 192;
computed_goto_l2:
    sink_add(0x11D00030ULL);
    return in.small_int() + 193;
computed_goto_redherring:
    return redherings_opaque_false_branch(in.small_int());
#else
    return computed_goto_fallback(in.small_int());
#endif
}

struct coroutine_resume_task {
    struct promise_type {
        CFG_NOINLINE coroutine_resume_task get_return_object() {
            return coroutine_resume_task{std::coroutine_handle<promise_type>::from_promise(*this)};
        }
        CFG_NOINLINE std::suspend_always initial_suspend() noexcept {
            sink_add(0x11E00000ULL);
            return {};
        }
        CFG_NOINLINE std::suspend_always final_suspend() noexcept {
            sink_add(0x11E00010ULL);
            return {};
        }
        CFG_NOINLINE void return_void() noexcept {
            sink_add(0x11E00020ULL);
        }
        CFG_NOINLINE void unhandled_exception() {
            std::terminate();
        }
    };

    std::coroutine_handle<promise_type> handle;

    CFG_NOINLINE explicit coroutine_resume_task(std::coroutine_handle<promise_type> h) : handle(h) {}
    coroutine_resume_task(const coroutine_resume_task &) = delete;
    coroutine_resume_task &operator=(const coroutine_resume_task &) = delete;
    CFG_NOINLINE coroutine_resume_task(coroutine_resume_task &&other) noexcept : handle(other.handle) {
        other.handle = {};
    }
    CFG_NOINLINE ~coroutine_resume_task() {
        if (handle) {
            handle.destroy();
        }
    }
    CFG_NOINLINE void coroutine_resume_resume() {
        if (handle && !handle.done()) {
            handle.resume();
        }
    }
};

struct coroutine_awaitable_object {
    int value;
    CFG_NOINLINE bool await_ready() const noexcept {
        sink_add(0x11E00030ULL + static_cast<unsigned>(value));
        return false;
    }
    CFG_NOINLINE void await_suspend(std::coroutine_handle<>) const noexcept {
        sink_add(0x11E00040ULL + static_cast<unsigned>(value));
    }
    CFG_NOINLINE int await_resume() const noexcept {
        sink_add(0x11E00050ULL + static_cast<unsigned>(value));
        return value + 201;
    }
};

CFG_NOINLINE static coroutine_resume_task coroutine_resume_make(int x) {
    sink_add(0x11E00060ULL + static_cast<unsigned>(x));
    int y = co_await coroutine_awaitable_object{x};
    sink_add(0x11E00070ULL + static_cast<unsigned>(y));
}

CFG_NOINLINE static int coroutine_resume_entry(fuzz_stream &in) {
    coroutine_resume_task task = coroutine_resume_make(in.small_int());
    task.coroutine_resume_resume();
    task.coroutine_resume_resume();
    return 211;
}

CFG_NOINLINE static void thread_entry_worker(int x) {
    sink_add(0x11F00000ULL + static_cast<unsigned>(x));
}

CFG_NOINLINE static int thread_entry_entry(fuzz_stream &in) {
    int x = in.small_int();
    std::thread t(thread_entry_worker, x);
    t.join();
    return x + 221;
}

CFG_NOINLINE static int async_future_worker(int x) {
    sink_add(0x12000000ULL + static_cast<unsigned>(x));
    return x + 231;
}

CFG_NOINLINE static int async_future_entry(fuzz_stream &in) {
    std::future<int> f = std::async(std::launch::deferred, async_future_worker, in.small_int());
    return f.get();
}

CFG_NOINLINE static int packaged_task_worker(int x) {
    sink_add(0x12100000ULL + static_cast<unsigned>(x));
    return x + 241;
}

CFG_NOINLINE static int packaged_task_entry(fuzz_stream &in) {
    std::packaged_task<int(int)> task(packaged_task_worker);
    std::future<int> future = task.get_future();
    task(in.small_int());
    return future.get();
}

static std::once_flag atexit_callback_once;

CFG_NOINLINE static void atexit_callback_handler() {
    sink_add(0x12200000ULL);
}

CFG_NOINLINE static int atexit_callback_entry(fuzz_stream &) {
    std::call_once(atexit_callback_once, []() CFG_NOINLINE {
        std::atexit(atexit_callback_handler);
    });
    return 251;
}

CFG_NOINLINE static void terminate_handler_custom() {
    sink_add(0x12300000ULL);
    std::_Exit(77);
}

CFG_NOINLINE static int terminate_handler_entry(fuzz_stream &) {
    auto old = std::set_terminate(terminate_handler_custom);
    std::set_terminate(old);
    return 252;
}

static volatile std::sig_atomic_t signal_callback_seen = 0;

CFG_NOINLINE static void signal_callback_handler(int sig) {
    signal_callback_seen = static_cast<std::sig_atomic_t>(signal_callback_seen + sig);
}

CFG_NOINLINE static int signal_callback_entry(fuzz_stream &) {
#if defined(SIGUSR1)
    using signal_handler_t = void (*)(int);
    signal_handler_t old = std::signal(SIGUSR1, signal_callback_handler);
    std::raise(SIGUSR1);
    std::signal(SIGUSR1, old);
    sink_add(0x12400000ULL + static_cast<unsigned>(signal_callback_seen));
    return static_cast<int>(signal_callback_seen);
#else
    sink_add(0x12400010ULL);
    return 0;
#endif
}

struct static_initialization_global_object {
    CFG_NOINLINE static_initialization_global_object() {
        sink_add(0x12500000ULL);
    }
    CFG_NOINLINE ~static_initialization_global_object() {
        sink_add(0x12500010ULL);
    }
    CFG_NOINLINE int static_initialization_touch(int x) {
        sink_add(0x12500020ULL + static_cast<unsigned>(x));
        return x + 261;
    }
};

static static_initialization_global_object static_initialization_global_instance;

CFG_NOINLINE static int static_initialization_entry(fuzz_stream &in) {
    return static_initialization_global_instance.static_initialization_touch(in.small_int());
}

struct thread_local_lifecycle_object {
    int value = 0;
    CFG_NOINLINE thread_local_lifecycle_object() {
        sink_add(0x12600000ULL);
    }
    CFG_NOINLINE ~thread_local_lifecycle_object() {
        sink_add(0x12600010ULL + static_cast<unsigned>(value));
    }
    CFG_NOINLINE int thread_local_lifecycle_touch(int x) {
        value ^= x;
        sink_add(0x12600020ULL + static_cast<unsigned>(value));
        return value + 271;
    }
};

thread_local thread_local_lifecycle_object thread_local_lifecycle_instance;

CFG_NOINLINE static int thread_local_lifecycle_entry(fuzz_stream &in) {
    return thread_local_lifecycle_instance.thread_local_lifecycle_touch(in.small_int());
}

#if defined(__GNUC__) || defined(__clang__)
CFG_NOINLINE static void runtime_constructor_array_entry() __attribute__((constructor));
CFG_NOINLINE static void runtime_constructor_array_entry() {
    sink_add(0x12700000ULL);
}

CFG_NOINLINE static void runtime_destructor_array_entry() __attribute__((destructor));
CFG_NOINLINE static void runtime_destructor_array_entry() {
    sink_add(0x12700010ULL);
}
#endif

CFG_NOINLINE static int import_indirect_strlen_entry(fuzz_stream &in) {
    char text[8] = {'a', 'b', static_cast<char>('c' + (in.byte() & 3U)), 0, 'x', 0, 0, 0};
    std::size_t n = std::strlen(text);
    sink_add(0x12800000ULL + static_cast<unsigned>(n));
    return static_cast<int>(n);
}

#if defined(__ELF__) && (defined(__GNUC__) || defined(__clang__))
extern "C" void *dlsym(void *, const char *) __attribute__((weak));
#ifndef RTLD_DEFAULT
#define RTLD_DEFAULT ((void *)0)
#endif
using dynamic_symbol_lookup_strchr_sig = char *(*)(const char *, int);

CFG_NOINLINE static int dynamic_symbol_lookup_entry(fuzz_stream &in) {
    if (!dlsym) {
        sink_add(0x12900000ULL);
        return 0;
    }
    void *sym = dlsym(RTLD_DEFAULT, "strchr");
    if (!sym) {
        sink_add(0x12900010ULL);
        return 0;
    }
    auto fn = reinterpret_cast<dynamic_symbol_lookup_strchr_sig>(sym);
    char buffer[8] = {'m', 'n', static_cast<char>('a' + (in.byte() % 4U)), 'z', 0, 0, 0, 0};
    char needle = buffer[in.index(4)];
    char *found = fn(buffer, needle);
    sink_ptr(found);
    return found ? static_cast<int>(*found) : -1;
}
#else
CFG_NOINLINE static int dynamic_symbol_lookup_entry(fuzz_stream &) {
    sink_add(0x12900020ULL);
    return 0;
}
#endif

#if defined(__ELF__) && (defined(__GNUC__) || defined(__clang__)) && !defined(__APPLE__)
extern "C" CFG_NOINLINE int ifunc_resolver_impl_a(int x) {
    sink_add(0x12A00000ULL + static_cast<unsigned>(x));
    return x + 281;
}

extern "C" CFG_NOINLINE int ifunc_resolver_impl_b(int x) {
    sink_add(0x12A00010ULL + static_cast<unsigned>(x));
    return x + 282;
}

using ifunc_resolver_sig = int (*)(int);

extern "C" CFG_NOINLINE ifunc_resolver_sig ifunc_resolver_selector() {
    return ifunc_resolver_impl_a;
}

extern "C" int ifunc_resolved_target(int) __attribute__((ifunc("ifunc_resolver_selector")));

CFG_NOINLINE static int ifunc_resolver_entry(fuzz_stream &in) {
    return ifunc_resolved_target(in.small_int());
}
#else
CFG_NOINLINE static int ifunc_resolver_entry(fuzz_stream &in) {
    sink_add(0x12A00020ULL + static_cast<unsigned>(in.small_int()));
    return 0;
}
#endif

struct sanitizer_coverage_shape_object {
    int value;
    CFG_NOINLINE int sanitizer_coverage_shape_touch(int x) {
        sink_add(0x12B00000ULL + static_cast<unsigned>(value ^ x));
        return value + x + 291;
    }
};

CFG_NOINLINE static int sanitizer_coverage_shape_entry(fuzz_stream &in) {
    sanitizer_coverage_shape_object obj{in.small_int()};
    int out = obj.sanitizer_coverage_shape_touch(in.small_int());
#if defined(__has_feature)
#if __has_feature(address_sanitizer)
    sink_add(0x12B00010ULL);
#endif
#endif
    return out;
}

struct control_block_erased_base {
    virtual ~control_block_erased_base() = default;
    virtual int control_block_erased_call(int x) = 0;
};

template <typename Callable>
struct control_block_erased_model final : control_block_erased_base {
    Callable callable;
    explicit control_block_erased_model(Callable c) : callable(std::move(c)) {}
    CFG_NOINLINE int control_block_erased_call(int x) override {
        sink_add(0x12C00000ULL + static_cast<unsigned>(x));
        return callable(x);
    }
};

CFG_NOINLINE static int control_block_erased_target(int x) {
    sink_add(0x12C00010ULL + static_cast<unsigned>(x));
    return x + 301;
}

CFG_NOINLINE static int control_block_erased_entry(fuzz_stream &in) {
    std::unique_ptr<control_block_erased_base> erased(
        new control_block_erased_model<function_pointer_sig>(control_block_erased_target));
    return erased->control_block_erased_call(in.small_int());
}

CFG_NOINLINE static bool redherings_opaque_false_predicate(const std::uint8_t *data, std::size_t size) {
    std::uintptr_t a = reinterpret_cast<std::uintptr_t>(data);
    std::uintptr_t b = reinterpret_cast<std::uintptr_t>(data);
    return (a != b) && (size == static_cast<std::size_t>(-1));
}

CFG_NOINLINE static int redherings_entry(fuzz_stream &in) {
    static function_pointer_sig table[] = {
        function_pointer_add,
        redherings_never_selected_function_pointer,
        function_pointer_mul
    };
    int out = table[(in.byte() & 1U) ? 2 : 0](in.small_int());
    if (redherings_opaque_false_predicate(in.data, in.size)) {
        out ^= redherings_opaque_false_branch(in.small_int());
        out ^= redherings_never_selected_virtual_target(in.small_int());
        out ^= redherings_uncalled_dlsym_shape(in.small_int());
    }
    return out;
}

extern "C" CFG_USED CFG_NOINLINE int LLVMFuzzerTestOneInput(const std::uint8_t *data, std::size_t size) {
    fuzz_stream in{data, size, 0};
    std::uint64_t total = 0;

    total ^= static_cast<std::uint64_t>(function_pointer_entry(in));
    total ^= static_cast<std::uint64_t>(function_pointer_table_entry(in));
    total ^= static_cast<std::uint64_t>(struct_function_pointer_entry(in));
    total ^= static_cast<std::uint64_t>(c_callback_entry(in));
    total ^= static_cast<std::uint64_t>(virtual_dispatch_entry(in));
    total ^= static_cast<std::uint64_t>(virtual_destructor_entry(in));
    total ^= static_cast<std::uint64_t>(multiple_inheritance_virtual_entry(in));
    total ^= static_cast<std::uint64_t>(covariant_return_entry(in));
    total ^= static_cast<std::uint64_t>(pointer_to_member_entry(in));
    total ^= static_cast<std::uint64_t>(std_function_entry(in));
    total ^= static_cast<std::uint64_t>(std_bind_entry(in));
    total ^= static_cast<std::uint64_t>(std_mem_fn_entry(in));
    total ^= static_cast<std::uint64_t>(std_invoke_entry(in));
    total ^= static_cast<std::uint64_t>(lambda_conversion_entry(in));
    total ^= static_cast<std::uint64_t>(function_object_operator_entry(in));
    total ^= static_cast<std::uint64_t>(overloaded_operator_entry(in));
    total ^= static_cast<std::uint64_t>(conversion_operator_entry(in));
    total ^= static_cast<std::uint64_t>(constructor_destructor_entry(in));
    total ^= static_cast<std::uint64_t>(overloaded_new_delete_entry(in));
    total ^= static_cast<std::uint64_t>(placement_new_entry(in));
    total ^= static_cast<std::uint64_t>(user_defined_literal_entry(in));
    total ^= static_cast<std::uint64_t>(range_for_entry(in));
    total ^= static_cast<std::uint64_t>(adl_dispatch_entry(in));
    total ^= static_cast<std::uint64_t>(tag_invoke_entry(in));
    total ^= static_cast<std::uint64_t>(adl_begin_end_entry(in));
    total ^= static_cast<std::uint64_t>(variant_visit_entry(in));
    total ^= static_cast<std::uint64_t>(smart_pointer_deleter_entry(in));
    total ^= static_cast<std::uint64_t>(pmr_virtual_resource_entry(in));
    total ^= static_cast<std::uint64_t>(exception_unwind_entry(in));
    total ^= static_cast<std::uint64_t>(setjmp_longjmp_entry(in));
    total ^= static_cast<std::uint64_t>(computed_goto_entry(in));
    total ^= static_cast<std::uint64_t>(coroutine_resume_entry(in));
    total ^= static_cast<std::uint64_t>(thread_entry_entry(in));
    total ^= static_cast<std::uint64_t>(async_future_entry(in));
    total ^= static_cast<std::uint64_t>(packaged_task_entry(in));
    total ^= static_cast<std::uint64_t>(atexit_callback_entry(in));
    total ^= static_cast<std::uint64_t>(terminate_handler_entry(in));
    total ^= static_cast<std::uint64_t>(signal_callback_entry(in));
    total ^= static_cast<std::uint64_t>(static_initialization_entry(in));
    total ^= static_cast<std::uint64_t>(thread_local_lifecycle_entry(in));
    total ^= static_cast<std::uint64_t>(import_indirect_strlen_entry(in));
    total ^= static_cast<std::uint64_t>(dynamic_symbol_lookup_entry(in));
    total ^= static_cast<std::uint64_t>(ifunc_resolver_entry(in));
    total ^= static_cast<std::uint64_t>(sanitizer_coverage_shape_entry(in));
    total ^= static_cast<std::uint64_t>(control_block_erased_entry(in));
    total ^= static_cast<std::uint64_t>(redherings_entry(in));

    sink_add(total);
    return 0;
}
