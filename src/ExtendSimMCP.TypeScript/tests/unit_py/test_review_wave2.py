# tests/unit_py/test_review_wave2.py
"""Offline regression tests for 2026-07-19 review wave 2 fixes:

- W2-4: queue_set_priority must validate block type ("Queue") before writing
  any dialog vars, exactly like its siblings (e.g. activity_set_delay).
- W2-5: db_relations_list must not fabricate per-relation entries it cannot
  actually read; it must return an honest failure (still reporting the
  relation COUNT, which is cheaply available) instead of a list of `{"index":
  N}` placeholders.
- W2-6: popup writes on queue_set_priority (QueueRank_Pop),
  batch_set_config (BatchType_pop), resource_pool_set_config (AllocRule),
  select_item_out_set_mode / select_item_in_set_mode (SelectType_pop) must
  route through _set_popup_verified (readback-verified) and surface a
  "warnings" entry when the readback doesn't match what was written.
- W2-7: the ad-hoc errorCode strings used by _validate_block_exists /
  _validate_model_open / _validate_block_type ("INVALID_BLOCK_ID",
  "BLOCK_QUERY_FAILED", "MODEL_QUERY_FAILED") must exist as ErrorCode
  constants with identical literal values (no wire-format change).
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
# W2-4: queue_set_priority validates block type before writing anything
# ---------------------------------------------------------------------------

class _FakeTypedApp:
    """Scripts BlockName() round-trips; records every Execute() call so tests
    can assert no dialog-var writes happened when validation fails."""

    def __init__(self, block_type_name):
        self.block_type_name = block_type_name
        self.executed = []
        self._last = ""

    def Execute(self, cmd):
        self.executed.append(cmd)
        self._last = cmd

    def Request(self, _system, _query):
        if "BlockName" in self._last:
            return self.block_type_name
        return ""


def test_queue_set_priority_rejects_wrong_block_type(monkeypatch):
    be = _load_backend()
    fake = _FakeTypedApp("Activity")
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    result = be.queue_set_priority(5, rank_type="fifo")

    assert result["success"] is False
    assert result["errorCode"] == be.ErrorCode.WRONG_BLOCK_TYPE
    assert result["actualType"] == "Activity"
    assert result["expectedType"] == "Queue"

    # No dialog writes should have been attempted once validation fails.
    assert not any("SetVariableNumeric" in c for c in fake.executed)
    assert not any("SetDialogVariable" in c for c in fake.executed)


# ---------------------------------------------------------------------------
# W2-5: db_relations_list returns an honest failure, not fake per-relation data
# ---------------------------------------------------------------------------

class _FakeDbRelationsApp:
    """Scripts DBDatabaseGetIndex/DBRelationsGetNum; records every Execute()
    call so the test can confirm the dead duplicate DBRelationsGetNames call
    is gone."""

    def __init__(self, db_idx=0, num_rels=3):
        self.db_idx = db_idx
        self.num_rels = num_rels
        self.executed = []
        self._last = ""

    def Execute(self, cmd):
        self.executed.append(cmd)
        self._last = cmd

    def Request(self, _system, _query):
        if "DBDatabaseGetIndex" in self._last:
            return str(self.db_idx)
        if "DBRelationsGetNum" in self._last:
            return str(self.num_rels)
        return ""


def test_db_relations_list_reports_honest_failure(monkeypatch):
    be = _load_backend()
    fake = _FakeDbRelationsApp(db_idx=0, num_rels=3)
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    result = be.db_relations_list("MyDb")

    assert result["success"] is False
    assert "not fully implemented" in result["error"]
    # The relation count is cheaply available and should still be reported.
    assert result["relationCount"] == 3
    # The old admitted-defeat fake-data path (duplicate DBRelationsGetNames
    # call, per-relation {"index": N} entries) must be gone.
    assert not any("DBRelationsGetNames" in c for c in fake.executed)
    assert "relations" not in result


# ---------------------------------------------------------------------------
# W2-7: ErrorCode gains the three ad-hoc validator error codes
# ---------------------------------------------------------------------------

def test_error_code_has_validator_constants():
    be = _load_backend()
    assert be.ErrorCode.INVALID_BLOCK_ID == "INVALID_BLOCK_ID"
    assert be.ErrorCode.BLOCK_QUERY_FAILED == "BLOCK_QUERY_FAILED"
    assert be.ErrorCode.MODEL_QUERY_FAILED == "MODEL_QUERY_FAILED"


# ---------------------------------------------------------------------------
# W2-6: popup writes are readback-verified and surface a warning on mismatch
# ---------------------------------------------------------------------------

class _FakeSelectInApp:
    """Scripts BlockName() (type validation) and GetVariableNumeric/
    SetVariableNumeric round-trips for SelectType_pop, returning a readback
    value that never matches what was written."""

    def __init__(self, block_type_name="Select Item In", readback_value="99"):
        self.block_type_name = block_type_name
        self.readback_value = readback_value
        self.executed = []
        self._last = ""

    def Execute(self, cmd):
        self.executed.append(cmd)
        self._last = cmd

    def Request(self, _system, _query):
        cmd = self._last
        if "BlockName" in cmd:
            return self.block_type_name
        if "GetVariableNumeric" in cmd:
            return self.readback_value
        return ""


def test_select_item_in_set_mode_warns_on_popup_readback_mismatch(monkeypatch):
    be = _load_backend()
    fake = _FakeSelectInApp(readback_value="99")  # mode "priority" writes code 2
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)

    result = be.select_item_in_set_mode(5, mode="priority")

    assert result["success"] is True  # write itself succeeded; only the verify failed
    assert "warnings" in result
    assert any("SelectType_pop" in w for w in result["warnings"])

    # Confirm it actually went through the verified path (a readback happened).
    assert any("GetVariableNumeric" in c for c in fake.executed)
