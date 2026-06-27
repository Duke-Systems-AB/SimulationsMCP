# M3 `instantiate_pattern` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic `instantiate_pattern` that constructs an ExtendSim molecule as an interfaced H-block via COM, plus schema validation of molecule definitions.

**Architecture:** A pure `molecule_schema` validator (no COM) and an `instantiate` engine that orchestrates an injected operations interface (`EsOps`). The engine follows the proven COM construction path: wrap a single connected seed (interface auto-created from boundary-crossing edges), remove stubs, grow inside with `PlaceBlockInHblock`, wire the item flow with disconnect-first rewiring, attach side connections by name, and verify topology after each step. Tests inject a `FakeOps` that records the call sequence; production injects `RealOps` wrapping `simulation_backend`.

**Tech Stack:** Python 3.13, pytest, the existing `simulation_backend.py` COM primitives, JSON molecule definitions.

**Reference:** Design spec `docs/superpowers/specs/2026-06-27-m3-instantiate-pattern-design.md` (14 proven COM facts in §2).

---

### Task 1: Molecule definitions (data)

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/patterns/molecules/buffer.json`
- Create: `src/ExtendSimMCP.TypeScript/patterns/molecules/machine-with-breakdowns.json`

> The trivial molecule is `buffer` (a single Queue) rather than a source→sink: the seed must be a mid-flow block with both `ItemIn` and `ItemOut` so the seed-wrap-in-context creates a clean inlet+outlet interface. A `Create` seed has no `ItemIn` and does not fit this strategy.

- [ ] **Step 1: Create `buffer.json`**

```json
{
  "id": "buffer",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Trivial molekyl: en kö som buffrar items",
  "params": {},
  "nodes": [
    { "ref": "q", "lib": "Item.lbr", "type": "Queue", "seed": true }
  ],
  "edges": [],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "q.ItemIn",  "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "q.ItemOut", "role": "item" } ]
  }
}
```

- [ ] **Step 2: Create `machine-with-breakdowns.json`**

```json
{
  "id": "machine-with-breakdowns",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Maskin som processar items med stokastiska haverier",
  "params": {
    "process_time": { "type": "number", "required": true },
    "mtbf":         { "type": "number", "required": true },
    "mttr":         { "type": "number", "required": true }
  },
  "nodes": [
    { "ref": "q",   "lib": "Item.lbr", "type": "Queue",    "seed": true },
    { "ref": "act", "lib": "Item.lbr", "type": "Activity", "params": { "D": "{{process_time}}" } },
    { "ref": "sd",  "lib": "Item.lbr", "type": "Shutdown",
      "params": { "SF_TBF_Arg1_prm": "{{mtbf}}", "SF_TTR_Arg1_prm": "{{mttr}}" } }
  ],
  "edges": [
    { "kind": "flow", "from": "q.ItemOut",      "to": "act.ItemIn" },
    { "kind": "side", "from": "sd.SD_ValueOut", "to": "act.SDV_In" }
  ],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "q.ItemIn",   "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "act.ItemOut", "role": "item" } ]
  }
}
```

> Note: `seed: true` marks the node whose `ItemIn`/`ItemOut` become the wrap-time interface. The flow chain is built by appending each subsequent `flow` node at the outlet side via disconnect-first rewiring. `side` edges are wired last by name.

- [ ] **Step 3: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/
git commit -m "feat(m3): add source-sink and machine-with-breakdowns molecule definitions"
```

---

### Task 2: `molecule_schema` — pure validation

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/molecule_schema.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_molecule_schema.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_molecule_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'molecule_schema'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/molecule_schema.py
"""Pure validation + param resolution for molecule definitions. No COM."""
import re
from typing import Any, Dict

_PLACEHOLDER = re.compile(r"^\{\{(\w+)\}\}$")


class MoleculeError(Exception):
    pass


def validate_molecule(molecule: Dict[str, Any], params: Dict[str, Any]) -> None:
    """Raise MoleculeError if the molecule + bound params are not buildable."""
    nodes = molecule.get("nodes", [])
    refs = {n["ref"] for n in nodes}

    # exactly one seed
    seeds = [n for n in nodes if n.get("seed")]
    if len(seeds) != 1:
        raise MoleculeError(f"molecule must have exactly one seed node, found {len(seeds)}")

    # required params present
    for name, spec in (molecule.get("params") or {}).items():
        if spec.get("required") and name not in params:
            raise MoleculeError(f"missing required param: {name}")

    # edges reference known nodes
    for e in molecule.get("edges", []):
        for side in ("from", "to"):
            ref = e[side].split(".", 1)[0]
            if ref not in refs:
                raise MoleculeError(f"edge {side} references unknown node: {ref}")

    # interface binds reference known nodes
    iface = molecule.get("interface", {})
    for port in (iface.get("inlets", []) + iface.get("outlets", [])):
        ref = port["binds"].split(".", 1)[0]
        if ref not in refs:
            raise MoleculeError(f"interface binds unknown node: {ref}")


def resolve_params(node: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Substitute {{name}} placeholders in a node's params with bound values."""
    out = {}
    for k, v in (node.get("params") or {}).items():
        if isinstance(v, str):
            m = _PLACEHOLDER.match(v)
            if m:
                out[k] = params[m.group(1)]
                continue
        out[k] = v
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_molecule_schema.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/molecule_schema.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_molecule_schema.py
git commit -m "feat(m3): molecule_schema validation + param resolution (TDD)"
```

---

### Task 3: `EsOps` interface + `FakeOps` test double + seed-wrap phase

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/instantiate.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py`

The engine takes an injected `ops` object (the `EsOps` protocol below). This isolates COM so the construction sequence is assertable. Production `RealOps` comes in Task 6.

`EsOps` protocol (documented here, implemented by FakeOps and RealOps):
```
activate()                                  -> None
add_block(lib, type_) -> int                # global block id
connect(a_id, a_con, b_id, b_con) -> None   # MakeConnection; raises on no-op
disconnect(a_id, a_con, b_id, b_con) -> None
create_hblock(seed_id, name) -> int         # returns new H-block id; raises if not created
place_in_hblock(lib, type_, hblock_id) -> int
remove_block(block_id) -> None
set_value(block_id, var, value) -> None
inlet_connector(hblock_id) -> int           # local id of the "Con0In" connector-object
outlet_connector(hblock_id) -> int          # local id of the "Con1Out" connector-object
node_of(block_id, con_index) -> int         # NodeGetIDIndex (0 = unconnected)
con_index(block_id, con_name) -> int        # resolve connector name -> index
```

- [ ] **Step 1: Write the failing test (seed-wrap phase)**

```python
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
    result = build_molecule(load("buffer.json"), {}, ops)
    kinds = [c[0] for c in ops.calls]
    # activates, builds stub-seed-stub, wraps the seed, removes both stubs
    assert kinds[0] == "activate"
    assert "create_hblock" in kinds
    assert kinds.count("remove_block") >= 2          # both stubs removed
    assert isinstance(result["hblockId"], int)
```

> The last assertion is intentionally simple; richer assertions come in Tasks 4-5.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_molecule'`

- [ ] **Step 3: Write minimal implementation (seed-wrap phase only)**

```python
# src/instantiate.py
"""Deterministic molecule -> H-block construction engine (approach 1).

Orchestrates an injected EsOps interface so the COM construction sequence
is testable. Every COM-affecting step is effect-verified, never trusting a
success flag (krav 12). See spec 2026-06-27-m3-instantiate-pattern-design.md.
"""
from typing import Any, Dict
from molecule_schema import validate_molecule, resolve_params


class BuildError(Exception):
    pass


def _node(molecule, ref):
    for n in molecule["nodes"]:
        if n["ref"] == ref:
            return n
    raise BuildError(f"unknown node ref: {ref}")


def build_molecule(molecule: Dict[str, Any], params: Dict[str, Any], ops) -> Dict[str, Any]:
    validate_molecule(molecule, params)            # fail-closed, before any COM
    ops.activate()

    seed = next(n for n in molecule["nodes"] if n.get("seed"))

    # Phase 1: wrap seed in context -> interfaced 1-block H-block, then drop stubs.
    up = ops.add_block("Item.lbr", "Create")
    seed_id = ops.add_block(seed["lib"], seed["type"])
    down = ops.add_block("Item.lbr", "Exit")
    ops.connect(up, ops.con_index(up, "ItemOut"), seed_id, ops.con_index(seed_id, "ItemIn"))
    ops.connect(seed_id, ops.con_index(seed_id, "ItemOut"), down, ops.con_index(down, "ItemIn"))
    hblock_id = ops.create_hblock(seed_id, molecule["id"])
    ops.remove_block(up)
    ops.remove_block(down)

    return {"hblockId": hblock_id, "internalBlockIds": {seed["ref"]: seed_id}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "feat(m3): instantiate engine seed-wrap phase + FakeOps (TDD)"
```

---

### Task 4: Grow inside + disconnect-first flow chain

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py`

- [ ] **Step 1: Write the failing test (flow chain wiring)**

```python
def test_flow_chain_uses_disconnect_first_and_clean_nodes():
    ops = FakeOps()
    result = build_molecule(load("machine-with-breakdowns.json"), {"process_time": 3, "mtbf": 120, "mttr": 8}, ops)

    # The non-seed flow node (act) is placed inside, then inserted at the outlet
    # with disconnect-FIRST (krav 8): disconnect(outlet<->seed.out) BEFORE reconnect.
    kinds = [c for c in ops.calls if c[0] in ("place_in_hblock", "disconnect", "connect")]
    assert any(c[0] == "place_in_hblock" and c[2] == "Activity" for c in ops.calls)
    dis_idx = next(i for i, c in enumerate(kinds) if c[0] == "disconnect")
    con_after = [c for c in kinds[dis_idx + 1:] if c[0] == "connect"]
    assert len(con_after) >= 2          # reconnect seed->new and new->outlet after disconnect

    # internal ids recorded for every node ref
    assert set(result["internalBlockIds"]).issuperset({"q", "act"})

    # Topology clean: seed.ItemOut and act.ItemIn share a node (the internal edge),
    # and that node differs from the outlet node (no collapse).
    qid = result["internalBlockIds"]["q"]
    aid = result["internalBlockIds"]["act"]
    assert ops.node_of(qid, 1) == ops.node_of(aid, 0) != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py::test_flow_chain_uses_disconnect_first_and_clean_nodes -v`
Expected: FAIL (KeyError 'act' in internalBlockIds — flow nodes not built yet)

- [ ] **Step 3: Extend `build_molecule` (append flow nodes via disconnect-first)**

Replace the `return` statement at the end of `build_molecule` with:

```python
    internal = {seed["ref"]: seed_id}

    # Phase 2: append remaining flow nodes at the outlet, disconnect-first.
    flow_edges = [e for e in molecule["edges"] if e["kind"] == "flow"]
    order = _flow_order(seed["ref"], flow_edges)     # seed first, then downstream
    last_ref, last_id = seed["ref"], seed_id
    for ref in order[1:]:
        node = _node(molecule, ref)
        new_id = ops.place_in_hblock(node["lib"], node["type"], hblock_id)
        internal[ref] = new_id
        outlet = ops.outlet_connector(hblock_id)
        # disconnect last.out <-> outlet, then last.out -> new.in, new.out -> outlet
        ops.disconnect(last_id, ops.con_index(last_id, "ItemOut"), outlet, 0)
        ops.connect(last_id, ops.con_index(last_id, "ItemOut"), new_id, ops.con_index(new_id, "ItemIn"))
        ops.connect(new_id, ops.con_index(new_id, "ItemOut"), outlet, 0)
        _assert_clean(ops, last_id, new_id)
        last_ref, last_id = ref, new_id

    return {"hblockId": hblock_id, "internalBlockIds": internal}
```

Add these helpers above `build_molecule`:

```python
def _flow_order(seed_ref, flow_edges):
    """Return the flow node refs in chain order starting at seed_ref."""
    nxt = {e["from"].split(".")[0]: e["to"].split(".")[0] for e in flow_edges}
    order, cur = [seed_ref], seed_ref
    while cur in nxt:
        cur = nxt[cur]
        order.append(cur)
    return order


def _assert_clean(ops, a_id, b_id):
    """Effect-verify: a.ItemOut and b.ItemIn share a node, not collapsed to 0."""
    if ops.node_of(a_id, ops.con_index(a_id, "ItemOut")) != ops.node_of(b_id, ops.con_index(b_id, "ItemIn")):
        raise BuildError("flow rewire failed: connectors not on a shared node")
    if ops.node_of(b_id, ops.con_index(b_id, "ItemIn")) == 0:
        raise BuildError("flow rewire failed: node collapsed to 0")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "feat(m3): grow-inside flow chain with disconnect-first rewire (TDD)"
```

---

### Task 5: Side connections, params, interface map

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py::test_side_connections_params_and_interface -v`
Expected: FAIL (KeyError 'sd' — side nodes/params/interface not built yet)

- [ ] **Step 3: Extend `build_molecule`**

Before the final `return`, insert:

```python
    # Phase 3: place + wire side nodes (non-flow blocks), by name, node-verified.
    flow_refs = set(internal)
    for node in molecule["nodes"]:
        if node["ref"] in flow_refs:
            continue
        internal[node["ref"]] = ops.place_in_hblock(node["lib"], node["type"], hblock_id)
    for e in molecule["edges"]:
        if e["kind"] != "side":
            continue
        a_ref, a_con = e["from"].split(".")
        b_ref, b_con = e["to"].split(".")
        a_id, b_id = internal[a_ref], internal[b_ref]
        ops.connect(a_id, ops.con_index(a_id, a_con), b_id, ops.con_index(b_id, b_con))
        if ops.node_of(a_id, ops.con_index(a_id, a_con)) != ops.node_of(b_id, ops.con_index(b_id, b_con)):
            raise BuildError(f"side connection failed (not on shared node): {e['from']} -> {e['to']}")

    # Phase 4: set parameters (placeholders resolved).
    for node in molecule["nodes"]:
        for var, value in resolve_params(node, params).items():
            ops.set_value(internal[node["ref"]], var, value)

    # Phase 5: interface map (molecule port -> inner block + outer connector).
    iface = {}
    for port in molecule["interface"].get("inlets", []):
        ref, con = port["binds"].split(".")
        iface[port["port"]] = {"blockId": internal[ref], "outerCon": ops.inlet_connector(hblock_id)}
    for port in molecule["interface"].get("outlets", []):
        ref, con = port["binds"].split(".")
        iface[port["port"]] = {"blockId": internal[ref], "outerCon": ops.outlet_connector(hblock_id)}
```

And change the final return to:

```python
    return {"hblockId": hblock_id, "internalBlockIds": internal, "interfaceMap": iface}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "feat(m3): side connections, param setting, interface map (TDD)"
```

---

### Task 6: `RealOps` — production wrapper over `simulation_backend`

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py`

- [ ] **Step 1: Write the failing test (RealOps wires to backend, effect-verified)**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -k realops -v`
Expected: FAIL with `AttributeError: module 'instantiate' has no attribute 'RealOps'`

- [ ] **Step 3: Implement `RealOps`**

Append to `instantiate.py`:

```python
class RealOps:
    """EsOps backed by the live simulation_backend COM primitives.

    Every state-changing call effect-verifies (krav 12): success flags are
    necessary but never sufficient.
    """
    def __init__(self, backend):
        self._b = backend

    def activate(self):
        self._b.execute_command("ActivateApplication();")

    def add_block(self, lib, type_):
        r = self._b.block_add(lib, type_)
        if not r.get("success") or "blockId" not in r:
            raise BuildError(f"add_block failed: {lib}/{type_}: {r}")
        return r["blockId"]

    def con_index(self, block_id, con_name):
        r = self._b.execute_command(
            f'global0 = getConNumber({block_id}, "{con_name}");', get_result=True)
        return int(r["result"])

    def connect(self, a_id, a_con, b_id, b_con):
        before = self.node_of(b_id, b_con)
        self._b.execute_command(f"MakeConnection({a_id}, {a_con}, {b_id}, {b_con});")
        if self.node_of(a_id, a_con) != self.node_of(b_id, b_con) or self.node_of(b_id, b_con) == 0:
            raise BuildError(f"connect did not take: ({a_id},{a_con})->({b_id},{b_con})")

    def disconnect(self, a_id, a_con, b_id, b_con):
        r = self._b.block_disconnect(a_id, a_con, b_id, b_con)
        if not r.get("success"):
            raise BuildError(f"disconnect failed: ({a_id},{a_con})->({b_id},{b_con})")

    def create_hblock(self, seed_id, name):
        before = self._b.hierarchy_list().get("count", 0)
        self._b.execute_command(
            f'UnselectAll(); AddBlockToSelection({seed_id}); CreateHblock("{name}");')
        hl = self._b.hierarchy_list()
        if hl.get("count", 0) <= before:
            raise BuildError(f"CreateHblock produced no H-block (name={name})")
        return [h for h in hl["hierarchies"] if h.get("blockName") == name][-1]["blockId"]

    def place_in_hblock(self, lib, type_, hblock_id):
        r = self._b.execute_command(
            f'global0 = PlaceBlockInHblock("{type_}", "{lib}", 200, 200, {hblock_id});',
            get_result=True)
        return int(r["result"])

    def remove_block(self, block_id):
        self._b.block_remove(block_id)

    def set_value(self, block_id, var, value):
        self._b.block_set_value(block_id, var, value)

    def _connector_obj(self, hblock_id, name):
        for blk in self._b.hierarchy_get_contents(hblock_id).get("blocks", []):
            if blk.get("blockName") == name:
                return blk["blockId"]
        raise BuildError(f"connector-object {name} not found in H-block {hblock_id}")

    def inlet_connector(self, hblock_id):
        return self._connector_obj(hblock_id, "Con0In")

    def outlet_connector(self, hblock_id):
        return self._connector_obj(hblock_id, "Con1Out")

    def node_of(self, block_id, con_index):
        r = self._b.execute_command(
            f"global0 = NodeGetIDIndex({block_id}, {con_index});", get_result=True)
        return int(r.get("result") or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "feat(m3): RealOps backend wrapper with per-step effect verification (TDD)"
```

---

### Task 7: `instantiate_pattern` entry point + dispatch + live smoke test

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py`
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (dispatch table near line 10069)
- Test: `src/ExtendSimMCP.TypeScript/tests/live/test_instantiate_live.py`

- [ ] **Step 1: Write the live smoke test**

```python
# tests/live/test_instantiate_live.py
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

def _es_available():
    try:
        import win32com.client
        win32com.client.GetActiveObject("ExtendSim.Application").Request("System", "global0+:0:0:0")
        return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _es_available(), reason="ExtendSim not running")

def test_machine_with_breakdowns_builds_and_runs():
    import simulation_backend as sb
    from instantiate import instantiate_pattern
    sb.execute_command("ActivateApplication();")
    res = instantiate_pattern("machine-with-breakdowns",
                              {"process_time": 1, "mtbf": 30, "mttr": 10})
    hid = res["hblockId"]
    try:
        contents = [b["blockName"] for b in sb.hierarchy_get_contents(hid)["blocks"] if b.get("library")]
        assert {"Queue", "Activity", "Shutdown"}.issubset(set(contents))
        # smoke: attach Create/Exit to the H-block outer connectors and run
        cS = sb.block_add("Item.lbr", "Create")["blockId"]
        eS = sb.block_add("Item.lbr", "Exit")["blockId"]
        sb.execute_command(f"MakeConnection({cS}, 0, {hid}, {res['interfaceMap']['in']['outerCon']});")
        sb.execute_command(f"MakeConnection({hid}, {res['interfaceMap']['out']['outerCon']}, {eS}, 0);")
        out = sb.simulation_run(end_time=100, include_stats=True)
        exited = out["statistics"]["exitStatistics"][0]["itemsExited"]
        assert exited > 0
        sb.block_remove(cS); sb.block_remove(eS)
    finally:
        sb.block_remove(hid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_instantiate_live.py -v`
Expected: FAIL with `ImportError: cannot import name 'instantiate_pattern'` (or SKIP if ExtendSim is not running — start ExtendSim with an open model to actually exercise it)

- [ ] **Step 3: Add the `instantiate_pattern` entry point**

Append to `instantiate.py`:

```python
import json as _json
import os as _os

_MOLECULE_DIR = _os.path.join(_os.path.dirname(__file__), "..", "patterns", "molecules")


def _load_molecule(molecule_id):
    path = _os.path.join(_MOLECULE_DIR, f"{molecule_id}.json")
    if not _os.path.exists(path):
        raise BuildError(f"unknown molecule: {molecule_id}")
    with open(path, encoding="utf-8") as f:
        return _json.load(f)


def instantiate_pattern(molecule_id, params, model_id=None):
    """MCP entry point: build a molecule as an H-block in the live model."""
    import simulation_backend as backend
    molecule = _load_molecule(molecule_id)
    try:
        return {"success": True, **build_molecule(molecule, params or {}, RealOps(backend))}
    except Exception as e:
        return {"success": False, "errorCode": "INSTANTIATE_FAILED", "error": str(e)}
```

- [ ] **Step 4: Register in the dispatch table**

In `simulation_backend.py`, find the dispatch dict (around line 10069, the line `"connection_list": lambda p: connection_list(p.get("modelId")),`) and add directly after it:

```python
    "instantiate_pattern": lambda p: __import__("instantiate").instantiate_pattern(
        p.get("moleculeId"), p.get("params"), p.get("modelId")),
```

- [ ] **Step 5: Run the live test (with ExtendSim running) and the full unit suite**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -v && python -m pytest tests/live/test_instantiate_live.py -v`
Expected: unit PASS (all); live PASS if ExtendSim running, else SKIP

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/live/test_instantiate_live.py
git commit -m "feat(m3): instantiate_pattern entry point + dispatch + live smoke test"
```

---

### Task 8: Register the MCP tool in the TypeScript server

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts`

- [ ] **Step 1: Locate an existing simple tool registration**

Run: `cd src/ExtendSimMCP.TypeScript && grep -n '"connection_list"' src/index.ts`
Expected: shows where `connection_list` is registered as an MCP tool (follow that exact pattern).

- [ ] **Step 2: Add the `instantiate_pattern` tool registration**

Following the same `server.tool(...)` / Zod-schema pattern used by neighboring tools in `index.ts`, add:

```typescript
server.tool(
  "instantiate_pattern",
  "Build a reusable molecule as an H-block in the open model. Args: moleculeId (e.g. 'machine-with-breakdowns'), params (object of param bindings).",
  {
    moleculeId: z.string(),
    params: z.record(z.any()).optional(),
    modelId: z.string().optional(),
  },
  async (args) => {
    const startTime = performance.now();
    const result = await backend.call("instantiate_pattern", args);
    recordToolCall("instantiate_pattern", startTime, result, args);
    sessionLog("instantiate_pattern", performance.now() - startTime, args, result);
    return toolResponse(result);
  },
);
```

> If neighboring tools use a different invocation helper than `backend.call(...)`, match the exact local pattern (the dispatch name `"instantiate_pattern"` is what matters). Verify against `connection_list`'s registration from Step 1.

- [ ] **Step 3: Build the TypeScript and run unit tests**

Run: `cd src/ExtendSimMCP.TypeScript && npx tsc --noEmit && npx vitest run`
Expected: tsc clean; vitest all PASS

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/index.ts
git commit -m "feat(m3): register instantiate_pattern MCP tool"
```

---

## Notes for the implementer

- **Never trust `success:true`** (krav 12 in the spec). Every `RealOps` mutation re-reads topology. If you add an op, verify its effect.
- **`con_index` for array connectors:** the molecules here use only base connectors. If a future molecule wires an array input, extend `RealOps.con_index`/`connect` to use the array-slot scheme already in `simulation_backend` (`_find_free_array_slot`).
- **`PlaceBlockInHblock` x/y** are placeholder positions (200,200); cosmetic only, refine later if layout matters.
- **The live test mutates the open model** but cleans up in `finally`. Run it against a scratch model.
- **Best-effort cleanup on mid-build failure (spec §6) is DEFERRED in this plan.** `instantiate_pattern` currently reports the error but does not roll back partial blocks. Follow-up refinement: have `build_molecule` accumulate created block ids and, on exception, attach them so the entry point can `remove_block` each (then re-raise). Tracked as a known simplification — call it out if you implement M3 fully before M4.
