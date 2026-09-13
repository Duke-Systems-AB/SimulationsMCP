# tests/unit_py/test_two_stage_line.py
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps
from compose import build_flow

def _load_flow():
    p = os.path.join(_SRC, "..", "patterns", "flows", "two-stage-line.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def test_two_stage_line_builds_two_instances_and_wires_them():
    ops = FakeOps()
    res = build_flow(_load_flow(), ops)
    assert set(res["instances"]) == {"s1", "s2"}
    h1 = res["instances"]["s1"]["hblockId"]
    h2 = res["instances"]["s2"]["hblockId"]
    out_idx = res["instances"]["s1"]["interfaceMap"]["out"]["outerCon"]
    in_idx = res["instances"]["s2"]["interfaceMap"]["in"]["outerCon"]
    assert ("connect", h1, out_idx, h2, in_idx) in ops.calls
