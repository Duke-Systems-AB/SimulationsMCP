# tests/unit_py/test_lbr_stat.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
import struct
import pytest
from lbr_stat import read_stat_variables, parse_stat_variables

VALUE_LBR = r"C:\Users\Jonas\Documents\ExtendSim_2026_Pro\Libraries\Value.lbr"
_needs_lbr = pytest.mark.skipif(not os.path.exists(VALUE_LBR), reason="Value.lbr not installed here")

@_needs_lbr
def test_python_bridge_dspythoncode_is_string_array():
    by = {v.name: v for v in read_stat_variables(VALUE_LBR, "Python Bridge")}
    assert "dsPythonCode" in by
    assert by["dsPythonCode"].is_scalar is False
    assert by["dsPythonCode"].dim_count >= 1

@_needs_lbr
def test_python_bridge_has_init_code_too():
    names = {v.name for v in read_stat_variables(VALUE_LBR, "Python Bridge")}
    assert "dsPythonInitCode" in names

@_needs_lbr
def test_equation_has_equationtext():
    names = {v.name for v in read_stat_variables(VALUE_LBR, "Equation")}
    assert "EQ_EquationText" in names

@_needs_lbr
def test_block_name_trim_case_insensitive_fallback():
    # exact name works; a padded/differently-cased name still resolves
    names = {v.name for v in read_stat_variables(VALUE_LBR, " python bridge ")}
    assert "dsPythonCode" in names

def test_bad_blob_raises_valueerror():
    with pytest.raises(ValueError):
        parse_stat_variables(b"not a real blob without sections")


def test_truncated_stat_section_raises_valueerror():
    # markers present and in order, STAT header claims 5 entries, but the section
    # (STAT magic + count only) has no entry bytes -> truncation must raise ValueError,
    # not silently return a short list.
    blob = b"DLOG____TABN____" + b"STAT" + struct.pack(">I", 5) + b"VIEW____"
    with pytest.raises(ValueError):
        parse_stat_variables(blob)
