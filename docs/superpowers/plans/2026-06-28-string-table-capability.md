# String-Table Capability (`table_get` / `table_set`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two MCP tools, `table_get` and `table_set`, that read and write ExtendSim dialog string-table cells (`*_ttbl`), with `table_set` write-read-back verified and fail-closed.

**Architecture:** A new pure module `src/dialog_table.py` holds two core functions that take an injected `backend` object (the `simulation_backend` module in production, a `FakeBackend` in tests) and reuse the backend's existing `get_extendsim_app`, `_validate_model_open`, `_get_var`, and `_set_var_string` helpers. Thin entry functions do the lazy `import simulation_backend` and delegate — exactly the pattern `instantiate.py` uses. The MCP layer (`index.ts` + `backend.ts`) registers the two tools and routes them through `COMMANDS` in `simulation_backend.py`.

**Tech Stack:** Python (COM backend, pytest), TypeScript (MCP server, vitest), ExtendSim MODL via `app.Execute` + `app.Request`.

---

## Background the implementer needs

`GetDialogVariable`/`SetDialogVariable` are **MODL language functions**, not COM methods. The backend already wraps them:

- `simulation_backend._get_var(app, block_id, var_name, row, col)` → for `_ttbl`/`_dtbl`/`_dtxt` suffixes runs `globalStr0 = GetDialogVariable(...)` then `Request("System", "globalStr0+:0:0:0")` and returns the raw string. (`src/simulation_backend.py:128`)
- `simulation_backend._set_var_string(app, block_id, var_name, value, row, col)` → runs `SetDialogVariable(..., "value", row, col)` with MODL-string escaping via `_escape_modl_string`. (`src/simulation_backend.py:141`)
- `simulation_backend.get_extendsim_app()` → acquires the COM app. (`src/simulation_backend.py:303`)
- `simulation_backend._validate_model_open(app)` → returns `{"success": True}` or an error dict. (`src/simulation_backend.py:3121`)
- `simulation_backend._error(code, message, **extra)` → builds `{"success": False, "errorCode": code, "error": message, ...}`. (`src/simulation_backend.py:220`)

**Why injected `backend`:** keeps the pure core unit-testable without COM (no `win32com` import), matching `instantiate.py`/`compose.py`. The core never imports `simulation_backend` at module load.

All commands run from the project's TypeScript directory:
```
cd src/ExtendSimMCP.TypeScript
```
Python unit tests insert `../../src` onto `sys.path` themselves (see existing `tests/unit_py/test_patterns.py`).

---

## File Structure

- **Create** `src/ExtendSimMCP.TypeScript/src/dialog_table.py` — pure `table_get`/`table_set` cores + thin `*_entry` wrappers. One responsibility: string-table cell IO with read-back verification.
- **Create** `src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py` — unit tests with a local `FakeBackend`.
- **Modify** `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` — add two `COMMANDS` entries (after the `get_pattern` entry, ~line 10104).
- **Modify** `src/ExtendSimMCP.TypeScript/src/backend.ts` — add `tableGet`/`tableSet` exports (after `getPattern`, ~line 1690).
- **Modify** `src/ExtendSimMCP.TypeScript/src/index.ts` — register the two `server.tool` blocks (after the `get_pattern` block, ~line 1266).
- **Modify** `src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts` — bump the tool count 96 → 98 (line 60).
- **Create** `src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table_live.py` — `skipif` live test against real ExtendSim.

---

### Task 1: Pure `table_get` core + read tests

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/dialog_table.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit_py/test_dialog_table.py`:

```python
# tests/unit_py/test_dialog_table.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dialog_table import table_get, table_set


class FakeBackend:
    """Test double for the simulation_backend module surface that
    dialog_table depends on. Records calls and scripts return values."""

    def __init__(self, get_returns="", model_open=True, raise_on=None):
        self.calls = []
        self._get_returns = get_returns      # str OR list of strs (consumed in order)
        self._model_open = model_open
        self._raise_on = raise_on            # None | "get" | "set"
        self.app = object()

    def get_extendsim_app(self):
        return self.app

    def _validate_model_open(self, app):
        if self._model_open:
            return {"success": True}
        return {"success": False, "errorCode": "MODEL_NOT_OPEN", "error": "no model"}

    def _get_var(self, app, block_id, var_name, row, col):
        self.calls.append(("get", block_id, var_name, row, col))
        if self._raise_on == "get":
            raise RuntimeError("com boom")
        if isinstance(self._get_returns, list):
            return self._get_returns.pop(0)
        return self._get_returns

    def _set_var_string(self, app, block_id, var_name, value, row, col):
        self.calls.append(("set", block_id, var_name, value, row, col))
        if self._raise_on == "set":
            raise RuntimeError("com boom")


def test_table_get_returns_string_cell():
    be = FakeBackend(get_returns="inCon0")
    res = table_get(be, 3, "IVars_ttbl", 0, 1)
    assert res["success"] is True
    assert res["value"] == "inCon0"
    assert res["blockId"] == 3
    assert res["variableName"] == "IVars_ttbl"
    assert res["row"] == 0 and res["col"] == 1
    assert be.calls == [("get", 3, "IVars_ttbl", 0, 1)]


def test_table_get_propagates_model_not_open():
    be = FakeBackend(model_open=False)
    res = table_get(be, 3, "IVars_ttbl", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "MODEL_NOT_OPEN"


def test_table_get_com_failure_is_fail_closed():
    be = FakeBackend(raise_on="get")
    res = table_get(be, 3, "IVars_ttbl", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_READ_FAILED"
    assert res["blockId"] == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit_py/test_dialog_table.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dialog_table'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/dialog_table.py`:

```python
# src/dialog_table.py
"""String-table cell read/write for ExtendSim dialog tables (*_ttbl).

Pure cores take an injected `backend` (the simulation_backend module in
production, a FakeBackend in tests) and reuse its MODL helpers
_get_var / _set_var_string. table_set is read-back verified and fail-closed.
"""


def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def table_get(backend, block_id, var_name, row=0, col=0):
    app = backend.get_extendsim_app()
    model_check = backend._validate_model_open(app)
    if not model_check.get("success"):
        return model_check
    try:
        value = backend._get_var(app, block_id, var_name, row, col)
    except Exception as e:
        return _err("TABLE_READ_FAILED", str(e),
                    blockId=block_id, variableName=var_name, row=row, col=col)
    return {"success": True, "blockId": block_id, "variableName": var_name,
            "row": row, "col": col, "value": str(value)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit_py/test_dialog_table.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/dialog_table.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py
git commit -m "feat: add table_get string-table cell reader (pure core)"
```

---

### Task 2: Pure `table_set` core + write tests (fail-closed)

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/dialog_table.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit_py/test_dialog_table.py`:

```python
def test_table_set_succeeds_when_readback_matches():
    # _get_var is only called once (the verification read) and returns the written value
    be = FakeBackend(get_returns="partType")
    res = table_set(be, 5, "AttribsTable_ttbl", "partType", 0, 0)
    assert res["success"] is True
    assert res["value"] == "partType"
    # set happened, then a verification read happened
    assert be.calls == [
        ("set", 5, "AttribsTable_ttbl", "partType", 0, 0),
        ("get", 5, "AttribsTable_ttbl", 0, 0),
    ]


def test_table_set_rejected_when_readback_differs():
    be = FakeBackend(get_returns="outCon0")  # block-controlled cell ignored the write
    res = table_set(be, 3, "OVars_ttbl", "testAttr", 0, 1)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_WRITE_REJECTED"
    assert res["requested"] == "testAttr"
    assert res["actual"] == "outCon0"


def test_table_set_com_failure_is_fail_closed():
    be = FakeBackend(raise_on="set")
    res = table_set(be, 3, "OVars_ttbl", "x", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_WRITE_FAILED"


def test_table_set_forwards_value_to_set_var_string():
    # Escaping is _set_var_string's responsibility; the core must forward the
    # raw value so the backend can escape it. Verify the forwarded argument.
    be = FakeBackend(get_returns='a"b')
    res = table_set(be, 7, "Equation_dtxt", 'a"b', 1, 2)
    assert res["success"] is True
    set_call = [c for c in be.calls if c[0] == "set"][0]
    assert set_call == ("set", 7, "Equation_dtxt", 'a"b', 1, 2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit_py/test_dialog_table.py -v -k table_set`
Expected: FAIL — `ImportError: cannot import name 'table_set'` (or `NameError`)

- [ ] **Step 3: Write the minimal implementation**

Append to `src/dialog_table.py`:

```python
def table_set(backend, block_id, var_name, value, row=0, col=0):
    app = backend.get_extendsim_app()
    model_check = backend._validate_model_open(app)
    if not model_check.get("success"):
        return model_check
    try:
        backend._set_var_string(app, block_id, var_name, str(value), row, col)
        readback = backend._get_var(app, block_id, var_name, row, col)
    except Exception as e:
        return _err("TABLE_WRITE_FAILED", str(e),
                    blockId=block_id, variableName=var_name, row=row, col=col)
    if str(readback) == str(value):
        return {"success": True, "blockId": block_id, "variableName": var_name,
                "row": row, "col": col, "value": str(readback)}
    return _err("TABLE_WRITE_REJECTED",
                f"write to {var_name}[{row},{col}] on block {block_id} did not persist",
                blockId=block_id, variableName=var_name, row=row, col=col,
                requested=str(value), actual=str(readback))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit_py/test_dialog_table.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/dialog_table.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py
git commit -m "feat: add table_set string-table writer (read-back verified, fail-closed)"
```

---

### Task 3: Entry functions + Python dispatch wiring

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/dialog_table.py`
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py:10104` (after the `get_pattern` COMMANDS entry)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit_py/test_dialog_table.py`:

```python
def test_entries_are_callable_and_lazy_import_backend():
    # The entry functions must exist and accept the documented arguments.
    # We don't call them here (they touch COM); we only assert their presence
    # and arity so the dispatch wiring has real targets.
    import inspect
    import dialog_table
    assert callable(dialog_table.table_get_entry)
    assert callable(dialog_table.table_set_entry)
    get_params = list(inspect.signature(dialog_table.table_get_entry).parameters)
    set_params = list(inspect.signature(dialog_table.table_set_entry).parameters)
    assert get_params == ["block_id", "var_name", "row", "col"]
    assert set_params == ["block_id", "var_name", "value", "row", "col"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit_py/test_dialog_table.py::test_entries_are_callable_and_lazy_import_backend -v`
Expected: FAIL — `AttributeError: module 'dialog_table' has no attribute 'table_get_entry'`

- [ ] **Step 3: Write the minimal implementation**

Append to `src/dialog_table.py`:

```python
def table_get_entry(block_id, var_name, row=0, col=0):
    import simulation_backend as backend
    return table_get(backend, block_id, var_name, row, col)


def table_set_entry(block_id, var_name, value, row=0, col=0):
    import simulation_backend as backend
    return table_set(backend, block_id, var_name, value, row, col)
```

- [ ] **Step 4: Add the COMMANDS dispatch entries**

In `src/simulation_backend.py`, immediately after the `get_pattern` line (currently `src/simulation_backend.py:10104`):

```python
    "get_pattern": lambda p: __import__("patterns").get_pattern(p.get("patternId")),
    "table_get": lambda p: __import__("dialog_table").table_get_entry(
        p.get("blockId"), p.get("variableName"), p.get("row", 0), p.get("col", 0)),
    "table_set": lambda p: __import__("dialog_table").table_set_entry(
        p.get("blockId"), p.get("variableName"), p.get("value"), p.get("row", 0), p.get("col", 0)),
```

(Keep the existing `get_pattern` line; the two new lines are inserted after it.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/unit_py/test_dialog_table.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/dialog_table.py src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table.py
git commit -m "feat: wire table_get/table_set entries into Python COMMANDS dispatch"
```

---

### Task 4: MCP tool registration (TypeScript) + coverage bump

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts:1690` (after `getPattern`)
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts:1266` (after the `get_pattern` `server.tool` block)
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts:60`

- [ ] **Step 1: Update the failing coverage test**

In `tests/unit/dispatch-coverage.test.ts`, change line 60 from `toBe(96)` to `toBe(98)`:

```typescript
  it("should have exactly 98 registered tools", () => {
    expect(toolNames.length).toBe(98);
  });
```

- [ ] **Step 2: Run the coverage test to verify it fails**

Run: `npx vitest run tests/unit/dispatch-coverage.test.ts`
Expected: FAIL — "should have exactly 98 registered tools" (received 96), AND the
"sendCommand call for each Python-backed tool" / "COMMANDS entry for each Python-backed tool"
checks still pass for now because the new tools aren't registered yet (they only run on `toolNames`).

- [ ] **Step 3: Add the backend.ts exports**

In `src/backend.ts`, after the `getPattern` function (currently ends at `src/backend.ts:1690`):

```typescript
export async function tableGet(params: {
  blockId: number;
  variableName: string;
  row?: number;
  col?: number;
}) {
  return await sendCommand("table_get", params);
}

export async function tableSet(params: {
  blockId: number;
  variableName: string;
  value: string;
  row?: number;
  col?: number;
}) {
  return await sendCommand("table_set", params);
}
```

- [ ] **Step 4: Add the index.ts tool registrations**

In `src/index.ts`, after the `get_pattern` `server.tool(...)` block (currently ends at `src/index.ts:1266`):

```typescript
server.tool(
  "table_get",
  "Read a string-table cell (e.g. *_ttbl dialog tables like IVars_ttbl/OVars_ttbl) from a block. Returns the cell as a string. Use this for cells that block_get_value cannot read (it is numeric and returns 'ERR' on string cells).",
  {
    blockId: z.number().describe("Block ID"),
    variableName: z.string().describe("Dialog table variable name, e.g. 'IVars_ttbl'"),
    row: z.number().optional().describe("Row index (0-based, default 0)"),
    col: z.number().optional().describe("Column index (0-based, default 0)"),
  },
  async ({ blockId, variableName, row, col }) =>
    safeToolCall("table_get", () => backend.tableGet({ blockId, variableName, row, col }),
      { blockId, variableName, row, col }),
);

server.tool(
  "table_set",
  "Write a string value into a string-table cell (e.g. *_ttbl). The write is read-back verified: it succeeds only if reading the cell returns the written value, otherwise it fails closed with errorCode TABLE_WRITE_REJECTED (block-controlled cells silently reject writes).",
  {
    blockId: z.number().describe("Block ID"),
    variableName: z.string().describe("Dialog table variable name, e.g. 'AttribsTable_ttbl'"),
    value: z.string().describe("String value to write into the cell"),
    row: z.number().optional().describe("Row index (0-based, default 0)"),
    col: z.number().optional().describe("Column index (0-based, default 0)"),
  },
  async ({ blockId, variableName, value, row, col }) =>
    safeToolCall("table_set", () => backend.tableSet({ blockId, variableName, value, row, col }),
      { blockId, variableName, value, row, col }),
);
```

- [ ] **Step 5: Run the coverage test to verify it passes**

Run: `npx vitest run tests/unit/dispatch-coverage.test.ts`
Expected: PASS — 98 tools, every Python-backed tool has a `sendCommand` + `COMMANDS` entry,
no duplicates.

- [ ] **Step 6: Typecheck/build the TypeScript**

Run: `npx tsc --noEmit`
Expected: no errors (confirms `tableGet`/`tableSet` types and the new `server.tool` blocks compile).

- [ ] **Step 7: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/tests/unit/dispatch-coverage.test.ts
git commit -m "feat: register table_get/table_set MCP tools (96 -> 98)"
```

---

### Task 5: Live verification test (skipif without ExtendSim)

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table_live.py`

This test runs only when ExtendSim is reachable AND a configured `Equation(I)` block id is supplied via the `ES_EQUATION_BLOCK_ID` environment variable. It verifies the three real behaviours: read a known cell, write a writable cell (read-back matches), and attempt a block-controlled cell (fail-closed).

- [ ] **Step 1: Write the live test**

Create `tests/unit_py/test_dialog_table_live.py`:

```python
# tests/unit_py/test_dialog_table_live.py
import os, sys
import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

BLOCK_ID = os.environ.get("ES_EQUATION_BLOCK_ID")


def _extendsim_available():
    try:
        import win32com.client  # noqa: F401
        import win32com.client as c
        c.GetActiveObject("ExtendSim.Application")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    BLOCK_ID is None or not _extendsim_available(),
    reason="Needs running ExtendSim + ES_EQUATION_BLOCK_ID of a configured Equation(I) block",
)


def test_live_read_string_cell():
    from dialog_table import table_get_entry
    res = table_get_entry(int(BLOCK_ID), "IVars_ttbl", 0, 1)
    assert res["success"] is True
    assert isinstance(res["value"], str)


def test_live_write_writable_cell_roundtrips():
    # Equation_dtxt holds the ModL code text — a writable string cell.
    from dialog_table import table_get_entry, table_set_entry
    original = table_get_entry(int(BLOCK_ID), "Equation_dtxt", 0, 0)["value"]
    try:
        res = table_set_entry(int(BLOCK_ID), "Equation_dtxt", "// table_set probe", 0, 0)
        assert res["success"] is True
        assert res["value"] == "// table_set probe"
    finally:
        table_set_entry(int(BLOCK_ID), "Equation_dtxt", original, 0, 0)


def test_live_block_controlled_cell_fails_closed():
    # The auto-named OVars_ttbl connector cell rejects writes (proven 2026-06-28).
    from dialog_table import table_set_entry
    res = table_set_entry(int(BLOCK_ID), "OVars_ttbl", "shouldNotStick", 0, 1)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_WRITE_REJECTED"
    assert "actual" in res
```

- [ ] **Step 2: Run the test (skips without ExtendSim/fixture)**

Run: `python -m pytest tests/unit_py/test_dialog_table_live.py -v`
Expected without fixture: 3 SKIPPED.
Expected with `ES_EQUATION_BLOCK_ID=<id>` set and ExtendSim open with a configured Equation block: 3 PASSED.

> Controller note: discover a real configured `Equation(I)` block id at execution time
> (the live model has one; the earlier inspection reported `id=3`). If `Equation_dtxt`
> turns out to be read-only in this build, substitute any cell confirmed writable during
> the live run; the assertion that read-back matches is what matters, not the specific cell.

- [ ] **Step 3: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/unit_py/test_dialog_table_live.py
git commit -m "test: add skipif live verification for table_get/table_set"
```

---

## Self-Review

**1. Spec coverage:**
- Spec §1 (`table_get`/`table_set`, new module, two tools, 96→98) → Tasks 1–4.
- Spec §3 component table (`table_get_entry`/`table_set_entry`, reuse `_get_var`/`_set_var_string`) → Tasks 1–3.
- Spec §4 dataflow (read; write→readback→verify→fail-closed) → Tasks 1–2 cores.
- Spec §5 fail-closed error codes `TABLE_READ_FAILED`/`TABLE_WRITE_FAILED`/`TABLE_WRITE_REJECTED` → asserted in Tasks 1–2.
- Spec §6 tests 1–5 (read ok/fail, write ok/rejected/com-fail, escaping-forward) → Task 1 (3 tests) + Task 2 (4 tests). Test 6 live → Task 5.
- Spec §7 sequencing (get → set → MCP reg/coverage → live) → Tasks 1→2→3→4→5.
- Spec §8 open question 1 (cell layout) → handled by the Task 5 controller note (discover at run time).

**2. Placeholder scan:** No "TBD"/"add error handling"/"similar to Task N". Every code step shows full code. Error handling is concrete (fail-closed dicts).

**3. Type consistency:** `table_get(backend, block_id, var_name, row, col)` and `table_set(backend, block_id, var_name, value, row, col)` are used identically in cores, entries, tests, and dispatch. Result keys (`success`, `blockId`, `variableName`, `row`, `col`, `value`, `errorCode`, `requested`, `actual`) match across tasks. TS params (`blockId`, `variableName`, `value`, `row`, `col`) map to Python `p.get("blockId")` / `p.get("variableName")` / `p.get("value")` / `p.get("row")` / `p.get("col")` in the dispatch — consistent.
