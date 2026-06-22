// =============================================================================
// cfg_indirect_test.cpp
//
// A stress test for static call-graph / reachability analysis that is rooted at
// an LLVMFuzzerTestOneInput harness. It exercises (close to) every non-direct
// call / control-transfer mechanism available in C and C++, mixes in precision
// traps ("red herrings") that should NOT be reachable, and adds genuinely dead
// code ("unreachable").
//
// Naming contract (so the analysis output can be diffed mechanically):
//   <mechanism>_*    -> reachable from LLVMFuzzerTestOneInput for SOME input
//                       (reachability is the UNION over all inputs; data[0]
//                        selects the mechanism, later bytes are parameters)
//   redherrings_*    -> address-taken / candidate / dead-branch, but never
//                       actually executed for any input (false positives for an
//                       imprecise analysis)
//   unreachable_*    -> no incoming edge from the reachable set at all
//
// Red-herring flavors included (all four):
//   1. Dead-branch call         (if(false) / volatile-guarded false branch)
//   2. Address-taken, uncalled  (stored in a table/sink, slot never invoked)
//   3. Uninstantiated vtable     (override of a really-called virtual, but the
//                                 declaring type is never constructed -> CHA
//                                 includes it, RTA excludes it)
//   4. Wrong-target same sig    (same signature as a real fn-ptr target,
//                                 address-taken, but never assigned to the
//                                 pointer that is actually called)
//
// ---------------------------------------------------------------------------
// Build (real, libFuzzer):
//   clang++ -std=c++20 -O0 -g -fno-inline \
//           -fsanitize=fuzzer,address \
//           -rdynamic cfg_indirect_test.cpp -o cfg_indirect_test -ldl -lpthread
//
// Build (emit LLVM IR for your tool, unoptimized so the traps survive):
//   clang++ -std=c++20 -O0 -Xclang -disable-O0-optnone \
//           -S -emit-llvm cfg_indirect_test.cpp -o cfg_indirect_test.ll
//   (At -O2+ the optimizer will correctly delete flavor 1/3 traps and may
//    devirtualize calls; analyze unoptimized IR to test precision.)
//
// Build (standalone self-check, no libFuzzer needed; prints what was reached):
//   g++ -std=c++20 -O0 -fno-inline -DSTANDALONE_MAIN \
//       -rdynamic cfg_indirect_test.cpp -o selfcheck -ldl -lpthread
//   ./selfcheck            # drives every selector, lists [reached] functions
// =============================================================================

#include <cstdint>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <csetjmp>
#include <csignal>
#include <cstdio>
#include <functional>
#include <coroutine>
#include <vector>

#if defined(__unix__) || defined(__APPLE__)
#  include <dlfcn.h>
#  include <pthread.h>
#  define HAVE_POSIX 1
#else
#  define HAVE_POSIX 0
#endif

// --- reached-marker: real telemetry under STANDALONE_MAIN, no-op for fuzzing --
#ifdef STANDALONE_MAIN
#  define REACHED(name) do { std::fprintf(stderr, "[reached] %s\n", (name)); } while (0)
#else
#  define REACHED(name) do { } while (0)
#endif

#define KEEP [[gnu::used]]   // keep traps/dead code in IR regardless of opt level

// =============================================================================
// (1) C: plain function pointers + dispatch table + qsort callback
// =============================================================================
static int fnptr_add_one(int x)   { REACHED("fnptr_add_one");   return x + 1; }
static int fnptr_times_two(int x) { REACHED("fnptr_times_two"); return x * 2; }

static int fnptr_table_neg(int x) { REACHED("fnptr_table_neg"); return -x; }
static int fnptr_table_sq(int x)  { REACHED("fnptr_table_sq");  return x * x; }

static int (*const k_dispatch_table[])(int) = { fnptr_table_neg, fnptr_table_sq };

extern "C" int callback_qsort_cmp(const void *a, const void *b) {
    REACHED("callback_qsort_cmp");
    return (*static_cast<const int *>(a) - *static_cast<const int *>(b));
}

// =============================================================================
// (2) C: setjmp / longjmp non-local control transfer
// =============================================================================
static jmp_buf g_jmp_buf;
static void setjmp_longjmp_target(int x) {
    REACHED("setjmp_longjmp_target");
    std::longjmp(g_jmp_buf, x ? x : 1);   // jumps back into the harness frame
}

// =============================================================================
// (3) C: computed goto (label addresses) -- GCC/Clang extension
// =============================================================================
static void computed_goto_dispatch(int sel) {
#if defined(__GNUC__)
    static void *const labels[] = { &&L0, &&L1, &&L2 };
    goto *labels[(unsigned)sel % 3];
L0: REACHED("computed_goto_L0"); return;
L1: REACHED("computed_goto_L1"); return;
L2: REACHED("computed_goto_L2"); return;
#else
    switch ((unsigned)sel % 3) {
        case 0: REACHED("computed_goto_L0"); return;
        case 1: REACHED("computed_goto_L1"); return;
        default: REACHED("computed_goto_L2"); return;
    }
#endif
}

// =============================================================================
// (4) C: indirect call written in inline assembly (x86-64 System V)
//     The target deliberately makes NO further calls and touches NO SSE state,
//     so the hand-rolled `call` cannot fault on stack misalignment.
// =============================================================================
static volatile int g_asm_reached = 0;
static int asm_call_target(int x) { g_asm_reached = 1; return x + 100; }

static int asm_call_invoke(int arg) {
#if defined(__x86_64__) && defined(__GNUC__)
    int (*fp)(int) = asm_call_target;
    int res;
    __asm__ volatile (
        "call *%[fn]\n\t"
        : "=a"(res)
        : [fn] "r"(fp), "D"(arg)
        : "rcx", "rdx", "rsi", "r8", "r9", "r10", "r11", "cc", "memory",
          "xmm0","xmm1","xmm2","xmm3","xmm4","xmm5","xmm6","xmm7",
          "xmm8","xmm9","xmm10","xmm11","xmm12","xmm13","xmm14","xmm15"
    );
    return res;
#else
    return asm_call_target(arg);   // portable fallback, still reaches the target
#endif
}

// =============================================================================
// (5) C++: virtual dispatch (vtable). Shape::describe is the call site that the
//     uninstantiated-vtable red herring (flavor 3) tries to attach to.
// =============================================================================
struct Shape {
    virtual void describe() = 0;
    virtual int  area() = 0;
    virtual ~Shape() = default;
};
struct virtual_Circle : Shape {
    void describe() override { REACHED("virtual_Circle_describe"); }
    int  area() override     { REACHED("virtual_Circle_area"); return 3; }
};
struct virtual_Square : Shape {
    void describe() override { REACHED("virtual_Square_describe"); }
    int  area() override     { REACHED("virtual_Square_area"); return 4; }
};

// =============================================================================
// (6) C++: pointer-to-member functions (non-virtual + virtual encodings)
// =============================================================================
struct PmfHolder {
    int pmf_nonvirtual_inc(int x) { REACHED("pmf_nonvirtual_inc"); return x + 1; }
    int pmf_nonvirtual_dec(int x) { REACHED("pmf_nonvirtual_dec"); return x - 1; }
    virtual int pmf_virtual_a(int x) { REACHED("pmf_virtual_a"); return x + 10; }
    virtual int pmf_virtual_b(int x) { REACHED("pmf_virtual_b"); return x + 20; }
    virtual ~PmfHolder() = default;
};

// =============================================================================
// (7) C++: std::function, std::bind, lambdas, virtual functor (operator())
// =============================================================================
static int stdfunction_target(int x)      { REACHED("stdfunction_target"); return x; }
static int stdbind_target(int a, int b)    { REACHED("stdbind_target");    return a + b; }

struct functor_Base { virtual int operator()(int) = 0; virtual ~functor_Base() = default; };
struct functor_Impl : functor_Base {
    int operator()(int x) override { REACHED("functor_Impl_call"); return x - 1; }
};

// =============================================================================
// (8) C++20: coroutine (resume / destroy go through the coroutine frame ptrs)
// =============================================================================
struct CoroTask {
    struct promise_type {
        CoroTask get_return_object() {
            return CoroTask{ std::coroutine_handle<promise_type>::from_promise(*this) };
        }
        std::suspend_always initial_suspend() noexcept { return {}; }
        std::suspend_always final_suspend()   noexcept { return {}; }
        void return_void() {}
        void unhandled_exception() {}
    };
    std::coroutine_handle<promise_type> h;
    ~CoroTask() { if (h) h.destroy(); }
};
static CoroTask coroutine_body(int x) {
    REACHED("coroutine_body_entry");
    co_await std::suspend_always{};
    REACHED("coroutine_body_resumed");
    (void)x;
    co_return;
}

// =============================================================================
// (9) C++: exception unwinding -- RAII dtor runs via the unwinder, then handler
// =============================================================================
struct ExcGuard {
    ~ExcGuard() { REACHED("exception_unwind_raii_dtor"); }  // called during unwind
};
static void exception_unwind_thrower(int x) {
    REACHED("exception_unwind_thrower");
    ExcGuard guard;                 // its dtor fires while the stack unwinds
    if (x >= 0) throw x;            // always thrown here (x is masked >= 0)
}
static void exception_unwind_handler(int x) {
    try {
        exception_unwind_thrower(x);
    } catch (int caught) {
        REACHED("exception_unwind_handler");
        (void)caught;
    }
}

// =============================================================================
// (10) C++: thunks -- this-pointer adjustment (multiple inheritance) and
//      covariant-return adjustment. The named overrides are reached *through*
//      compiler-generated thunks placed in the secondary vtables.
// =============================================================================
struct ThunkBaseA { virtual void from_a() { } virtual ~ThunkBaseA() = default; long pad_a = 0xA; };
struct ThunkBaseB { virtual void from_b() { } virtual ~ThunkBaseB() = default; long pad_b = 0xB; };
struct ThunkDerived : ThunkBaseA, ThunkBaseB {
    void from_b() override { REACHED("thunk_adjusted_from_b"); }  // via this-adjusting thunk
};

struct Mixin { virtual ~Mixin() = default; long pad_m = 0xABCD; };
struct CovBase { virtual CovBase *cov_clone() { return this; } virtual ~CovBase() = default; };
struct CovDerived : Mixin, CovBase {     // offset makes the covariant return non-trivial
    CovDerived *cov_clone() override { REACHED("thunk_covariant_clone"); return this; }
};

// =============================================================================
// (11) Loader-level: PLT (call into libc), weak/interposable symbol
// =============================================================================
static size_t plt_external_call(const char *s) {
    size_t a = std::strlen(s);          // -> strlen@plt (external symbol edge)
    int    b = std::abs((int)a - 3);    // -> abs@plt
    REACHED("plt_external_call");
    return a + (size_t)b;
}

extern "C" KEEP __attribute__((weak)) void weak_interposable_fn(int x) {
    REACHED("weak_interposable_fn");    // a stronger def could interpose at link/load
    (void)x;
}

// =============================================================================
// (12) Loader-level: GNU indirect function (ifunc) -- resolver picks impl @load
// =============================================================================
#if defined(__linux__) && defined(__GNUC__)
extern "C" void ifunc_impl_default(int x) { REACHED("ifunc_impl_default"); (void)x; }
typedef void (*ifunc_fn_t)(int);
extern "C" ifunc_fn_t ifunc_target_resolver(void) { return ifunc_impl_default; }
extern "C" void ifunc_target(int) __attribute__((ifunc("ifunc_target_resolver")));
#else
extern "C" void ifunc_target(int x) { REACHED("ifunc_impl_default"); (void)x; }
#endif

// =============================================================================
// (13) Loader-level: dlopen(self)/dlsym -- string-resolved edge, invisible to
//      a module-level static analysis (requires -rdynamic to find the symbol).
// =============================================================================
extern "C" KEEP void dlsym_target_fn(int x) { REACHED("dlsym_target_fn"); (void)x; }

// =============================================================================
// (14) OS-dispatched: async signal handler, pthread start routine, atexit
// =============================================================================
extern "C" void signal_handler_fn(int sig) { REACHED("signal_handler_fn"); (void)sig; }
extern "C" void *pthread_start_fn(void *arg) { REACHED("pthread_start_fn"); return arg; }
extern "C" void atexit_handler_fn(void)      { REACHED("atexit_handler_fn"); }

// =============================================================================
// (15) Static initialization: global ctor (llvm.global_ctors) + constructor attr
//      These run before main / the fuzzer loop. A reachability tool should treat
//      global_ctors as roots.
// =============================================================================
static void staticinit_ctor_fn(void) { REACHED("staticinit_ctor_fn"); }
struct StaticInitProbe { StaticInitProbe() { staticinit_ctor_fn(); } };
static StaticInitProbe g_static_probe;     // emitted into global_ctors
__attribute__((constructor)) static void staticinit_attr_ctor(void) {
    REACHED("staticinit_attr_ctor");
}

// =============================================================================
// RED HERRINGS -- must never execute for any input
// =============================================================================

// Flavor 1: dead-branch calls (planted inside a function that *is* reachable).
KEEP void redherrings_deadbranch_const(int)    { REACHED("!! redherrings_deadbranch_const"); }
KEEP void redherrings_deadbranch_volatile(int) { REACHED("!! redherrings_deadbranch_volatile"); }
static void plant_dead_branches(int x) {        // reachable; the calls inside are not
    if (false) redherrings_deadbranch_const(x);            // const-false: a good opt deletes it
    volatile int z = 0;
    if (z) redherrings_deadbranch_volatile(x);             // volatile-false: survives -O2, never taken
}

// Flavor 2: address-taken, never invoked.
KEEP int redherrings_addrtaken_a(int x) { REACHED("!! redherrings_addrtaken_a"); return x; }
KEEP int redherrings_addrtaken_b(int x) { REACHED("!! redherrings_addrtaken_b"); return x; }
static int (*const g_uncalled_table[])(int) = { redherrings_addrtaken_a, redherrings_addrtaken_b };
// reference the table (keep it live) but never call through it:
KEEP volatile uintptr_t g_addrtaken_sink = reinterpret_cast<uintptr_t>(g_uncalled_table[0]);

// Flavor 3: override of a really-called virtual (Shape::describe), but the
// declaring type is NEVER instantiated -> CHA false positive, RTA-correct.
struct redherrings_UninstShape : Shape {
    KEEP void describe() override { REACHED("!! redherrings_uninst_describe"); }
    KEEP int  area() override     { REACHED("!! redherrings_uninst_area"); return -1; }
};

// Flavor 4: same signature int(int) as the real fnptr targets, address-taken so
// a type/signature-based resolver lists them as candidates, but never stored in
// the pointer that is actually called.
KEEP int redherrings_samesig_x(int x) { REACHED("!! redherrings_samesig_x"); return x; }
KEEP int redherrings_samesig_y(int x) { REACHED("!! redherrings_samesig_y"); return x; }
KEEP volatile uintptr_t g_samesig_sink =
    reinterpret_cast<uintptr_t>(reinterpret_cast<void *>(&redherrings_samesig_x)) ^
    reinterpret_cast<uintptr_t>(reinterpret_cast<void *>(&redherrings_samesig_y));

// =============================================================================
// UNREACHABLE -- no incoming edge from the reachable set; internally connected
// cluster + a couple of singletons. External linkage so they survive to IR.
// =============================================================================
KEEP void unreachable_leaf(void)        { REACHED("!! unreachable_leaf"); }
KEEP void unreachable_mid(void)         { unreachable_leaf(); }
KEEP void unreachable_root(void)        { unreachable_mid(); }
KEEP int  unreachable_standalone(int x) { return x * x + 7; }
KEEP void unreachable_calls_real(void)  { (void)fnptr_add_one(1); }  // calls a reachable fn,
                                                                     // but is itself unreachable

// =============================================================================
// HARNESS
// =============================================================================
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 1) return 0;

    const unsigned sel = data[0] % 24u;
    const unsigned d1  = (size > 1) ? data[1] : 0u;
    const int      n   = (size > 2) ? (int)data[2] : 0;

    // Reachable, but the red-herring dead-branch call sites inside are not taken.
    plant_dead_branches(n);

    switch (sel) {
    case 0: {   // direct function pointer, target chosen by input
        int (*fp)(int) = (d1 & 1) ? fnptr_add_one : fnptr_times_two;
        (void)fp(n);
        break;
    }
    case 1: {   // function-pointer dispatch table, index from input
        (void)k_dispatch_table[d1 % 2](n);
        break;
    }
    case 2: {   // qsort comparator callback
        int arr[4] = { n, n ^ 1, (int)d1, 0 };
        std::qsort(arr, 4, sizeof(int), callback_qsort_cmp);
        break;
    }
    case 3: {   // setjmp / longjmp
        if (setjmp(g_jmp_buf) == 0) {
            setjmp_longjmp_target(n);   // never returns; control resumes above
        }
        break;
    }
    case 4:     // computed goto
        computed_goto_dispatch((int)d1);
        break;
    case 5:     // inline-asm indirect call
        (void)asm_call_invoke(n);
        break;
    case 6: {   // virtual dispatch
        Shape *s = (d1 & 1) ? static_cast<Shape *>(new virtual_Circle)
                            : static_cast<Shape *>(new virtual_Square);
        s->describe();
        (void)s->area();
        delete s;
        break;
    }
    case 7: {   // pointer-to-member, non-virtual
        int (PmfHolder::*pnv)(int) =
            (d1 & 1) ? &PmfHolder::pmf_nonvirtual_inc : &PmfHolder::pmf_nonvirtual_dec;
        PmfHolder h;
        (void)(h.*pnv)(n);
        break;
    }
    case 8: {   // pointer-to-member, virtual (encodes vtable index)
        int (PmfHolder::*pv)(int) =
            (d1 & 1) ? &PmfHolder::pmf_virtual_a : &PmfHolder::pmf_virtual_b;
        PmfHolder h;
        (void)(h.*pv)(n);
        break;
    }
    case 9: {   // std::function
        std::function<int(int)> f = stdfunction_target;
        (void)f(n);
        break;
    }
    case 10: {  // std::bind
        auto g = std::bind(stdbind_target, std::placeholders::_1, 7);
        (void)g(n);
        break;
    }
    case 11: {  // lambdas: type-erased + captureless decayed to fn ptr
        auto lam = [](int x) { REACHED("lambda_in_stdfunction"); return x * 3; };
        std::function<int(int)> lf = lam;
        (void)lf(n);
        int (*lp)(int) = [](int x) { REACHED("lambda_fnptr_decay"); return x + 5; };
        (void)lp(n);
        break;
    }
    case 12: {  // virtual functor operator()
        functor_Base *fb = new functor_Impl();
        (void)(*fb)(n);
        delete fb;
        break;
    }
    case 13: {  // C++20 coroutine resume/destroy
        CoroTask t = coroutine_body(n);
        while (!t.h.done()) t.h.resume();
        break;
    }
    case 14:    // exception unwinding
        exception_unwind_handler(n < 0 ? -n : n);
        break;
    case 15: {  // thunks: this-adjust + covariant
        ThunkDerived d;
        ThunkBaseB *pb = &d;            // points into the second base subobject
        pb->from_b();                  // dispatched through a this-adjusting thunk
        CovDerived cd;
        CovBase *cp = &cd;
        (void)cp->cov_clone();         // covariant-return thunk
        break;
    }
    case 16:    // PLT (external libc symbols)
        (void)plt_external_call((d1 & 1) ? "harness" : "fuzz");
        break;
    case 17:    // GNU ifunc
        ifunc_target(n);
        break;
    case 18: {  // dlopen(self)/dlsym
#if HAVE_POSIX
        void *h = dlopen(nullptr, RTLD_NOW | RTLD_GLOBAL);
        if (h) {
            auto f = reinterpret_cast<void (*)(int)>(dlsym(h, "dlsym_target_fn"));
            if (f) f(n);
            dlclose(h);
        }
#else
        dlsym_target_fn(n);
#endif
        break;
    }
    case 19:    // weak / interposable symbol
        weak_interposable_fn(n);
        break;
    case 20:    // async signal handler
#if HAVE_POSIX
        signal(SIGUSR1, signal_handler_fn);
        raise(SIGUSR1);
#else
        signal_handler_fn(0);
#endif
        break;
    case 21: {  // pthread start routine
#if HAVE_POSIX
        pthread_t th;
        int carg = n;
        if (pthread_create(&th, nullptr, pthread_start_fn, &carg) == 0)
            pthread_join(th, nullptr);
#else
        pthread_start_fn(nullptr);
#endif
        break;
    }
    case 22: {  // atexit handler (registered once; runs at process exit)
        static bool registered = false;
        if (!registered) { std::atexit(atexit_handler_fn); registered = true; }
        break;
    }
    case 23:    // static-init confirmation (ctors already ran at load time)
        // nothing to call; staticinit_ctor_fn / staticinit_attr_ctor ran pre-main.
        break;
    default:
        break;
    }
    return 0;
}

// =============================================================================
// Standalone self-check driver (only when STANDALONE_MAIN is defined).
// Feeds one input per selector and prints every [reached] function. A correct
// run prints NO line beginning with "!!" (those are red herrings / unreachable).
// =============================================================================
#ifdef STANDALONE_MAIN
int main(void) {
    // For each selector, sweep a few d1 values so every fn-ptr/virtual arm and
    // all three computed-goto labels are exercised.
    for (unsigned sel = 0; sel < 24; ++sel) {
        for (uint8_t d1 = 0; d1 < 4; ++d1) {
            uint8_t buf[3] = { (uint8_t)sel, d1, 0x2a };
            LLVMFuzzerTestOneInput(buf, sizeof(buf));
        }
    }
    // asm_call_target has no REACHED marker (kept SSE-free on purpose); confirm
    // it executed via its flag so the self-check covers it too.
    if (g_asm_reached) std::fprintf(stderr, "[reached] asm_call_target\n");
    std::fprintf(stderr, "[selfcheck] done (atexit handler fires after this)\n");
    return 0;
}
#endif
