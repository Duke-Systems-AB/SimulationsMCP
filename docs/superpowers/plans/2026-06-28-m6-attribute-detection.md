# M6 (step 1) Attribute Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `detect_attributes(block_id)` derives which item attributes an ExtendSim equation block reads (from its `IVars_ttbl`) and writes (from its `OVars_ttbl`).

**Architecture:** A pure `detect_attributes(block_id, reader)` mapping function over an injected `reader` interface (block type + table rows), a `RealReader` wrapping `simulation_backend` (effect-verified `block_get_value`), and a `detect_attributes` MCP entry. Equation block types come from a static writer-catalog; non-equation blocks return `confidence: "none"`.

**Tech Stack:** Python 3.13, pytest, `simulation_backend.block_get_value(id, table, row, col, as_string=True)` (string-table cells MUST be read with `as_string=True` — see note below), the TS MCP server.

**Reference:** Spec `docs/superpowers/specs/2026-06-28-m6-attribute-detection-design.md`. Equation(I) tables: `IVars_ttbl` (reads), `OVars_ttbl` (writes). Reader interface: `block_type(block_id)->str`, `table_rows(block_id, table_name)->list[dict]` where each dict has at least `{"variable": str, "attribute": str|None}`.

> **String-read correction (post string-table capability, 2026-06-29):** `IVars_ttbl`/`OVars_ttbl` cells are STRING cells. `block_get_value(...)` with its default `as_string=False` runs `parse_float` on the string and fails (`GET_VALUE_FAILED`, the `'ERR'` we saw). Reading with **`as_string=True`** routes through `_get_var`→`GetDialogVariable` and returns the raw string. `RealReader` therefore reads cells with `as_string=True`. (The dedicated `table_get` tool from the string-table capability is an alternative, but `block_get_value(..., as_string=True)` lives in the same `simulation_backend` module `RealReader` already holds, so it is the smaller, more cohesive choice.)

## Execution sequencing (2026-06-29)

- **COM-free, do now (ExtendSim not required):** Task 2 (pure `detect_attributes` + FakeReader), Task 3 (`RealReader` + mocked tests), Task 5 (MCP registration). `VAR_COL`/`ATTR_COL` in Task 3 stay as clearly-marked placeholders until Task 1 confirms them.
- **Live, do when ExtendSim is responsive:** Task 1 (column discovery) → confirm/fix `VAR_COL`/`ATTR_COL` → Task 4 (live test). Bound-attribute live verification may now be possible by writing a binding via `table_set` first IF the IVars/OVars cells are user-writable (unknown until Task 1 — OVars auto-named connector cells are block-controlled and reject writes).

---

### Task 1: Discover the IVars_ttbl/OVars_ttbl column layout (read-only spike)

**Files:** none (investigation only — record findings in the Task 2 commit message).

- [ ] **Step 1 (ExtendSim running):** Determine which column of `IVars_ttbl`/`OVars_ttbl` holds the variable name and which holds the bound attribute name, and how an unbound row reads. Run:

```bash
cd src/ExtendSimMCP.TypeScript && python -c "
import sys; sys.path.insert(0,'src')
import simulation_backend as sb
sb.execute_command('ActivateApplication();')
b = sb.block_add('Item.lbr','Equation(I)')['blockId']
for tbl in ('IVars_ttbl','OVars_ttbl'):
    print('---', tbl, '---')
    for row in range(3):
        cells=[]
        for col in range(6):
            r=sb.block_get_value(b, tbl, row, col, as_string=True)
            cells.append((col, r.get('value') if r.get('success') else 'ERR'))
        print(' row', row, cells)
sb.block_remove(b)
"
```
Note: a default block may have empty tables. If so, also inspect a **manually-configured** Equation(I) (ask the user to bind one in-var to an attribute and one out-var to an attribute in the UI, leave the block) and re-read to see which column holds the attribute string. **Record the column indices** (e.g. "variable name = col 0, bound attribute = col 2") — they are needed for `RealReader` in Task 3. The snippet already uses `as_string=True` so string cells return text; if any cell still reads as `''`/`-nan`, note which (an unbound binding may legitimately be empty).

- [ ] **Step 2:** No commit (investigation). Carry the column indices forward to Task 3.

---

### Task 2: Pure `detect_attributes(block_id, reader)` + FakeReader tests

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/attribute_detect.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_detect.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit_py/test_attribute_detect.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from attribute_detect import detect_attributes

class FakeReader:
    def __init__(self, block_type, ivars=None, ovars=None):
        self._t = block_type
        self._tables = {"IVars_ttbl": ivars or [], "OVars_ttbl": ovars or []}
    def block_type(self, block_id):
        return self._t
    def table_rows(self, block_id, table_name):
        return self._tables[table_name]

def test_equation_block_maps_ivars_to_reads_ovars_to_writes():
    r = FakeReader("Equation(I)",
                   ivars=[{"variable": "v_in", "attribute": "partType"}],
                   ovars=[{"variable": "v_out", "attribute": "cost"}])
    res = detect_attributes(7, r)
    assert res["reads"] == ["partType"]
    assert res["writes"] == ["cost"]
    assert res["confidence"] == "high"

def test_unbound_row_yields_question_mark_and_low_confidence():
    r = FakeReader("Equation(I)",
                   ivars=[{"variable": "v_in", "attribute": None}],
                   ovars=[])
    res = detect_attributes(7, r)
    assert res["reads"] == ["?"]
    assert res["confidence"] == "low"

def test_empty_tables_yield_empty_high_confidence():
    r = FakeReader("Equation(I)", ivars=[], ovars=[])
    res = detect_attributes(7, r)
    assert res["reads"] == [] and res["writes"] == []
    assert res["confidence"] == "high"

def test_non_equation_block_is_confidence_none():
    r = FakeReader("Queue")
    res = detect_attributes(7, r)
    assert res == {"reads": [], "writes": [], "confidence": "none"}
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: No module named 'attribute_detect'`)

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_attribute_detect.py -v`

- [ ] **Step 3: Create `src/attribute_detect.py`**

```python
# src/attribute_detect.py
"""Attribute detection for equation blocks (M6 step 1).

Pure mapping over an injected reader; RealReader wraps the live COM backend.
A block's in-variable table (IVars_ttbl) yields reads, the out-variable table
(OVars_ttbl) yields writes. See spec 2026-06-28-m6-attribute-detection-design.md.
"""
_EQUATION_TYPES = {"Equation(I)", "Query Equation (I)", "Queue Equation"}


def _attrs_from_rows(rows):
    """Return (attribute_list, saw_unbound) from a variable table's rows."""
    attrs, saw_unbound = [], False
    for row in rows:
        attr = row.get("attribute")
        if attr:
            attrs.append(attr)
        else:
            saw_unbound = True
    return attrs, saw_unbound


def detect_attributes(block_id, reader):
    """Derive {reads, writes, confidence} for a block via the injected reader."""
    if reader.block_type(block_id) not in _EQUATION_TYPES:
        return {"reads": [], "writes": [], "confidence": "none"}

    reads, r_unbound = _attrs_from_rows(reader.table_rows(block_id, "IVars_ttbl"))
    writes, w_unbound = _attrs_from_rows(reader.table_rows(block_id, "OVars_ttbl"))
    if r_unbound:
        reads.append("?")
    if w_unbound:
        writes.append("?")
    confidence = "low" if (r_unbound or w_unbound) else "high"
    return {"reads": reads, "writes": writes, "confidence": confidence}
```

- [ ] **Step 4: Run — expect PASS** (4 passed), then full suite green

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`

- [ ] **Step 5: Commit** (include the Task 1 column findings in the message)

```bash
git add src/ExtendSimMCP.TypeScript/src/attribute_detect.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_detect.py
git commit -m "feat(m6): pure detect_attributes for equation blocks (IVars->reads, OVars->writes) (TDD)

IVars_ttbl/OVars_ttbl column layout (from Task 1): <fill in discovered columns>"
```

---

### Task 3: `RealReader` over the COM backend

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/attribute_detect.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_detect.py`

- [ ] **Step 1: Write the failing tests** (append):

```python
from unittest import mock

def test_realreader_block_type_reads_GetBlockType():
    import attribute_detect as ad
    backend = mock.Mock()
    backend.execute_command.return_value = {"success": True, "result": "Equation(I)"}
    rr = ad.RealReader(backend)
    assert rr.block_type(5) == "Equation(I)"

def test_realreader_table_rows_maps_variable_and_attribute_columns():
    import attribute_detect as ad
    backend = mock.Mock()
    # 1 row: variable col -> "v_in", attribute col -> "partType"; then an empty row stops it
    cells = {
        (0, ad.VAR_COL): {"success": True, "value": "v_in"},
        (0, ad.ATTR_COL): {"success": True, "value": "partType"},
        (1, ad.VAR_COL): {"success": True, "value": ""},     # empty row terminates
    }
    # RealReader reads string cells with as_string=True; the mock accepts the kwarg.
    backend.block_get_value.side_effect = (
        lambda bid, tbl, row, col, as_string=False: cells.get((row, col), {"success": True, "value": ""})
    )
    rr = ad.RealReader(backend)
    rows = rr.table_rows(9, "IVars_ttbl")
    assert rows == [{"variable": "v_in", "attribute": "partType"}]
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: ... 'RealReader'`)

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_attribute_detect.py -k realreader -v`

- [ ] **Step 3: Append `RealReader` to `attribute_detect.py`** — set `VAR_COL`/`ATTR_COL` to the columns discovered in Task 1 (placeholders `0`/`1` below — REPLACE with the real indices):

```python
VAR_COL = 0    # column holding the variable name (from Task 1 discovery)
ATTR_COL = 1   # column holding the bound attribute name (from Task 1 discovery)


class RealReader:
    """Reads block type + variable-table rows from the live COM backend.
    Effect-verifies each cell read (never trusts an unread cell)."""
    def __init__(self, backend):
        self._b = backend

    def block_type(self, block_id):
        r = self._b.execute_command(
            f"globalStr0 = GetBlockType({block_id});", get_result=True, result_type="string")
        if not r.get("success"):
            return ""
        return (r.get("result") or "").strip()

    def table_rows(self, block_id, table_name):
        rows, row = [], 0
        while True:
            var_cell = self._b.block_get_value(block_id, table_name, row, VAR_COL, as_string=True)
            if not var_cell.get("success"):
                break
            var = (str(var_cell.get("value")) if var_cell.get("value") is not None else "").strip()
            if var == "" or var == "nan":          # empty row terminates the table
                break
            attr_cell = self._b.block_get_value(block_id, table_name, row, ATTR_COL, as_string=True)
            attr = (str(attr_cell.get("value")).strip()
                    if attr_cell.get("success") and attr_cell.get("value") not in (None, "", "nan")
                    else None)
            rows.append({"variable": var, "attribute": attr})
            row += 1
        return rows
```

> Cells are read with `as_string=True` (string-table cells; see the String-read correction at the top). If Task 1's live discovery shows the binding lives somewhere other than a plain `ATTR_COL` cell, adjust `table_rows`/`ATTR_COL` before finalizing; the mocked test stays green, but the live test (Task 4) is the real check.

- [ ] **Step 4: Run — expect PASS**, then full suite green

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q`

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/attribute_detect.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_detect.py
git commit -m "feat(m6): RealReader (block type + variable tables via COM, effect-verified) (TDD)"
```

---

### Task 4: `detect_attributes` entry point + dispatch + live test

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/attribute_detect.py`
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (dispatch)
- Test: `src/ExtendSimMCP.TypeScript/tests/live/test_attribute_detect_live.py`

- [ ] **Step 1: Append the entry point to `attribute_detect.py`:**

```python
def detect_attributes_entry(block_id, model_id=None):
    """MCP entry point: detect read/written attributes of a block in the live model."""
    import simulation_backend as backend
    try:
        return {"success": True, **detect_attributes(block_id, RealReader(backend))}
    except Exception as e:
        return {"success": False, "errorCode": "DETECT_FAILED", "error": str(e)}
```

- [ ] **Step 2: Register dispatch** in `simulation_backend.py`, after the `"get_pattern"` line:
```python
    "detect_attributes": lambda p: __import__("attribute_detect").detect_attributes_entry(
        p.get("blockId"), p.get("modelId")),
```

- [ ] **Step 3: Create the live test** `tests/live/test_attribute_detect_live.py`:

```python
# tests/live/test_attribute_detect_live.py
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

def test_detect_attributes_on_fresh_equation_block_is_empty_high():
    """A freshly placed Equation(I) has no variable bindings -> empty reads/writes, high confidence.
    (Detecting *bound* attributes needs a manually-configured block; see the spec — that case
    is verified by hand, since we cannot write the variable table programmatically yet.)"""
    import simulation_backend as sb
    from attribute_detect import detect_attributes_entry
    sb.execute_command("ActivateApplication();")
    b = sb.block_add("Item.lbr", "Equation(I)")["blockId"]
    try:
        res = detect_attributes_entry(b)
        assert res.get("success"), res
        assert res["confidence"] in ("high", "low")
        assert isinstance(res["reads"], list) and isinstance(res["writes"], list)
    finally:
        sb.block_remove(b)

def test_detect_attributes_non_equation_is_none():
    import simulation_backend as sb
    from attribute_detect import detect_attributes_entry
    sb.execute_command("ActivateApplication();")
    b = sb.block_add("Item.lbr", "Queue")["blockId"]
    try:
        res = detect_attributes_entry(b)
        assert res.get("success") and res["confidence"] == "none"
    finally:
        sb.block_remove(b)
```

- [ ] **Step 4: Run** unit suite + live test:
```
cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/ -q && python -m pytest tests/live/test_attribute_detect_live.py -v
```
Expected: unit all pass; live 2 passed. If the live test reveals `RealReader` mis-reads the table (e.g. wrong columns / string cells unreadable), fix `RealReader`/`VAR_COL`/`ATTR_COL` per the real behaviour and re-run; if it cannot be made to read cleanly, report BLOCKED with the cell dump.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/attribute_detect.py src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/live/test_attribute_detect_live.py
git commit -m "feat(m6): detect_attributes entry + dispatch + live test"
```

---

### Task 5: Register the `detect_attributes` MCP tool

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts`
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts`
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts` (98 -> 99)

- [ ] **Step 1: Study + match the pattern.** Run `grep -n "get_pattern\|getPattern" src/index.ts src/backend.ts` and match the exact `server.tool` + `safeToolCall` + `backend.<helper>` + `sendCommand` shape used there.

- [ ] **Step 2: Add `detectAttributes` to `backend.ts`** mirroring `getPattern`, calling `sendCommand("detect_attributes", { blockId, modelId })`.

- [ ] **Step 3: Register in `index.ts`** after `get_pattern`:

```typescript
server.tool(
  "detect_attributes",
  "Detect which item attributes a block reads/writes (equation blocks: from their in/out variable tables). Returns { reads, writes, confidence }.",
  { blockId: z.number(), modelId: z.string().optional() },
  async (args) => safeToolCall("detect_attributes", () => backend.detectAttributes(args), args),
);
```
> Match the local pattern if it differs from this snippet.

- [ ] **Step 4: Bump the tool-count assertion** in `tests/unit/dispatch-coverage.test.ts` from `98` to `99` (both the `it("should have exactly 98 ...")` title and the `toBe(98)` number), and update the header comment on line 2 (`verify all 98 tools`) to `99`.

- [ ] **Step 5: Build and test**

Run: `cd src/ExtendSimMCP.TypeScript && npx tsc --noEmit && npx vitest run`
Expected: tsc clean; vitest all pass.

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts
git commit -m "feat(m6): register detect_attributes MCP tool"
```

---

## Notes for the implementer

- **The mapping logic (Task 2) is the deliverable's core and is fully testable without ExtendSim** via FakeReader. Get it solid first.
- **`VAR_COL`/`ATTR_COL` come from Task 1's live discovery** — do not ship the `0`/`1` placeholders unverified; the live test (Task 4) confirms them.
- **Detecting *bound* attributes live needs a manually-configured equation block** (we cannot write the variable table programmatically yet — same wall as tag-items/resource-machine). The live tests therefore only assert the fresh-block (empty) and non-equation (none) cases; bound-attribute detection is verified by hand against a user-configured block.
- **Never trust `success`** — `RealReader` checks each `block_get_value`/`execute_command` result.
- **Tool count**: M6 adds one tool (`detect_attributes`) → 98 to 99 (main already shipped string-table's `table_get`/`table_set` at 98).
