# tests/unit_py/test_compose_build.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps
from compose import build_flow

FLOW = {
    "id": "line2",
    "instances": [
        {"ref": "m1", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
        {"ref": "m2", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
    ],
    "wiring": [{"from": "m1.out", "to": "m2.in"}],
}

def test_build_flow_instantiates_each_and_wires_hblocks():
    ops = FakeOps()
    res = build_flow(FLOW, ops)

    # both molecules instantiated -> two distinct H-blocks recorded
    assert set(res["instances"]) == {"m1", "m2"}
    h1 = res["instances"]["m1"]["hblockId"]
    h2 = res["instances"]["m2"]["hblockId"]
    assert h1 != h2

    # the inter-molecule wiring connected m1's outlet H-block connector to m2's inlet
    out_idx = res["instances"]["m1"]["interfaceMap"]["out"]["outerCon"]
    in_idx = res["instances"]["m2"]["interfaceMap"]["in"]["outerCon"]
    assert ("connect", h1, out_idx, h2, in_idx) in ops.calls
