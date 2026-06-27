# M4 `compose_flow` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a whole ExtendSim process flow by instantiating several molecules (M3) and wiring their H-blocks outlet→inlet, validating role compatibility and the declared attribute contract.

**Architecture:** A new `compose.py` with a pure `validate_flow` (role + attribute-contract checks over the wiring graph), an ops-injected `build_flow(flow_def, ops)` orchestration that reuses `instantiate.build_molecule`, and a `compose_flow` MCP entry point. The existing `FakeOps` test double is extracted to a shared helper so both M3 and M4 unit tests use it. H-block↔H-block wiring uses the same verified `ops.connect` as M3.

**Tech Stack:** Python 3.13, pytest, the M3 engine (`src/instantiate.py`), JSON molecule definitions, the TS MCP server.

**Reference:** Spec `docs/superpowers/specs/2026-06-27-m4-compose-flow-design.md`. M3 returns from `build_molecule`: `{hblockId, internalBlockIds, interfaceMap}` where `interfaceMap` is `{portName: {blockId, outerCon}}`.

---

### Task 1: Declare `attributes` on molecules

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/patterns/molecules/buffer.json`
- Modify: `src/ExtendSimMCP.TypeScript/patterns/molecules/machine-with-breakdowns.json`

- [ ] **Step 1: Add an empty `attributes` block to `buffer.json`** — insert a top-level `"attributes"` key (after `"params"`) so the file contains:

```json
  "attributes": { "reads": [], "writes": [] },
```

The full `buffer.json` becomes:
```json
{
  "id": "buffer",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Trivial molekyl: en kö som buffrar items",
  "params": {},
  "attributes": { "reads": [], "writes": [] },
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

- [ ] **Step 2: Add the same `attributes` block to `machine-with-breakdowns.json`** — insert `"attributes": { "reads": [], "writes": [] },` immediately after the `"params": { ... },` block (before `"nodes"`). Do not change any other field.

- [ ] **Step 3: Verify both files are valid JSON**

Run: `cd src/ExtendSimMCP.TypeScript && python -c "import json; [json.load(open('patterns/molecules/'+f, encoding='utf-8')) for f in ('buffer.json','machine-with-breakdowns.json')]; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Confirm the M3 suite still passes**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/
git commit -m "feat(m4): declare empty attributes block on molecules"
```

---

### Task 2: `validate_flow` — pure flow validation

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/compose.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_compose_validate.py`

- [ ] **Step 1: Write the failing tests**

```python
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
    val = mol()
    item = mol()
    # make m2's inlet a different role
    val_in = {"interface": {"inlets": [{"port": "in", "binds": "q.ValuesIn", "role": "value"}],
                            "outlets": [{"port": "out", "binds": "q.ItemOut", "role": "item"}]},
              "attributes": {"reads": [], "writes": []}}
    f = {"id": "f",
         "instances": [{"ref": "m1", "pattern": "a"}, {"ref": "m2", "pattern": "b"}],
         "wiring": [{"from": "m1.out", "to": "m2.in"}]}
    with pytest.raises(FlowError, match="role"):
        validate_flow(f, {"a": item, "b": val_in})

def test_attribute_contract_unsatisfied_fails():
    # m2 reads "partType" but nothing upstream writes it
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_compose_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'compose'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/compose.py
"""Compose molecule instances into a whole flow (M4).

build_flow orchestrates an injected EsOps interface (reuses the M3 engine);
validate_flow is pure (role + declared attribute-contract checks). See spec
2026-06-27-m4-compose-flow-design.md.
"""
import collections
from typing import Any, Dict


class FlowError(Exception):
    pass


def _port_role(molecule, port, kind):
    for p in molecule.get("interface", {}).get(kind, []):
        if p["port"] == port:
            return p["role"]
    return None


def _attrs(molecule):
    a = molecule.get("attributes", {})
    return set(a.get("reads", [])), set(a.get("writes", []))


def validate_flow(flow_def: Dict[str, Any], molecules: Dict[str, Any]) -> None:
    """Raise FlowError if the flow is not buildable. `molecules` maps pattern id -> molecule dict."""
    instances = flow_def.get("instances", [])
    refs = [i["ref"] for i in instances]
    if len(refs) != len(set(refs)):
        raise FlowError("duplicate instance ref in flow")
    ref_to_pattern = {i["ref"]: i["pattern"] for i in instances}

    edges = []
    for w in flow_def.get("wiring", []):
        a_ref, a_port = w["from"].split(".", 1)
        b_ref, b_port = w["to"].split(".", 1)
        if a_ref not in ref_to_pattern:
            raise FlowError(f"wiring references unknown instance: {a_ref}")
        if b_ref not in ref_to_pattern:
            raise FlowError(f"wiring references unknown instance: {b_ref}")
        a_role = _port_role(molecules[ref_to_pattern[a_ref]], a_port, "outlets")
        b_role = _port_role(molecules[ref_to_pattern[b_ref]], b_port, "inlets")
        if a_role is None:
            raise FlowError(f"unknown outlet port: {w['from']}")
        if b_role is None:
            raise FlowError(f"unknown inlet port: {w['to']}")
        if a_role != b_role:
            raise FlowError(f"role mismatch: {w['from']}({a_role}) -> {w['to']}({b_role})")
        edges.append((a_ref, b_ref))

    _check_attribute_contract(ref_to_pattern, molecules, edges)


def _check_attribute_contract(ref_to_pattern, molecules, edges):
    preds = collections.defaultdict(set)
    for a, b in edges:
        preds[b].add(a)

    def ancestors(node):
        seen, stack = set(), list(preds[node])
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack.extend(preds[p])
        return seen

    for ref, pattern in ref_to_pattern.items():
        reads, _ = _attrs(molecules[pattern])
        if not reads:
            continue
        upstream_writes = set()
        for anc in ancestors(ref):
            _, w = _attrs(molecules[ref_to_pattern[anc]])
            upstream_writes |= w
        missing = reads - upstream_writes
        if missing:
            raise FlowError(
                f"instance {ref} reads {sorted(missing)} but no upstream instance writes them")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_compose_validate.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/compose.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_compose_validate.py
git commit -m "feat(m4): validate_flow (role + attribute contract, pure) (TDD)"
```

---

### Task 3: Extract `FakeOps` to a shared test helper

The M4 `build_flow` test needs the same `FakeOps` double the M3 tests use. Move it to a shared module so both import it (DRY).

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py`
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py`

- [ ] **Step 1: Create `fake_ops.py` with the `FakeOps` class and the `load` helper.** Copy the `FakeOps` class definition and the `load(name)` function **exactly** as they currently exist at the top of `tests/unit_py/test_instantiate.py` (the class with `CONS`, `__init__`, `activate`, `add_block`, `con_index`, `connect`, `disconnect`, `create_hblock`, `_mk_node`, `place_in_hblock`, `remove_block`, `set_value`, `inlet_connector`, `outer_index`, `node_of`; and the `load` function that reads `patterns/molecules/<name>`). Put this at the top of `fake_ops.py`:

```python
# tests/unit_py/fake_ops.py
"""Shared test double for the EsOps interface + molecule loader (used by M3 and M4 unit tests)."""
import os, json

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")

def load(name):
    p = os.path.join(_SRC, "..", "patterns", "molecules", name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)
```

Then paste the existing `FakeOps` class below it (verbatim from `test_instantiate.py`).

- [ ] **Step 2: Update `test_instantiate.py` to import from the helper.** Delete the in-file `FakeOps` class definition and the in-file `load` function from `test_instantiate.py`, and add this import near the top (after the existing `sys.path` block, replacing the now-removed `load`):

```python
from fake_ops import FakeOps, load
```

Leave all test functions unchanged.

- [ ] **Step 3: Run the M3 suite to confirm the refactor is behaviour-preserving**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`
Expected: 15 passed (same as before the move)

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "refactor(m4): extract FakeOps + load to shared test helper"
```

---

### Task 4: `build_flow` — ops-injected orchestration

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/compose.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_compose_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit_py/test_compose_build.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps
from compose import build_flow

FLOW = {
    "id": "line2",
    "instances": [
        {"ref": "m1", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
        {"ref": "m2", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
    ],
    "wiring": [{"from": "m1.out", "to": "m2.in"}],
}

def test_build_flow_instantiates_each_and_wires_hblocks():
    ops = FakeOps()
    res = build_flow(FLOW, ops)

    # both molecules instantiated -> two distinct H-blocks recorded
    assert set(res["instances"]) == {"m1", "m2"}
    h1 = res["instances"]["m1"]["hblockId"]
    h2 = res["instances"]["m2"]["hblockId"]
    assert h1 != h2

    # the inter-molecule wiring connected m1's outlet H-block connector to m2's inlet
    out_idx = res["instances"]["m1"]["interfaceMap"]["out"]["outerCon"]
    in_idx = res["instances"]["m2"]["interfaceMap"]["in"]["outerCon"]
    assert ("connect", h1, out_idx, h2, in_idx) in ops.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_compose_build.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_flow'`

- [ ] **Step 3: Add `build_flow` to `compose.py`** (append at the end):

```python
def build_flow(flow_def, ops):
    """Instantiate every molecule instance and wire them per the flow definition.

    Reuses the M3 engine; `ops` is the injected EsOps interface.
    """
    from instantiate import build_molecule, _load_molecule
    molecules = {i["pattern"]: _load_molecule(i["pattern"]) for i in flow_def["instances"]}
    validate_flow(flow_def, molecules)

    instances = {}
    for i in flow_def["instances"]:
        res = build_molecule(molecules[i["pattern"]], i.get("params") or {}, ops)
        instances[i["ref"]] = {"hblockId": res["hblockId"], "interfaceMap": res["interfaceMap"]}

    for w in flow_def.get("wiring", []):
        a_ref, a_port = w["from"].split(".", 1)
        b_ref, b_port = w["to"].split(".", 1)
        a, b = instances[a_ref], instances[b_ref]
        ops.connect(a["hblockId"], a["interfaceMap"][a_port]["outerCon"],
                    b["hblockId"], b["interfaceMap"][b_port]["outerCon"])

    return {"flowId": flow_def.get("id"), "instances": instances,
            "wiring": flow_def.get("wiring", [])}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`
Expected: all pass (24 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/compose.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_compose_build.py
git commit -m "feat(m4): build_flow orchestration (instantiate + wire H-blocks) (TDD)"
```

---

### Task 5: `compose_flow` entry point + dispatch + live test

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/compose.py`
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (dispatch table)
- Test: `src/ExtendSimMCP.TypeScript/tests/live/test_compose_flow_live.py`

- [ ] **Step 1: Create `tests/live/test_compose_flow_live.py`**

```python
# tests/live/test_compose_flow_live.py
import os, sys
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

def test_two_machine_flow_builds_and_runs():
    import simulation_backend as sb
    from compose import compose_flow
    sb.execute_command("ActivateApplication();")
    flow = {
        "id": "line2",
        "instances": [
            {"ref": "m1", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
            {"ref": "m2", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
        ],
        "wiring": [{"from": "m1.out", "to": "m2.in"}],
    }
    res = compose_flow(flow)
    assert res.get("success"), f"compose_flow failed: {res}"
    m1, m2 = res["instances"]["m1"], res["instances"]["m2"]
    cS = eS = None
    try:
        cS = sb.block_add("Item.lbr", "Create")["blockId"]
        eS = sb.block_add("Item.lbr", "Exit")["blockId"]
        sb.execute_command(f"MakeConnection({cS}, 0, {m1['hblockId']}, {m1['interfaceMap']['in']['outerCon']});")
        sb.execute_command(f"MakeConnection({m2['hblockId']}, {m2['interfaceMap']['out']['outerCon']}, {eS}, 0);")
        out = sb.simulation_run(end_time=100, include_stats=True)
        exited = next(e["itemsExited"] for e in out["statistics"]["exitStatistics"]
                      if e["blockId"] == eS)
        assert exited > 0
    finally:
        if eS is not None:
            sb.block_remove(eS)
        if cS is not None:
            sb.block_remove(cS)
        sb.block_remove(m2["hblockId"])
        sb.block_remove(m1["hblockId"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_compose_flow_live.py -v`
Expected: FAIL with `ImportError: cannot import name 'compose_flow'` (ExtendSim is running, so it will not skip)

- [ ] **Step 3: Add the `compose_flow` entry point to `compose.py`** (append at the end):

```python
def compose_flow(flow_def, model_id=None):
    """MCP entry point: build a whole flow in the live model.

    model_id accepted for forward-compatibility but currently ignored.
    """
    import simulation_backend as backend
    from instantiate import RealOps
    from molecule_schema import MoleculeError
    try:
        return {"success": True, **build_flow(flow_def, RealOps(backend))}
    except FlowError as e:
        return {"success": False, "errorCode": "INVALID_FLOW", "error": str(e)}
    except MoleculeError as e:
        return {"success": False, "errorCode": "INVALID_MOLECULE", "error": str(e)}
    except Exception as e:
        return {"success": False, "errorCode": "COMPOSE_FAILED", "error": str(e)}
```

- [ ] **Step 4: Register in the dispatch table.** In `src/simulation_backend.py`, find the line added in M3:
```python
    "instantiate_pattern": lambda p: __import__("instantiate").instantiate_pattern(
        p.get("moleculeId"), p.get("params"), p.get("modelId")),
```
Add directly after it:
```python
    "compose_flow": lambda p: __import__("compose").compose_flow(
        p.get("flow"), p.get("modelId")),
```

- [ ] **Step 5: Run the unit suite and the live test**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q && python -m pytest tests/live/test_compose_flow_live.py -v`
Expected: unit all pass; live PASSES (builds two machines, wires them, items flow through to the connected Exit). If the live test fails, capture the full output and report BLOCKED — do not weaken the engine to force a pass.

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/compose.py src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/live/test_compose_flow_live.py
git commit -m "feat(m4): compose_flow entry point + dispatch + live flow test"
```

---

### Task 6: Register the `compose_flow` MCP tool in the TypeScript server

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts`
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts`
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts` (tool count 93 -> 94)

- [ ] **Step 1: Study how `instantiate_pattern` was registered.** Run `grep -n "instantiate_pattern\|instantiatePattern" src/index.ts src/backend.ts` and read the surrounding lines. Match that exact pattern (the same `backend.*` helper using `sendCommand`, the same `server.tool(...)` + `safeToolCall` shape).

- [ ] **Step 2: Add `composeFlow` to `backend.ts`** following the `instantiatePattern` helper, calling `sendCommand("compose_flow", params)` and passing `{ flow, modelId }`.

- [ ] **Step 3: Register the tool in `index.ts`** immediately after the `instantiate_pattern` registration, matching the local pattern:

```typescript
server.tool(
  "compose_flow",
  "Build a whole process flow from molecule instances. Args: flow = { id, instances:[{ref, pattern, params}], wiring:[{from:'m1.out', to:'m2.in'}] }.",
  {
    flow: z.object({
      id: z.string().optional(),
      instances: z.array(z.object({
        ref: z.string(),
        pattern: z.string(),
        params: z.record(z.any()).optional(),
      })),
      wiring: z.array(z.object({ from: z.string(), to: z.string() })).optional(),
    }),
    modelId: z.string().optional(),
  },
  async (args) => safeToolCall("compose_flow", () => backend.composeFlow(args), args),
);
```
> If `instantiate_pattern`'s registration uses a slightly different helper/return shape, match it exactly — the dispatch name `compose_flow` and the arg `flow` are what matter.

- [ ] **Step 4: Update the tool-count assertion** in `tests/unit/dispatch-coverage.test.ts` from 93 to 94 (the same one M3 bumped from 92 to 93).

- [ ] **Step 5: Build and test**

Run: `cd src/ExtendSimMCP.TypeScript && npx tsc --noEmit && npx vitest run`
Expected: tsc clean; vitest all pass.

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts
git commit -m "feat(m4): register compose_flow MCP tool"
```

---

## Notes for the implementer

- **Reuse M3 unchanged.** `compose.py` imports `build_molecule`, `_load_molecule`, `RealOps` from `instantiate`; do not modify `instantiate.py`.
- **H-block↔H-block wiring uses the same `ops.connect`** as M3. The H-block's *outer* connectors report nodes normally (unlike the inner boundary connector-objects), so the existing node-based verification in `RealOps.connect` works without change.
- **Never trust `success`** — `RealOps.connect` already effect-verifies; if you add COM calls, verify them.
- **Best-effort rollback on partial-flow failure is DEFERRED** (same decision as M3). On a mid-build failure the response should still surface which H-blocks were created; a follow-up can add rollback.
- **Attribute contract uses declared reads/writes only** (today's molecules declare empty sets, so it is a no-op in practice until attribute detection lands).
