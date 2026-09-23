"""Offline coverage for the global-array tools (gap sweep G2, wave 2).

All four of `ga_list`, `ga_create`, `ga_read` and `ga_write` had no test at all —
the only complete tool group in that state. Driven against a fake COM app, so no
ExtendSim is required.

The behaviour worth pinning here is type dispatch: a global array is real,
integer or string, and each type reaches ExtendSim through a *different* ModL
function. Send a string cell to GASetReal and the write is silently wrong, which
is the failure class this suite exists to catch.
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
    """Records Execute() strings, replays scripted Request() answers, and can be
    told to raise on a particular ModL call (to exercise the defensive paths)."""

    def __init__(self, answers=None, raise_on=None):
        self.executed = []
        self.answers = list(answers or [])
        self.raise_on = raise_on

    def Execute(self, cmd):
        if self.raise_on and self.raise_on in cmd:
            raise RuntimeError(f"ExtendSim refused: {cmd}")
        self.executed.append(cmd)

    def Request(self, _system, _query):
        return self.answers.pop(0) if self.answers else "0"


def _use(monkeypatch, be, fake, model_open=True):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open",
                        lambda app: {"success": True} if model_open
                        else {"success": False, "errorCode": "MODEL_NOT_OPEN"})
    return fake


def _calls(fake, needle):
    return [c for c in fake.executed if needle in c]


# ---------------------------------------------------------------------------
# ga_create
# ---------------------------------------------------------------------------

def test_ga_create_maps_each_type_to_its_code(monkeypatch):
    be = _load_backend()
    for ga_type, code in (("real", 1), ("integer", 2), ("string", 3)):
        fake = _use(monkeypatch, be, _FakeApp(answers=["4"]))
        r = be.ga_create("counts", ga_type=ga_type, cols=2)
        assert r["success"] is True and r["index"] == 4
        assert f'GACreate("counts", {code}, 2)' in _calls(fake, "GACreate")[0]


def test_ga_create_falls_back_to_real_for_an_unknown_type(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0"]))
    be.ga_create("x", ga_type="complex")
    assert 'GACreate("x", 1, 1)' in _calls(fake, "GACreate")[0]


def test_ga_create_resizes_only_when_rows_requested(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1"]))
    be.ga_create("a", rows=0)
    assert _calls(fake, "GAResize") == []

    fake = _use(monkeypatch, be, _FakeApp(answers=["1"]))
    be.ga_create("a", rows=25)
    assert 'GAResize("a", 25)' in _calls(fake, "GAResize")[0]


def test_ga_create_escapes_the_array_name(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1"]))
    be.ga_create('bad" ; Evil("')
    call = _calls(fake, "GACreate")[0]
    assert '" ; Evil("' not in call, call


def test_ga_create_fails_when_extendsim_returns_a_negative_index(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["-1"]))
    r = be.ga_create("dup")
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.COMMAND_FAILED


def test_ga_create_refuses_when_no_model_is_open(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.ga_create("a")
    assert r["success"] is False
    assert fake.executed == [], "must not touch COM without an open model"


# ---------------------------------------------------------------------------
# ga_read / ga_write — type dispatch is the point
# ---------------------------------------------------------------------------

def test_ga_read_dispatches_on_array_type(monkeypatch):
    be = _load_backend()
    # answers: GAGetIndex, GAGetType, GAGetRows, GAGetCols, then the value
    cases = [("3", "hello", "GAGetString", "hello"),
             ("2", "42", "GAGetInteger", 42),
             ("1", "2.5", "GAGetReal", 2.5)]
    for type_code, raw, fn, expected in cases:
        fake = _use(monkeypatch, be, _FakeApp(answers=["0", type_code, "10", "10", raw]))
        r = be.ga_read("arr", row=1, col=2)
        assert r["success"] is True
        assert r["value"] == expected, (fn, r)
        assert _calls(fake, fn), (fn, fake.executed)


def test_ga_write_dispatches_on_array_type(monkeypatch):
    be = _load_backend()
    cases = [("3", "hi", "GASetString"),
             ("2", 7, "GASetInteger"),
             ("1", 1.5, "GASetReal")]
    for type_code, value, fn in cases:
        fake = _use(monkeypatch, be, _FakeApp(answers=["0", type_code, "10", "10"]))
        r = be.ga_write("arr", 0, 0, value)
        assert r["success"] is True
        assert _calls(fake, fn), (fn, fake.executed)


def test_ga_write_escapes_string_values(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "3", "10", "10"]))
    be.ga_write("arr", 0, 0, 'x" ; Evil("')
    call = _calls(fake, "GASetString")[0]
    assert '" ; Evil("' not in call, call


def test_ga_read_and_write_fail_when_the_array_does_not_exist(monkeypatch):
    be = _load_backend()
    for call in (lambda b: b.ga_read("nope"),
                 lambda b: b.ga_write("nope", 0, 0, 1)):
        _use(monkeypatch, be, _FakeApp(answers=["-1"]))
        r = call(be)
        assert r["success"] is False
        assert r["errorCode"] == be.ErrorCode.COMMAND_FAILED
        assert "not found" in r["error"].lower()


# ---------------------------------------------------------------------------
# ga_list
# ---------------------------------------------------------------------------

def test_ga_list_reports_each_array_with_its_type_name(monkeypatch):
    be = _load_backend()
    # GALastUsedIndex=0, then for index 0: name, rows, cols, type
    _use(monkeypatch, be, _FakeApp(answers=["0", "demand", "10", "3", "2"]))
    r = be.ga_list()
    assert r["success"] is True
    assert r["count"] == 1
    assert r["arrays"][0] == {"index": 0, "name": "demand", "rows": 10, "cols": 3,
                              "type": "integer"}


def test_ga_list_skips_unnamed_and_internal_arrays(monkeypatch):
    be = _load_backend()
    # three slots: "" (empty), "_internal", "real_one"
    _use(monkeypatch, be, _FakeApp(
        answers=["2", "", "_internal", "real_one", "5", "1", "1"]))
    r = be.ga_list()
    assert r["success"] is True
    assert [a["name"] for a in r["arrays"]] == ["real_one"]
    assert r["arrays"][0]["type"] == "real"


def test_ga_list_degrades_gracefully_when_the_model_has_no_global_arrays(monkeypatch):
    """GALastUsedIndex raises on such models and would otherwise pop a dialog."""
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(raise_on="GALastUsedIndex"))
    r = be.ga_list()
    assert r["success"] is True
    assert r["arrays"] == [] and r["count"] == 0
    assert "warning" in r


# ---------------------------------------------------------------------------
# Bounds: reading or writing past the end of a global array raises a MODAL
# dialog in ExtendSim ("Row or column reference out of range in Global Array
# call") that blocks COM. Observed live 2026-09-14: a range read of
# _AttributeList past its last row. The server must never issue that call.
# ---------------------------------------------------------------------------

def _value_calls(fake):
    return [c for c in fake.executed
            if any(f in c for f in ("GAGetReal", "GAGetInteger", "GAGetString",
                                    "GASetReal", "GASetInteger", "GASetString"))]


def test_ga_read_range_is_clamped_to_the_array_size(monkeypatch):
    be = _load_backend()
    # idx 0, real, 3 rows, 1 col; then values for the rows that exist
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "3", "1", "1.0", "2.0", "3.0"]))
    r = be.ga_read("arr", row=0, col=0, end_row=10, end_col=0)
    assert r["success"] is True
    assert r["toRow"] == 2, r
    assert [row[0] for row in r["data"]] == [1.0, 2.0, 3.0]
    assert r.get("clamped") is True and "warning" in r
    reads = _value_calls(fake)
    assert len(reads) == 3, reads
    assert not any(", 3, " in c for c in reads), "row 3 does not exist and must not be read"


def test_ga_read_range_is_clamped_in_columns_too(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "1", "2", "1", "2"]))
    r = be.ga_read("arr", row=0, col=0, end_row=0, end_col=9)
    assert r["success"] is True and r["toCol"] == 1
    assert len(_value_calls(fake)) == 2


def test_ga_read_single_cell_out_of_range_fails_closed(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "3", "1"]))
    r = be.ga_read("arr", row=5, col=0)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.INVALID_PARAMETER
    assert r["rows"] == 3 and r["cols"] == 1
    assert _value_calls(fake) == [], "an out-of-range read must never reach ExtendSim"


def test_ga_read_range_starting_past_the_end_fails_closed(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "3", "1"]))
    r = be.ga_read("arr", row=7, col=0, end_row=9, end_col=0)
    assert r["success"] is False
    assert _value_calls(fake) == []


def test_ga_read_negative_indices_fail_closed(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "3", "3"]))
    r = be.ga_read("arr", row=-1, col=0)
    assert r["success"] is False
    assert _value_calls(fake) == []


def test_ga_write_out_of_range_fails_closed(monkeypatch):
    be = _load_backend()
    for row, col in ((3, 0), (0, 1), (-1, 0)):
        fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "3", "1"]))
        r = be.ga_write("arr", row, col, 1.5)
        assert r["success"] is False, (row, col, r)
        assert r["errorCode"] == be.ErrorCode.INVALID_PARAMETER
        assert _value_calls(fake) == [], (row, col, fake.executed)


def test_ga_read_on_an_empty_array_fails_closed(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["0", "1", "0", "1"]))
    r = be.ga_read("arr", row=0, col=0)
    assert r["success"] is False
    assert _value_calls(fake) == []
