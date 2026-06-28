# M5 Base-Pack + Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a base pack of hand-authored linear molecules + base flows, and discovery tools (`list_patterns`/`get_pattern`) so an agent can find and select patterns.

**Architecture:** New molecule/flow JSON data files (reuse M3 `instantiate_pattern` / M4 `compose_flow` unchanged) plus a pure `patterns.py` discovery module reading the library directories. Low-risk tasks first; the two molecules needing live dialog-param discovery (`tag-items`, `resource-machine`) come last and may be deferred if their discovery balloons.

**Tech Stack:** Python 3.13, pytest, M3/M4 engine, JSON library files, TS MCP server.

**Reference:** Spec `docs/superpowers/specs/2026-06-28-m5-base-pack-discovery-design.md`. Shared test double: `tests/unit_py/fake_ops.py` (`FakeOps`, `load`). `build_molecule`/`build_flow` are in `src/instantiate.py`/`src/compose.py`.

> **Verified COM facts for this plan (from a live spike):** `resource-machine` wiring is `pool.ValuesOut → queue.ResourcePoolQuantityIn` (a side connection that connects cleanly, node-matched), and the Queue's mode is the `QueueType_pop` dialog popup. `tag-items` uses an `Item.lbr` `Set` block (has ItemIn/ItemOut). The exact popup index for the Queue's "Resource Pool" mode, the Resource Pool's capacity param, and the Set block's attribute params are discovered live in Tasks 6-7.

---

### Task 1: `simple-machine` molecule

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/patterns/molecules/simple-machine.json`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_simple_machine.py`

- [ ] **Step 1: Write the failing build test**

```python
# tests/unit_py/test_simple_machine.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps, load
from instantiate import build_molecule

def test_simple_machine_builds_queue_then_activity():
    ops = FakeOps()
    res = build_molecule(load("simple-machine.json"), {"process_time": 5}, ops)
    # both blocks present; Queue.ItemOut shares a node with Activity.ItemIn (clean chain)
    q = res["internalBlockIds"]["q"]
    act = res["internalBlockIds"]["act"]
    assert ops.node_of(q, 1) == ops.node_of(act, 0) != 0
    # process_time set on the Activity
    sets = {(c[1], c[2]): c[3] for c in ops.calls if c[0] == "set_value"}
    assert sets[(act, "D")] == 5
```

- [ ] **Step 2: Run — expect FAIL** (`FileNotFoundError` for simple-machine.json)

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_simple_machine.py -v`

- [ ] **Step 3: Create `simple-machine.json`**

```json
{
  "id": "simple-machine",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Maskin: en kö följd av en aktivitet med konfigurerbar processtid",
  "params": { "process_time": { "type": "number", "required": true } },
  "attributes": { "reads": [], "writes": [] },
  "nodes": [
    { "ref": "q",   "lib": "Item.lbr", "type": "Queue" },
    { "ref": "act", "lib": "Item.lbr", "type": "Activity", "seed": true, "params": { "D": "{{process_time}}" } }
  ],
  "edges": [
    { "kind": "flow", "from": "q.ItemOut", "to": "act.ItemIn" }
  ],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "q.ItemIn",    "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "act.ItemOut", "role": "item" } ]
  }
}
```

- [ ] **Step 4: Run — expect PASS**, then full suite still green

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`
Expected: all pass (25 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/simple-machine.json src/ExtendSimMCP.TypeScript/tests/unit_py/test_simple_machine.py
git commit -m "feat(m5): simple-machine molecule (Queue->Activity) (TDD)"
```

---

### Task 2: `patterns.py` discovery (pure)

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/patterns.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_patterns.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit_py/test_patterns.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from patterns import list_patterns, get_pattern

def test_list_patterns_includes_known_molecules():
    res = list_patterns()
    ids = {p["id"] for p in res["patterns"]}
    assert {"buffer", "machine-with-breakdowns", "simple-machine"}.issubset(ids)
    # each entry carries kind + intent + params + interface
    sm = next(p for p in res["patterns"] if p["id"] == "simple-machine")
    assert sm["kind"] == "molecule"
    assert "process_time" in sm["params"]

def test_list_patterns_intent_filter_is_substring_case_insensitive():
    res = list_patterns(intent="HAVERI")  # machine-with-breakdowns intent mentions haverier
    ids = {p["id"] for p in res["patterns"]}
    assert "machine-with-breakdowns" in ids
    assert "buffer" not in ids

def test_get_pattern_returns_full_definition():
    res = get_pattern("simple-machine")
    assert res["success"] and res["kind"] == "molecule"
    assert res["pattern"]["nodes"][0]["ref"] == "q"

def test_get_pattern_unknown_is_fail_closed():
    res = get_pattern("does-not-exist")
    assert res["success"] is False and res["errorCode"] == "UNKNOWN_PATTERN"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: No module named 'patterns'`)

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_patterns.py -v`

- [ ] **Step 3: Create `patterns.py`**

```python
# src/patterns.py
"""Discovery over the molecule + flow library (M5). Pure file I/O, no COM."""
import os
import json

_BASE = os.path.join(os.path.dirname(__file__), "..", "patterns")
_DIRS = {"molecule": "molecules", "flow": "flows"}


def _iter_defs():
    for kind, sub in _DIRS.items():
        d = os.path.join(_BASE, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    yield kind, json.load(f)   # raises on broken JSON (fail-closed)


def list_patterns(intent=None):
    out = []
    for kind, d in _iter_defs():
        if intent and intent.lower() not in d.get("intent", "").lower():
            continue
        out.append({
            "id": d.get("id"),
            "kind": kind,
            "intent": d.get("intent", ""),
            "params": d.get("params", {}),
            "interface": d.get("interface", {}),
        })
    return {"success": True, "patterns": out, "count": len(out)}


def get_pattern(pattern_id):
    for kind, d in _iter_defs():
        if d.get("id") == pattern_id:
            return {"success": True, "kind": kind, "pattern": d}
    return {"success": False, "errorCode": "UNKNOWN_PATTERN",
            "error": f"unknown pattern: {pattern_id}"}
```

- [ ] **Step 4: Run — expect PASS**, then full suite green

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`
Expected: all pass (29 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/patterns.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_patterns.py
git commit -m "feat(m5): patterns.py discovery (list_patterns/get_pattern, pure) (TDD)"
```

---

### Task 3: `two-stage-line` base flow

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/patterns/flows/two-stage-line.json`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_two_stage_line.py`

- [ ] **Step 1: Write the failing build test**

```python
# tests/unit_py/test_two_stage_line.py
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps
from compose import build_flow

def _load_flow():
    p = os.path.join(_SRC, "..", "patterns", "flows", "two-stage-line.json")
    with open(p, encoding="utf-8") as f:
        return json.load(f)

def test_two_stage_line_builds_two_instances_and_wires_them():
    ops = FakeOps()
    res = build_flow(_load_flow(), ops)
    assert set(res["instances"]) == {"s1", "s2"}
    h1 = res["instances"]["s1"]["hblockId"]
    h2 = res["instances"]["s2"]["hblockId"]
    out_idx = res["instances"]["s1"]["interfaceMap"]["out"]["outerCon"]
    in_idx = res["instances"]["s2"]["interfaceMap"]["in"]["outerCon"]
    assert ("connect", h1, out_idx, h2, in_idx) in ops.calls
```

- [ ] **Step 2: Run — expect FAIL** (`FileNotFoundError` for the flow)

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_two_stage_line.py -v`

- [ ] **Step 3: Create `two-stage-line.json`**

```json
{
  "id": "two-stage-line",
  "kind": "flow",
  "intent": "Två maskiner med haveri i serie",
  "instances": [
    { "ref": "s1", "pattern": "machine-with-breakdowns", "params": { "process_time": 2, "mtbf": 120, "mttr": 8 } },
    { "ref": "s2", "pattern": "machine-with-breakdowns", "params": { "process_time": 3, "mtbf": 90,  "mttr": 6 } }
  ],
  "wiring": [ { "from": "s1.out", "to": "s2.in" } ]
}
```

- [ ] **Step 4: Run — expect PASS**, then full suite green

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`
Expected: all pass (30 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/flows/two-stage-line.json src/ExtendSimMCP.TypeScript/tests/unit_py/test_two_stage_line.py
git commit -m "feat(m5): two-stage-line base flow (TDD)"
```

---

### Task 4: Live tests — simple-machine + two-stage-line

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/live/test_m5_live.py`

- [ ] **Step 1: Create the live test**

```python
# tests/live/test_m5_live.py
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

def _smoke_through(sb, hid, in_outer, out_outer):
    cS = sb.block_add("Item.lbr", "Create")["blockId"]
    eS = sb.block_add("Item.lbr", "Exit")["blockId"]
    try:
        sb.execute_command(f"MakeConnection({cS}, 0, {hid}, {in_outer});")
        sb.execute_command(f"MakeConnection({hid}, {out_outer}, {eS}, 0);")
        out = sb.simulation_run(end_time=100, include_stats=True)
        return next(e["itemsExited"] for e in out["statistics"]["exitStatistics"] if e["blockId"] == eS)
    finally:
        sb.block_remove(eS); sb.block_remove(cS)

def test_simple_machine_instantiates_and_runs():
    import simulation_backend as sb
    from instantiate import instantiate_pattern
    sb.execute_command("ActivateApplication();")
    res = instantiate_pattern("simple-machine", {"process_time": 1})
    assert res.get("success"), res
    hid = res["hblockId"]
    try:
        exited = _smoke_through(sb, hid, res["interfaceMap"]["in"]["outerCon"],
                                res["interfaceMap"]["out"]["outerCon"])
        assert exited > 0
    finally:
        sb.block_remove(hid)

def test_two_stage_line_composes_and_runs():
    import simulation_backend as sb
    from compose import compose_flow
    sb.execute_command("ActivateApplication();")
    p = os.path.join(_SRC, "..", "patterns", "flows", "two-stage-line.json")
    with open(p, encoding="utf-8") as f:
        flow = json.load(f)
    res = compose_flow(flow)
    assert res.get("success"), res
    s1, s2 = res["instances"]["s1"], res["instances"]["s2"]
    try:
        exited = _smoke_through(sb, s1["hblockId"], s1["interfaceMap"]["in"]["outerCon"],
                                s2["interfaceMap"]["out"]["outerCon"])
        # Note: smoke connects Create to s1's inlet and Exit to s2's outlet (s1->s2 already wired).
        assert exited > 0
    finally:
        sb.block_remove(s2["hblockId"]); sb.block_remove(s1["hblockId"])
```

- [ ] **Step 2: Run the live tests (ExtendSim running)**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_m5_live.py -v`
Expected: 2 passed. If a test fails, capture full output and report BLOCKED (do not weaken the engine).

- [ ] **Step 3: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/live/test_m5_live.py
git commit -m "feat(m5): live tests for simple-machine + two-stage-line"
```

---

### Task 5: Register `list_patterns` + `get_pattern` MCP tools

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts`
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts`
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (dispatch)
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts` (94 -> 96)

- [ ] **Step 1: Add dispatch entries** in `simulation_backend.py`, directly after the `"compose_flow"` line:
```python
    "list_patterns": lambda p: __import__("patterns").list_patterns(p.get("intent")),
    "get_pattern": lambda p: __import__("patterns").get_pattern(p.get("patternId")),
```

- [ ] **Step 2: Study + match the M3/M4 tool pattern.** Run `grep -n "compose_flow\|composeFlow" src/index.ts src/backend.ts` and read around it.

- [ ] **Step 3: Add `listPatterns` and `getPattern` helpers to `backend.ts`** mirroring `composeFlow`: `listPatterns` calls `sendCommand("list_patterns", { intent })`; `getPattern` calls `sendCommand("get_pattern", { patternId })`.

- [ ] **Step 4: Register both tools in `index.ts`** after the `compose_flow` registration, matching the local `server.tool(...)` + `safeToolCall` pattern:

```typescript
server.tool(
  "list_patterns",
  "List available molecule/flow patterns. Optional intent substring filter. Returns id, kind, intent, params, interface.",
  { intent: z.string().optional() },
  async (args) => safeToolCall("list_patterns", () => backend.listPatterns(args), args),
);

server.tool(
  "get_pattern",
  "Get the full definition of a molecule or flow pattern by id.",
  { patternId: z.string() },
  async (args) => safeToolCall("get_pattern", () => backend.getPattern(args), args),
);
```
> Match the exact local helper/return shape used by `compose_flow` if it differs from this snippet.

- [ ] **Step 5: Bump the tool-count assertion** in `tests/unit/dispatch-coverage.test.ts` from `94` to `96` (two new tools).

- [ ] **Step 6: Build and test**

Run: `cd src/ExtendSimMCP.TypeScript && npx tsc --noEmit && npx vitest run`
Expected: tsc clean; vitest all pass.

- [ ] **Step 7: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts
git commit -m "feat(m5): register list_patterns + get_pattern MCP tools"
```

---

### Task 6: `tag-items` molecule (Set block) — discovery + author

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/patterns/molecules/tag-items.json`
- Create: `src/ExtendSimMCP.TypeScript/patterns/flows/tagged-line.json`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_tag_items.py`

- [ ] **Step 1: Discover the Set block's attribute params (live, ExtendSim running).** Run this and read the output to find how the `Set` block stores the attribute name + value it writes:

```bash
cd src/ExtendSimMCP.TypeScript && python -c "
import sys; sys.path.insert(0,'src')
import simulation_backend as sb, json
sb.execute_command('ActivateApplication();')
b = sb.block_add('Item.lbr','Set')['blockId']
print('Set connectors:', [c['name'] for c in sb.block_info(block_id=b).get('connectors',[])])
print(json.dumps(sb.block_discover_variables(block_id=b), ensure_ascii=False)[:1500])
sb.block_remove(b)
"
```
Identify the dialog variable(s) that set (a) which attribute and (b) its value. If the Set block writes attributes only via a table/database (not simple `block_set_value` dialog vars), and wiring it would require non-trivial table setup, STOP and report DONE_WITH_CONCERNS proposing to defer `tag-items` — do not force a fragile implementation.

- [ ] **Step 2: Write the failing build test** (`tests/unit_py/test_tag_items.py`):

```python
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps, load
from instantiate import build_molecule

def test_tag_items_builds_a_single_set_block_with_interface():
    ops = FakeOps()
    res = build_molecule(load("tag-items.json"), {}, ops)
    assert "set" in res["internalBlockIds"]
    # declares it writes partType
    mol = load("tag-items.json")
    assert "partType" in mol["attributes"]["writes"]
```
> The `Set` block must be added to `FakeOps.CONS` if the test exercises connectors not present there; add `"Set": {"ItemIn": 0, "ItemOut": 1}` to the `CONS` dict in `tests/unit_py/fake_ops.py` (Set is the seed; it needs ItemIn+ItemOut for the seed-wrap).

- [ ] **Step 3: Run — expect FAIL**, then create `tag-items.json`:

```json
{
  "id": "tag-items",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Märk items med attributet partType",
  "params": {},
  "attributes": { "reads": [], "writes": ["partType"] },
  "nodes": [
    { "ref": "set", "lib": "Item.lbr", "type": "Set", "seed": true,
      "params": { "__DISCOVERED_ATTR_VAR__": "partType" } }
  ],
  "edges": [],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "set.ItemIn",  "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "set.ItemOut", "role": "item" } ]
  }
}
```
Replace `__DISCOVERED_ATTR_VAR__` with the real dialog variable name found in Step 1 (and add a value var if the Set block needs both). If Step 1 showed attribute-setting is table-based and not expressible via `params`, instead omit `params` (leave the Set block unconfigured) and keep `attributes.writes` declared — the molecule still builds and demonstrates an upstream-writer in the library; note this in the commit.

- [ ] **Step 4: Run the unit suite — expect PASS.** `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`

- [ ] **Step 5: Create `tagged-line.json` flow:**

```json
{
  "id": "tagged-line",
  "kind": "flow",
  "intent": "Märk items och processa dem i en maskin",
  "instances": [
    { "ref": "tag", "pattern": "tag-items", "params": {} },
    { "ref": "mc",  "pattern": "simple-machine", "params": { "process_time": 2 } }
  ],
  "wiring": [ { "from": "tag.out", "to": "mc.in" } ]
}
```

- [ ] **Step 6: Live-verify** (append to `tests/live/test_m5_live.py` or run ad hoc): instantiate `tag-items` and smoke-run; compose `tagged-line` and smoke-run; assert items flow. If it builds + runs, commit.

- [ ] **Step 7: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/tag-items.json src/ExtendSimMCP.TypeScript/patterns/flows/tagged-line.json src/ExtendSimMCP.TypeScript/tests/unit_py/test_tag_items.py src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py
git commit -m "feat(m5): tag-items molecule (Set, writes partType) + tagged-line flow"
```

---

### Task 7: `resource-machine` molecule — discovery + author

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/patterns/molecules/resource-machine.json`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_machine.py`

- [ ] **Step 1: Discover the Queue resource-pool-mode index + Resource Pool capacity param (live).** Run:

```bash
cd src/ExtendSimMCP.TypeScript && python -c "
import sys; sys.path.insert(0,'src')
import simulation_backend as sb, json
sb.execute_command('ActivateApplication();')
q = sb.block_add('Item.lbr','Queue')['blockId']
# enumerate QueueType_pop popup labels to find the 'Resource Pool' index
for i in range(8):
    sb.execute_command(f'globalStr0 = GetDialogItemLabel({q}, \"QueueType_pop\", {i});')
    print(i, repr(sb.execute_command('globalStr0 = GetDialogItemLabel('+str(q)+', \"QueueType_pop\", '+str(i)+');', get_result=True, result_type='string')['result']))
pool = sb.block_add('Item.lbr','Resource Pool')['blockId']
print('Pool vars:', json.dumps(sb.block_discover_variables(block_id=pool), ensure_ascii=False)[:1200])
sb.block_remove(q); sb.block_remove(pool)
"
```
Find (a) the integer index of the "Resource Pool" entry in `QueueType_pop`, and (b) the Resource Pool's capacity/quantity dialog var. If the Queue's resource-pool mode requires more than setting `QueueType_pop` + the side connection (e.g. additional required config), STOP and report DONE_WITH_CONCERNS proposing to defer `resource-machine`.

- [ ] **Step 2: Add `Resource Pool` connectors to `FakeOps.CONS`** in `tests/unit_py/fake_ops.py`:
```python
        "Resource Pool": {"ValuesOut": 1},
```
(Queue already has `ItemIn`/`ItemOut`; the side edge uses `ResourcePoolQuantityIn` — add it to the Queue entry: `"Queue": {"ItemIn": 0, "ItemOut": 1, "ResourcePoolQuantityIn": 5}`.)

- [ ] **Step 3: Write the failing build test** (`tests/unit_py/test_resource_machine.py`):

```python
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from fake_ops import FakeOps, load
from instantiate import build_molecule

def test_resource_machine_wires_pool_side_connection():
    ops = FakeOps()
    res = build_molecule(load("resource-machine.json"), {"process_time": 4, "capacity": 2}, ops)
    q = res["internalBlockIds"]["q"]
    pool = res["internalBlockIds"]["pool"]
    # pool.ValuesOut shares a node with queue.ResourcePoolQuantityIn (side connection)
    assert ops.node_of(pool, 1) == ops.node_of(q, 5) != 0
```

- [ ] **Step 4: Run — expect FAIL**, then create `resource-machine.json` (fill `__RP_MODE_INDEX__` and `__POOL_CAPACITY_VAR__` from Step 1):

```json
{
  "id": "resource-machine",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Maskin med kapacitetsbegränsning av en delad resurspool",
  "params": {
    "process_time": { "type": "number", "required": true },
    "capacity":     { "type": "number", "required": true }
  },
  "attributes": { "reads": [], "writes": [] },
  "nodes": [
    { "ref": "q",    "lib": "Item.lbr", "type": "Queue",
      "params": { "QueueType_pop": __RP_MODE_INDEX__ } },
    { "ref": "act",  "lib": "Item.lbr", "type": "Activity", "seed": true, "params": { "D": "{{process_time}}" } },
    { "ref": "pool", "lib": "Item.lbr", "type": "Resource Pool", "params": { "__POOL_CAPACITY_VAR__": "{{capacity}}" } }
  ],
  "edges": [
    { "kind": "flow", "from": "q.ItemOut",    "to": "act.ItemIn" },
    { "kind": "side", "from": "pool.ValuesOut", "to": "q.ResourcePoolQuantityIn" }
  ],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "q.ItemIn",    "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "act.ItemOut", "role": "item" } ]
  }
}
```

- [ ] **Step 5: Run unit suite — expect PASS.** Then live-verify: instantiate `resource-machine` and smoke-run (items flow). If it builds + runs, commit.

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/resource-machine.json src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_machine.py src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py
git commit -m "feat(m5): resource-machine molecule (Queue+Resource Pool) (TDD)"
```

---

## Notes for the implementer

- **Reuse M3/M4 unchanged.** Molecules/flows are data; `instantiate_pattern`/`compose_flow` build them.
- **Tasks 6-7 carry live-discovery risk.** If the Set attribute-table (Task 6) or the Queue resource-pool mode (Task 7) turns out to need non-trivial config beyond a side connection + a couple of `params`, report DONE_WITH_CONCERNS and propose deferring that one molecule. Tasks 1-5 deliver the core M5 value and do not depend on 6-7.
- **`FakeOps.CONS`** must list every connector name a molecule's `con_index` is called for. Add `Set` / `Resource Pool` / the Queue's `ResourcePoolQuantityIn` as noted.
- **Tool count** in `dispatch-coverage.test.ts`: M5 adds exactly two tools (`list_patterns`, `get_pattern`) → 94 to 96.
- **`__DISCOVERED_*__` / `__RP_MODE_INDEX__` / `__POOL_CAPACITY_VAR__`** are placeholders to be replaced with values found by the Step-1 discovery commands in their tasks; do not commit them literally.
