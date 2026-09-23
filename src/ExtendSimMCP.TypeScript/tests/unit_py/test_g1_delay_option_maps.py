"""G1 closed: the delay popup maps match the blocks' own source constants.

Verified 2026-09-23 by reading the DELAY_IS_* constants out of each block's ModL
source in Item.lbr (ExtendSim 2024 R1):

    Activity:    CONSTANT=1, CONNECTOR=2, ATTRIBUTE=3, DISTRIBUTION=4, TABLE=5
    Workstation: CONSTANT=1, ATTRIBUTE=2, DISTRIBUTION=3, TABLE=4, CONNECTOR=1000

Workstation's CONNECTOR=1000 is a sentinel, not a popup row: connecting the D
connector overrides the delay automatically. An earlier investigation read the
blocks' help text - which lists the WAYS a delay can be set, D connector included -
as if it listed the popup rows, and wrongly concluded the Workstation map was off
by one. These tests pin the maps to the source so that mistake cannot recur.
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


def test_activity_map_matches_its_source_constants():
    be = _load_backend()
    assert be.DELAY_OPTIONS["fixed"] == 1
    assert be.DELAY_OPTIONS["connector"] == 2
    assert be.DELAY_OPTIONS["attribute"] == 3
    assert be.DELAY_OPTIONS["distribution"] == 4
    assert be.DELAY_OPTIONS["table"] == 5


def test_workstation_map_matches_its_source_constants():
    be = _load_backend()
    assert be.WORKSTATION_DELAY_OPTIONS == {
        "fixed": 1, "attribute": 2, "distribution": 3, "table": 4}
    # No popup row for the connector: it is a sentinel (1000) in the block.
    assert "connector" not in be.WORKSTATION_DELAY_OPTIONS


class _FakeApp:
    def __init__(self):
        self.executed = []

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return "1"


def test_workstation_config_no_longer_warns_the_index_may_be_wrong(monkeypatch):
    """1.22.2 and 1.22.3 shipped a warning saying these indices might be off by one. They are
    not, so the warning was itself misinformation and must be gone."""
    be = _load_backend()
    fake = _FakeApp()
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open", lambda app: {"success": True})
    monkeypatch.setattr(be, "_set_popup_verified",
                        lambda app, bid, var, val: {"success": True})
    for delay_type in ("attribute", "distribution", "table"):
        r = be.workstation_set_config(5, delay_type=delay_type)
        for w in r.get("warnings", []):
            assert "UNVERIFIED" not in w and "off by one" not in w, (delay_type, w)
