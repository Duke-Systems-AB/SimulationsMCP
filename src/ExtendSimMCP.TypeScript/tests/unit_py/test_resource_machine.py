import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps, load
from instantiate import build_molecule

def test_resource_machine_wires_pool_side_connection():
    ops = FakeOps()
    res = build_molecule(load("resource-machine.json"), {"process_time": 4, "capacity": 2}, ops)
    q = res["internalBlockIds"]["q"]
    pool = res["internalBlockIds"]["pool"]
    assert ops.node_of(pool, 1) == ops.node_of(q, 5) != 0
