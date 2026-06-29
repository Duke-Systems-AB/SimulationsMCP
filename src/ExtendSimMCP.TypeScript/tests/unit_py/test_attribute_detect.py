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
