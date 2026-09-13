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


def _candidate_with_attributes():
    """A candidate whose template has a Set node writing 'partType' (verbatim,
    literal value — as pattern_cluster now carries setAttributes) plus the
    existing Queue node 'reading' partType via its sortAttribute param."""
    c = _candidate()
    c["template"]["nodes"][0]["params"]["sortAttribute"] = "partType"  # b2 = Queue
    c["template"]["nodes"].append({
        "ref": "s1", "lib": "Item", "type": "Set", "isHBlock": False,
        "setAttributes": [{"name": "partType", "value": 5}],
    })
    return c


def test_build_entry_infers_writes_from_set_attributes():
    e = build_library_entry(_candidate_with_attributes(), _naming())
    assert e["attributes"]["writes"] == ["partType"]


def test_build_entry_infers_reads_from_attribute_named_param():
    e = build_library_entry(_candidate_with_attributes(), _naming())
    assert e["attributes"]["reads"] == ["partType"]


def test_build_entry_carries_set_attributes_onto_node():
    e = build_library_entry(_candidate_with_attributes(), _naming())
    s1 = next(n for n in e["nodes"] if n["ref"] == "s1")
    assert s1["setAttributes"] == [{"name": "partType", "value": 5}]


def test_build_entry_no_attributes_is_empty_reads_and_writes():
    e = build_library_entry(_candidate(), _naming())
    assert e["attributes"] == {"reads": [], "writes": []}


def test_build_entry_naming_attributes_override_wins():
    naming = _naming()
    naming["attributes"] = {"reads": ["x"], "writes": ["y"]}
    e = build_library_entry(_candidate_with_attributes(), naming)
    assert e["attributes"] == {"reads": ["x"], "writes": ["y"]}


def test_build_entry_rewrites_placeholder_in_set_attributes_value():
    c = _candidate_with_attributes()
    c["params"]["s1.qty"] = {"type": "number", "required": True, "default": 3}
    c["template"]["nodes"][2]["setAttributes"] = [
        {"name": "partType", "value": "{{s1.qty}}"}]
    naming = _naming()
    naming["params"]["s1.qty"] = "quantity"
    e = build_library_entry(c, naming)
    s1 = next(n for n in e["nodes"] if n["ref"] == "s1")
    assert s1["setAttributes"][0]["value"] == "{{quantity}}"


import json as _json
from pattern_approve import approve_pattern_entry


def test_approve_dry_run_returns_preview_no_write(tmp_path):
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(),
                                dry_run=True, molecules_dir=str(tmp_path))
    assert res["success"] is True
    assert res["preview"]["id"] == "machine"
    assert list(tmp_path.iterdir()) == []   # nothing written


def test_approve_writes_valid_entry(tmp_path):
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(),
                                molecules_dir=str(tmp_path))
    assert res["success"] is True and res["id"] == "machine"
    out = tmp_path / "machine.json"
    assert out.exists()
    written = _json.loads(out.read_text(encoding="utf-8"))
    validate_molecule(written, written["example"])   # written entry is valid
    assert written["kind"] == "molecule"


def test_approve_refuses_overwrite_without_flag(tmp_path):
    approve_pattern_entry(candidate=_candidate(), naming=_naming(), molecules_dir=str(tmp_path))
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(), molecules_dir=str(tmp_path))
    assert res["success"] is False and res["errorCode"] == "ALREADY_EXISTS"


def test_approve_overwrite_with_flag(tmp_path):
    approve_pattern_entry(candidate=_candidate(), naming=_naming(), molecules_dir=str(tmp_path))
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(),
                                overwrite=True, molecules_dir=str(tmp_path))
    assert res["success"] is True


def test_approve_invalid_build_no_write(tmp_path):
    naming = _naming()
    naming["seed"] = "nope"
    res = approve_pattern_entry(candidate=_candidate(), naming=naming, molecules_dir=str(tmp_path))
    assert res["success"] is False and res["errorCode"] == "BUILD_FAILED"
    assert list(tmp_path.iterdir()) == []


def test_approve_from_patterns_path_by_fingerprint(tmp_path):
    patterns_file = tmp_path / "patterns.json"
    patterns_file.write_text(_json.dumps({"patterns": [_candidate()]}), encoding="utf-8")
    res = approve_pattern_entry(patterns_path=str(patterns_file), pattern_fingerprint="FP1",
                                naming=_naming(), molecules_dir=str(tmp_path))
    assert res["success"] is True and res["id"] == "machine"


def test_approve_unknown_fingerprint(tmp_path):
    patterns_file = tmp_path / "patterns.json"
    patterns_file.write_text(_json.dumps({"patterns": [_candidate()]}), encoding="utf-8")
    res = approve_pattern_entry(patterns_path=str(patterns_file), pattern_fingerprint="NOPE",
                                naming=_naming(), molecules_dir=str(tmp_path))
    assert res["success"] is False and res["errorCode"] == "UNKNOWN_FINGERPRINT"


def test_approve_no_candidate_source():
    res = approve_pattern_entry(naming=_naming())
    assert res["success"] is False and res["errorCode"] == "NO_CANDIDATE"
