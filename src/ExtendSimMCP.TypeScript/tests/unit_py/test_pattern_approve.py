# tests/unit_py/test_pattern_approve.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from pattern_approve import build_library_entry, ApproveError
from molecule_schema import validate_molecule


def _candidate():
    return {
        "wl_fingerprint": "FP1", "support": 3, "nearMiss": False,
        "hblockType": "pure", "kind": "molecule",
        "params": {
            "b3.D": {"type": "number", "required": True, "default": 5, "range": [2, 8]},
            "b2.capacity": {"type": "number", "required": False, "fixed": 1},
        },
        "template": {
            "nodes": [
                {"ref": "b2", "lib": "Item", "type": "Queue", "isHBlock": False,
                 "params": {"capacity": 1}},
                {"ref": "b3", "lib": "Item", "type": "Activity", "isHBlock": False,
                 "params": {"D": "{{b3.D}}"}},
            ],
            "edges": [{"from": "b2.ItemOut", "to": "b3.ItemIn"}],
        },
        "interface": {
            "inlets": [{"binds": "b2.ItemIn", "role": "item"}],
            "outlets": [{"binds": "b3.ItemOut", "role": "item"}],
        },
        "instances": [{"scopeId": "h140", "source": "modelA.mox"}],
        "example": {"b3.D": 5, "b2.capacity": 1},
    }


def _naming():
    return {
        "id": "machine", "intent": "A machine", "seed": "b3",
        "params": {"b3.D": "process_time"},
        "inlet": {"binds": "b2.ItemIn", "port": "in"},
        "outlet": {"binds": "b3.ItemOut", "port": "out"},
    }


def test_build_entry_params_are_friendly_and_only_required():
    e = build_library_entry(_candidate(), _naming())
    assert e["params"] == {"process_time": {"type": "number", "required": True,
                                            "default": 5, "range": [2, 8]}}


def test_build_entry_rewrites_placeholder_to_friendly_name():
    e = build_library_entry(_candidate(), _naming())
    b3 = next(n for n in e["nodes"] if n["ref"] == "b3")
    assert b3["params"]["D"] == "{{process_time}}"


def test_build_entry_sets_seed_on_named_node_only():
    e = build_library_entry(_candidate(), _naming())
    seeds = [n["ref"] for n in e["nodes"] if n.get("seed")]
    assert seeds == ["b3"]


def test_build_entry_normalizes_lib_to_lbr():
    e = build_library_entry(_candidate(), _naming())
    assert all(n["lib"] == "Item.lbr" for n in e["nodes"])


def test_build_entry_infers_edge_kind_flow_from_item_ports():
    e = build_library_entry(_candidate(), _naming())
    assert e["edges"][0] == {"kind": "flow", "from": "b2.ItemOut", "to": "b3.ItemIn"}


def test_build_entry_edge_kind_override():
    naming = _naming()
    naming["edgeKinds"] = {"b3.ItemIn": "side"}
    e = build_library_entry(_candidate(), naming)
    assert e["edges"][0]["kind"] == "side"


def test_build_entry_interface_has_port_binds_role():
    e = build_library_entry(_candidate(), _naming())
    assert e["interface"]["inlets"] == [{"port": "in", "binds": "b2.ItemIn", "role": "item"}]
    assert e["interface"]["outlets"] == [{"port": "out", "binds": "b3.ItemOut", "role": "item"}]


def test_build_entry_provenance_and_example_renamed():
    e = build_library_entry(_candidate(), _naming())
    assert e["provenance"]["mined_from"] == 3
    assert e["provenance"]["wl_fingerprint"] == "FP1"
    assert e["provenance"]["sources"] == ["modelA.mox"]
    assert e["example"]["process_time"] == 5


def test_build_entry_envelope_fields():
    e = build_library_entry(_candidate(), _naming())
    assert e["id"] == "machine" and e["version"] == "1.0" and e["kind"] == "molecule"
    assert e["intent"] == "A machine"
    assert e["attributes"] == {"reads": [], "writes": []}


def test_build_entry_passes_validate_molecule():
    e = build_library_entry(_candidate(), _naming())
    validate_molecule(e, e["example"])   # must not raise


def test_build_entry_composite_is_error():
    c = _candidate()
    c["kind"] = "composite"
    with pytest.raises(ApproveError):
        build_library_entry(c, _naming())


def test_build_entry_bad_seed_is_error():
    naming = _naming()
    naming["seed"] = "nonexistent"
    with pytest.raises(ApproveError):
        build_library_entry(_candidate(), naming)


def test_build_entry_non_word_friendly_name_is_error():
    naming = _naming()
    naming["params"] = {"b3.D": "process time"}   # space -> not \w+
    with pytest.raises(ApproveError):
        build_library_entry(_candidate(), naming)


def test_build_entry_unmapped_param_uses_sanitized_key():
    naming = _naming()
    naming["params"] = {}   # no friendly name for b3.D
    e = build_library_entry(_candidate(), naming)
    assert "b3_D" in e["params"]
    b3 = next(n for n in e["nodes"] if n["ref"] == "b3")
    assert b3["params"]["D"] == "{{b3_D}}"


def test_build_entry_duplicate_friendly_name_is_error():
    naming = _naming()
    naming["params"] = {"b3.D": "rate", "b2.capacity": "rate"}   # two keys -> same name
    with pytest.raises(ApproveError):
        build_library_entry(_candidate(), naming)


def test_build_entry_edge_kind_ignores_ref_only_port_names():
    # a node ref containing "item" must NOT make a non-item-port edge infer "flow"
    c = _candidate()
    c["template"]["nodes"][0]["ref"] = "itemNode"
    c["template"]["edges"] = [{"from": "itemNode.outCon0", "to": "b3.inCon0"}]
    c["interface"]["inlets"] = [{"binds": "itemNode.inCon0", "role": "item"}]
    naming = _naming()
    naming["seed"] = "b3"
    naming["inlet"] = {"binds": "itemNode.inCon0", "port": "in"}
    naming["params"] = {}
    e = build_library_entry(c, naming)
    assert e["edges"][0]["kind"] == "side"   # ports outCon0/inCon0 -> no "item" -> side
