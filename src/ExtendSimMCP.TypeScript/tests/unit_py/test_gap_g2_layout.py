"""Offline coverage for the block layout tools (gap sweep G2, wave 4).

`block_move`, `block_get_position`, `block_find`, `block_align` and
`block_duplicate` all had no test. Driven against a fake COM app.

The one that earns its test most is `block_get_position`. BUG-003 was caused by
passing a scalar where `GetBlockTypePosition` wants an array NAME: ExtendSim
answers "expecting array name in function call" with a compile dialog, which
blocks COM and wedges the session. The shape of that ModL call is therefore load
bearing, and is asserted directly below.
"""
import os
import sys

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from modl_lexer import split_modl  # noqa: E402  (ModL literal rules, measured live)


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


def _calls(fake, needle):
    return [c for c in fake.executed if needle in c]


# ---------------------------------------------------------------------------
# block_move
# ---------------------------------------------------------------------------

def test_block_move_issues_move_block_to(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp())
    r = be.block_move(12, 300, 450)
    assert r == {"success": True, "blockId": 12, "x": 300, "y": 450}
    assert "MoveBlockTo(12, 300, 450);" in _calls(fake, "MoveBlockTo")[0]


def test_block_move_refuses_with_no_model_open(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.block_move(1, 0, 0)
    assert r["success"] is False
    assert fake.executed == []


# ---------------------------------------------------------------------------
# block_get_position — BUG-003 guard
# ---------------------------------------------------------------------------

def test_block_get_position_passes_an_array_name_not_a_scalar(monkeypatch):
    """A scalar here triggers a compile dialog that locks COM. See BUG-003."""
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["10", "20", "60", "120"]))
    be.block_get_position(5)
    call = _calls(fake, "GetBlockTypePosition")[0]
    assert "integer _esPos[4];" in call, call
    assert "GetBlockTypePosition(5, _esPos)" in call, call


def test_block_get_position_converts_edges_to_xy_and_size(monkeypatch):
    """GetBlockTypePosition fills [top, left, bottom, right]."""
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["10", "20", "60", "120"]))
    r = be.block_get_position(5)
    assert r["success"] is True
    assert r["x"] == 20 and r["y"] == 10          # left, top
    assert r["width"] == 100 and r["height"] == 50  # right-left, bottom-top


# ---------------------------------------------------------------------------
# block_find
# ---------------------------------------------------------------------------

def test_block_find_searches_by_label_or_by_block_name(monkeypatch):
    be = _load_backend()
    for which in (1, 2):
        fake = _use(monkeypatch, be, _FakeApp(answers=["7", "Activity", "Station A"]))
        r = be.block_find("Station A", which=which)
        assert r["success"] is True and r["blockId"] == 7
        assert f'FindBlock("Station A", {which}, 0, 0, 1)' in _calls(fake, "FindBlock")[0]


def test_block_find_escapes_the_search_string(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["1", "Activity", "x"]))
    be.block_find('a" ; Evil("')
    call = _calls(fake, "FindBlock")[0]
    assert "Evil" not in split_modl(call)[0], call   # code outside the ModL string literals must not contain the injected call


def test_block_find_reports_not_found_with_the_search_type(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["-1"]))
    r = be.block_find("ghost", which=2)
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.BLOCK_NOT_FOUND
    assert "block name" in r["error"], r["error"]

    _use(monkeypatch, be, _FakeApp(answers=["-1"]))
    r = be.block_find("ghost", which=1)
    assert "label" in r["error"], r["error"]


# ---------------------------------------------------------------------------
# block_align
# ---------------------------------------------------------------------------

def test_block_align_resolves_both_connectors_and_passes_the_axis(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp())
    monkeypatch.setattr(be, "_resolve_connector",
                        lambda app, bid, con, auto_expand_array=True: 3 if bid == 1 else 4)

    r = be.block_align(1, "ItemOut", 2, "ItemIn", vertical=True)
    assert r["success"] is True
    assert "AlignConnection(1, 3, 2, 4, 1);" in _calls(fake, "AlignConnection")[0]

    fake = _use(monkeypatch, be, _FakeApp())
    be.block_align(1, "ItemOut", 2, "ItemIn", vertical=False)
    assert "AlignConnection(1, 3, 2, 4, 0);" in _calls(fake, "AlignConnection")[0]


# ---------------------------------------------------------------------------
# block_duplicate
# ---------------------------------------------------------------------------

def test_block_duplicate_identifies_the_id_that_appeared(monkeypatch):
    be = _load_backend()
    # before sweep: 1, 2, end | after sweep: 1, 2, 7, end
    fake = _use(monkeypatch, be, _FakeApp(
        answers=["1", "2", "-1", "1", "2", "7", "-1"]))
    r = be.block_duplicate(2)
    assert r["success"] is True
    assert r["newBlockId"] == 7, r
    assert _calls(fake, "DuplicateBlock(2)"), fake.executed


def test_block_duplicate_refuses_with_no_model_open(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(), model_open=False)
    r = be.block_duplicate(1)
    assert r["success"] is False
    assert fake.executed == []
