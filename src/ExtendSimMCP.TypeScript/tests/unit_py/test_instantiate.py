# tests/unit_py/test_instantiate.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from instantiate import build_molecule
from fake_ops import FakeOps, load


def test_seed_is_wrapped_in_context_then_stubs_removed():
    ops = FakeOps()
    result = build_molecule(load("buffer.json"), {}, ops)
    kinds = [c[0] for c in ops.calls]
    # activates, builds stub-seed-stub, wraps the seed, removes both stubs
    assert kinds[0] == "activate"
    assert "create_hblock" in kinds
    assert kinds.count("remove_block") >= 2          # both stubs removed
    assert isinstance(result["hblockId"], int)


def test_flow_chain_uses_disconnect_first_and_clean_nodes():
    ops = FakeOps()
    result = build_molecule(load("machine-with-breakdowns.json"), {"process_time": 3, "mtbf": 120, "mttr": 8}, ops)

    # The non-seed flow node (q) is placed inside, then prepended at the inlet
    # with disconnect-FIRST (krav 8): disconnect(inlet<->seed.in) BEFORE reconnect.
    kinds = [c for c in ops.calls if c[0] in ("place_in_hblock", "disconnect", "connect")]
    assert any(c[0] == "place_in_hblock" and c[2] == "Queue" for c in ops.calls)
    dis_idx = next(i for i, c in enumerate(kinds) if c[0] == "disconnect")
    con_after = [c for c in kinds[dis_idx + 1:] if c[0] == "connect"]
    assert len(con_after) >= 2          # reconnect inlet->new and new->seed after disconnect

    # internal ids recorded for every node ref
    assert set(result["internalBlockIds"]).issuperset({"q", "act"})

    # Topology clean: seed.ItemOut and act.ItemIn share a node (the internal edge),
    # and that node differs from the outlet node (no collapse).
    qid = result["internalBlockIds"]["q"]
    aid = result["internalBlockIds"]["act"]
    assert ops.node_of(qid, 1) == ops.node_of(aid, 0) != 0


def test_side_connections_params_and_interface():
    ops = FakeOps()
    result = build_molecule(load("machine-with-breakdowns.json"), {"process_time": 3, "mtbf": 120, "mttr": 8}, ops)

    # Shutdown placed inside and side-connected by name (krav 10), node-verified.
    sd = result["internalBlockIds"]["sd"]
    act = result["internalBlockIds"]["act"]
    assert ops.node_of(sd, ops.con_index(sd, "SD_ValueOut")) == ops.node_of(act, ops.con_index(act, "SDV_In")) != 0

    # Params resolved and set: process_time on act, mtbf/mttr on sd.
    sets = {(c[1], c[2]): c[3] for c in ops.calls if c[0] == "set_value"}
    assert sets[(act, "D")] == 3
    assert sets[(sd, "SF_TBF_Arg1_prm")] == 120
    assert sets[(sd, "SF_TTR_Arg1_prm")] == 8

    # Interface map binds molecule ports to inner block + outer connector.
    assert result["interfaceMap"]["in"]["blockId"] == result["internalBlockIds"]["q"]
    assert result["interfaceMap"]["out"]["blockId"] == act


from unittest import mock

def test_realops_create_hblock_verifies_effect():
    import instantiate as inst
    fake_backend = mock.Mock()
    fake_backend.execute_command.return_value = {"success": True}
    # hierarchy_list count goes 0 -> 1 after CreateHblock; new H-block named after seed
    fake_backend.hierarchy_list.side_effect = [
        {"count": 0, "hierarchies": []},
        {"count": 1, "hierarchies": [{"blockId": 57, "blockName": "m"}]},
    ]
    ops = inst.RealOps(fake_backend)
    hid = ops.create_hblock(seed_id=10, name="m")
    assert hid == 57
    # it must NOT trust success:true alone — it called hierarchy_list to verify
    assert fake_backend.hierarchy_list.call_count == 2

def test_realops_create_hblock_raises_when_not_created():
    import instantiate as inst
    fake_backend = mock.Mock()
    fake_backend.execute_command.return_value = {"success": True}
    fake_backend.hierarchy_list.side_effect = [
        {"count": 0, "hierarchies": []},
        {"count": 0, "hierarchies": []},   # nothing created despite success:true
    ]
    ops = inst.RealOps(fake_backend)
    with pytest.raises(inst.BuildError, match="H-block"):
        ops.create_hblock(seed_id=10, name="m")


def test_build_applies_set_attributes_with_default_param():
    from instantiate import build_molecule
    from fake_ops import FakeOps
    mol = {
        "id": "t", "kind": "molecule",
        "params": {"partType": {"required": False, "default": 5}},
        "attributes": {"reads": [], "writes": ["partType"]},
        "nodes": [{"ref": "set", "lib": "Item.lbr", "type": "Set", "seed": True,
                   "setAttributes": [{"name": "partType", "value": "{{partType}}"}]}],
        "edges": [],
        "interface": {"inlets": [{"port": "in", "binds": "set.ItemIn", "role": "item"}],
                      "outlets": [{"port": "out", "binds": "set.ItemOut", "role": "item"}]},
    }
    ops = FakeOps()
    res = build_molecule(mol, {}, ops)          # no explicit param -> default 5
    set_id = res["internalBlockIds"]["set"]
    assert ("set_attribute", set_id, "partType", 5, "constant") in ops.calls


def test_build_lays_out_blocks_without_overlap():
    from instantiate import build_molecule
    from fake_ops import FakeOps
    mol = {
        "id": "lin", "kind": "molecule", "params": {},
        "attributes": {"reads": [], "writes": []},
        "nodes": [
            {"ref": "a", "lib": "Item.lbr", "type": "Queue"},
            {"ref": "b", "lib": "Item.lbr", "type": "Activity", "seed": True},
        ],
        "edges": [{"kind": "flow", "from": "a.ItemOut", "to": "b.ItemIn"}],
        "interface": {"inlets": [{"port": "in", "binds": "a.ItemIn", "role": "item"}],
                      "outlets": [{"port": "out", "binds": "b.ItemOut", "role": "item"}]},
    }
    ops = FakeOps()
    build_molecule(mol, {}, ops)
    moves = [c for c in ops.calls if c[0] == "move"]
    assert len(moves) >= 2
    xs = [m[2] for m in moves]
    assert len(set(xs)) == len(xs)
