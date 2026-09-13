# tests/unit_py/test_simple_machine.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps, load
from instantiate import build_molecule

def test_simple_machine_builds_queue_then_activity():
    ops = FakeOps()
    res = build_molecule(load("simple-machine.json"), {"process_time": 5}, ops)
    q = res["internalBlockIds"]["q"]
    act = res["internalBlockIds"]["act"]
    assert ops.node_of(q, 1) == ops.node_of(act, 0) != 0
    sets = {(c[1], c[2]): c[3] for c in ops.calls if c[0] == "set_value"}
    assert sets[(act, "D")] == 5
