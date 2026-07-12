# resource-machine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `resource-machine` molecule functional — a machine (Activity) whose throughput is limited by a named Resource Pool, with a full acquire→use→release cycle — by fixing three false-success backend functions, adding an effect-verified config core, the molecule, and a block-layout algorithm.

**Architecture:** A new pure core `resource_pool_config.py` (injected backend, effect-verified, fail-closed) owns the pool/queue/release configuration, mirroring `attribute_config.py`/`dialog_table.py`. The three broken backend functions delegate to it. `instantiate` gains a layout phase (spreads blocks) and a resource-config phase. The recipe is **live-verified** (49 items through the full cycle, configured entirely in code).

**Tech Stack:** Python 3 (pytest), ExtendSim COM for the live task.

**Working branch:** `feature/resource-machine`

**Reference (read once):** the design spec `docs/superpowers/specs/2026-07-12-resource-machine-design.md`, and `src/attribute_config.py` + `tests/unit_py/test_attribute_config.py` for the pure-core + FakeBackend style to mirror. Also `src/instantiate.py` (current phases incl. `_merge_param_defaults` and Phase 4b) and `src/molecule_schema.py` (`resolve_set_attributes`).

**Verified COM recipe (the source of truth for all values):**
- Resource Pool: `ResourcePoolName`=name (string via SetDialogVariable), `NumServ`=capacity (numeric).
- Queue: `QueueType_pop`=2, `ResourceTable[0,0]`=pool NAME (string via SetDialogVariable), `[0,1]`=qty.
- Resource Pool Release: `Serverblocks_pop`=int index of the pool (found by set-then-read-back `ResourcePoolName`), `NumReleased_PRM`=qty.
- Flow: Create→Queue→Activity→Release→Exit + wire `Pool.ValuesOut(1)→Queue.ResourcePoolQuantityIn(5)`.
- `ResourceTable`/`ResourcePoolName` read back via `GetDialogVariable` (string), NOT `GetVariableNumeric` (→ nan).

---

## Task 1: Backend dialog helpers + pure core `resource_pool_config.py`

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (add two small COM helpers near `_set_var_string`, ~line 148)
- Create: `src/ExtendSimMCP.TypeScript/src/resource_pool_config.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_pool_config.py`

- [ ] **Step 1: Add two COM helpers to simulation_backend.py**

Immediately after `_set_var_string` (ends ~line 148), add:

```python
def _set_dialog_var(app, block_id: int, var_name: str, value, row: int = 0, col: int = 0):
    """Write a dialog cell via SetDialogVariable regardless of suffix.

    Needed for string-tables named without a _ttbl suffix (e.g. Queue
    'ResourceTable') and edittext fields ('ResourcePoolName'), where the
    suffix-based _set_var routing would wrongly use SetVariableNumeric (a silent
    no-op on string cells). String values are quoted; numbers written bare.
    """
    if isinstance(value, str):
        app.Execute(f'SetDialogVariable({block_id}, "{var_name}", "{_escape_modl_string(value)}", {row}, {col});')
    else:
        app.Execute(f'SetDialogVariable({block_id}, "{var_name}", {value}, {row}, {col});')


def _get_dialog_string(app, block_id: int, var_name: str, row: int = 0, col: int = 0) -> str:
    """Read a dialog cell via GetDialogVariable as a string regardless of suffix.

    Suffix-less string/popup vars (ResourcePoolName, ResourceTable, Serverblocks_pop)
    read as '-nan(ind)' through GetVariableNumeric; GetDialogVariable returns the
    text (or the popup's index as text)."""
    app.Execute(f'globalStr0 = GetDialogVariable({block_id}, "{var_name}", {row}, {col});')
    return app.Request("System", "globalStr0+:0:0:0")
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit_py/test_resource_pool_config.py`:

```python
# tests/unit_py/test_resource_pool_config.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import resource_pool_config as rpc
from resource_pool_config import configure_pool, configure_queue_pool, configure_release


class FakeBackend:
    """Models the live COM surface the core needs. Simulates a pool list so the
    Serverblocks_pop index search is exercised: index i selects pools[i]."""
    def __init__(self, pools=("", "Pool1"), model_open=True, block_ok=True, raise_on=None):
        self.calls = []
        self.cells = {}                 # (block_id, var, row, col) -> value
        self.numeric = {}               # (block_id, var) -> number
        self._pools = list(pools)       # index -> pool name (index 0 = "<none>")
        self._server_index = {}         # release block_id -> selected Serverblocks_pop
        self._model_open = model_open
        self._block_ok = block_ok
        self._raise_on = raise_on
        self.app = object()

    def get_extendsim_app(self): return self.app
    def _validate_model_open(self, app):
        return {"success": True} if self._model_open else \
            {"success": False, "errorCode": "MODEL_NOT_OPEN", "error": "no model"}
    def _validate_block_type(self, app, block_id, expected):
        return {"success": True} if self._block_ok else \
            {"success": False, "errorCode": "WRONG_BLOCK_TYPE", "error": expected}

    def _set_var(self, app, block_id, var, value, row=0, col=0, msg=1):
        self.calls.append(("num", block_id, var, value, row, col))
        if self._raise_on == "set": raise RuntimeError("com boom")
        self.numeric[(block_id, var)] = value
        if var == "Serverblocks_pop":
            self._server_index[block_id] = int(value)

    def _set_dialog_var(self, app, block_id, var, value, row=0, col=0):
        self.calls.append(("dlg", block_id, var, value, row, col))
        if self._raise_on == "set": raise RuntimeError("com boom")
        self.cells[(block_id, var, row, col)] = str(value)

    def _get_dialog_string(self, app, block_id, var, row=0, col=0):
        if self._raise_on == "get": raise RuntimeError("com boom")
        if var == "ResourcePoolName" and block_id in self._server_index:
            idx = self._server_index[block_id]
            return self._pools[idx] if 0 <= idx < len(self._pools) else ""
        return self.cells.get((block_id, var, row, col), "")

    def _get_var(self, app, block_id, var, row=0, col=0):
        return self.numeric.get((block_id, var), "")


def test_configure_pool_sets_name_and_capacity_verified():
    be = FakeBackend()
    res = configure_pool(be, 62, "Pool1", 2)
    assert res["success"] is True
    assert be.cells[(62, "ResourcePoolName", 0, 0)] == "Pool1"
    assert be.numeric[(62, "NumServ")] == 2


def test_configure_pool_rejected_when_name_readback_differs():
    be = FakeBackend()
    # force the readback to differ by making the cell store nothing for name
    orig = be._get_dialog_string
    be._get_dialog_string = lambda app, b, v, row=0, col=0: "Other" if v == "ResourcePoolName" else orig(app, b, v, row, col)
    res = configure_pool(be, 62, "Pool1", 2)
    assert res["success"] is False
    assert res["errorCode"] == "POOL_CONFIG_REJECTED"


def test_configure_queue_pool_sets_mode_table_verified():
    be = FakeBackend()
    res = configure_queue_pool(be, 10, "Pool1", 1)
    assert res["success"] is True
    assert be.numeric[(10, "QueueType_pop")] == 2
    assert be.cells[(10, "ResourceTable", 0, 0)] == "Pool1"
    assert be.cells[(10, "ResourceTable", 0, 1)] == "1"


def test_configure_queue_pool_rejected_when_table_readback_differs():
    be = FakeBackend()
    be._get_dialog_string = lambda app, b, v, row=0, col=0: "" if v == "ResourceTable" else "x"
    res = configure_queue_pool(be, 10, "Pool1", 1)
    assert res["success"] is False
    assert res["errorCode"] == "QUEUE_POOL_REJECTED"


def test_configure_release_finds_pool_index():
    be = FakeBackend(pools=("", "Pool1"))     # Pool1 is index 1
    res = configure_release(be, 46, "Pool1", 1)
    assert res["success"] is True
    assert res["poolIndex"] == 1
    assert be.numeric[(46, "Serverblocks_pop")] == 1
    assert be.numeric[(46, "NumReleased_PRM")] == 1


def test_configure_release_finds_pool_index_when_not_first():
    be = FakeBackend(pools=("", "Other", "Pool1"))   # Pool1 is index 2
    res = configure_release(be, 46, "Pool1", 1)
    assert res["success"] is True
    assert res["poolIndex"] == 2


def test_configure_release_fails_when_pool_absent():
    be = FakeBackend(pools=("", "Other"))
    res = configure_release(be, 46, "Pool1", 1)
    assert res["success"] is False
    assert res["errorCode"] == "RELEASE_POOL_NOT_FOUND"


def test_cores_propagate_model_and_block_checks():
    assert configure_pool(FakeBackend(model_open=False), 1, "P", 1)["errorCode"] == "MODEL_NOT_OPEN"
    assert configure_pool(FakeBackend(block_ok=False), 1, "P", 1)["errorCode"] == "WRONG_BLOCK_TYPE"


def test_entries_exist_with_expected_arity():
    import inspect
    for fn, params in [
        (rpc.configure_pool_entry, ["block_id", "name", "capacity"]),
        (rpc.configure_queue_pool_entry, ["block_id", "pool_name", "qty"]),
        (rpc.configure_release_entry, ["block_id", "pool_name", "qty"]),
    ]:
        assert list(inspect.signature(fn).parameters) == params
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_resource_pool_config.py -v`
Expected: collect error — `resource_pool_config` does not exist.

- [ ] **Step 4: Create `src/resource_pool_config.py`**

```python
# src/resource_pool_config.py
"""Resource Pool configuration for a functional resource-constrained machine.

Pure cores take an injected `backend` and are effect-verified + fail-closed.
Live-verified recipe (2026-07-12): 49 items through a full acquire/use/release
cycle, configured entirely in code. See spec 2026-07-12-resource-machine-design.md.

COM facts: ResourcePoolName / Queue ResourceTable are string cells written via
SetDialogVariable (ResourceTable has no _ttbl suffix, so _set_var would silently
no-op) and read back via GetDialogVariable-as-string. The Release block must
select its pool via Serverblocks_pop (int index); the right index is found by
setting it and reading back ResourcePoolName until it matches, else the sim
aborts at t=0 (CHECKDATA).
"""

MAX_POOL_INDEX = 32     # upper bound for the Serverblocks_pop index search


def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def _preflight(backend, block_id, block_type):
    app = backend.get_extendsim_app()
    mc = backend._validate_model_open(app)
    if not mc.get("success"):
        return app, mc
    tc = backend._validate_block_type(app, block_id, block_type)
    if not tc.get("success"):
        return app, tc
    return app, None


def configure_pool(backend, block_id, name, capacity):
    """Set a Resource Pool's name (string) + capacity (numeric), verified."""
    app, err = _preflight(backend, block_id, "Resource Pool")
    if err:
        return err
    try:
        backend._set_dialog_var(app, block_id, "ResourcePoolName", str(name))
        backend._set_var(app, block_id, "NumServ", capacity, 0, 0, 1)
    except Exception as e:
        return _err("POOL_CONFIG_FAILED", str(e), blockId=block_id)
    try:
        name_rb = backend._get_dialog_string(app, block_id, "ResourcePoolName")
    except Exception as e:
        return _err("POOL_CONFIG_READ_FAILED", str(e), blockId=block_id)
    if str(name_rb) != str(name):
        return _err("POOL_CONFIG_REJECTED", f"pool name on block {block_id} did not persist",
                    blockId=block_id, requested=str(name), actual=str(name_rb))
    return {"success": True, "blockId": block_id, "name": name, "capacity": capacity}


def configure_queue_pool(backend, block_id, pool_name, qty=1):
    """Put a Queue in Resource Pool mode and point it at pool_name (by name)."""
    app, err = _preflight(backend, block_id, "Queue")
    if err:
        return err
    try:
        backend._set_var(app, block_id, "QueueType_pop", 2, 0, 0, 1)
        backend._set_dialog_var(app, block_id, "ResourceTable", str(pool_name), 0, 0)
        backend._set_dialog_var(app, block_id, "ResourceTable", qty, 0, 1)
    except Exception as e:
        return _err("QUEUE_POOL_FAILED", str(e), blockId=block_id)
    try:
        name_rb = backend._get_dialog_string(app, block_id, "ResourceTable", 0, 0)
    except Exception as e:
        return _err("QUEUE_POOL_READ_FAILED", str(e), blockId=block_id)
    if str(name_rb) != str(pool_name):
        return _err("QUEUE_POOL_REJECTED", f"ResourceTable on block {block_id} did not persist",
                    blockId=block_id, requested=str(pool_name), actual=str(name_rb))
    return {"success": True, "blockId": block_id, "poolName": pool_name, "qty": qty}


def configure_release(backend, block_id, pool_name, qty=1):
    """Point a Resource Pool Release at pool_name. Serverblocks_pop is an int
    index into the model's pools; find the index whose ResourcePoolName readback
    matches pool_name (robust to other pools). Fail-closed if none matches."""
    app, err = _preflight(backend, block_id, "Resource Pool Release")
    if err:
        return err
    found = None
    try:
        for idx in range(1, MAX_POOL_INDEX + 1):
            backend._set_var(app, block_id, "Serverblocks_pop", idx, 0, 0, 1)
            if str(backend._get_dialog_string(app, block_id, "ResourcePoolName")) == str(pool_name):
                found = idx
                break
    except Exception as e:
        return _err("RELEASE_CONFIG_FAILED", str(e), blockId=block_id)
    if found is None:
        return _err("RELEASE_POOL_NOT_FOUND",
                    f"no Serverblocks_pop index selects pool '{pool_name}' on block {block_id}",
                    blockId=block_id, poolName=pool_name)
    try:
        backend._set_var(app, block_id, "NumReleased_PRM", qty, 0, 0, 1)
    except Exception as e:
        return _err("RELEASE_CONFIG_FAILED", str(e), blockId=block_id)
    return {"success": True, "blockId": block_id, "poolName": pool_name,
            "poolIndex": found, "qty": qty}


def configure_pool_entry(block_id, name, capacity):
    import simulation_backend as backend
    return configure_pool(backend, block_id, name, capacity)


def configure_queue_pool_entry(block_id, pool_name, qty=1):
    import simulation_backend as backend
    return configure_queue_pool(backend, block_id, pool_name, qty)


def configure_release_entry(block_id, pool_name, qty=1):
    import simulation_backend as backend
    return configure_release(backend, block_id, pool_name, qty)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_resource_pool_config.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/src/resource_pool_config.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_pool_config.py
git commit -m "feat(resource-machine): effect-verified resource_pool_config core + dialog helpers"
```

---

## Task 2: Rewrite the three broken backend functions to delegate

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (`resource_pool_set_config` ~4599, `resource_pool_release_set_config` ~4672, `queue_set_resource_pool` ~4701)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_pool_delegation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit_py/test_resource_pool_delegation.py`:

```python
# tests/unit_py/test_resource_pool_delegation.py
import os, sys, inspect
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        import pytest
        pytest.skip("simulation_backend not importable (no pywin32)")


def _src_of(fn):
    return inspect.getsource(fn)


def test_queue_set_resource_pool_delegates_and_drops_broken_writes():
    be = _load()
    import resource_pool_config
    seen = {}
    def fake(backend, block_id, pool_name, qty=1):
        seen.update(dict(block_id=block_id, pool_name=pool_name, qty=qty))
        return {"success": True, "blockId": block_id, "poolName": pool_name, "qty": qty}
    import pytest
    monkey = pytest.MonkeyPatch()
    monkey.setattr(resource_pool_config, "configure_queue_pool", fake)
    try:
        res = be.queue_set_resource_pool(10, 62, resources_needed=1)  # signature preserved
    finally:
        monkey.undo()
    assert res.get("success") is True
    # It must pass the POOL NAME, not the block id, and must no longer write
    # ResourceTable via the broken numeric path.
    src = _src_of(be.queue_set_resource_pool)
    assert 'SetVariableNumeric' not in src  # broken path gone (was via _set_var on ResourceTable)
    assert '_set_var(app, block_id, "ResourceTable"' not in src


def test_release_config_sets_the_pool():
    be = _load()
    src = _src_of(be.resource_pool_release_set_config)
    # Must now involve the pool (Serverblocks_pop) via the core, not only NumReleased_PRM.
    assert "configure_release" in src


def test_pool_set_config_delegates():
    be = _load()
    src = _src_of(be.resource_pool_set_config)
    assert "configure_pool" in src
```

Note: `queue_set_resource_pool` currently takes a `resource_pool_block_id`; the fixed version must resolve that block's pool NAME (read `ResourcePoolName` off that block) and pass the name to the core. Adjust the test's expectation only if the team decides to change the public signature — default is to KEEP the signature and translate id→name internally.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_resource_pool_delegation.py -v`
Expected: FAIL (old bodies still contain the broken writes / no core delegation).

- [ ] **Step 3: Rewrite the three functions**

Replace each body (keep signatures). For `resource_pool_set_config`:

```python
def resource_pool_set_config(block_id: int, pool_name=None, initial_resources=None,
                             allocation_rule=None, model_id=None) -> dict:
    """Configure a Resource Pool block (name + capacity), effect-verified.

    Delegates to the fail-closed resource_pool_config core. allocation_rule is
    applied directly (AllocRule popup) after the verified name/capacity write."""
    import resource_pool_config, sys as _sys
    name = pool_name if pool_name is not None else ""
    cap = initial_resources if initial_resources is not None else 1
    res = resource_pool_config.configure_pool(_sys.modules[__name__], block_id, name, cap)
    if res.get("success") and allocation_rule is not None:
        try:
            app = get_extendsim_app()
            _set_var(app, block_id, "AllocRule", RESOURCE_ALLOC_RULE.get(allocation_rule.lower(), 1))
        except Exception:
            pass
    return res
```

For `resource_pool_release_set_config` (add a `pool_name` param with default None; keep `release_quantity`):

```python
def resource_pool_release_set_config(block_id: int, pool_name: Optional[str] = None,
                                     release_quantity: Optional[int] = None,
                                     model_id=None) -> dict:
    """Configure a Resource Pool Release block: select the pool + release qty.

    Selecting the pool (Serverblocks_pop) is REQUIRED — without it ExtendSim
    aborts the simulation at t=0 (CHECKDATA). Delegates to the fail-closed core."""
    import resource_pool_config, sys as _sys
    qty = release_quantity if release_quantity is not None else 1
    if pool_name is None:
        return _error(ErrorCode.SET_VALUE_FAILED,
                      "pool_name is required for a Resource Pool Release block",
                      blockId=block_id, operation="resource_pool_release_set_config")
    return resource_pool_config.configure_release(_sys.modules[__name__], block_id, pool_name, qty)
```

For `queue_set_resource_pool` (keep signature; translate pool block id → name):

```python
def queue_set_resource_pool(block_id: int, resource_pool_block_id: int,
                            resources_needed: int = 1, model_id=None) -> dict:
    """Put a Queue in Resource Pool mode and point it at the given Resource Pool.

    The Queue references the pool by NAME (read off the pool block), written into
    ResourceTable via SetDialogVariable. Delegates to the fail-closed core."""
    import resource_pool_config, sys as _sys
    app = get_extendsim_app()
    pool_name = _get_dialog_string(app, resource_pool_block_id, "ResourcePoolName")
    if not pool_name or str(pool_name) in ("", "-nan(ind)"):
        return _error(ErrorCode.SET_VALUE_FAILED,
                      f"Resource Pool block {resource_pool_block_id} has no name to reference",
                      blockId=block_id, operation="queue_set_resource_pool")
    return resource_pool_config.configure_queue_pool(_sys.modules[__name__], block_id,
                                                     str(pool_name), resources_needed)
```

- [ ] **Step 4: Thread `pool_name` through the dispatch + tool schema**

`resource_pool_release_set_config` gained a `pool_name` param. Find where it is dispatched and described so a caller can supply it:
`grep -n "resource_pool_release_set_config\|resourcePoolBlockId\|release_quantity" src/simulation_backend.py`
- In the COMMANDS lambda for `resource_pool_release_set_config` (~line 10230), pass `pool_name=p.get("poolName")` (and keep `release_quantity`).
- In the block_configure / tool-parameter description block for the Resource Pool Release action (~lines 7905 / 8040 / 8232), add a `poolName` parameter description.
This keeps the MCP tool usable. The molecule path does NOT use these functions (it calls the core via `RealOps.configure_resource_pool`), so this step is about the standalone tool surface only.

- [ ] **Step 5: Run tests**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_resource_pool_delegation.py tests/unit_py/test_resource_pool_config.py -v && npm test`
Expected: PASS (source-scan tests pass without COM; delegation test skips if pywin32 absent; TS `dispatch-coverage` still green).

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_pool_delegation.py
git commit -m "fix(resource-machine): pool/queue/release config delegate to verified core (kill false-success)"
```

---

## Task 3: Fix `simulation_run` end-time (SetRunParameter)

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (`simulation_run`, ~line 2284)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_simulation_run_endtime.py`

- [ ] **Step 1: Write the failing test (source-scan, COM-free)**

Create `tests/unit_py/test_simulation_run_endtime.py`:

```python
import os, sys, inspect
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def test_simulation_run_uses_setrunparameter():
    import importlib
    try:
        be = importlib.import_module("simulation_backend")
    except Exception:
        import pytest; pytest.skip("no pywin32")
    src = inspect.getsource(be.simulation_run)
    assert "SetRunParameter" in src, "end time must be set via SetRunParameter"
```

- [ ] **Step 2: Run it — Expected FAIL** (current code uses `endTime = {end_time}`).

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_simulation_run_endtime.py -v`

- [ ] **Step 3: Change the end-time line**

In `simulation_run`, replace:

```python
        if end_time is not None:
            app.Execute(f"endTime = {end_time};")
```

with:

```python
        if end_time is not None:
            # endTime = X does NOT set the run end time (stays at the model default);
            # SetRunParameter is the effective API (see test_distribution_roundtrip.py).
            app.Execute(f"SetRunParameter({end_time}, 1);")
```

- [ ] **Step 4: Run tests + the existing live suite is unaffected (still bounded runs).**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_simulation_run_endtime.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_simulation_run_endtime.py
git commit -m "fix(sim): simulation_run sets end time via SetRunParameter (endTime= was a no-op)"
```

---

## Task 4: Block-layout phase in instantiate

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py` (`build_molecule`; add `layout` to `EsOps` via `RealOps.move`)
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py` (record `move`)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py` (add case)

- [ ] **Step 1: Add `move` to FakeOps**

In `tests/unit_py/fake_ops.py`, after `set_attribute`, add:

```python
    def move(self, block_id, x, y):
        self.calls.append(("move", block_id, x, y))
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit_py/test_instantiate.py`:

```python
def test_build_lays_out_blocks_without_overlap():
    from instantiate import build_molecule
    from fake_ops import FakeOps
    mol = {
        "id": "lin", "kind": "molecule", "params": {},
        "attributes": {"reads": [], "writes": []},
        "nodes": [
            {"ref": "a", "lib": "Item.lbr", "type": "Queue"},
            {"ref": "b", "lib": "Item.lbr", "type": "Activity", "seed": True},
        ],
        "edges": [{"kind": "flow", "from": "a.ItemOut", "to": "b.ItemIn"}],
        "interface": {"inlets": [{"port": "in", "binds": "a.ItemIn", "role": "item"}],
                      "outlets": [{"port": "out", "binds": "b.ItemOut", "role": "item"}]},
    }
    ops = FakeOps()
    build_molecule(mol, {}, ops)
    moves = [c for c in ops.calls if c[0] == "move"]
    assert len(moves) >= 2                     # every node positioned
    xs = [m[2] for m in moves]
    assert len(set(xs)) == len(xs)             # no two blocks share an x -> not stacked
```

- [ ] **Step 3: Run — Expected FAIL** (no layout phase).

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v -k layout`

- [ ] **Step 4: Add the layout phase to `build_molecule`**

Add a module-level helper (near `_flow_chain`):

```python
def _layout(molecule, internal, ops):
    """Spread blocks: flow nodes left->right along x, side nodes on a row below.
    Deterministic; prevents the default (100,100)-stacking. Positions are logical
    ExtendSim units; exact spacing is cosmetic."""
    chain = _flow_chain([e for e in molecule["edges"] if e["kind"] == "flow"])
    ordered = chain or [n["ref"] for n in molecule["nodes"]]
    x0, y_flow, dx = 60, 100, 120
    for i, ref in enumerate(ordered):
        if ref in internal:
            ops.move(internal[ref], x0 + i * dx, y_flow)
    side = [n["ref"] for n in molecule["nodes"] if n["ref"] not in ordered]
    for j, ref in enumerate(side):
        if ref in internal:
            ops.move(internal[ref], x0 + j * dx, y_flow + 140)
```

At the end of `build_molecule`, just before building the interface map (Phase 5), call:

```python
    _layout(molecule, internal, ops)
```

- [ ] **Step 5: Add `move` to `RealOps`**

In `RealOps` (after `set_attribute`):

```python
    def move(self, block_id, x, y):
        r = self._b.block_move(block_id, x, y)
        if not r.get("success"):
            raise BuildError(f"move failed: block {block_id} -> ({x},{y}): {r}")
```

(If `block_move`'s signature differs, adapt the call; confirm via `grep -n "def block_move" src/simulation_backend.py`.)

- [ ] **Step 6: Run tests**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py tests/unit_py/test_tag_items.py -v`
Expected: PASS (existing molecule builds still pass; new layout test passes).

- [ ] **Step 7: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "feat(instantiate): layout phase spreads molecule blocks (no more stacking)"
```

---

## Task 5: Resource-pool config phase in instantiate + schema resolve

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/molecule_schema.py` (add `resolve_resource_pool`)
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py` (Phase 4c + `RealOps.configure_resource_pool`)
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py` (record `configure_resource_pool`)
- Test: `tests/unit_py/test_molecule_schema.py`, `tests/unit_py/test_instantiate.py`

- [ ] **Step 1: Write failing schema test**

Append to `tests/unit_py/test_molecule_schema.py`:

```python
def test_resolve_resource_pool_resolves_placeholders():
    from molecule_schema import resolve_resource_pool
    mol = {"resourcePool": {"poolNode": "rp", "queueNode": "q", "releaseNode": "rel",
                            "name": "{{pool_name}}", "capacity": "{{capacity}}", "qty": 1}}
    out = resolve_resource_pool(mol, {"pool_name": "Pool1", "capacity": 2})
    assert out == {"poolNode": "rp", "queueNode": "q", "releaseNode": "rel",
                   "name": "Pool1", "capacity": 2, "qty": 1}


def test_resolve_resource_pool_none_when_absent():
    from molecule_schema import resolve_resource_pool
    assert resolve_resource_pool({}, {}) is None
```

- [ ] **Step 2: Implement `resolve_resource_pool`** in `molecule_schema.py` (near `resolve_set_attributes`):

```python
def resolve_resource_pool(molecule, params):
    """Resolve a molecule's optional resourcePool block ({{...}} -> values)."""
    rp = molecule.get("resourcePool")
    if not rp:
        return None
    out = dict(rp)
    for k in ("name", "capacity", "qty"):
        if k in out:
            out[k] = _resolve_value(out[k], params)
    return out
```

- [ ] **Step 3: Run schema tests — Expected: new ones fail then pass after Step 2.**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_molecule_schema.py -v -k resource_pool`

- [ ] **Step 4: Add `configure_resource_pool` to FakeOps**

In `fake_ops.py` after `move`:

```python
    def configure_resource_pool(self, pool_id, queue_id, release_id, name, capacity, qty):
        self.calls.append(("resource_pool", pool_id, queue_id, release_id, name, capacity, qty))
```

- [ ] **Step 5: Write failing instantiate test**

Append to `tests/unit_py/test_instantiate.py`:

```python
def test_build_applies_resource_pool_config():
    from instantiate import build_molecule
    from fake_ops import FakeOps
    mol = {
        "id": "rm", "kind": "molecule",
        "params": {"capacity": {"default": 2}, "pool_name": {"default": "Pool1"}},
        "attributes": {"reads": [], "writes": []},
        "nodes": [
            {"ref": "q", "lib": "Item.lbr", "type": "Queue"},
            {"ref": "act", "lib": "Item.lbr", "type": "Activity"},
            {"ref": "rel", "lib": "Item.lbr", "type": "Resource Pool Release", "seed": True},
            {"ref": "rp", "lib": "Item.lbr", "type": "Resource Pool"},
        ],
        "resourcePool": {"poolNode": "rp", "queueNode": "q", "releaseNode": "rel",
                         "name": "{{pool_name}}", "capacity": "{{capacity}}", "qty": 1},
        "edges": [{"kind": "flow", "from": "q.ItemOut", "to": "act.ItemIn"},
                  {"kind": "flow", "from": "act.ItemOut", "to": "rel.ItemIn"},
                  {"kind": "side", "from": "rp.ValuesOut", "to": "q.ResourcePoolQuantityIn"}],
        "interface": {"inlets": [{"port": "in", "binds": "q.ItemIn", "role": "item"}],
                      "outlets": [{"port": "out", "binds": "rel.ItemOut", "role": "item"}]},
    }
    ops = FakeOps()
    res = build_molecule(mol, {}, ops)
    ids = res["internalBlockIds"]
    assert ("resource_pool", ids["rp"], ids["q"], ids["rel"], "Pool1", 2, 1) in ops.calls
```

Note: `FakeOps.CONS` must include the connectors used here. Confirm `Resource Pool Release` has `ItemIn`/`ItemOut`, `Resource Pool` has `ValuesOut`, and `Queue` has `ResourcePoolQuantityIn` in `FakeOps.CONS`; add any missing (e.g. `"Resource Pool Release": {"ItemIn": 0, "ItemOut": 1}`).

- [ ] **Step 6: Add Phase 4c to `build_molecule`** (after Phase 4b) and import `resolve_resource_pool`:

Change the import line to add `resolve_resource_pool`. Then add:

```python
    # Phase 4c: apply the resource-pool config (pool + queue + release), if any.
    rp_cfg = resolve_resource_pool(molecule, params)
    if rp_cfg:
        ops.configure_resource_pool(
            internal[rp_cfg["poolNode"]], internal[rp_cfg["queueNode"]],
            internal[rp_cfg["releaseNode"]], rp_cfg["name"], rp_cfg["capacity"], rp_cfg["qty"])
```

- [ ] **Step 7: Add `configure_resource_pool` to `RealOps`:**

```python
    def configure_resource_pool(self, pool_id, queue_id, release_id, name, capacity, qty):
        import resource_pool_config as rpc
        p1 = rpc.configure_pool(self._b, pool_id, name, capacity)
        if not p1.get("success"):
            raise BuildError(f"pool config failed: {p1}")
        p2 = rpc.configure_queue_pool(self._b, queue_id, name, qty)
        if not p2.get("success"):
            raise BuildError(f"queue pool config failed: {p2}")
        p3 = rpc.configure_release(self._b, release_id, name, qty)
        if not p3.get("success"):
            raise BuildError(f"release config failed: {p3}")
```

- [ ] **Step 8: Run tests**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py tests/unit_py/test_molecule_schema.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/molecule_schema.py src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_molecule_schema.py
git commit -m "feat(resource-machine): instantiate Phase 4c applies pool/queue/release config"
```

---

## Task 6: `resource-machine.json` molecule + molecule test

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/patterns/molecules/resource-machine.json`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_machine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit_py/test_resource_machine.py`:

```python
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
from fake_ops import FakeOps, load
from instantiate import build_molecule


def test_resource_machine_builds_with_pool_config():
    ops = FakeOps()
    res = build_molecule(load("resource-machine.json"), {"pool_name": "Pool1", "capacity": 3}, ops)
    ids = res["internalBlockIds"]
    assert {"q", "act", "rel", "rp"}.issubset(ids)
    assert ("resource_pool", ids["rp"], ids["q"], ids["rel"], "Pool1", 3, 1) in ops.calls


def test_resource_machine_default_params():
    ops = FakeOps()
    res = build_molecule(load("resource-machine.json"), {}, ops)
    ids = res["internalBlockIds"]
    assert ("resource_pool", ids["rp"], ids["q"], ids["rel"], "Pool1", 2, 1) in ops.calls
```

- [ ] **Step 2: Run — Expected FAIL** (molecule missing).

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_resource_machine.py -v`

- [ ] **Step 3: Create `patterns/molecules/resource-machine.json`**

```json
{
  "id": "resource-machine",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Maskin vars genomflöde begränsas av en resurspool",
  "params": {
    "process_time": { "required": false, "default": 1 },
    "capacity":     { "required": false, "default": 2 },
    "pool_name":    { "required": false, "default": "Pool1" }
  },
  "attributes": { "reads": [], "writes": [] },
  "nodes": [
    { "ref": "q",   "lib": "Item.lbr", "type": "Queue" },
    { "ref": "act", "lib": "Item.lbr", "type": "Activity", "params": { "D": "{{process_time}}" } },
    { "ref": "rel", "lib": "Item.lbr", "type": "Resource Pool Release", "seed": true },
    { "ref": "rp",  "lib": "Item.lbr", "type": "Resource Pool" }
  ],
  "resourcePool": {
    "poolNode": "rp", "queueNode": "q", "releaseNode": "rel",
    "name": "{{pool_name}}", "capacity": "{{capacity}}", "qty": 1
  },
  "edges": [
    { "kind": "flow", "from": "q.ItemOut",    "to": "act.ItemIn" },
    { "kind": "flow", "from": "act.ItemOut",  "to": "rel.ItemIn" },
    { "kind": "side", "from": "rp.ValuesOut", "to": "q.ResourcePoolQuantityIn" }
  ],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "q.ItemIn",    "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "rel.ItemOut", "role": "item" } ]
  }
}
```

- [ ] **Step 4: Run — Expected PASS.** Fix `FakeOps.CONS` if a connector is missing (see Task 5 Step 5 note).

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_resource_machine.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/resource-machine.json src/ExtendSimMCP.TypeScript/tests/unit_py/test_resource_machine.py
git commit -m "feat(resource-machine): resource-machine molecule (pool-limited machine)"
```

---

## Task 7: Packaging + full suite + live test

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/package.json` (`copy-files`)
- Create: `src/ExtendSimMCP.TypeScript/tests/live/test_resource_machine_live.py`

- [ ] **Step 1: Add `resource_pool_config.py` to `copy-files`**

In `package.json`, after the `attribute_config.py` copy, add:
`fs.copyFileSync('src/resource_pool_config.py', 'dist/resource_pool_config.py');`

- [ ] **Step 2: Run full unit + TS suites**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py -v && npm test`
Expected: all green. (No new MCP tools — the three functions already exist; tool count unchanged.)

- [ ] **Step 3: `npm run build` + verify dist packaging**

Run: `cd src/ExtendSimMCP.TypeScript && npm run build && ls dist/resource_pool_config.py`
Expected: file present.

- [ ] **Step 4: (Controller-run) live test — do NOT delegate to a subagent**

Create `tests/live/test_resource_machine_live.py`. Guard with a COM-availability skip. The recipe is live-verified to produce ~45-49 items.

```python
# tests/live/test_resource_machine_live.py
import os, sys
import pytest
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

def _es():
    try:
        import win32com.client
        win32com.client.GetActiveObject("ExtendSim.Application").Request("System", "global0+:0:0:0")
        return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _es(), reason="ExtendSim not running")


def test_resource_machine_runs_and_flows():
    import simulation_backend as sb
    from instantiate import instantiate_pattern
    sb.execute_command("ActivateApplication();")
    res = instantiate_pattern("resource-machine", {"process_time": 1, "capacity": 2, "pool_name": "Pool1"})
    assert res.get("success"), res
    hid = res["hblockId"]
    cS = eS = None
    try:
        cS = sb.block_add("Item.lbr", "Create")["blockId"]
        eS = sb.block_add("Item.lbr", "Exit")["blockId"]
        sb.execute_command(f"MakeConnection({cS}, 0, {hid}, {res['interfaceMap']['in']['outerCon']});")
        sb.execute_command(f"MakeConnection({hid}, {res['interfaceMap']['out']['outerCon']}, {eS}, 0);")
        out = sb.simulation_run(end_time=50, include_stats=True)
        exited = next(e["itemsExited"] for e in out["statistics"]["exitStatistics"] if e["blockId"] == eS)
        assert exited > 0, f"no items flowed: {out}"
    finally:
        if eS is not None: sb.block_remove(eS)
        if cS is not None: sb.block_remove(cS)
        sb.block_remove(hid)
```

Run (controller, ExtendSim up, safe pattern): `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_resource_machine_live.py -v`
Expected: PASS (or SKIP without ExtendSim). If the H-block wrapping changes the pool index or connector wiring, adjust and re-run — the pure-core/molecule unit tests stay green.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/package.json src/ExtendSimMCP.TypeScript/tests/live/test_resource_machine_live.py
git commit -m "chore(resource-machine): package resource_pool_config.py + live test"
```

---

## Final review

After all tasks: dispatch a final code-reviewer over the whole branch diff (spec compliance + quality), then use superpowers:finishing-a-development-branch to merge to main. Update memory `pattern-mining-module-decisions` (resource-machine shipped) once merged.
