# tests/unit_py/test_attribute_detect.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from attribute_detect import detect_attributes

class FakeReader:
    def __init__(self, block_type, ivars=None, ovars=None):
        self._t = block_type
        self._tables = {"IVars_ttbl": ivars or [], "OVars_ttbl": ovars or []}
    def block_type(self, block_id):
        return self._t
    def table_rows(self, block_id, table_name):
        return self._tables[table_name]

def test_equation_block_maps_ivars_to_reads_ovars_to_writes():
    r = FakeReader("Equation(I)",
                   ivars=[{"variable": "v_in", "attribute": "partType"}],
                   ovars=[{"variable": "v_out", "attribute": "cost"}])
    res = detect_attributes(7, r)
    assert res["reads"] == ["partType"]
    assert res["writes"] == ["cost"]
    assert res["confidence"] == "high"

def test_unbound_row_yields_question_mark_and_low_confidence():
    r = FakeReader("Equation(I)",
                   ivars=[{"variable": "v_in", "attribute": None}],
                   ovars=[])
    res = detect_attributes(7, r)
    assert res["reads"] == ["?"]
    assert res["confidence"] == "low"

def test_empty_tables_yield_empty_high_confidence():
    r = FakeReader("Equation(I)", ivars=[], ovars=[])
    res = detect_attributes(7, r)
    assert res["reads"] == [] and res["writes"] == []
    assert res["confidence"] == "high"

def test_non_equation_block_is_confidence_none():
    r = FakeReader("Queue")
    res = detect_attributes(7, r)
    assert res == {"reads": [], "writes": [], "confidence": "none"}

def test_query_equation_with_space_is_treated_as_equation():
    # Real ExtendSim block type name has a space: "Query Equation (I)"
    r = FakeReader("Query Equation (I)",
                   ivars=[{"variable": "v_in", "attribute": "partType"}],
                   ovars=[])
    res = detect_attributes(7, r)
    assert res["reads"] == ["partType"]
    assert res["confidence"] == "high"


# ---------------------------------------------------------------------------
# RealReader tests (Task 3) — COM backend replaced by mock.Mock
# ---------------------------------------------------------------------------
from unittest import mock


def test_realreader_block_type_reads_GetBlockType():
    import attribute_detect as ad
    backend = mock.Mock()
    backend.execute_command.return_value = {"success": True, "result": "Equation(I)"}
    rr = ad.RealReader(backend)
    assert rr.block_type(5) == "Equation(I)"


def test_realreader_table_rows_maps_variable_and_attribute_columns():
    import attribute_detect as ad
    backend = mock.Mock()
    # 1 row: variable col -> "v_in", attribute col -> "partType"; then an empty row stops it
    cells = {
        (0, ad.VAR_COL): {"success": True, "value": "v_in"},
        (0, ad.ATTR_COL): {"success": True, "value": "partType"},
        (1, ad.VAR_COL): {"success": True, "value": ""},     # empty row terminates
    }
    # RealReader reads string cells with as_string=True; the mock accepts the kwarg.
    backend.block_get_value.side_effect = (
        lambda bid, tbl, row, col, as_string=False: cells.get((row, col), {"success": True, "value": ""})
    )
    rr = ad.RealReader(backend)
    rows = rr.table_rows(9, "IVars_ttbl")
    assert rows == [{"variable": "v_in", "attribute": "partType"}]
