# src/attribute_detect.py
"""Attribute detection for equation blocks (M6 step 1).

Pure mapping over an injected reader; RealReader wraps the live COM backend.
A block's in-variable table (IVars_ttbl) yields reads, the out-variable table
(OVars_ttbl) yields writes. See spec 2026-06-28-m6-attribute-detection-design.md.
"""
_EQUATION_TYPES = {"Equation(I)", "Query Equation (I)", "Queue Equation"}


def _attrs_from_rows(rows):
    """Return (attribute_list, saw_unbound) from a variable table's rows."""
    attrs, saw_unbound = [], False
    for row in rows:
        attr = row.get("attribute")
        if attr:  # None or "" both mean "no attribute bound"
            attrs.append(attr)
        else:
            saw_unbound = True
    return attrs, saw_unbound


def detect_attributes(block_id, reader):
    """Derive {reads, writes, confidence} for a block via the injected reader."""
    if reader.block_type(block_id) not in _EQUATION_TYPES:
        return {"reads": [], "writes": [], "confidence": "none"}

    reads, r_unbound = _attrs_from_rows(reader.table_rows(block_id, "IVars_ttbl"))
    writes, w_unbound = _attrs_from_rows(reader.table_rows(block_id, "OVars_ttbl"))
    if r_unbound:
        reads.append("?")
    if w_unbound:
        writes.append("?")
    confidence = "low" if (r_unbound or w_unbound) else "high"
    return {"reads": reads, "writes": writes, "confidence": confidence}


def detect_attributes_entry(block_id, model_id=None):
    """MCP entry point: detect read/written attributes of a block in the live model."""
    import simulation_backend as backend
    try:
        return {"success": True, **detect_attributes(block_id, RealReader(backend))}
    except Exception as e:
        return {"success": False, "errorCode": "DETECT_FAILED", "error": str(e)}


# Columns holding the variable name / bound attribute name in IVars_ttbl/OVars_ttbl.
# PLACEHOLDERS (0/1) pending live discovery against a configured Equation(I) block —
# the live test (Task 4) confirms these before they ship.
VAR_COL = 0
ATTR_COL = 1


class RealReader:
    """Reads block type + variable-table rows from the live COM backend.
    Effect-verifies each read (never trusts an unread cell). String-table cells
    are read with as_string=True (they are text, not numbers)."""
    def __init__(self, backend):
        self._b = backend

    def block_type(self, block_id):
        r = self._b.execute_command(
            f"globalStr0 = GetBlockType({block_id});", get_result=True, result_type="string")
        if not r.get("success"):
            return ""
        return (r.get("result") or "").strip()

    def table_rows(self, block_id, table_name):
        rows, row = [], 0
        while True:
            var_cell = self._b.block_get_value(block_id, table_name, row, VAR_COL, as_string=True)
            if not var_cell.get("success"):
                break
            var = (str(var_cell.get("value")) if var_cell.get("value") is not None else "").strip()
            if var == "" or var == "nan":          # empty row terminates the table
                break
            attr_cell = self._b.block_get_value(block_id, table_name, row, ATTR_COL, as_string=True)
            attr = (str(attr_cell.get("value")).strip()
                    if attr_cell.get("success") and attr_cell.get("value") not in (None, "", "nan")
                    else None)
            rows.append({"variable": var, "attribute": attr})
            row += 1
        return rows
