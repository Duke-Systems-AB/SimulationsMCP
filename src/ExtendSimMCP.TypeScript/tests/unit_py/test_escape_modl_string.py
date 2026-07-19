# tests/unit_py/test_escape_modl_string.py
"""BUG-009a: _escape_modl_string must produce a valid SINGLE-LINE ModL string
literal from arbitrary text (e.g. a multi-line Python script), so embedded
newlines/CR/tab do not raise ExtendSim's "unterminated string" modal.
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from simulation_backend import _escape_modl_string


def test_escapes_backslash():
    assert _escape_modl_string("C:\\tmp") == "C:\\\\tmp"


def test_escapes_double_quote():
    assert _escape_modl_string('say "hi"') == 'say \\"hi\\"'


def test_newline_becomes_backslash_n_not_raw():
    out = _escape_modl_string("a\nb")
    assert "\n" not in out          # no raw newline -> literal won't span lines
    assert out == "a\\nb"


def test_cr_and_tab_escaped():
    assert _escape_modl_string("a\rb") == "a\\rb"
    assert _escape_modl_string("a\tb") == "a\\tb"


def test_multiline_python_script_has_no_raw_control_chars():
    script = "import datetime\nwith open('C:/tmp/x.txt','w') as f:\n\tf.write('hi')\n"
    out = _escape_modl_string(script)
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert "\\n" in out and "\\t" in out


def test_literal_backslash_n_is_doubled_not_turned_into_newline():
    # a literal backslash+n in the source (2 chars) must become \\n (double
    # backslash) so ModL un-escapes it back to a single backslash, NOT a newline.
    out = _escape_modl_string("a\\nb")   # a, backslash, n, b
    assert out == "a\\\\nb"


def test_none_returns_empty():
    assert _escape_modl_string(None) == ""
