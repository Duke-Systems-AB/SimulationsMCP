# src/attribute_detect.py
"""Attribute detection for equation blocks (M6 step 1).

Pure mapping over an injected reader; RealReader wraps the live COM backend.
A block's in-variable table (IVars_ttbl) yields reads, the out-variable table
(OVars_ttbl) yields writes. See spec 2026-06-28-m6-attribute-detection-design.md.
"""
import re

_EQUATION_TYPES = {"Equation(I)", "Query Equation (I)", "Queue Equation"}

# Column holding the variable name in IVars_ttbl/OVars_ttbl (confirmed live 2026-06-29).
VAR_COL = 1

# Connector-default names (inCon0, outCon3, ...) mean "reads/writes via the connector,
# not an item attribute" -> skipped during detection.
_CONNECTOR_DEFAULT = re.compile(r"^(in|out)Con\d+$", re.IGNORECASE)


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
    """MCP entry point: detect read/written attributes of a block in the live model.

    model_id is accepted for API forward-compatibility but currently ignored
    (detection always operates on the active model).
    """
    import simulation_backend as backend
    try:
        return {"success": True, **detect_attributes(block_id, RealReader(backend))}
    except Exception as e:
        return {"success": False, "errorCode": "DETECT_FAILED", "error": str(e)}


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
            cell = self._b.block_get_value(block_id, table_name, row, VAR_COL, as_string=True)
            if not cell.get("success"):
                # fail-closed: an unreadable row is "unknown", never silently dropped
                rows.append({"variable": "?", "attribute": None})
                break
            name = (str(cell.get("value")) if cell.get("value") is not None else "").strip()
            if name == "" or name == "nan":          # empty name terminates the table
                break
            if not _CONNECTOR_DEFAULT.match(name):    # connector default = not an attribute
                rows.append({"variable": name, "attribute": name})
            row += 1
        return rows
