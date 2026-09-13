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


def test_realreader_table_rows_reads_col1_skips_connector_defaults():
    import attribute_detect as ad
    backend = mock.Mock()
    # col 1 holds the name. row0=attribute 'partType' (kept), row1='inCon0'
    # (connector default -> skipped), row2='' (terminates).
    cells = {
        (0, ad.VAR_COL): {"success": True, "value": "partType"},
        (1, ad.VAR_COL): {"success": True, "value": "inCon0"},
        (2, ad.VAR_COL): {"success": True, "value": ""},
    }
    backend.block_get_value.side_effect = (
        lambda bid, tbl, row, col, as_string=False: cells.get((row, col), {"success": True, "value": ""})
    )
    rr = ad.RealReader(backend)
    rows = rr.table_rows(9, "IVars_ttbl")
    assert rows == [{"variable": "partType", "attribute": "partType"}]
    assert ad.VAR_COL == 1


def test_realreader_pure_connector_block_yields_no_attributes():
    import attribute_detect as ad
    backend = mock.Mock()
    cells = {
        (0, ad.VAR_COL): {"success": True, "value": "inCon0"},
        (1, ad.VAR_COL): {"success": True, "value": "outCon0"},
        (2, ad.VAR_COL): {"success": True, "value": ""},
    }
    backend.block_get_value.side_effect = (
        lambda bid, tbl, row, col, as_string=False: cells.get((row, col), {"success": True, "value": ""})
    )
    rr = ad.RealReader(backend)
    assert rr.table_rows(9, "IVars_ttbl") == []


def test_detect_attributes_entry_wraps_detect(monkeypatch):
    import sys, types
    # Avoid importing the real COM backend: inject a dummy module the entry will import.
    monkeypatch.setitem(sys.modules, "simulation_backend", types.ModuleType("simulation_backend"))
    import attribute_detect as ad

    class FakeRR:
        def __init__(self, backend): pass
        def block_type(self, b): return "Equation(I)"
        def table_rows(self, b, t):
            return [{"variable": "v", "attribute": "partType"}] if t == "IVars_ttbl" else []

    monkeypatch.setattr(ad, "RealReader", FakeRR)
    res = ad.detect_attributes_entry(5)
    assert res["success"] is True
    assert res["reads"] == ["partType"]
    assert res["writes"] == []
    assert res["confidence"] == "high"


def test_detect_attributes_entry_maps_exceptions_to_DETECT_FAILED(monkeypatch):
    import sys, types
    monkeypatch.setitem(sys.modules, "simulation_backend", types.ModuleType("simulation_backend"))
    import attribute_detect as ad

    class BoomRR:
        def __init__(self, backend): pass
        def block_type(self, b): raise RuntimeError("boom")

    monkeypatch.setattr(ad, "RealReader", BoomRR)
    res = ad.detect_attributes_entry(5)
    assert res["success"] is False
    assert res["errorCode"] == "DETECT_FAILED"


def test_realreader_unreadable_cell_is_failclosed_unbound():
    import attribute_detect as ad
    backend = mock.Mock()
    # row 0 reads fine; row 1 read FAILS -> must be marked unbound (fail-closed), not silently dropped.
    cells = {
        (0, ad.VAR_COL): {"success": True, "value": "partType"},
        (1, ad.VAR_COL): {"success": False, "errorCode": "GET_VALUE_FAILED"},
    }
    backend.block_get_value.side_effect = (
        lambda bid, tbl, row, col, as_string=False: cells.get((row, col), {"success": True, "value": ""})
    )
    rr = ad.RealReader(backend)
    rows = rr.table_rows(9, "IVars_ttbl")
    assert {"variable": "?", "attribute": None} in rows
    # and it propagates to low confidence through the pure logic
    from attribute_detect import detect_attributes
    class _R:
        def block_type(self, b): return "Equation(I)"
        def table_rows(self, b, t): return rows if t == "IVars_ttbl" else []
    res = detect_attributes(9, _R())
    assert res["confidence"] == "low"
    assert "?" in res["reads"]
