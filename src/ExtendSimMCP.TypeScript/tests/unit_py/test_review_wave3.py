# tests/unit_py/test_review_wave3.py
"""Offline regression tests for 2026-07-19 review wave 3 fixes:

- W3-2: merge_set_config/diverge_set_config now share a private
  `_merge_or_diverge_set_config` body. Behavior must be identical between the
  two public wrappers (same Execute sequence, same result shape) except for
  the `operation` string surfaced on error.
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


class _FakeApp:
    """Records every Execute() call; no Request() needed since
    merge/diverge writes are fire-and-forget SetVariableNumeric calls."""

    def __init__(self):
        self.executed = []

    def Execute(self, cmd):
        self.executed.append(cmd)


def test_merge_and_diverge_set_config_produce_identical_execute_sequences(monkeypatch):
    be = _load_backend()

    fake_merge = _FakeApp()
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake_merge)
    merge_result = be.merge_set_config(
        5, mode=1, initial_value_selected=2,
        initialize_selected=True, param_from_connectors=False)

    fake_diverge = _FakeApp()
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake_diverge)
    diverge_result = be.diverge_set_config(
        5, mode=1, initial_value_selected=2,
        initialize_selected=True, param_from_connectors=False)

    # Same block_id -> same dialog vars -> identical Execute sequence.
    assert fake_merge.executed == fake_diverge.executed
    assert len(fake_merge.executed) == 4  # Mode_pop, InitialValueSelected_prm, InitializeSelected_chk, ParamFromConnectors_chk

    # Result shapes are identical except nothing (operation isn't echoed on
    # success) -- both should be equal on the happy path.
    assert merge_result == diverge_result
    assert merge_result["success"] is True


def test_merge_and_diverge_set_config_error_reports_distinct_operation(monkeypatch):
    be = _load_backend()

    class _BoomApp:
        def Execute(self, cmd):
            raise RuntimeError("COM boom")

    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: _BoomApp())
    merge_err = be.merge_set_config(5, mode=1)

    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: _BoomApp())
    diverge_err = be.diverge_set_config(5, mode=1)

    assert merge_err["success"] is False
    assert diverge_err["success"] is False
    assert merge_err["operation"] == "merge_set_config"
    assert diverge_err["operation"] == "diverge_set_config"
    # Only the operation field differs between the two error payloads.
    merge_err_no_op = {k: v for k, v in merge_err.items() if k != "operation"}
    diverge_err_no_op = {k: v for k, v in diverge_err.items() if k != "operation"}
    assert merge_err_no_op == diverge_err_no_op
