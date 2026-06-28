# tests/unit_py/test_dialog_table_live.py
import os, sys
import pytest

_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

BLOCK_ID = os.environ.get("ES_EQUATION_BLOCK_ID")


def _extendsim_available():
    try:
        import win32com.client as c
        c.GetActiveObject("ExtendSim.Application")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    BLOCK_ID is None or not _extendsim_available(),
    reason="Needs running ExtendSim + ES_EQUATION_BLOCK_ID of a configured Equation(I) block",
)


def test_live_read_string_cell():
    from dialog_table import table_get_entry
    res = table_get_entry(int(BLOCK_ID), "IVars_ttbl", 0, 1)
    assert res["success"] is True
    assert isinstance(res["value"], str)


def test_live_write_writable_cell_roundtrips():
    # Equation_dtxt holds the ModL code text — a writable string cell.
    from dialog_table import table_get_entry, table_set_entry
    original = table_get_entry(int(BLOCK_ID), "Equation_dtxt", 0, 0)["value"]
    try:
        res = table_set_entry(int(BLOCK_ID), "Equation_dtxt", "// table_set probe", 0, 0)
        assert res["success"] is True
        assert res["value"] == "// table_set probe"
    finally:
        table_set_entry(int(BLOCK_ID), "Equation_dtxt", original, 0, 0)


def test_live_block_controlled_cell_fails_closed():
    # The auto-named OVars_ttbl connector cell rejects writes (proven 2026-06-28).
    from dialog_table import table_set_entry
    res = table_set_entry(int(BLOCK_ID), "OVars_ttbl", "shouldNotStick", 0, 1)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_WRITE_REJECTED"
    assert "actual" in res
