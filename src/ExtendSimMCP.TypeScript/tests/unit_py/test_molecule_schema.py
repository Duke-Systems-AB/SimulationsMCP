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
