import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from fake_ops import FakeOps, load
from instantiate import build_molecule


def test_resource_machine_builds_with_pool_config():
    ops = FakeOps()
    res = build_molecule(load("resource-machine.json"), {"pool_name": "Pool1", "capacity": 3}, ops)
    ids = res["internalBlockIds"]
    assert {"q", "act", "rel", "rp"}.issubset(ids)
    assert ("resource_pool", ids["rp"], ids["q"], ids["rel"], "Pool1", 3, 1) in ops.calls


def test_resource_machine_default_params():
    ops = FakeOps()
    res = build_molecule(load("resource-machine.json"), {}, ops)
    ids = res["internalBlockIds"]
    assert ("resource_pool", ids["rp"], ids["q"], ids["rel"], "Pool1", 2, 1) in ops.calls
