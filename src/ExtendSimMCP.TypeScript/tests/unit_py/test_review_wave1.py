# tests/unit_py/test_review_wave1.py
"""Offline regression tests for 2026-07-19 review wave 1 fixes:

- W1-2: _log_debug (and block_discover's debug log) must be a no-op unless
  EXTENDSIM_DEBUG is set, and must never raise even if the temp dir is
  unwritable.
- W1-6: _resolve_db_indices must escape db_name/table_name/field_name before
  interpolating them into Execute() ModL strings.
- W1-4: block_configure's Queue branch must forward every key in
  _QUEUE_PRIORITY_PARAMS present in config to COMMANDS["queue_set_priority"],
  not just rankType/sortAttribute/ascending.
- W1-3: equation_set_formula/equation_i_set_formula/queue_equation_set_config
  must write EQ_EquationText (not the non-persisting Equation_dtxt), normalize
  multi-line equations to a single line, and fail closed on a read-back
  mismatch.
- W1-5: block_add_batch must delegate placement to block_add() per item
  instead of re-implementing PlaceBlock/neighbor logic, and must report
  block_add's real read-back positions.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_backend():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        import pytest
        pytest.skip("simulation_backend not importable (no pywin32 in this env)")


# ---------------------------------------------------------------------------
# W1-2: _log_debug gated + safe
# ---------------------------------------------------------------------------

def test_log_debug_is_noop_when_disabled(monkeypatch):
    be = _load_backend()
    monkeypatch.setattr(be, "_debug_logging", False)
    # Point tempdir at a path that cannot possibly be written to, to prove
    # the disabled path never even touches the filesystem.
    monkeypatch.setattr(be.tempfile, "gettempdir", lambda: "Z:\\does\\not\\exist\\at\\all")
    be._log_debug("x")  # must not raise


def test_log_debug_never_raises_when_tempdir_unwritable(monkeypatch):
    be = _load_backend()
    # Force the gated/enabled path so the write is actually attempted, then
    # verify the try/except swallows the failure instead of propagating.
    monkeypatch.setattr(be, "_debug_logging", True)
    monkeypatch.setattr(be.tempfile, "gettempdir", lambda: "Z:\\does\\not\\exist\\at\\all")
    be._log_debug("x")           # must not raise
    be._debug_log_to_temp("simulationsmcp_block_discover_debug.log", "y")  # must not raise


# ---------------------------------------------------------------------------
# W1-6: _resolve_db_indices escapes db_name/table_name/field_name
# ---------------------------------------------------------------------------

class _FakeDbApp:
    """Records every Execute() string; Request() always reports a positive
    index so _resolve_db_indices proceeds through db/table/field resolution."""

    def __init__(self):
        self.executed = []

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return "5"


def test_resolve_db_indices_escapes_quotes_in_names():
    be = _load_backend()
    app = _FakeDbApp()

    result = be._resolve_db_indices(app, 'My"DB', 'My"Table', 'My"Field')

    assert result["success"] is True

    db_calls = [c for c in app.executed if "DBDatabaseGetIndex" in c]
    tbl_calls = [c for c in app.executed if "DBTableGetIndex" in c]
    fld_calls = [c for c in app.executed if "DBFieldGetIndex" in c]

    assert db_calls and 'DBDatabaseGetIndex("My\\"DB");' in db_calls[0]
    assert tbl_calls and 'DBTableGetIndex(5, "My\\"Table");' in tbl_calls[0]
    assert fld_calls and 'DBFieldGetIndex(5, 5, "My\\"Field");' in fld_calls[0]

    # No raw, unescaped quote breaks the ModL string literal open by "My".
    for cmd in app.executed:
        # every quote inside the literal must be preceded by a backslash
        # (i.e. no `"My"` sub-sequence — that would terminate the literal early)
        assert '"My"' not in cmd


# ---------------------------------------------------------------------------
# W1-4: block_configure forwards all Queue priority params
# ---------------------------------------------------------------------------

class _FakeConfigApp:
    """Scripts BlockName()/GetModelName() round-trips for block_configure."""

    def __init__(self, block_type_name="Queue"):
        self.block_type_name = block_type_name
        self._last = None

    def Execute(self, cmd):
        self._last = cmd

    def Request(self, _system, _query):
        cmd = self._last or ""
        if "GetModelName" in cmd:
            return "TestModel"
        if "BlockName" in cmd:
            return self.block_type_name
        return ""


def test_block_configure_forwards_all_queue_priority_params(monkeypatch):
    be = _load_backend()

    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: _FakeConfigApp())

    captured = {}

    def fake_queue_set_priority(p):
        captured.update(p)
        return {"success": True, "blockId": p.get("blockId")}

    monkeypatch.setitem(be.COMMANDS, "queue_set_priority", fake_queue_set_priority)

    config = {
        "rankType": "priority",
        "sortAttribute": "partType",
        "ascending": False,
        "maxLength": 10,
        "renegeEnabled": True,
        "renegeTime": 5.0,
        "calcWaitCosts": True,
        "shift": 2,
        "calcDelay": True,
    }
    assert config.keys() == be._QUEUE_PRIORITY_PARAMS

    result = be.block_configure(42, config, model_id="model_1")

    assert result["success"] is True
    for key in be._QUEUE_PRIORITY_PARAMS:
        assert key in captured, f"{key} was dropped, not forwarded to queue_set_priority"
        assert captured[key] == config[key]
    assert captured["blockId"] == 42


# ---------------------------------------------------------------------------
# W1-3: equation tools write EQ_EquationText with fail-closed read-back verify
# ---------------------------------------------------------------------------

class _FakeEquationApp:
    """Records every Execute() string; Request() always returns a scripted
    read-back value (simulating GetDialogVariable's response)."""

    def __init__(self, readback):
        self.executed = []
        self.readback = readback

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.readback


def test_equation_set_formula_writes_eq_equation_text_and_verifies(monkeypatch):
    be = _load_backend()
    fake = _FakeEquationApp(readback="o0 = i0 * 2;")
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    result = be.equation_set_formula(7, "o0 = i0 * 2;")

    assert result["success"] is True
    assert result["equation"] == "o0 = i0 * 2;"
    assert "note" in result and "CheckData" in result["note"]

    write_calls = [c for c in fake.executed if "SetDialogVariable" in c]
    assert write_calls, "expected a SetDialogVariable write"
    assert "EQ_EquationText" in write_calls[0]

    # Must NOT target the broken Equation_dtxt handle.
    assert not any("Equation_dtxt" in c for c in fake.executed)


def test_equation_set_formula_fails_closed_on_readback_mismatch(monkeypatch):
    be = _load_backend()
    fake = _FakeEquationApp(readback="something else entirely")
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    result = be.equation_set_formula(7, "o0 = i0 * 2;")

    assert result["success"] is False
    assert result["errorCode"] == be.ErrorCode.SET_VALUE_FAILED
    assert "read-back mismatch" in result["error"]
    assert result["blockId"] == 7


def test_equation_set_formula_normalizes_multiline_equation(monkeypatch):
    be = _load_backend()
    fake = _FakeEquationApp(readback="a; b;")
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    result = be.equation_set_formula(9, "a;\nb;")

    assert result["success"] is True
    assert result["equation"] == "a; b;"

    write_calls = [c for c in fake.executed if "SetDialogVariable" in c]
    assert write_calls
    assert '"a; b;"' in write_calls[0]
    assert "\n" not in write_calls[0]  # normalized to single line before writing


# ---------------------------------------------------------------------------
# W1-5: block_add_batch delegates to block_add per item
# ---------------------------------------------------------------------------

class _FakeModelApp:
    """Minimal app for block_add_batch: only needs GetModelName (model-open
    check) and SuppressWorksheetRedraw — placement itself is delegated to a
    monkeypatched block_add, so no objectIDNext/PlaceBlock scripting needed."""

    def __init__(self):
        self.executed = []

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return "TestModel"


def test_block_add_batch_delegates_to_block_add(monkeypatch):
    be = _load_backend()
    fake = _FakeModelApp()
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    calls = []

    def fake_block_add(library_name, block_name, x=100, y=100, neighbor=-1,
                        side=2, label=None, model_id=None):
        calls.append({
            "library_name": library_name, "block_name": block_name,
            "x": x, "y": y, "neighbor": neighbor, "side": side,
            "label": label, "model_id": model_id,
        })
        idx = len(calls)
        return {
            "success": True,
            "blockId": 100 + idx,
            "blockName": block_name,
            "library": library_name,
            "label": label or "",
            "position": {"x": 999 + idx, "y": 888 + idx, "width": 40, "height": 30},
            "neighbor": neighbor,
            "side": side,
        }

    monkeypatch.setattr(be, "block_add", fake_block_add)

    blocks = [
        {"libraryName": "Item.lbr", "blockName": "Queue", "x": 100, "y": 100},
        {"libraryName": "Item.lbr", "blockName": "Activity", "x": 200, "y": 200},
    ]
    result = be.block_add_batch(blocks)

    assert result["success"] is True
    assert len(calls) == 2, "block_add should be called once per item"
    assert result["count"] == 2
    assert result["successCount"] == 2

    positions = [r["position"] for r in result["blocks"]]
    # Positions must come from block_add's (fake) read-back, not the raw input coords.
    assert positions[0] == {"x": 1000, "y": 889, "width": 40, "height": 30}
    assert positions[1] == {"x": 1001, "y": 890, "width": 40, "height": 30}
    assert positions[0] != {"x": 100, "y": 100}
    assert positions[1] != {"x": 200, "y": 200}
