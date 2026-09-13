# tests/unit_py/test_compose_validate.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from compose import validate_flow, FlowError

def mol(reads=None, writes=None):
    return {
        "interface": {
            "inlets":  [{"port": "in",  "binds": "q.ItemIn",   "role": "item"}],
            "outlets": [{"port": "out", "binds": "q.ItemOut",  "role": "item"}],
        },
        "attributes": {"reads": reads or [], "writes": writes or []},
    }

FLOW = {
    "id": "f",
    "instances": [
        {"ref": "m1", "pattern": "p"},
        {"ref": "m2", "pattern": "p"},
    ],
    "wiring": [{"from": "m1.out", "to": "m2.in"}],
}

def test_valid_flow_passes():
    validate_flow(FLOW, {"p": mol()})  # no raise

def test_duplicate_instance_ref_fails():
    f = {**FLOW, "instances": [{"ref": "m1", "pattern": "p"}, {"ref": "m1", "pattern": "p"}]}
    with pytest.raises(FlowError, match="duplicate"):
        validate_flow(f, {"p": mol()})

def test_unknown_instance_in_wiring_fails():
    f = {**FLOW, "wiring": [{"from": "ghost.out", "to": "m2.in"}]}
    with pytest.raises(FlowError, match="ghost"):
        validate_flow(f, {"p": mol()})

def test_unknown_port_fails():
    f = {**FLOW, "wiring": [{"from": "m1.nope", "to": "m2.in"}]}
    with pytest.raises(FlowError, match="outlet"):
        validate_flow(f, {"p": mol()})

def test_role_mismatch_fails():
    item = mol()
    val_in = {"interface": {"inlets": [{"port": "in", "binds": "q.ValuesIn", "role": "value"}],
                            "outlets": [{"port": "out", "binds": "q.ItemOut", "role": "item"}]},
              "attributes": {"reads": [], "writes": []}}
    f = {"id": "f",
         "instances": [{"ref": "m1", "pattern": "a"}, {"ref": "m2", "pattern": "b"}],
         "wiring": [{"from": "m1.out", "to": "m2.in"}]}
    with pytest.raises(FlowError, match="role"):
        validate_flow(f, {"a": item, "b": val_in})

def test_attribute_contract_unsatisfied_fails():
    f = {"id": "f",
         "instances": [{"ref": "m1", "pattern": "plain"}, {"ref": "m2", "pattern": "reader"}],
         "wiring": [{"from": "m1.out", "to": "m2.in"}]}
    with pytest.raises(FlowError, match="partType"):
        validate_flow(f, {"plain": mol(), "reader": mol(reads=["partType"])})

def test_attribute_contract_satisfied_by_upstream_writer():
    f = {"id": "f",
         "instances": [{"ref": "m1", "pattern": "writer"}, {"ref": "m2", "pattern": "reader"}],
         "wiring": [{"from": "m1.out", "to": "m2.in"}]}
    validate_flow(f, {"writer": mol(writes=["partType"]), "reader": mol(reads=["partType"])})  # no raise

def test_self_loop_does_not_satisfy_own_read():
    # An instance must not satisfy its own attribute read via a self-loop.
    f = {"id": "f",
         "instances": [{"ref": "m1", "pattern": "rw"}],
         "wiring": [{"from": "m1.out", "to": "m1.in"}]}
    with pytest.raises(FlowError, match="partType"):
        validate_flow(f, {"rw": mol(reads=["partType"], writes=["partType"])})
