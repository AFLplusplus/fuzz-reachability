import json

import pytest

from reachability import verify


def _check(tmp_path, reachable, unreachable, functions):
    reach = tmp_path / "reachability.json"
    coverage = tmp_path / "coverage.json"
    reach.write_text(json.dumps({"schema_version": 2, "reachable": reachable,
                                 "unreachable_defined": unreachable}))
    coverage.write_text(json.dumps({"type": "llvm.coverage.json.export",
                                    "data": [{"functions": functions}]}))
    return verify.verify_files(str(reach), str(coverage))


def _fn(name, count=1, **extra):
    return {"name": name, "count": count, "filenames": ["/src/main.cpp"], **extra}


def test_confirmed_contradiction_and_reachable_function(tmp_path):
    result = _check(tmp_path, [{"mangled": "live"}],
                    [{"mangled": "dead"}], [_fn("live"), _fn("dead")])
    assert result.executed == 2
    assert result.violations == ("dead",)
    assert result.unknown == ()


def test_local_name_uses_coverage_file_and_ignores_other_file(tmp_path):
    result = _check(tmp_path,
                    [{"mangled": "helper", "file": "/src/a.cpp"}],
                    [{"mangled": "helper", "file": "/src/b.cpp"}],
                    [_fn("/build/a.cpp:helper")])
    assert result.violations == ()
    assert result.unknown == ()


def test_local_name_with_conflicting_file_and_missing_file_is_unknown(tmp_path):
    result = _check(tmp_path,
                    [{"mangled": "helper", "file": "/src/a.cpp"}],
                    [{"mangled": "helper", "file": None}],
                    [_fn("/build/b.cpp:helper")])
    assert result.violations == ()
    assert result.unknown == ("/build/b.cpp:helper",)


def test_ambiguous_name_is_unknown(tmp_path):
    result = _check(tmp_path, [{"mangled": "shared"}],
                    [{"mangled": "shared"}], [_fn("shared")])
    assert result.violations == ()
    assert result.unknown == ("shared",)


def test_duplicate_exports_count_once(tmp_path):
    reach = tmp_path / "reachability.json"
    coverage = tmp_path / "coverage.json"
    reach.write_text(json.dumps({"reachable": [],
                                 "unreachable_defined": [{"mangled": "dead"}]}))
    coverage.write_text(json.dumps({"data": [
        {"functions": [_fn("dead")]}, {"functions": [_fn("dead")]}
    ]}))
    result = verify.verify_files(str(reach), str(coverage))
    assert result.executed == 1
    assert result.violations == ("dead",)


def test_positive_region_counts_as_executed(tmp_path):
    result = _check(tmp_path, [], [{"mangled": "dead"}],
                    [_fn("dead", count=0, regions=[[1, 1, 2, 1, 3, 0, 0, 0]])])
    assert result.violations == ("dead",)


def test_unmatched_executed_function_is_unknown(tmp_path):
    result = _check(tmp_path, [{"mangled": "live"}], [],
                    [_fn("live"), _fn("outside")])
    assert result.unknown == ("outside",)


def test_rust_legacy_build_hash_joins_by_key(tmp_path):
    result = _check(tmp_path, [],
                    [{"mangled": "_ZN3dep4work17h0123456789abcdefE",
                      "key": "_ZN3dep4work"}],
                    [_fn("_ZN3dep4work17hfedcba9876543210E")])
    assert result.violations == ("_ZN3dep4work17hfedcba9876543210E",)


def test_rust_legacy_key_does_not_match_unhashed_name(tmp_path):
    result = _check(tmp_path, [],
                    [{"mangled": "_ZN3dep4work17h0123456789abcdefE",
                      "key": "_ZN3dep4work"}],
                    [_fn("_ZN3dep4work")])
    assert result.violations == ()
    assert result.unknown == ("_ZN3dep4work",)


def test_rust_v0_instantiating_crate_joins_by_key(tmp_path):
    result = _check(tmp_path, [],
                    [{"mangled": "_RNvCs123_3dep4workCs111_3foo",
                      "key": "_RNvCs123_3dep4work"}],
                    [_fn("_RNvCs123_3dep4workCs222_3bar")])
    assert result.violations == ("_RNvCs123_3dep4workCs222_3bar",)


@pytest.mark.parametrize("reach_data,coverage_data", [
    ({"reachable": []}, {"data": [{"functions": [_fn("work")]}]}),
    ({"reachable": [], "unreachable_defined": []}, {"data": []}),
    ({"reachable": [], "unreachable_defined": []},
     {"data": [{"functions": [_fn("work", count=0)]}]}),
    ({"reachable": [], "unreachable_defined": []},
     {"data": [{"functions": [{"name": "work", "count": "bad"}]}]}),
])
def test_unusable_evidence_fails(tmp_path, reach_data, coverage_data):
    reach = tmp_path / "reachability.json"
    coverage = tmp_path / "coverage.json"
    reach.write_text(json.dumps(reach_data))
    coverage.write_text(json.dumps(coverage_data))
    with pytest.raises(verify.VerifyError):
        verify.verify_files(str(reach), str(coverage))
