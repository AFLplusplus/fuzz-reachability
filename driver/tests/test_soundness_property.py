"""Property test: for random call graphs the analyzer's reachable set must equal
(direct case) / be a superset of (indirect case) a brute-force BFS oracle. The
tool's core promise is never to under-report reachability. Deterministic seeds --
no reliance on wall-clock randomness."""

import json
import random

import pytest


def _gen_direct(seed, n=12):
    rnd = random.Random(seed)
    edges = {i: sorted({rnd.randrange(n) for _ in range(rnd.randrange(0, 4))} - {i})
             for i in range(n)}
    lines = []
    for i in range(n):
        body = "".join(f"  call void @f{j}()\n" for j in edges[i])
        lines.append(f"define void @f{i}() {{\n{body}  ret void\n}}\n")
    seen, work = set(), [0]
    while work:
        c = work.pop()
        if c in seen:
            continue
        seen.add(c)
        work.extend(edges[c])
    return "".join(lines), {f"f{i}" for i in seen}


@pytest.mark.parametrize("seed", range(25))
def test_direct_reachability_matches_oracle(run_analyzer, tmp_path, seed):
    ir, oracle = _gen_direct(seed)
    p = tmp_path / f"g{seed}.ll"
    p.write_text(ir)
    r = run_analyzer([str(p), "--entry", "f0", "--no-name-roots"])
    assert r.returncode == 0, r.stderr
    got = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert got == oracle, f"seed {seed}: symmetric diff {got ^ oracle}"


def _gen_indirect(seed, n=8):
    rnd = random.Random(seed)
    edges = {i: sorted({rnd.randrange(n) for _ in range(rnd.randrange(0, 4))} - {i})
             for i in range(n)}
    addr_taken = sorted(rnd.sample(range(1, n), rnd.randint(1, n - 1)))
    table = ", ".join(f"ptr @f{i}" for i in addr_taken)
    lines = [f"@table = global [{len(addr_taken)} x ptr] [{table}]\n"]
    for i in range(n):
        body = "".join(f"  call void @f{j}()\n" for j in edges[i])
        if i == 0:
            body += (
                f"  %slot = getelementptr [{len(addr_taken)} x ptr], ptr @table, i32 0, i32 0\n"
                "  %p = load ptr, ptr %slot\n"
                "  call void %p()\n"
            )
        lines.append(f"define void @f{i}() {{\n{body}  ret void\n}}\n")
    seen, work = set(), [0]
    while work:
        c = work.pop()
        if c in seen:
            continue
        seen.add(c)
        work.extend(edges[c])
    oracle = {f"f{i}" for i in seen} | {f"f{i}" for i in addr_taken}
    return "".join(lines), oracle


@pytest.mark.parametrize("seed", range(25))
def test_indirect_reachability_is_superset_of_oracle(run_analyzer, tmp_path, seed):
    ir, oracle = _gen_indirect(seed)
    p = tmp_path / f"gi{seed}.ll"
    p.write_text(ir)
    r = run_analyzer([str(p), "--entry", "f0", "--no-name-roots"])
    assert r.returncode == 0, r.stderr
    got = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert got >= oracle, f"seed {seed}: oracle - got = {oracle - got} (UNSOUND)"


_SIGNATURES = [
    ("void", []), ("i32", ["i32"]), ("void", ["ptr"]), ("i64", ["ptr", "i64"]),
]
_ZERO = {"i32": "i32 0", "i64": "i64 0", "ptr": "ptr null"}


def _call(index, signature):
    ret, params = signature
    args = ", ".join(_ZERO[p] for p in params)
    prefix = "" if ret == "void" else f"%r{index} = "
    return f"  {prefix}call {ret} @f{index}({args})\n"


def _gen_mixed(seed, n=10):
    rnd = random.Random(seed)
    sigs = [_SIGNATURES[0]] + [rnd.choice(_SIGNATURES) for _ in range(n - 1)]
    edges = {i: sorted({rnd.randrange(1, n) for _ in range(rnd.randrange(0, 3))} - {i})
             for i in range(n)}
    table = sorted(rnd.sample(range(1, n), rnd.randint(1, n - 1)))
    stashed = rnd.randrange(1, n)
    lines = [
        "declare ptr @malloc(i64)\n",
        f"@table = global [{len(table)} x ptr] ["
        + ", ".join(f"ptr @f{i}" for i in table) + "]\n",
        "define void @stash(ptr %obj, ptr %fn) {\n"
        "  %o.addr = alloca ptr\n  %f.addr = alloca ptr\n"
        "  store ptr %obj, ptr %o.addr\n  store ptr %fn, ptr %f.addr\n"
        "  %o = load ptr, ptr %o.addr\n  %f = load ptr, ptr %f.addr\n"
        "  store ptr %f, ptr %o\n  ret void\n}\n",
        "define void @invoke(ptr %obj) {\n"
        "  %o.addr = alloca ptr\n  store ptr %obj, ptr %o.addr\n"
        "  %o = load ptr, ptr %o.addr\n  %fp = load ptr, ptr %o\n"
        "  call void %fp()\n  ret void\n}\n",
    ]
    for i in range(n):
        ret, params = sigs[i]
        header = ", ".join(f"{p} %a{k}" for k, p in enumerate(params))
        body = "".join(_call(j, sigs[j]) for j in edges[i])
        if i == 0:
            body += (
                f"  %slot = getelementptr [{len(table)} x ptr], ptr @table, i32 0, i32 0\n"
                "  %p = load ptr, ptr %slot\n  call void %p()\n"
                "  %obj = call ptr @malloc(i64 8)\n"
                f"  call void @stash(ptr %obj, ptr @f{stashed})\n"
                "  call void @invoke(ptr %obj)\n"
            )
        tail = "  ret void\n" if ret == "void" else f"  ret {ret} 0\n"
        lines.append(f"define {ret} @f{i}({header}) {{\n{body}{tail}}}\n")
    seen, work = set(), [0, table[0], stashed]
    while work:
        c = work.pop()
        if c in seen:
            continue
        seen.add(c)
        work.extend(edges[c])
    return "".join(lines), {f"f{i}" for i in seen} | {"stash", "invoke"}


@pytest.mark.parametrize("seed", range(25))
def test_mixed_signature_flows_are_superset_of_oracle(run_analyzer, tmp_path, seed):
    ir, oracle = _gen_mixed(seed)
    p = tmp_path / f"gm{seed}.ll"
    p.write_text(ir)
    r = run_analyzer([str(p), "--entry", "f0", "--no-name-roots"])
    assert r.returncode == 0, r.stderr
    got = {f["mangled"] for f in json.loads(r.stdout)["reachable"]}
    assert got >= oracle, f"seed {seed}: oracle - got = {oracle - got} (UNSOUND)"
