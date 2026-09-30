# src/queue_block_config.py
"""Queue Matching and Queue Equation configuration over COM (spec 2026-09-30).

Only what was proved live on ExtendSim 2024 and 2026 (block learning recipes, 2026-09-30):

Queue Matching matches on an attribute only with VARIABLE groups. With GT_pop = 1 (fixed)
and one group the block ignores the attribute (its ModL sets matchAttribVal = 1.0), so
items pair regardless of their values. GT_pop's handler resets the match attribute name to
"None", so GT_pop goes first, then the statics attribNamesChosen[0] / attribType[0].

Queue Equation: a new block has one input row (inCon0, "Connector 0") and one output row
(iRank_0). The input row is turned into an attribute input by writing the internal name and
type AND the name cell of the dialog table: at run start CheckData copies names FROM
iVars_ttbl / oVars_ttbl into the internal arrays, and a new block's output-name cell stays
empty until its dialog is opened - so without filling it every run stops with "A name needs
to be specified for the output variable in row 0" (measured 2026-09-30). Rows cannot be
added from COM (iVars_NumTableRows + OPENMODELMSG raises an out-of-bounds box).

Array statics (attribNamesChosen, attribType, the Queue Equation iVars_*/oVars_ttbl rows) go
through raw SetDialogVariable / GetDialogVariable (block_set_value does not write them,
measured 2026-09-30) and are checked against GetDimensionByName first - never read or written
at or beyond it. NumQueues_prm, ReleaseOptions_pop and GT_pop are SCALAR dialog parameters:
GetDimensionByName describes an array's missing left dimension, not a scalar's, so they go
through backend.block_set_value (SetVariableNumeric + read-back) instead, the same path the
block-learning recipes used to probe them. Every write is read back; a write that did not take
stops the configuration with SET_VALUE_FAILED, never a cheerful success.
"""
from attribute_config import ATTRIB_TYPE_VALUE, _NAME_OK, _RESERVED, _err, _num_eq

GT_VARIABLE = 2                 # Queue Matching ModL: constant GT_VARIABLE is 2
RELEASE_OPTIONS = {"matchedOnly": 1, "allInGroup": 2}   # ReleaseOptions_pop popup order
I_VAR_NAME_COL = 1              # Equation.h: constant I_VAR_NAME_COL is 1
O_VAR_NAME_COL = 1              # Equation.h: constant O_VAR_NAME_COL is 1
GUIDE_HINT = "see block_search with detail: true for the block's guide"


class Com:
    """The ExtendSim operations this module needs, over the backend's COM helpers."""

    def __init__(self, backend, app):
        self._b, self._app = backend, app

    def dim(self, bid, var):
        self._app.Execute("global0 = -12345;")
        self._app.Execute(f'global0 = GetDimensionByName({bid}, "{var}");')
        return int(self._b.parse_float(self._app.Request("System", "global0+:0:0:0")))

    def get(self, bid, var, row=0, col=0):
        self._app.Execute('globalStr0 = "";')
        self._app.Execute(f'globalStr0 = GetDialogVariable({bid}, "{var}", {row}, {col});')
        return self._b._read_str0(self._app)

    def set(self, bid, var, value, row=0, col=0):
        v = self._b._escape_modl_string(str(value))
        self._app.Execute(f'SetDialogVariable({bid}, "{var}", "{v}", {row}, {col});')

    def set_value(self, bid, var, value):
        return self._b.block_set_value(bid, var, value)


def _name_error(param, name):
    if not isinstance(name, str) or not _NAME_OK.match(name) or name.lower() in _RESERVED:
        return _err("INVALID_PARAMETER",
                    f"{param} {name!r} is not a valid attribute name (at most 15 characters, no spaces "
                    "or quotes, not starting with '_', not 'None')")
    return None


def _write(com, bid, var, value, row=0, col=0):
    """Write one ARRAY static and read it back. Returns an error dict or None.

    Dimension-guarded: only for statics GetDimensionByName actually describes (attribute
    arrays, the Queue Equation table rows). Scalar dialog parameters (NumQueues_prm,
    ReleaseOptions_pop, GT_pop) go through com.set_value instead - see module docstring.
    """
    if com.dim(bid, var) <= row:
        return _err("SET_VALUE_FAILED", f"block {bid} has no row {row} in {var}; nothing written")
    com.set(bid, var, value, row, col)
    got = com.get(bid, var, row, col)
    same = _num_eq(got, value) if isinstance(value, (int, float)) else got.strip() == str(value).strip()
    if not same:
        return _err("SET_VALUE_FAILED", f"write of {var}[{row}][{col}] did not take: the block kept "
                                        f"{got!r}, not {value!r}", blockId=bid, variableName=var)
    return None


def configure_queue_matching(com, bid, config):
    """numQueues, groupType ("variable" only), matchAttribute (numeric attribute), releaseOptions."""
    group = config.get("groupType", "variable")
    if group != "variable":
        return _err("INVALID_PARAMETER",
                    f"groupType {group!r} is not supported: only 'variable' is proved (fixed groups with "
                    f"one group ignore the attribute); {GUIDE_HINT}")
    n = config.get("numQueues")
    if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 1):
        return _err("INVALID_PARAMETER", f"numQueues must be a whole number of at least 1, got {n!r}")
    attr = config.get("matchAttribute")
    if attr is not None and (e := _name_error("matchAttribute", attr)):
        return e
    rel = config.get("releaseOptions")
    if rel is not None and rel not in RELEASE_OPTIONS:
        return _err("INVALID_PARAMETER",
                    f"releaseOptions must be one of {sorted(RELEASE_OPTIONS)}, got {rel!r}")

    applied = {}
    if n is not None:
        r = com.set_value(bid, "NumQueues_prm", n)
        if not r.get("success"):
            return r
        applied["numQueues"] = n
    if attr is not None:
        # GT_pop first: its handler resets the match attribute name to "None".
        r = com.set_value(bid, "GT_pop", GT_VARIABLE)
        if not r.get("success"):
            return r
        applied["groupType"] = "variable"
        for var, value in (("attribNamesChosen", attr), ("attribType", ATTRIB_TYPE_VALUE)):
            if (e := _write(com, bid, var, value)):
                return e
        applied["matchAttribute"] = attr
    if rel is not None:
        r = com.set_value(bid, "ReleaseOptions_pop", RELEASE_OPTIONS[rel])
        if not r.get("success"):
            return r
        applied["releaseOptions"] = rel
    return {"success": True, "blockId": bid, "applied": applied}


def prepare_queue_equation(com, bid, input_attribute):
    """Optionally turn the single input row into an attribute input; always fill the output-name
    cells from oVars_Names (rows the table has, non-blank names)."""
    if input_attribute is not None:
        if (e := _name_error("inputAttribute", input_attribute)):
            return e
        rows = com.dim(bid, "iVars_Names")
        if rows != 1 or com.dim(bid, "iVars_Types_str") < 1 or com.dim(bid, "iVars_ttbl") < 1:
            return _err("INVALID_PARAMETER",
                        f"inputAttribute needs a Queue Equation with exactly one input row; this block has "
                        f"{rows}. Rows cannot be added from COM - {GUIDE_HINT}")
        name = com.get(bid, "iVars_Names", 0).strip()
        kind = com.get(bid, "iVars_Types_str", 0).strip()
        if kind == "Attribute" and name != input_attribute:
            return _err("INVALID_PARAMETER",
                        f"the input row already reads the attribute {name!r}; one input row only - "
                        f"{GUIDE_HINT}")
        if kind != "Attribute" and not kind.startswith("Connector"):
            return _err("INVALID_PARAMETER",
                        f"the input row is of type {kind!r}, not the default connector row; it is left "
                        f"as it is - {GUIDE_HINT}")
        for var, value, col in (("iVars_Names", input_attribute, 0), ("iVars_Types_str", "Attribute", 0),
                                ("iVars_ttbl", input_attribute, I_VAR_NAME_COL)):
            if (e := _write(com, bid, var, value, 0, col)):
                return e
    filled = 0
    table_rows = com.dim(bid, "oVars_ttbl")
    for r in range(min(com.dim(bid, "oVars_Names"), table_rows)):
        out = com.get(bid, "oVars_Names", r).strip()
        if not out:
            continue
        if (e := _write(com, bid, "oVars_ttbl", out, r, O_VAR_NAME_COL)):
            return e
        filled += 1
    return {"success": True, "inputAttribute": input_attribute, "outputNamesFilled": filled}
