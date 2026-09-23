"""Pattern mining must see top-level H-blocks, and only real blocks inside them.

Measured live on Bank.mox (ExtendSim 2024), 2026-09-23:

* _psg_top_level_ids walked ObjectIDNext(id, 0), which visits ordinary blocks only.
  Mining therefore never saw a top-level H-block and never descended into one: one
  scope of 6 blocks, while the model has 5 top-level H-blocks, 10 in all, and 44
  blocks inside them.
* Inside an H-block, LocalToGlobal2 also returns text blocks and anchor points (H17:
  12 blocks, 7 text, 72 anchors). Both have a BlockName - a text block's is its text,
  an anchor's a connector name - so they would have been mined as blocks of type
  "Tellers available" or "ItemOut".
"""
import os
import re
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_backend():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        pytest.skip("simulation_backend not importable (no pywin32 in this env)")


# Objects: id -> (GetBlockTypeNumeric, BlockName, enclosing H-block)
# Root: ordinary block 1 and H-block 5. H-block 5 holds block 6, text 7, anchor 8.
OBJECTS = {
    1: (3, "Create", -1),
    5: (4, "Tellers", -1),
    6: (3, "Activity", 5),
    7: (2, "Tellers available", 5),
    8: (1, "ItemOut", 5),
}
WALKS = {0: [1, 6], 1: [5], 2: [1, 5, 6]}    # ObjectIDNext(which): ordinary / H / all
LOCAL = {5: [6, 7, 8, -1]}                   # LocalToGlobal2 slots (a negative one too)


class _App:
    """Answers each Request from the last query command, as ExtendSim would."""

    def __init__(self):
        self.pending = ""
        self.text = ""       # the value of globalStr0

    def Execute(self, cmd):
        if cmd.startswith("globalStr9 = StrPart("):
            return           # _read_str0 copying globalStr0 out - not a new query
        if (m := re.search(r"globalStr0 = BlockName\((\d+)\)", cmd)):
            self.text = OBJECTS[int(m.group(1))][1]
        self.pending = cmd

    def Request(self, _system, key):
        cmd = self.pending
        if key.startswith("globalStr9"):             # _read_str0 piece
            return self.text
        m = re.search(r"objectIDNext\((-?\d+), (\d+)\)", cmd, re.I)
        if m:
            cur, which = int(m.group(1)), int(m.group(2))
            seq = WALKS[which]
            nxt = seq[0] if cur == -1 else (seq[seq.index(cur) + 1] if seq.index(cur) + 1 < len(seq) else -1)
            return str(nxt)
        if (m := re.search(r"GetEnclosingHBlockNum2\((\d+)\)", cmd, re.I)):
            return str(OBJECTS[int(m.group(1))][2])
        if (m := re.search(r"GetBlockTypeNumeric\((-?\d+)\)", cmd)):
            return str(OBJECTS.get(int(m.group(1)), (0,))[0])
        if (m := re.search(r"LocalNumBlocks2\((\d+)\)", cmd)):
            return str(len(LOCAL[int(m.group(1))]))
        if (m := re.search(r"LocalToGlobal2\((\d+), (\d+)\)", cmd)):
            return str(LOCAL[int(m.group(1))][int(m.group(2))])
        return "0"                                    # e.g. GetNumCons: no connectors


def _gather(monkeypatch, be):
    monkeypatch.setattr(be, "_extract_parameters", lambda app, blocks: {"blocks": {}})
    monkeypatch.setattr(be, "_psg_hblock_type", lambda app, bid: "physical")
    return be._gather_psg_raw(_App(), "Bank.mox")


def test_top_level_ids_include_h_blocks():
    be = _load_backend()
    assert be._psg_top_level_ids(_App()) == [1, 5]


def test_mining_descends_into_a_top_level_h_block(monkeypatch):
    be = _load_backend()
    raw = _gather(monkeypatch, be)
    scopes = {s["scopeId"]: s for s in raw["scopes"]}
    assert set(scopes) == {"root", "h5"}
    root_types = [b["type"] for b in scopes["root"]["blocks"]]
    assert root_types == ["Create", "Tellers"]
    assert scopes["root"]["blocks"][1]["isHBlock"] is True


def test_text_blocks_and_anchor_points_are_not_mined_as_blocks(monkeypatch):
    be = _load_backend()
    raw = _gather(monkeypatch, be)
    inner = next(s for s in raw["scopes"] if s["scopeId"] == "h5")
    assert [b["type"] for b in inner["blocks"]] == ["Activity"], \
        "'Tellers available' is a text block and 'ItemOut' an anchor point, not blocks"


def test_scopes_keep_their_kind_and_h_block_details(monkeypatch):
    """Caught live: a loop variable named `kind` shadowed the scope's own `kind`
    parameter, so every scope got a block-type code as its kind and H-block scopes
    lost hblockType and label."""
    be = _load_backend()
    raw = _gather(monkeypatch, be)
    scopes = {s["scopeId"]: s for s in raw["scopes"]}
    assert scopes["root"]["kind"] == "root"
    assert scopes["h5"]["kind"] == "hblock"
    assert scopes["h5"]["hblockType"] == "physical" and "label" in scopes["h5"]
