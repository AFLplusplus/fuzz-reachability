import json
import os
import re
from dataclasses import dataclass


class VerifyError(Exception):
    pass


@dataclass(frozen=True)
class VerificationResult:
    executed: int
    violations: tuple[str, ...]
    unknown: tuple[str, ...]


_RUST_LEGACY_HASH = re.compile(r"17h[0-9a-f]{16}E$")
_LOCAL_SOURCE = re.compile(r"^(.+?\.(?:c|cc|cpp|cxx|h|hh|hpp|hxx|rs)):(.+)$")


def _load(path, label):
    try:
        with open(path, encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifyError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VerifyError(f"{label} root must be a JSON object")
    return value


def _report_functions(report):
    exact = {}
    legacy = {}
    v0 = {}
    for field, state in (("reachable", "reachable"),
                         ("unreachable_defined", "unreachable")):
        values = report.get(field)
        if not isinstance(values, list):
            raise VerifyError(f"reachability field {field!r} must be an array")
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("mangled"), str) or not item["mangled"]:
                raise VerifyError(f"reachability field {field!r} has a function without a mangled name")
            filename = item.get("file")
            if filename is not None and not isinstance(filename, str):
                raise VerifyError(f"reachability field {field!r} has an invalid source file")
            key = item.get("key", item["mangled"])
            if not isinstance(key, str) or not key:
                raise VerifyError(f"reachability field {field!r} has an invalid key")
            names = {item["mangled"]}
            if isinstance(item.get("demangled"), str) and item["demangled"]:
                names.add(item["demangled"])
            entry = (state, os.path.basename(filename) if filename else None)
            for name in names:
                exact.setdefault(name, []).append(entry)
            if key != item["mangled"] and _RUST_LEGACY_HASH.search(item["mangled"]):
                legacy.setdefault(key, []).append(entry)
            if key != item["mangled"] and item["mangled"].startswith("_R"):
                v0.setdefault(key, []).append(entry)
    if not exact:
        raise VerifyError("reachability report contains no classified functions")
    return exact, legacy, v0


def _count(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VerifyError(f"coverage {label} must be a non-negative integer")
    return value


def _executed_functions(coverage):
    data = coverage.get("data")
    if not isinstance(data, list) or not data:
        raise VerifyError("coverage field 'data' must be a nonempty array")
    executed = set()
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("functions"), list):
            raise VerifyError("coverage data entry must contain a functions array")
        for fn in item["functions"]:
            if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
                raise VerifyError("coverage function must have a nonempty name")
            count = _count(fn.get("count"), "function count")
            regions = fn.get("regions", [])
            if not isinstance(regions, list):
                raise VerifyError("coverage function regions must be an array")
            for region in regions:
                if not isinstance(region, list) or len(region) < 5:
                    raise VerifyError("coverage function has an invalid region")
                count = max(count, _count(region[4], "region count"))
            if count:
                executed.add(fn["name"])
    if not executed:
        raise VerifyError("coverage contains no executed functions")
    return sorted(executed)


def _identity(name):
    local = _LOCAL_SOURCE.match(name)
    if local:
        return local.group(2), os.path.basename(local.group(1))
    return name, None


def _matches(symbol, source, index):
    exact, legacy, v0 = index
    candidates = exact.get(symbol, [])
    if not candidates and _RUST_LEGACY_HASH.search(symbol):
        candidates = legacy.get(_RUST_LEGACY_HASH.sub("", symbol), [])
    if not candidates and symbol.startswith("_R"):
        candidates = []
        for position in range(len(symbol)):
            if symbol.startswith(("Cs", "B"), position):
                candidates.extend(v0.get(symbol[:position], []))
    if source is not None:
        qualified = [entry for entry in candidates if entry[1] == source]
        if qualified:
            candidates = qualified
        elif any(entry[1] is not None for entry in candidates):
            candidates = []
        else:
            candidates = [entry for entry in candidates if entry[1] is None]
    return {entry[0] for entry in candidates}


def verify_files(reachability_path: str, coverage_path: str) -> VerificationResult:
    index = _report_functions(_load(reachability_path, "reachability report"))
    executed = _executed_functions(_load(coverage_path, "coverage export"))
    violations = []
    unknown = []
    for name in executed:
        symbol, source = _identity(name)
        states = _matches(symbol, source, index)
        if states == {"unreachable"}:
            violations.append(name)
        elif states != {"reachable"}:
            unknown.append(name)
    return VerificationResult(len(executed), tuple(violations), tuple(unknown))
