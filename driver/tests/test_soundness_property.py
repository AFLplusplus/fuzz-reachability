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
