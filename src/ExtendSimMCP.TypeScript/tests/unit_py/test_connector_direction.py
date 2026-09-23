"""Connector direction follows ExtendSim's own rule.

ModL has no built-in direction query. Imagine That's isOutputCon (Extensions/Includes/
MouseClick.h and Item Block Utilities.h) strips any "[...]" array suffix and calls the
connector an output if its name ends in "Out" - an input otherwise.

The server used to test for "in" ANYWHERE in the name, before "out", in ten separate
places (four different variants). "WaitingOut", "LinkOut", "ContainsOut" all read as
inputs, so connections touching them were built backwards or dropped.
"""
import os
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


@pytest.mark.parametrize("name, expected", [
    ("ItemOut", "out"),
    ("ItemIn", "in"),
    ("ValueOut", "out"),
    ("ValueIn", "in"),
    ("itemOut", "out"),         # seen in ExtendSim's own _RightClickConnect data
    ("WaitingOut", "out"),      # contains "in" - read as an input before
    ("LinkOut", "out"),         # contains "in"
    ("ContainsOut", "out"),
    ("ItemOut[2]", "out"),      # array suffix is stripped first
    ("ValuesIn[0]", "in"),
    ("D", "in"),                # a name with neither marks an input, as in isOutputCon
    ("TR", "in"),
    ("", "unknown"),
])
def test_direction_follows_extendsims_is_output_con(name, expected):
    be = _load_backend()
    assert be._get_connector_direction(name) == expected


def test_every_direction_decision_goes_through_the_one_rule():
    """Ten copies drifted apart once; there is now exactly one place that decides."""
    be = _load_backend()
    with open(be.__file__, encoding="utf-8") as f:
        src = f.read()
    for guess in ('"in" in con_name', '"out" in con_name', '"in" in name_lower',
                  '"out" in name_lower', '"in" in low', '"in" in ep0', '"out" in connector_lower'):
        assert guess not in src, f"a private direction guess came back: {guess}"


def test_dynamic_lookup_finds_an_output_whose_name_contains_in(monkeypatch):
    """_find_connector_by_direction_dynamic's name pass used the broken rule too."""
    be = _load_backend()
    connectors = [{"index": 0, "name": "LinkIn"}, {"index": 1, "name": "WaitingOut"}]
    monkeypatch.setattr(be, "_get_block_connectors", lambda app, bid: connectors)
    out = be._find_connector_by_direction_dynamic(None, 7, "out")
    assert out == {"index": 1, "name": "WaitingOut"}, out
    inp = be._find_connector_by_direction_dynamic(None, 7, "in")
    assert inp == {"index": 0, "name": "LinkIn"}, inp
