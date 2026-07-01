# tests/unit_py/test_molecule_schema.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from molecule_schema import validate_molecule, MoleculeError, resolve_params

VALID = {
    "id": "m", "version": "1.0", "kind": "molecule",
    "params": {"process_time": {"type": "number", "required": True}},
    "nodes": [
        {"ref": "q",   "lib": "Item.lbr", "type": "Queue", "seed": True},
        {"ref": "act", "lib": "Item.lbr", "type": "Activity", "params": {"D": "{{process_time}}"}},
    ],
    "edges": [{"kind": "flow", "from": "q.ItemOut", "to": "act.ItemIn"}],
    "interface": {"inlets": [{"port": "in", "binds": "q.ItemIn", "role": "item"}],
                  "outlets": [{"port": "out", "binds": "act.ItemOut", "role": "item"}]},
}

def test_valid_molecule_passes():
    validate_molecule(VALID, {"process_time": 3})  # no raise

def test_missing_required_param_fails():
    with pytest.raises(MoleculeError, match="process_time"):
        validate_molecule(VALID, {})

def test_exactly_one_seed_required():
    m = {**VALID, "nodes": [{"ref": "q", "lib": "Item.lbr", "type": "Queue"}]}
    with pytest.raises(MoleculeError, match="seed"):
        validate_molecule(m, {"process_time": 3})

def test_edge_references_unknown_node_fails():
    m = {**VALID, "edges": [{"kind": "flow", "from": "ghost.ItemOut", "to": "act.ItemIn"}]}
    with pytest.raises(MoleculeError, match="ghost"):
        validate_molecule(m, {"process_time": 3})

def test_interface_binds_unknown_node_fails():
    m = {**VALID, "interface": {"inlets": [{"port": "in", "binds": "ghost.ItemIn", "role": "item"}], "outlets": []}}
    with pytest.raises(MoleculeError, match="ghost"):
        validate_molecule(m, {"process_time": 3})

def test_resolve_params_substitutes_placeholders():
    node = {"ref": "act", "params": {"D": "{{process_time}}", "fixed": 5}}
    assert resolve_params(node, {"process_time": 3}) == {"D": 3, "fixed": 5}

def test_edge_kind_must_be_flow_or_side():
    m = {**VALID, "edges": [{"kind": "bogus", "from": "q.ItemOut", "to": "act.ItemIn"}]}
    with pytest.raises(MoleculeError, match="kind"):
        validate_molecule(m, {"process_time": 3})

def test_at_most_one_inlet_and_outlet():
    m = {**VALID, "interface": {
        "inlets": [{"port": "in1", "binds": "q.ItemIn", "role": "item"},
                   {"port": "in2", "binds": "act.ItemIn", "role": "item"}],
        "outlets": []}}
    with pytest.raises(MoleculeError, match="at most one"):
        validate_molecule(m, {"process_time": 3})

def test_resolve_set_attributes_substitutes_placeholder_and_default():
    from molecule_schema import resolve_set_attributes
    node = {"ref": "set", "setAttributes": [{"name": "partType", "value": "{{partType}}"}]}
    out = resolve_set_attributes(node, {"partType": 3})
    assert out == [{"name": "partType", "value": 3, "valueType": "constant"}]


def test_resolve_set_attributes_empty_when_absent():
    from molecule_schema import resolve_set_attributes
    assert resolve_set_attributes({"ref": "set"}, {}) == []


def test_validate_rejects_set_attribute_without_name():
    from molecule_schema import validate_molecule, MoleculeError
    mol = {
        "nodes": [{"ref": "set", "lib": "Item.lbr", "type": "Set", "seed": True,
                   "setAttributes": [{"value": 1}]}],
        "edges": [], "interface": {}, "params": {},
    }
    import pytest
    with pytest.raises(MoleculeError):
        validate_molecule(mol, {})


def test_validate_accepts_valid_set_attribute():
    from molecule_schema import validate_molecule
    mol = {
        "nodes": [{"ref": "set", "lib": "Item.lbr", "type": "Set", "seed": True,
                   "setAttributes": [{"name": "partType", "value": "{{partType}}"}]}],
        "edges": [], "interface": {}, "params": {},
    }
    validate_molecule(mol, {})   # must not raise
