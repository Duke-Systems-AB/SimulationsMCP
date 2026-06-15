"""Unit tests for connection_list array-connector handling.

Regression for the bug where connection_list silently drops connections made
to array-connector slots (e.g. a second source wired into a Queue's ItemIn).

These tests use a scripted fake COM app — no live ExtendSim required.

COM interface exercised by connection_list (and the fix):
  objectIDNext(id, 0)            -> next block id, -1 when done
  GetNumCons(id)                 -> number of *base* connectors
  NodeGetIDIndex(id, conIdx)     -> node index for a connector (0 = unconnected)
  GetConName(id, conIdx)         -> connector name
  ConArrayGetNumCons(id, "name") -> slot count for array connector, -1 if not array

Array slot indexing (see _get_array_connector_index): slot 0 = base index,
slot N>0 = 256 - N. So a Queue ItemIn with a 2nd connection exposes that
connection at connector index 255.
"""

import os
import re
import sys
from unittest import mock

import pytest

# Import simulation_backend from src/
_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import simulation_backend  # noqa: E402


class FakeExtendSimApp:
    """Scripts the Execute/Request COM round-trip from a static model spec.

    model = {
      "order": [5, 14, 18],        # objectIDNext iteration order
      "blocks": {
        blockId: [                 # base connectors, in index order
          {"name": "ItemOut", "node": 4},
          # array connector: base slot is "node"; extra slots keyed by slot index
          {"name": "ItemIn", "node": 4, "slots": {1: 77}},
        ],
      },
    }
    """

    def __init__(self, model):
        self.model = model
        self.global0 = -1
        self.globalStr0 = ""

    # -- helpers -----------------------------------------------------------
    def _base_cons(self, block_id):
        return self.model["blocks"].get(block_id, [])

    def _array_connector(self, block_id):
        for con in self._base_cons(block_id):
            if "slots" in con:
                return con
        return None

    def _node_for(self, block_id, con_idx):
        cons = self._base_cons(block_id)
        if con_idx < len(cons):
            return cons[con_idx].get("node", 0)
        # array slot: con_idx = 256 - slot
        slot = 256 - con_idx
        arr = self._array_connector(block_id)
        if arr is not None:
            return arr.get("slots", {}).get(slot, 0)
        return 0

    def _name_for(self, block_id, con_idx):
        cons = self._base_cons(block_id)
        if con_idx < len(cons):
            return cons[con_idx].get("name", "")
        arr = self._array_connector(block_id)
        return arr.get("name", "") if arr is not None else ""

    def _array_num_cons(self, block_id, name):
        for con in self._base_cons(block_id):
            if con.get("name") == name:
                if "slots" in con:
                    return 1 + max(con["slots"].keys())  # total slot count
                return -1
        return -1

    # -- COM surface -------------------------------------------------------
    def Execute(self, cmd):
        m = re.search(r"objectIDNext\((-?\d+),\s*0\)", cmd)
        if m:
            current = int(m.group(1))
            order = self.model["order"]
            if current == -1:
                self.global0 = order[0] if order else -1
            elif current in order:
                i = order.index(current)
                self.global0 = order[i + 1] if i + 1 < len(order) else -1
            else:
                self.global0 = -1
            return

        m = re.search(r"GetNumCons\((\d+)\)", cmd)
        if m:
            self.global0 = len(self._base_cons(int(m.group(1))))
            return

        m = re.search(r"NodeGetIDIndex\((\d+),\s*(\d+)\)", cmd)
        if m:
            self.global0 = self._node_for(int(m.group(1)), int(m.group(2)))
            return

        m = re.search(r"ConArrayGetNumCons\((\d+),\s*\"([^\"]+)\"\)", cmd)
        if m:
            self.global0 = self._array_num_cons(int(m.group(1)), m.group(2))
            return

        m = re.search(r"GetConName\((\d+),\s*(\d+)\)", cmd, re.IGNORECASE)
        if m:
            self.globalStr0 = self._name_for(int(m.group(1)), int(m.group(2)))
            return

        # Unknown command: leave globals unchanged.

    def Request(self, _system, key):
        if key.startswith("globalStr0"):
            return self.globalStr0
        return str(self.global0)


# Model: Create(5) + Create(14) both wired into Queue(18) ItemIn (array).
#   Create(5).ItemOut  -> node 4  -> Queue.ItemIn slot 0 (con 0,  node 4)
#   Create(14).ItemOut -> node 77 -> Queue.ItemIn slot 1 (con 255, node 77)
TWO_SOURCES_INTO_QUEUE = {
    "order": [5, 14, 18],
    "blocks": {
        5: [{"name": "ItemOut", "node": 4}],
        14: [{"name": "ItemOut", "node": 77}],
        18: [
            {"name": "ItemIn", "node": 4, "slots": {1: 77}},
            {"name": "ItemOut", "node": 0},
        ],
    },
}


def _run_connection_list(model):
    fake = FakeExtendSimApp(model)
    with mock.patch.object(simulation_backend, "get_extendsim_app", return_value=fake):
        return simulation_backend.connection_list()


def _involves(conn, block_id):
    if "from" in conn and "to" in conn:
        return conn["from"]["blockId"] == block_id or conn["to"]["blockId"] == block_id
    return any(ep["blockId"] == block_id for ep in conn.get("endpoints", []))


def test_second_source_into_array_input_is_reported():
    """Create(14) wired to Queue.ItemIn array slot 1 must appear in connection_list."""
    result = _run_connection_list(TWO_SOURCES_INTO_QUEUE)
    conns = result["connections"]

    # The existing, base-slot connection must still be there.
    assert any(_involves(c, 5) and _involves(c, 18) for c in conns), \
        "Create(5) -> Queue connection missing"

    # The array-slot connection (the bug) must now be reported.
    assert any(_involves(c, 14) and _involves(c, 18) for c in conns), \
        "Create(14) -> Queue (array slot 1) connection was dropped"

    # Fully-paired model -> no dangling nodes.
    assert result.get("danglingNodes", []) == []


# A connector wired to a node with no enumerable second endpoint (e.g. a line
# into a hierarchical sub-block). Previously dropped silently.
ONE_SIDED_NODE = {
    "order": [7],
    "blocks": {
        7: [{"name": "ItemOut", "node": 99}],
    },
}


def test_one_sided_node_is_surfaced_as_dangling():
    """A node with a single endpoint must be reported, not silently dropped."""
    result = _run_connection_list(ONE_SIDED_NODE)

    # It is not a normal paired connection.
    assert result["connections"] == []

    # ...but it must be visible as a dangling node so callers know something
    # is wired there.
    dangling = result.get("danglingNodes", [])
    assert any(
        d["nodeIndex"] == 99 and d["endpoint"]["blockId"] == 7
        for d in dangling
    ), "one-sided node 99 (block 7) was silently dropped"
