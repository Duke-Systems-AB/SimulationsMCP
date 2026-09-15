"""Offline coverage for the simulation-control tools (gap sweep G2, wave 4).

`simulation_pause`, `simulation_resume`, `simulation_step`, `simulation_status`,
`simulation_setup_get` and `simulation_get_state` had no test. Driven against a
fake COM app.

Two magic-number mappings carry the risk here, and both fail silently if they
drift: pause/resume/step are menu command ids 30001/30002/30003, and
`simulation_setup_get` reads its fields through `GetRunParameter(which)` with a
fixed which->field mapping. Swap either and the tool still "succeeds" while doing
something else entirely.
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
    def __init__(self, answers=None):
        self.executed = []
        self.answers = list(answers or [])

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.answers.pop(0) if self.answers else "0"


def _use(monkeypatch, be, fake, model_open=True):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open",
                        lambda app: {"success": True} if model_open
                        else {"success": False, "errorCode": "MODEL_NOT_OPEN"})
    return fake


def _joined(fake):
    return "\n".join(fake.executed)


# ---------------------------------------------------------------------------
# pause / resume / step use distinct menu command ids
# ---------------------------------------------------------------------------

def test_pause_resume_and_step_use_their_own_menu_commands(monkeypatch):
    be = _load_backend()

    fake = _use(monkeypatch, be, _FakeApp(answers=["12.5"]))
    r = be.simulation_pause()
    assert r["success"] is True and r["pausedAtTime"] == 12.5
    assert "ExecuteMenuCommand(30001)" in _joined(fake)

    fake = _use(monkeypatch, be, _FakeApp())
    r = be.simulation_resume()
    assert r["success"] is True
    assert "ExecuteMenuCommand(30002)" in _joined(fake)

    fake = _use(monkeypatch, be, _FakeApp(answers=["13.0", "2"]))
    r = be.simulation_step()
    assert r["success"] is True
    assert "ExecuteMenuCommand(30003)" in _joined(fake)


def test_the_three_menu_commands_are_not_the_same_number(monkeypatch):
    """A copy-paste that makes pause resume the run would still 'succeed'."""
    be = _load_backend()
    seen = {}
    for name, answers in (("simulation_pause", ["1"]),
                          ("simulation_resume", []),
                          ("simulation_step", ["1", "0"])):
        fake = _use(monkeypatch, be, _FakeApp(answers=answers))
        getattr(be, name)()
        cmds = [c for c in fake.executed if "ExecuteMenuCommand" in c]
        seen[name] = cmds[0]
    assert len(set(seen.values())) == 3, seen


# ---------------------------------------------------------------------------
# simulation_status
# ---------------------------------------------------------------------------

def test_simulation_status_derives_is_running_from_the_phase(monkeypatch):
    be = _load_backend()
    # answers: currentTime, endTime, phase
    _use(monkeypatch, be, _FakeApp(answers=["5", "100", "0"]))
    r = be.simulation_status()
    assert r["isRunning"] is False and r["simulationPhase"] == 0

    _use(monkeypatch, be, _FakeApp(answers=["5", "100", "2"]))
    r = be.simulation_status()
    assert r["isRunning"] is True and r["simulationPhase"] == 2
    assert r["currentTime"] == 5 and r["endTime"] == 100


def test_simulation_status_names_an_unknown_phase_instead_of_hiding_it(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["0", "0", "99"]))
    r = be.simulation_status()
    assert "99" in r["phaseName"], r["phaseName"]


# ---------------------------------------------------------------------------
# simulation_setup_get — the GetRunParameter(which) mapping
# ---------------------------------------------------------------------------

def test_simulation_setup_get_maps_each_run_parameter_to_its_field(monkeypatch):
    be = _load_backend()
    # GetRunParameter is called in a fixed order; give each a distinct value so a
    # swapped mapping cannot pass by coincidence.
    fake = _use(monkeypatch, be, _FakeApp(
        answers=["111", "222", "333", "444", "555", "666", "777", "888"]))
    r = be.simulation_setup_get()
    assert r["success"] is True
    # which=1 is endTime and which=2 is startTime - the first two reads, in order.
    assert r["endTime"] == 111, r
    assert r["startTime"] == 222, r
    calls = [c for c in fake.executed if "GetRunParameter" in c]
    assert calls[0].endswith("GetRunParameter(1);"), calls[0]
    assert calls[1].endswith("GetRunParameter(2);"), calls[1]


def test_simulation_setup_get_refuses_with_no_model_open(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.simulation_setup_get()
    assert r["success"] is False
    assert fake.executed == []


# ---------------------------------------------------------------------------
# simulation_get_state
# ---------------------------------------------------------------------------

def test_simulation_get_state_reads_each_live_system_variable(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(
        answers=["7", "3", "1", "10", "5", "100", "42"]))
    r = be.simulation_get_state()
    assert r["success"] is True
    joined = _joined(fake)
    for var in ("CurrentTime", "CurrentStep", "CurrentSim",
                "NumSteps", "NumSims", "EndTime"):
        assert var in joined, var
    # The seed comes from GetRunParameter(5), not a bare system variable.
    assert "GetRunParameter(5)" in joined
