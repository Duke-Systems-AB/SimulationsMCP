# tests/unit_py/test_escape_modl_string.py
"""_escape_modl_string must turn ANY text into a ModL string expression that
(a) never ends early - no quote may close the literal and leak code,
(b) never spans lines - a raw newline raises an "unterminated string" modal (BUG-009a),
(c) evaluates in ExtendSim to exactly the original text.

The ModL rules these tests encode were measured live on 2026-09-23 (see modl_lexer.py):
a backslash is always an ordinary character, a double quote always ends the literal,
and a literal holds at most 255 characters. The previous escaper assumed C rules
(\\", \\n, doubled backslashes) and failed (a) and (c): a quote raised a compile modal,
line breaks were stored as "\\n", and C:\\tmp arrived with its backslashes doubled.
"""
import os
import sys

import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from modl_lexer import modl_string_value, split_modl  # noqa: E402
from simulation_backend import _escape_modl_string  # noqa: E402


def _as_literal(text):
    return f'"{_escape_modl_string(text)}"'


@pytest.mark.parametrize("text", [
    "plain",
    "C:\\tmp\\model.mox",
    'say "hi"',
    '["a", "b"]',
    '{"k": "v", "n": [1, 2]}',
    "a\nb\r\nc\td",
    "trailing backslash \\",
    'backslash before quote \\"',
    "a\\nb",                      # a literal backslash + n, not a newline
    "",
])
def test_round_trips_to_the_original_text(text):
    assert modl_string_value(_as_literal(text)) == text


def test_a_quote_is_spliced_in_as_ascii_34_not_escaped():
    assert _escape_modl_string('say "hi"') == 'say " + StrPutAscii(34) + "hi" + StrPutAscii(34) + "'


def test_backslashes_pass_through_unchanged():
    """Live: a doubled backslash stays two characters in ModL."""
    assert _escape_modl_string("C:\\tmp") == "C:\\tmp"


def test_no_raw_control_characters_reach_the_command():
    out = _escape_modl_string("import x\nwith open('C:/t.txt') as f:\r\n\tf.write('hi')\n")
    assert "\n" not in out and "\r" not in out and "\t" not in out


def test_injection_stays_inside_string_literals():
    """The W1-6 case. With the old \\" escaping, `; Evil(` landed OUTSIDE the literal."""
    cmd = f'FindBlock({_as_literal(chr(34) + " ; Evil(" + chr(34))});'
    code, literals = split_modl(cmd)
    assert "Evil" not in code, code
    assert any("Evil" in lit for lit in literals)


def test_the_old_escaping_really_was_injectable():
    """Pins WHY this changed: under ModL's rules, \\" ends the string."""
    old = 'x\\" ; Evil(\\"'
    code, _ = split_modl(f'FindBlock("{old}");')
    assert "Evil" in code


@pytest.mark.parametrize("text", [
    "x" * 300,
    "\\" * 300,
    ("ab\\" * 120) + '"' + ("q" * 400),
])
def test_no_literal_exceeds_modl_limit_of_255(text):
    """"String literals cannot be larger than 255 characters" - a modal, hit live."""
    raw = _as_literal(text)
    _, literals = split_modl(raw)
    assert all(len(lit) <= 255 for lit in literals), raw[:200]
    assert modl_string_value(raw) == text


def test_none_returns_empty():
    assert _escape_modl_string(None) == ""
