"""Offline coverage for the model readers (gap sweep G2, wave 5).

Writing these found a contract hole the W1-1 fix missed. The TypeScript layer marks a
result as an error only when `success === false`. Three readers answered a COM failure
with an empty list, an errorCode and NO `success` key:

    model_list       -> {"models": [], "errorCode": "COM_ERROR", ...}
    block_list       -> {"blocks": [], "errorCode": "COM_ERROR", ...}
    connection_list  -> {"connections": [], "errorCode": "COM_ERROR", ...}

So "ExtendSim could not be read" reached the client as a SUCCESSFUL "the model is
empty". A client that trusts an empty block_list may start building a fresh model on top
of one that is really there. They now fail closed.

extendsim_status is deliberately different and pinned as such: it is a status query, and
"ExtendSim is not running" is its answer, not a failure of the tool.
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
    monkeypatch.setattr(be, "_validate_model_open", lambda app: {"success": True})
    return fake


# ---------------------------------------------------------------------------
# The contract hole: a COM failure must not look like an empty model
# ---------------------------------------------------------------------------

def test_model_list_reports_a_com_failure_as_a_failure(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(raise_on=""))      # every Execute raises
    r = be.model_list()
    assert r.get("success") is False, r
    assert r["errorCode"] == be.ErrorCode.COM_ERROR


def test_block_list_reports_a_com_failure_as_a_failure(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(raise_on=""))
    r = be.block_list()
    assert r.get("success") is False, r
    assert r["errorCode"] == be.ErrorCode.COM_ERROR


def test_connection_list_reports_a_com_failure_as_a_failure(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(raise_on=""))
    r = be.connection_list()
    assert r.get("success") is False, r
    assert r["errorCode"] == be.ErrorCode.COM_ERROR


def test_extendsim_status_treats_not_running_as_an_answer_not_a_failure(monkeypatch):
    """Deliberate: the status tool's job is to report, and "not running" is a report."""
    be = _load_backend()

    def boom(_progid):
        raise RuntimeError("Operation unavailable")

    # extendsim_status calls GetActiveObject directly rather than get_extendsim_app,
    # so that is what must be patched. Patching get_extendsim_app here once let this
    # "unit" test reach the real ExtendSim on a dev machine - never again.
    monkeypatch.setattr(be.win32com.client, "GetActiveObject", boom)
    r = be.extendsim_status()
    assert r["running"] is False and r["connected"] is False
    assert r.get("success") is not False, "a negative status is not a tool failure"


# ---------------------------------------------------------------------------
# model_list / model_info
# ---------------------------------------------------------------------------

def test_model_list_names_the_open_model_or_returns_none(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox"]))
    r = be.model_list()
    assert r["models"] == [{"modelId": "model_1", "name": "Plant.mox"}]

    _use(monkeypatch, be, _FakeApp(answers=[""]))
    assert be.model_list()["models"] == []


def test_model_info_derives_is_running_from_the_simulation_phase(monkeypatch):
    be = _load_backend()
    # name, endTime, currentTime, phase
    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "480", "120", "3"]))
    r = be.model_info()
    assert r["success"] is True
    assert r["name"] == "Plant.mox"
    assert r["endTime"] == 480 and r["currentTime"] == 120
    assert r["isRunning"] is True and r["simulationPhase"] == 3

    _use(monkeypatch, be, _FakeApp(answers=["Plant.mox", "480", "0", "0"]))
    assert be.model_info()["isRunning"] is False


# ---------------------------------------------------------------------------
# model_close - the difference between saving and discarding
# ---------------------------------------------------------------------------

def test_model_close_saves_first_only_when_asked(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp())
    r = be.model_close(save_first=True)
    assert r == {"success": True, "wasSaved": True}
    save = [i for i, c in enumerate(fake.executed) if "SaveModel" in c]
    close = [i for i, c in enumerate(fake.executed) if "ExecuteMenuCommand(4)" in c]
    assert save and close and save[0] < close[0], "must save BEFORE closing"
    assert not any("SetDirty(False)" in c for c in fake.executed)


def test_model_close_without_save_discards_explicitly(monkeypatch):
    """Without SetDirty(False) ExtendSim would raise a 'save changes?' dialog and block COM."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp())
    r = be.model_close(save_first=False)
    assert r["success"] is True and r["wasSaved"] is False
    assert any("SetDirty(False)" in c for c in fake.executed)
    assert not any("SaveModel" in c for c in fake.executed)
