"""Offline coverage for the result collectors (gap sweep G2, wave 7).

block_get_stats, resource_pool_get_stats, optimizer_get_results,
scenario_manager_status and scenario_manager_get_results had no test.

One wrong error code found: both Scenario Manager tools reported their failures as
OPTIMIZER_FAILED - three places, evidently copied from the optimizer. A client that
gets OPTIMIZER_FAILED from the Scenario Manager goes looking in the wrong block. They
now use MULTI_RUN_FAILED, which the user manual already defines as "a multi-run or
scenario sweep failed".
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
    def __init__(self, answers=None, raise_on=None):
        self.executed = []
        self.answers = list(answers or [])
        self.raise_on = raise_on

    def Execute(self, cmd):
        if self.raise_on is not None and self.raise_on in cmd:
            raise RuntimeError("RPC server is unavailable")
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.answers.pop(0) if self.answers else "0"


def _use(monkeypatch, be, fake):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    return fake


# ---------------------------------------------------------------------------
# block_get_stats
# ---------------------------------------------------------------------------

def test_block_get_stats_reads_every_variable_for_the_block_type(monkeypatch):
    be = _load_backend()
    n = len(be.BLOCK_STAT_VARS["Queue"])
    # BlockName, label, then one value per stat variable
    _use(monkeypatch, be, _FakeApp(answers=["Queue", "Buffer"] + ["2.5"] * n))
    r = be.block_get_stats(7)
    assert r["success"] is True
    assert r["blockType"] == "Queue" and r["label"] == "Buffer"
    assert set(r["statistics"]) == set(be.BLOCK_STAT_VARS["Queue"])
    assert all(v == 2.5 for v in r["statistics"].values())


def test_block_get_stats_reports_a_missing_block(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=[""]))
    r = be.block_get_stats(999)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.BLOCK_NOT_FOUND


def test_block_get_stats_names_the_supported_types_for_an_unknown_one(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Text"]))
    r = be.block_get_stats(3)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.WRONG_BLOCK_TYPE
    assert "Queue" in r["supportedTypes"]


def test_block_get_stats_turns_one_failed_read_into_null_not_an_error(monkeypatch):
    """Documented partial-data behaviour: one unreadable statistic must not sink the rest."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["Exit", "Out", "12"]))
    monkeypatch.setattr(be, "_get_var",
                        lambda app, bid, var, row=0, col=0: (_ for _ in ()).throw(RuntimeError("x")))
    r = be.block_get_stats(4)
    assert r["success"] is True
    assert all(v is None for v in r["statistics"].values())
    assert fake  # the fake was used for the name and label reads


# ---------------------------------------------------------------------------
# resource_pool_get_stats / optimizer_get_results - both gate on block type
# ---------------------------------------------------------------------------

def test_resource_pool_get_stats_reads_the_four_counters(monkeypatch):
    be = _load_backend()
    # BlockName, then Utilization, NumAvailable, NumInUse, NumServ
    _use(monkeypatch, be, _FakeApp(answers=["Resource Pool", "0.75", "1", "3", "4"]))
    r = be.resource_pool_get_stats(9)
    assert r == {"success": True, "blockId": 9, "utilization": 0.75,
                 "available": 1, "inUse": 3, "totalResources": 4}


def test_resource_pool_get_stats_refuses_another_block_type(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["Queue"]))
    r = be.resource_pool_get_stats(9)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.WRONG_BLOCK_TYPE
    assert not any("Utilization" in c for c in fake.executed), "must not read after the type check"


def test_optimizer_get_results_refuses_another_block_type(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Queue"]))
    r = be.optimizer_get_results(5)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.WRONG_BLOCK_TYPE


def test_optimizer_get_results_reads_numbers_and_strings(monkeypatch):
    be = _load_backend()
    # BlockName, five numeric outputs, two string outputs
    _use(monkeypatch, be, _FakeApp(answers=["Optimizer", "12.5", "40", "8", "13.1", "3",
                                            "0.98", "00:02:11"]))
    r = be.optimizer_get_results(5)
    assert r["success"] is True
    assert r["results"]["Cost"] == 12.5
    assert r["results"]["Convergence"] == "0.98"
    assert r["results"]["ElapsedTime"] == "00:02:11"


# ---------------------------------------------------------------------------
# Scenario Manager - and its wrong error code
# ---------------------------------------------------------------------------

def test_scenario_manager_status_reports_progress_and_phase(monkeypatch):
    be = _load_backend()
    monkeypatch.setattr(be, "_get_sm_block_id", lambda app: 44)
    _use(monkeypatch, be, _FakeApp(answers=["3/8", "2", "2"]))
    r = be.scenario_manager_status()
    assert r["success"] is True
    assert r["running"] is True and r["currentScenario"] == "3/8"


def test_scenario_manager_status_without_an_sm_block(monkeypatch):
    be = _load_backend()
    monkeypatch.setattr(be, "_get_sm_block_id", lambda app: None)
    _use(monkeypatch, be, _FakeApp())
    r = be.scenario_manager_status()
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.BLOCK_NOT_FOUND


def test_scenario_manager_failures_are_not_reported_as_optimizer_failures(monkeypatch):
    be = _load_backend()

    def boom(app):
        raise RuntimeError("RPC server is unavailable")

    _use(monkeypatch, be, _FakeApp())
    monkeypatch.setattr(be, "_get_sm_block_id", boom)
    r = be.scenario_manager_status()
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.MULTI_RUN_FAILED, r["errorCode"]

    monkeypatch.setattr(be, "_read_sm_config", boom)
    r = be.scenario_manager_get_results()
    assert r["errorCode"] == be.ErrorCode.MULTI_RUN_FAILED, r["errorCode"]


def test_scenario_manager_get_results_with_no_scenarios(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp())
    monkeypatch.setattr(be, "_read_sm_config",
                        lambda app, auto_select_all=False: {"smBlockId": 44, "totalScenarios": 0})
    r = be.scenario_manager_get_results()
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.MULTI_RUN_FAILED
    assert "No scenarios" in r["error"]
