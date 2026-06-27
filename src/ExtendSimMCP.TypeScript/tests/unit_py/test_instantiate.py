# tests/unit_py/test_instantiate.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import json
import pytest
from instantiate import build_molecule

def load(name):
    p = os.path.join(_SRC, "..", "patterns", "molecules", name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class FakeOps:
    """Records the construction call sequence and models enough topology
    (krav 2-14) for the engine's verification to pass.

    Connector names per block type are scripted in CONS. node_of returns a
    shared node id when two connectors have been connect()-ed.
    """
    CONS = {
        "Create":   {"ItemOut": 0},
        "Exit":     {"ItemIn": 0},
        "Queue":    {"ItemIn": 0, "ItemOut": 1},
        "Activity": {"ItemIn": 0, "ItemOut": 1, "SDV_In": 6},
        "Shutdown": {"SD_ValueOut": 1},
    }

    def __init__(self):
        self.calls = []
        self._next_id = 1
        self._next_node = 1000
        self._types = {}                 # block_id -> type
        self._nodes = {}                 # (block_id, con_index) -> node id
        self._hblocks = {}               # hblock_id -> {"inlet": cid, "outlet": cid}

    def _new_id(self):
        i = self._next_id; self._next_id += 1; return i

    def activate(self):
        self.calls.append(("activate",))

    def add_block(self, lib, type_):
        bid = self._new_id(); self._types[bid] = type_
        self.calls.append(("add_block", lib, type_, bid)); return bid

    def con_index(self, block_id, con_name):
        return self.CONS[self._types[block_id]][con_name]

    def connect(self, a_id, a_con, b_id, b_con):
        self.calls.append(("connect", a_id, a_con, b_id, b_con))
        node = self._next_node; self._next_node += 1
        self._nodes[(a_id, a_con)] = node
        self._nodes[(b_id, b_con)] = node

    def disconnect(self, a_id, a_con, b_id, b_con):
        self.calls.append(("disconnect", a_id, a_con, b_id, b_con))
        self._nodes.pop((a_id, a_con), None)
        self._nodes.pop((b_id, b_con), None)

    def create_hblock(self, seed_id, name):
        hid = self._new_id()
        inlet = self._new_id(); outlet = self._new_id()
        self._types[inlet] = "_con"; self._types[outlet] = "_con"
        self._hblocks[hid] = {"inlet": inlet, "outlet": outlet}
        # wrap-time interface: seed.ItemIn<->inlet, seed.ItemOut<->outlet share nodes
        self._nodes[(inlet, 0)] = self._nodes.get((seed_id, 0), self._mk_node(seed_id, 0))
        self._nodes[(outlet, 0)] = self._nodes.get((seed_id, 1), self._mk_node(seed_id, 1))
        self.calls.append(("create_hblock", seed_id, name, hid)); return hid

    def _mk_node(self, bid, con):
        n = self._next_node; self._next_node += 1; self._nodes[(bid, con)] = n; return n

    def place_in_hblock(self, lib, type_, hblock_id):
        bid = self._new_id(); self._types[bid] = type_
        self.calls.append(("place_in_hblock", lib, type_, hblock_id, bid)); return bid

    def remove_block(self, block_id):
        self.calls.append(("remove_block", block_id))

    def set_value(self, block_id, var, value):
        self.calls.append(("set_value", block_id, var, value))

    def inlet_connector(self, hblock_id):
        return self._hblocks[hblock_id]["inlet"]

    def outlet_connector(self, hblock_id):
        return self._hblocks[hblock_id]["outlet"]

    def node_of(self, block_id, con_index):
        return self._nodes.get((block_id, con_index), 0)


def test_seed_is_wrapped_in_context_then_stubs_removed():
    ops = FakeOps()
    result = build_molecule(load("source-sink.json"), {}, ops)
    kinds = [c[0] for c in ops.calls]
    # activates, builds stub-seed-stub, wraps the seed, removes both stubs
    assert kinds[0] == "activate"
    assert "create_hblock" in kinds
    assert kinds.count("remove_block") >= 2          # both stubs removed
    assert isinstance(result["hblockId"], int)
