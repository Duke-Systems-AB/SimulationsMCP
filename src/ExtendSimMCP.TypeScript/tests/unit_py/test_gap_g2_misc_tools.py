"""Offline coverage for block_info and text_block_add (gap sweep G2, wave 6).

Both had the same fault as the list tools in wave 5, in a different shape: they
reported success for something that did not happen.

* text_block_add returned success with blockId -1 when nothing was placed. Wave 6
  made it fail closed - but it still found the block by diffing objectIDNext before
  and after, and objectIDNext never visits text blocks (measured live on Bank.mox,
  2026-09-23), so after wave 6 EVERY call failed. It now reads the number that
  PlaceTextBlock returns and checks that it is a text block (BT_TEXT = 2).
* block_info's live mode swallows the exception from every read and substitutes "".
  For a block ID that does not exist the result was success with every field blank
  - a missing block dressed up as a real one with no name. It now reports
  BLOCK_NOT_FOUND, but only when label, type AND name are all empty, so an object
  that has any one of them is never rejected.
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


def _use(monkeypatch, be, fake):
    monkeypatch.setattr(be, "get_extendsim_app", lambda create_if_missing=False: fake)
    monkeypatch.setattr(be, "_validate_model_open", lambda app: {"success": True})
    return fake


# ---------------------------------------------------------------------------
# text_block_add
# ---------------------------------------------------------------------------

def test_text_block_add_reports_the_number_placetextblock_returns(monkeypatch):
    """PlaceTextBlock returns the new block's number (ExtendSim's own code relies on
    it). Wave 6 found the block by diffing objectIDNext - which, measured live on
    Bank.mox, never visits text blocks, so every call reported failure."""
    be = _load_backend()
    # PlaceTextBlock -> 9 | GetBlockTypeNumeric(9) -> 2 (BT_TEXT)
    fake = _use(monkeypatch, be, _FakeApp(answers=["9", "2"]))
    r = be.text_block_add("Arrivals come from the gate", x=40, y=60)
    assert r["success"] is True and r["blockId"] == 9
    assert any(c.startswith("global0 = PlaceTextBlock(") for c in fake.executed)
    assert not any("objectIDNext" in c for c in fake.executed)


def test_text_block_add_fails_closed_when_nothing_was_placed(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["-1"]))
    r = be.text_block_add("note")
    assert r["success"] is False, r
    assert r["errorCode"] == be.ErrorCode.COMMAND_FAILED


def test_text_block_add_fails_closed_when_the_number_is_not_a_text_block(monkeypatch):
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=["9", "3"]))     # 3 = BT_EXECUTABLE
    r = be.text_block_add("note")
    assert r["success"] is False and r["returned"] == 9


def test_text_block_add_escapes_the_text(monkeypatch):
    be = _load_backend()
    fake = _use(monkeypatch, be, _FakeApp(answers=["4", "2"]))
    be.text_block_add('note" ; Evil("')
    place = [c for c in fake.executed if "PlaceTextBlock(" in c][0]
    assert "Evil" not in split_modl(place)[0], place   # code outside the ModL string literals must not contain the injected call


# ---------------------------------------------------------------------------
# block_info
# ---------------------------------------------------------------------------

def test_block_info_reference_mode_lists_every_library():
    be = _load_backend()
    r = be.block_info(query="all")
    assert r["success"] is True and r["mode"] == "reference"
    assert r["libraries"], "block_reference.json should list at least one library"


def test_block_info_reference_mode_finds_a_block_by_exact_name():
    be = _load_backend()
    r = be.block_info(query="Activity")
    assert r["success"] is True
    assert r.get("results"), r


def test_block_info_reference_mode_reports_an_unknown_block():
    be = _load_backend()
    r = be.block_info(query="NoSuchBlockAnywhere")
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.BLOCK_NOT_FOUND


def test_block_info_needs_a_query_or_a_block_id():
    be = _load_backend()
    r = be.block_info()
    assert r["success"] is False
    assert r["errorCode"] == be.ErrorCode.INVALID_PARAMETER


def test_block_info_live_mode_reports_a_block_that_does_not_exist(monkeypatch):
    """Every read comes back empty: there is no evidence the block exists."""
    be = _load_backend()
    _use(monkeypatch, be, _FakeApp(answers=[""] * 40))
    r = be.block_info(block_id=999)
    assert r["success"] is False, r
    assert r["errorCode"] == be.ErrorCode.BLOCK_NOT_FOUND


def test_block_info_live_mode_keeps_an_object_that_has_any_identity(monkeypatch):
    """A label alone is evidence enough - never reject a real object."""
    be = _load_backend()
    # label present, type and name empty, then zeros for the connector reads
    _use(monkeypatch, be, _FakeApp(answers=["Loading dock", "", ""] + ["0"] * 40))
    r = be.block_info(block_id=12)
    assert r["success"] is True, r
    assert r["label"] == "Loading dock"
