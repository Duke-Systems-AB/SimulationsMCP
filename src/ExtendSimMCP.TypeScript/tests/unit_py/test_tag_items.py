import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps, load
from instantiate import build_molecule

def test_tag_items_builds_a_single_set_block():
    ops = FakeOps()
    res = build_molecule(load("tag-items.json"), {}, ops)
    assert "set" in res["internalBlockIds"]

def test_tag_items_declares_partType_write():
    mol = load("tag-items.json")
    assert "partType" in mol["attributes"]["writes"]

def test_tag_items_configures_partType_write():
    from fake_ops import FakeOps, load
    from instantiate import build_molecule
    ops = FakeOps()
    res = build_molecule(load("tag-items.json"), {"partType": 4}, ops)
    set_id = res["internalBlockIds"]["set"]
    assert ("set_attribute", set_id, "partType", 4, "constant") in ops.calls


def test_tag_items_default_partType_is_one():
    from fake_ops import FakeOps, load
    from instantiate import build_molecule
    ops = FakeOps()
    res = build_molecule(load("tag-items.json"), {}, ops)
    set_id = res["internalBlockIds"]["set"]
    assert ("set_attribute", set_id, "partType", 1, "constant") in ops.calls
