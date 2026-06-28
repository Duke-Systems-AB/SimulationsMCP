# tests/unit_py/test_patterns.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from patterns import list_patterns, get_pattern

def test_list_patterns_includes_known_molecules():
    res = list_patterns()
    ids = {p["id"] for p in res["patterns"]}
    assert {"buffer", "machine-with-breakdowns", "simple-machine"}.issubset(ids)
    sm = next(p for p in res["patterns"] if p["id"] == "simple-machine")
    assert sm["kind"] == "molecule"
    assert "process_time" in sm["params"]

def test_list_patterns_intent_filter_is_substring_case_insensitive():
    res = list_patterns(intent="HAVERI")
    ids = {p["id"] for p in res["patterns"]}
    assert "machine-with-breakdowns" in ids
    assert "buffer" not in ids

def test_get_pattern_returns_full_definition():
    res = get_pattern("simple-machine")
    assert res["success"] and res["kind"] == "molecule"
    assert res["pattern"]["nodes"][0]["ref"] == "q"

def test_get_pattern_unknown_is_fail_closed():
    res = get_pattern("does-not-exist")
    assert res["success"] is False and res["errorCode"] == "UNKNOWN_PATTERN"
