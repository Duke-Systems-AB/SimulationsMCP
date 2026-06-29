# src/attribute_detect.py
"""Attribute detection for equation blocks (M6 step 1).

Pure mapping over an injected reader; RealReader wraps the live COM backend.
A block's in-variable table (IVars_ttbl) yields reads, the out-variable table
(OVars_ttbl) yields writes. See spec 2026-06-28-m6-attribute-detection-design.md.
"""
_EQUATION_TYPES = {"Equation(I)", "Query Equation(I)", "Queue Equation"}


def _attrs_from_rows(rows):
    """Return (attribute_list, saw_unbound) from a variable table's rows."""
    attrs, saw_unbound = [], False
    for row in rows:
        attr = row.get("attribute")
        if attr:
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
