# tag-items Attribute-Set Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `attribute_set` so it actually configures ExtendSim 2024's Set block (via `AttribsTable_ttbl`, effect-verified, fail-closed), and wire attribute-writing into the molecule/instantiate pipeline so `tag-items` really tags items with `partType`.

**Architecture:** A new pure core `attribute_config.py` (injected backend, testable without COM) owns the write-and-verify logic, mirroring `dialog_table.py`. `simulation_backend.attribute_set` is rewritten to delegate there. Molecules gain a `setAttributes` node config that `instantiate` applies in a new Phase 4b via `ops.set_attribute`. Column indices live as named constants (provisional, pinned by a live-discovery task — like M6's `VAR_COL`).

**Tech Stack:** Python 3 (pytest) for the backend/cores, TypeScript (vitest) only if dispatch changes, ExtendSim COM for the live tasks.

**Working branch:** `feature/tag-items-attribute-set`

**Reference (read once before starting):** the design spec `docs/superpowers/specs/2026-07-01-tag-items-attribute-set-design.md`, and `src/dialog_table.py` + `tests/unit_py/test_dialog_table.py` for the exact pure-core + FakeBackend style to mirror.

---

## Task 0: Live discovery — `AttribsTable_ttbl` layout (controller-run, not a subagent)

> **Note for the executing controller:** This task touches live ExtendSim COM and carries the freeze risk documented in memory `extendsim-com-freeze-live-work`. DO NOT delegate it to a subagent (a subagent timing out mid-COM-call orphans the STA server). Run it yourself with the safe pattern: one-shot `dialog_watcher.py` concurrent with `block_add`, in-range reads only, never kill mid-call. If ExtendSim is not running or discovery cannot be completed safely, proceed to Tasks 1-5 with the PROVISIONAL constants below and pin them in Task 6.

**Goal:** determine the real `AttribsTable_ttbl` column layout on a live Set block, and whether attributes must pre-exist.

- [ ] **Step 1: Add a Set block and write a probe row**

Create a scratch script under `temp/` that (safely): opens/uses the active model, `block_add("Item.lbr","Set")`, then uses `table_set_entry(set_id, "AttribsTable_ttbl", "partType", 0, col)` for `col` in `0..3` (in-range only), reading each back with `table_get_entry`. Also try writing a numeric value to each column via `block_set_value(set_id, "AttribsTable_ttbl", 2, 0, col)`.

- [ ] **Step 2: Record findings**

Determine and write down: which column accepts the attribute NAME string (`ATTR_NAME_COL`), which holds the VALUE (`ATTR_VALUE_COL`), whether a value-source popup column exists (`ATTR_TYPE_COL`, else `None`) and its "constant" code, and whether the attribute must be pre-defined in the model or the Set block binds it from the name alone.

- [ ] **Step 3: Pin constants**

These become the real values in `attribute_config.py` (Task 1 / Task 6). If discovery is deferred, the PROVISIONAL values are: `ATTR_NAME_COL=0`, `ATTR_VALUE_COL=1`, `ATTR_TYPE_COL=None`, `_CONSTANT_CODE=1`.

No commit for this task (scratch scripts live in `temp/`, already git-ignored).

---

## Task 1: Pure core `attribute_config.set_attribute`

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/attribute_config.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit_py/test_attribute_config.py`. Mirror the FakeBackend style from `test_dialog_table.py`, but this backend also exposes `_set_var` (numeric) and `_validate_block_type`.

```python
# tests/unit_py/test_attribute_config.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import attribute_config
from attribute_config import set_attribute, ATTR_NAME_COL, ATTR_VALUE_COL


class FakeBackend:
    """Test double for the simulation_backend surface attribute_config needs."""
    def __init__(self, name_rb=None, value_rb="2", model_open=True,
                 block_ok=True, raise_on=None):
        self.calls = []
        self._name_rb = name_rb          # what the name cell reads back (defaults to written)
        self._value_rb = value_rb
        self._model_open = model_open
        self._block_ok = block_ok
        self._raise_on = raise_on         # None | "set" | "get"
        self._written_name = None
        self.app = object()

    def get_extendsim_app(self):
        return self.app

    def _validate_model_open(self, app):
        return {"success": True} if self._model_open else \
            {"success": False, "errorCode": "MODEL_NOT_OPEN", "error": "no model"}

    def _validate_block_type(self, app, block_id, expected):
        return {"success": True} if self._block_ok else \
            {"success": False, "errorCode": "WRONG_BLOCK_TYPE", "error": expected}

    def _set_var_string(self, app, block_id, var, value, row, col):
        self.calls.append(("set_str", block_id, var, value, row, col))
        if self._raise_on == "set":
            raise RuntimeError("com boom")
        self._written_name = value

    def _set_var(self, app, block_id, var, value, row, col):
        self.calls.append(("set_num", block_id, var, value, row, col))
        if self._raise_on == "set":
            raise RuntimeError("com boom")

    def _get_var(self, app, block_id, var, row, col):
        self.calls.append(("get", block_id, var, row, col))
        if self._raise_on == "get":
            raise RuntimeError("com boom")
        if col == ATTR_NAME_COL:
            return self._written_name if self._name_rb is None else self._name_rb
        return self._value_rb


def test_set_attribute_writes_name_and_value_and_verifies():
    be = FakeBackend(value_rb="2")
    res = set_attribute(be, 5, "partType", 2, "constant", 0)
    assert res["success"] is True
    assert res["attributeName"] == "partType"
    assert res["blockId"] == 5
    # name written as string to the name column, value written to the value column
    assert ("set_str", 5, "AttribsTable_ttbl", "partType", 0, ATTR_NAME_COL) in be.calls
    assert ("set_num", 5, "AttribsTable_ttbl", 2, 0, ATTR_VALUE_COL) in be.calls
    # a verification read happened after the writes
    assert any(c[0] == "get" for c in be.calls)


def test_set_attribute_rejected_when_name_readback_differs():
    be = FakeBackend(name_rb="")   # block ignored the name write
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_WRITE_REJECTED"
    assert res["requested"] == "partType"
    assert res["actual"] == ""


def test_set_attribute_write_failure_is_fail_closed():
    be = FakeBackend(raise_on="set")
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_WRITE_FAILED"


def test_set_attribute_readback_failure_is_distinct():
    be = FakeBackend(raise_on="get")
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_READ_FAILED"
    # the write was attempted before the failing readback
    assert be.calls[0][0] == "set_str"


def test_set_attribute_unsupported_value_type_no_com_write():
    be = FakeBackend()
    res = set_attribute(be, 3, "partType", 1, "connector", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_VALUETYPE_UNSUPPORTED"
    assert be.calls == []          # nothing written


def test_set_attribute_propagates_wrong_block_type():
    be = FakeBackend(block_ok=False)
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "WRONG_BLOCK_TYPE"


def test_set_attribute_propagates_model_not_open():
    be = FakeBackend(model_open=False)
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "MODEL_NOT_OPEN"


def test_entry_is_callable_with_expected_arity():
    import inspect
    assert callable(attribute_config.set_attribute_entry)
    params = list(inspect.signature(attribute_config.set_attribute_entry).parameters)
    assert params == ["block_id", "name", "value", "value_type", "row"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_attribute_config.py -v`
Expected: FAIL/collect error — `attribute_config` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `src/attribute_config.py`:

```python
# src/attribute_config.py
"""Set-block attribute configuration via AttribsTable_ttbl (ExtendSim 2024).

Pure core takes an injected `backend` (simulation_backend in production, a
FakeBackend in tests) and reuses its MODL helpers. Write is read-back verified
and fail-closed. The old single-param path (AttributeName_prm / ValueType_pop /
ConstantValue_prm) does NOT exist on the 2024 Set block, which configures
attributes through the AttribsTable_ttbl dialog table.
"""

# Column layout of AttribsTable_ttbl on the Set block.
# Pinned by live discovery (see plan Task 0). Named so tests bind to the symbol,
# not the number, and a discovery adjustment does not churn the tests.
ATTR_NAME_COL = 0       # string column: attribute name
ATTR_VALUE_COL = 1      # value column: constant value
ATTR_TYPE_COL = None    # value-source popup column, or None if absent
_CONSTANT_CODE = 1      # popup code for "constant" (used only if ATTR_TYPE_COL set)


def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def _num_eq(a, b):
    """True if a and b are equal as numbers (tolerant), else as strings."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)


def set_attribute(backend, block_id, name, value, value_type="constant", row=0):
    """Configure a Set block to assign `value` to attribute `name` (constant).

    Effect-verified: the name (and value) are read back and compared; a write
    that does not persist returns ATTRIBUTE_WRITE_REJECTED, never false success.
    """
    app = backend.get_extendsim_app()
    model_check = backend._validate_model_open(app)
    if not model_check.get("success"):
        return model_check
    type_check = backend._validate_block_type(app, block_id, "Set")
    if not type_check.get("success"):
        return type_check
    if value_type != "constant":
        return _err("ATTRIBUTE_VALUETYPE_UNSUPPORTED",
                    f"value_type '{value_type}' not supported yet (only 'constant')",
                    blockId=block_id, attributeName=name, valueType=value_type)
    try:
        backend._set_var_string(app, block_id, "AttribsTable_ttbl", str(name), row, ATTR_NAME_COL)
        if ATTR_TYPE_COL is not None:
            backend._set_var(app, block_id, "AttribsTable_ttbl", _CONSTANT_CODE, row, ATTR_TYPE_COL)
        backend._set_var(app, block_id, "AttribsTable_ttbl", value, row, ATTR_VALUE_COL)
    except Exception as e:
        return _err("ATTRIBUTE_WRITE_FAILED", str(e),
                    blockId=block_id, attributeName=name, row=row)
    try:
        name_rb = backend._get_var(app, block_id, "AttribsTable_ttbl", row, ATTR_NAME_COL)
        value_rb = backend._get_var(app, block_id, "AttribsTable_ttbl", row, ATTR_VALUE_COL)
    except Exception as e:
        return _err("ATTRIBUTE_READ_FAILED", str(e),
                    blockId=block_id, attributeName=name, row=row)
    if str(name_rb) != str(name):
        return _err("ATTRIBUTE_WRITE_REJECTED",
                    f"attribute name write to block {block_id} row {row} did not persist",
                    blockId=block_id, attributeName=name, row=row,
                    requested=str(name), actual=str(name_rb))
    if not _num_eq(value_rb, value):
        return _err("ATTRIBUTE_WRITE_REJECTED",
                    f"attribute value write to block {block_id} row {row} did not persist",
                    blockId=block_id, attributeName=name, row=row,
                    requested=str(value), actual=str(value_rb))
    return {"success": True, "blockId": block_id, "attributeName": name,
            "value": value, "valueType": value_type, "row": row,
            "nameActual": str(name_rb), "valueActual": str(value_rb)}


def set_attribute_entry(block_id, name, value, value_type="constant", row=0):
    import simulation_backend as backend
    return set_attribute(backend, block_id, name, value, value_type, row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_attribute_config.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/attribute_config.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_config.py
git commit -m "feat(tag-items): pure attribute_config.set_attribute core (effect-verified, fail-closed)"
```

---

## Task 2: Rewrite `simulation_backend.attribute_set` to delegate

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py:3871-3937` (the `attribute_set` body)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_set_delegation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit_py/test_attribute_set_delegation.py`. It imports the module, monkeypatches `attribute_config.set_attribute`, and asserts `attribute_set` delegates and returns the core's dict. It also asserts the dead dialog-var names are gone from the function source.

```python
# tests/unit_py/test_attribute_set_delegation.py
import os, sys, inspect
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_backend(monkeypatch=None):
    # simulation_backend imports win32com at module load; skip if unavailable.
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        import pytest
        pytest.skip("simulation_backend not importable (no pywin32 in this env)")


def test_attribute_set_delegates_to_core(monkeypatch):
    be = _load_backend()
    import attribute_config
    captured = {}

    def fake_core(backend, block_id, name, value, value_type="constant", row=0):
        captured.update(dict(block_id=block_id, name=name, value=value,
                             value_type=value_type, row=row))
        return {"success": True, "blockId": block_id, "attributeName": name,
                "value": value, "valueType": value_type}

    monkeypatch.setattr(attribute_config, "set_attribute", fake_core)
    res = be.attribute_set(7, "partType", value_type="constant", value=2)
    assert res["success"] is True
    assert res["attributeName"] == "partType"
    assert captured == dict(block_id=7, name="partType", value=2,
                            value_type="constant", row=0)


def test_attribute_set_no_longer_references_dead_dialog_vars():
    be = _load_backend()
    src = inspect.getsource(be.attribute_set)
    for dead in ("AttributeName_prm", "ValueType_pop", "ConstantValue_prm"):
        assert dead not in src, f"{dead} still referenced in attribute_set"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_attribute_set_delegation.py -v`
Expected: `test_attribute_set_no_longer_references_dead_dialog_vars` FAILS (dead vars still present); delegation test may fail too.
(If it SKIPs because pywin32 is absent, note that and rely on the live test in Task 6 plus the pure-core tests in Task 1.)

- [ ] **Step 3: Rewrite the implementation**

Replace the body of `attribute_set` (lines ~3871-3937) with a delegating implementation. Keep the existing signature and docstring intro. New body:

```python
def attribute_set(block_id: int,
                  attribute_name: str,
                  value_type: str = "constant",
                  value: Optional[float] = None,
                  distribution: Optional[str] = None,
                  arg1: Optional[float] = None,
                  arg2: Optional[float] = None,
                  arg3: Optional[float] = None,
                  model_id: Optional[str] = None) -> dict:
    """Configures a Set block to assign an attribute value to items.

    ExtendSim 2024's Set block stores attribute assignments in the
    AttribsTable_ttbl dialog table (not the removed AttributeName_prm/
    ValueType_pop/ConstantValue_prm variables). Delegates to the effect-verified,
    fail-closed attribute_config core. Currently only value_type="constant" is
    supported; other types return ATTRIBUTE_VALUETYPE_UNSUPPORTED.
    """
    import attribute_config
    import sys as _sys
    return attribute_config.set_attribute(
        _sys.modules[__name__], block_id, attribute_name, value, value_type)
```

Note: `_sys.modules[__name__]` passes the live backend module itself as the injected `backend`, so the core uses the real `get_extendsim_app`, `_validate_model_open`, `_validate_block_type`, `_set_var`, `_set_var_string`, `_get_var`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_attribute_set_delegation.py tests/unit_py/test_attribute_config.py -v`
Expected: PASS (or SKIP for the pywin32-dependent delegation test in a bare env; the source-scan test must PASS regardless — `inspect.getsource` works without COM).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_attribute_set_delegation.py
git commit -m "fix(tag-items): attribute_set delegates to AttribsTable_ttbl core (kills false-success path)"
```

---

## Task 3: Molecule schema — validate + resolve `setAttributes`

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/molecule_schema.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_molecule_schema.py` (add cases)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit_py/test_molecule_schema.py`:

```python
def test_resolve_set_attributes_substitutes_placeholder_and_default():
    from molecule_schema import resolve_set_attributes
    node = {"ref": "set", "setAttributes": [{"name": "partType", "value": "{{partType}}"}]}
    # explicit param
    out = resolve_set_attributes(node, {"partType": 3})
    assert out == [{"name": "partType", "value": 3, "valueType": "constant"}]


def test_resolve_set_attributes_empty_when_absent():
    from molecule_schema import resolve_set_attributes
    assert resolve_set_attributes({"ref": "set"}, {}) == []


def test_validate_rejects_set_attribute_without_name():
    from molecule_schema import validate_molecule, MoleculeError
    mol = {
        "nodes": [{"ref": "set", "lib": "Item.lbr", "type": "Set", "seed": True,
                   "setAttributes": [{"value": 1}]}],
        "edges": [], "interface": {}, "params": {},
    }
    import pytest
    with pytest.raises(MoleculeError):
        validate_molecule(mol, {})


def test_validate_accepts_valid_set_attribute():
    from molecule_schema import validate_molecule
    mol = {
        "nodes": [{"ref": "set", "lib": "Item.lbr", "type": "Set", "seed": True,
                   "setAttributes": [{"name": "partType", "value": "{{partType}}"}]}],
        "edges": [], "interface": {}, "params": {},
    }
    validate_molecule(mol, {})   # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_molecule_schema.py -v -k "set_attribute"`
Expected: FAIL — `resolve_set_attributes` undefined; validation does not check `setAttributes`.

- [ ] **Step 3: Implement**

In `molecule_schema.py`, add a `setAttributes` validation loop inside `validate_molecule` (after the seed/param checks, iterating nodes), and a new `resolve_set_attributes` function. Add near the existing `resolve_params`:

```python
def _resolve_value(v, params):
    """Resolve a single {{name}} placeholder against params; pass through otherwise."""
    if isinstance(v, str):
        m = _PLACEHOLDER.match(v)
        if m:
            return params[m.group(1)]
    return v


def resolve_set_attributes(node, params):
    """Return the node's setAttributes with placeholders/defaults resolved.

    Each entry -> {"name": str, "value": <resolved>, "valueType": str}.
    """
    out = []
    for entry in (node.get("setAttributes") or []):
        out.append({
            "name": entry["name"],
            "value": _resolve_value(entry.get("value"), params),
            "valueType": entry.get("valueType", "constant"),
        })
    return out
```

And inside `validate_molecule`, after the required-params check, add:

```python
    # setAttributes entries must name an attribute
    for n in nodes:
        for entry in (n.get("setAttributes") or []):
            if not entry.get("name"):
                raise MoleculeError(f"setAttributes entry on node {n.get('ref')} missing 'name'")
```

Default-param application: extend the required-params loop in `validate_molecule` is NOT the place; defaults are applied at resolve time. Update `resolve_set_attributes` callers to pass a params dict that already includes molecule defaults — this is handled in Task 4 (instantiate merges defaults before resolving). To keep `resolve_set_attributes` self-contained for the unit test above (which passes `{"partType": 3}` explicitly), no default logic is needed here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_molecule_schema.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/molecule_schema.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_molecule_schema.py
git commit -m "feat(tag-items): molecule schema validates + resolves setAttributes node config"
```

---

## Task 4: instantiate Phase 4b + `ops.set_attribute` + default-param merge

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/instantiate.py` (`build_molecule` Phase 4, add Phase 4b; `RealOps.set_attribute`)
- Modify: `src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py` (add `set_attribute`)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py` (add case)

- [ ] **Step 1: Add `set_attribute` to FakeOps**

In `tests/unit_py/fake_ops.py`, add a recording method to `FakeOps` (after `set_value`):

```python
    def set_attribute(self, block_id, name, value, value_type):
        self.calls.append(("set_attribute", block_id, name, value, value_type))
```

- [ ] **Step 2: Write the failing test**

Append to `tests/unit_py/test_instantiate.py` a test that builds a molecule whose seed node carries `setAttributes` and asserts a `set_attribute` call is recorded with the default-resolved value:

```python
def test_build_applies_set_attributes_with_default_param():
    from instantiate import build_molecule
    from fake_ops import FakeOps
    mol = {
        "id": "t", "kind": "molecule",
        "params": {"partType": {"required": False, "default": 5}},
        "attributes": {"reads": [], "writes": ["partType"]},
        "nodes": [{"ref": "set", "lib": "Item.lbr", "type": "Set", "seed": True,
                   "setAttributes": [{"name": "partType", "value": "{{partType}}"}]}],
        "edges": [],
        "interface": {"inlets": [{"port": "in", "binds": "set.ItemIn", "role": "item"}],
                      "outlets": [{"port": "out", "binds": "set.ItemOut", "role": "item"}]},
    }
    ops = FakeOps()
    res = build_molecule(mol, {}, ops)          # no explicit param -> default 5
    set_id = res["internalBlockIds"]["set"]
    assert ("set_attribute", set_id, "partType", 5, "constant") in ops.calls
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py -v -k set_attributes`
Expected: FAIL — no Phase 4b, and defaults not applied.

- [ ] **Step 4: Implement Phase 4b + default merge**

In `instantiate.py`, add a helper to merge molecule param defaults, and a Phase 4b after Phase 4. At the top, extend the import:

```python
from molecule_schema import validate_molecule, resolve_params, resolve_set_attributes, MoleculeError
```

Add a module-level helper:

```python
def _merge_param_defaults(molecule, params):
    """Return params with molecule-declared defaults filled in where absent."""
    merged = dict(params or {})
    for name, spec in (molecule.get("params") or {}).items():
        if name not in merged and isinstance(spec, dict) and "default" in spec:
            merged[name] = spec["default"]
    return merged
```

In `build_molecule`, right after `validate_molecule(...)`, compute merged params and use them for BOTH param resolution and set-attributes:

```python
    validate_molecule(molecule, params)            # fail-closed, before any COM
    params = _merge_param_defaults(molecule, params)
    ops.activate()
```

Then after Phase 4 (the `ops.set_value` loop), add Phase 4b:

```python
    # Phase 4b: apply attribute-set configs (Set blocks tag items).
    for node in molecule["nodes"]:
        for a in resolve_set_attributes(node, params):
            ops.set_attribute(internal[node["ref"]], a["name"], a["value"], a["valueType"])
```

- [ ] **Step 5: Implement `RealOps.set_attribute`**

Add to `RealOps` (after `set_value`):

```python
    def set_attribute(self, block_id, name, value, value_type):
        r = self._b.attribute_set(block_id, name, value_type=value_type, value=value)
        if not r.get("success"):
            raise BuildError(f"set_attribute failed: block {block_id} {name}={value}: {r}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_instantiate.py tests/unit_py/test_tag_items.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/instantiate.py src/ExtendSimMCP.TypeScript/tests/unit_py/fake_ops.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_instantiate.py
git commit -m "feat(tag-items): instantiate Phase 4b applies setAttributes via ops.set_attribute"
```

---

## Task 5: Wire `tag-items.json` + molecule test

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/patterns/molecules/tag-items.json`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_tag_items.py` (add case)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit_py/test_tag_items.py`:

```python
def test_tag_items_configures_partType_write():
    from fake_ops import FakeOps, load
    from instantiate import build_molecule
    ops = FakeOps()
    res = build_molecule(load("tag-items.json"), {"partType": 4}, ops)
    set_id = res["internalBlockIds"]["set"]
    assert ("set_attribute", set_id, "partType", 4, "constant") in ops.calls


def test_tag_items_default_partType_is_one():
    from fake_ops import FakeOps, load
    from instantiate import build_molecule
    ops = FakeOps()
    res = build_molecule(load("tag-items.json"), {}, ops)
    set_id = res["internalBlockIds"]["set"]
    assert ("set_attribute", set_id, "partType", 1, "constant") in ops.calls
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_tag_items.py -v`
Expected: FAIL — tag-items.json has no `setAttributes` / `partType` param yet.

- [ ] **Step 3: Update `tag-items.json`**

Replace the file contents with:

```json
{
  "id": "tag-items",
  "version": "1.0",
  "kind": "molecule",
  "intent": "Märk items med attributet partType",
  "params": { "partType": { "required": false, "default": 1 } },
  "attributes": { "reads": [], "writes": ["partType"] },
  "nodes": [
    { "ref": "set", "lib": "Item.lbr", "type": "Set", "seed": true,
      "setAttributes": [ { "name": "partType", "value": "{{partType}}" } ] }
  ],
  "edges": [],
  "interface": {
    "inlets":  [ { "port": "in",  "binds": "set.ItemIn",  "role": "item" } ],
    "outlets": [ { "port": "out", "binds": "set.ItemOut", "role": "item" } ]
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_tag_items.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/patterns/molecules/tag-items.json src/ExtendSimMCP.TypeScript/tests/unit_py/test_tag_items.py
git commit -m "feat(tag-items): molecule writes partType via setAttributes (param, default 1)"
```

---

## Task 6: Packaging + full unit run + (controller) live verification

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/package.json` (`copy-files`)
- Test: `src/ExtendSimMCP.TypeScript/tests/live/test_tag_items_live.py` (create; skips without ExtendSim)

- [ ] **Step 1: Add `attribute_config.py` to the dist copy step**

In `package.json` `copy-files`, add after the `dialog_table.py` copy:
`fs.copyFileSync('src/attribute_config.py', 'dist/attribute_config.py');`

- [ ] **Step 2: Run the full Python + TS unit suites**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py -v && npm test`
Expected: all green. (Tool count unchanged at 99 — `attribute_set` and `instantiate_pattern` are already-registered tools; verify `dispatch-coverage.test.ts` still passes.)

- [ ] **Step 3: Verify the build packages the new module**

Run: `cd src/ExtendSimMCP.TypeScript && npm run build && ls dist/attribute_config.py`
Expected: file present in `dist/`.

- [ ] **Step 4: (Controller-run) live verification — do NOT delegate to a subagent**

With ExtendSim running and the safe COM pattern: create `tests/live/test_tag_items_live.py` that instantiates the molecule and reads the table back. Guard with a skip if ExtendSim/COM is unavailable.

```python
# tests/live/test_tag_items_live.py
import os, sys
import pytest
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import simulation_backend as be
    from instantiate import instantiate_pattern
    from attribute_config import ATTR_NAME_COL, ATTR_VALUE_COL
    _HAVE_COM = be.get_extendsim_app() is not None
except Exception:
    _HAVE_COM = False

pytestmark = pytest.mark.skipif(not _HAVE_COM, reason="ExtendSim COM not available")


def test_tag_items_writes_partType_live():
    res = instantiate_pattern("tag-items", {"partType": 2})
    assert res["success"], res
    set_id = res["internalBlockIds"]["set"]
    name = be.block_get_value(set_id, "AttribsTable_ttbl", 0, ATTR_NAME_COL, as_string=True)
    assert name["success"] and str(name["value"]).strip() == "partType", name
```

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_tag_items_live.py -v`
Expected: PASS live (or SKIP without ExtendSim). If live reveals the provisional column constants are wrong, correct them in `attribute_config.py` and re-run Tasks 1/6 unit tests (they bind to the symbols, so they stay green).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/package.json src/ExtendSimMCP.TypeScript/tests/live/test_tag_items_live.py
git commit -m "chore(tag-items): package attribute_config.py + live tag-items verification test"
```

---

## Final review

After all tasks: dispatch a final code-reviewer over the whole branch diff (spec compliance + quality), then use superpowers:finishing-a-development-branch to merge to main. Update memory `pattern-mining-module-decisions` (attribute_set fixed, tag-items live) once merged.
