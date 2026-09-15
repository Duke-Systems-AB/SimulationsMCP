"""Offline coverage for tools the gap sweep (G2) found had no test at all.

Wave 1: the cheap ones - time_convert, template_list, context_clear. Each is
driven against a fake COM app, the pattern the rest of this suite uses, so no
ExtendSim is required.

What these lock down is the contract, not the arithmetic ExtendSim does: the
right ModL call is built, user input is escaped before it reaches a command
string, missing parameters fail before any COM traffic, and the guard rails
(confirm flags, unknown operations) hold.
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
    """Records Execute() strings and replays scripted Request() answers."""

    def __init__(self, answers=None):
        self.executed = []
        self.answers = list(answers or [])
        self.requests = []

    def Execute(self, cmd):
        self.executed.append(cmd)

    def Request(self, _system, query):
        self.requests.append(query)
        return self.answers.pop(0) if self.answers else "0"


def _use_fake(monkeypatch, be, fake):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    return fake


# ---------------------------------------------------------------------------
# time_convert
# ---------------------------------------------------------------------------

def test_time_convert_units_builds_the_modl_call_and_parses_the_result(monkeypatch):
    be = _load_backend()
    fake = _use_fake(monkeypatch, be, _FakeApp(answers=["120"]))

    r = be.time_convert("convert_units", value=2, from_type=3, to_type=4)

    assert r["success"] is True
    assert r["result"] == 120
    assert r["fromType"] == 3 and r["toType"] == 4
    assert any("ConvertTimeUnits(2, 3, 4)" in c for c in fake.executed), fake.executed


def test_time_convert_date_to_sim_escapes_the_date_string(monkeypatch):
    """The date is user input and is interpolated into a ModL string literal."""
    be = _load_backend()
    fake = _use_fake(monkeypatch, be, _FakeApp(answers=["17.5"]))

    r = be.time_convert("date_to_sim", date='1 Jan 2026" ; DoSomethingElse("', time_units=2)

    assert r["success"] is True
    assert r["simTime"] == 17.5
    call = next(c for c in fake.executed if "EDDateToSimTime" in c)
    # The raw closing quote must not survive into the command string.
    assert '" ; DoSomethingElse("' not in call, call


def test_time_convert_sim_to_date_returns_the_string_unparsed(monkeypatch):
    be = _load_backend()
    _use_fake(monkeypatch, be, _FakeApp(answers=["3 Feb 2026"]))

    r = be.time_convert("sim_to_date", sim_time=42, time_units=2)

    assert r["success"] is True
    assert r["date"] == "3 Feb 2026"


def test_time_convert_rejects_an_unknown_operation(monkeypatch):
    be = _load_backend()
    fake = _use_fake(monkeypatch, be, _FakeApp())

    r = be.time_convert("teleport", value=1)

    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.INVALID_PARAMETER
    assert fake.executed == [], "an unknown operation must not reach COM"


def test_time_convert_missing_parameters_fail_before_any_com_call(monkeypatch):
    be = _load_backend()
    fake = _use_fake(monkeypatch, be, _FakeApp())

    for kwargs in ({"value": 1, "from_type": 2},           # no to_type
                   {"sim_time": 5},                         # no time_units
                   {"date": "1 Jan 2026"}):                 # no time_units
        op = ("convert_units" if "value" in kwargs
              else "sim_to_date" if "sim_time" in kwargs else "date_to_sim")
        r = be.time_convert(op, **kwargs)
        assert r["success"] is False, (op, r)
        assert r["errorCode"] == be.ErrorCode.MISSING_PARAMETER

    assert fake.executed == [], "validation must happen before COM traffic"


# ---------------------------------------------------------------------------
# template_list
# ---------------------------------------------------------------------------

def test_template_list_summarizes_every_template(monkeypatch):
    be = _load_backend()
    monkeypatch.setattr(be, "_load_templates", lambda: {
        "simple_process": {
            "description": "A source, a server and a sink",
            "blocks": [{"b": 1}, {"b": 2}, {"b": 3}],
            "connections": [{"c": 1}, {"c": 2}],
            "parameters": {"rate": 1, "capacity": 2},
        },
        "bare": {},
    })

    r = be.template_list()

    assert r["success"] is True
    by_name = {t["name"]: t for t in r["templates"]}
    assert by_name["simple_process"]["blockCount"] == 3
    assert by_name["simple_process"]["connectionCount"] == 2
    assert sorted(by_name["simple_process"]["parameters"]) == ["capacity", "rate"]
    # A template missing every optional key must still be listed, not crash.
    assert by_name["bare"]["blockCount"] == 0
    assert by_name["bare"]["description"] == ""


def test_template_list_matches_the_shipped_reference_data():
    """Guards the copy-files step: if templates.json stops shipping, this fails."""
    be = _load_backend()
    r = be.template_list()
    assert r["success"] is True
    assert len(r["templates"]) > 0
    assert all(t["name"] for t in r["templates"])


# ---------------------------------------------------------------------------
# context_clear
# ---------------------------------------------------------------------------

def test_context_clear_refuses_without_confirm(monkeypatch):
    be = _load_backend()
    fake = _use_fake(monkeypatch, be, _FakeApp())

    r = be.context_clear(confirm=False)

    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.INVALID_PARAMETER
    assert "confirm" in r["error"].lower()
    assert fake.executed == [], "a destructive call must not touch COM before confirmation"


def test_context_clear_is_a_noop_when_no_context_database_exists(monkeypatch):
    be = _load_backend()
    fake = _use_fake(monkeypatch, be, _FakeApp(answers=["-1"]))
    monkeypatch.setattr(be, "_validate_model_open", lambda app: {"success": True})

    r = be.context_clear(confirm=True)

    assert r["success"] is True
    assert "nothing to delete" in r["message"].lower()
    assert not any("DBDatabaseDelete" in c for c in fake.executed), fake.executed
