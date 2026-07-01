# src/attribute_config.py
"""Set-block attribute configuration via AttribsTable_ttbl (ExtendSim 2024).

Pure core takes an injected `backend` (simulation_backend in production, a
FakeBackend in tests) and reuses its MODL helpers. Write is read-back verified
and fail-closed. The old single-param path (AttributeName_prm / ValueType_pop /
ConstantValue_prm) does NOT exist on the 2024 Set block, which configures
attributes through the AttribsTable_ttbl dialog table.
"""

# Column layout of AttribsTable_ttbl on the Set block.
# Pinned by live discovery (see plan Task 0). Named so tests bind to the symbol,
# not the number, and a discovery adjustment does not churn the tests.
ATTR_NAME_COL = 0       # string column: attribute name
ATTR_VALUE_COL = 1      # value column: constant value
ATTR_TYPE_COL = None    # value-source popup column, or None if absent
_CONSTANT_CODE = 1      # popup code for "constant" (used only if ATTR_TYPE_COL set)


def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def _num_eq(a, b):
    """True if a and b are equal as numbers (tolerant), else as strings."""
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a) == str(b)


def set_attribute(backend, block_id, name, value, value_type="constant", row=0):
    """Configure a Set block to assign `value` to attribute `name` (constant).

    Effect-verified: the name (and value) are read back and compared; a write
    that does not persist returns ATTRIBUTE_WRITE_REJECTED, never false success.
    """
    app = backend.get_extendsim_app()
    model_check = backend._validate_model_open(app)
    if not model_check.get("success"):
        return model_check
    type_check = backend._validate_block_type(app, block_id, "Set")
    if not type_check.get("success"):
        return type_check
    if value_type != "constant":
        return _err("ATTRIBUTE_VALUETYPE_UNSUPPORTED",
                    f"value_type '{value_type}' not supported yet (only 'constant')",
                    blockId=block_id, attributeName=name, valueType=value_type)
    try:
        backend._set_var_string(app, block_id, "AttribsTable_ttbl", str(name), row, ATTR_NAME_COL)
        if ATTR_TYPE_COL is not None:
            backend._set_var(app, block_id, "AttribsTable_ttbl", _CONSTANT_CODE, row, ATTR_TYPE_COL)
        backend._set_var(app, block_id, "AttribsTable_ttbl", value, row, ATTR_VALUE_COL)
    except Exception as e:
        return _err("ATTRIBUTE_WRITE_FAILED", str(e),
                    blockId=block_id, attributeName=name, row=row)
    try:
        name_rb = backend._get_var(app, block_id, "AttribsTable_ttbl", row, ATTR_NAME_COL)
        value_rb = backend._get_var(app, block_id, "AttribsTable_ttbl", row, ATTR_VALUE_COL)
    except Exception as e:
        return _err("ATTRIBUTE_READ_FAILED", str(e),
                    blockId=block_id, attributeName=name, row=row)
    if str(name_rb) != str(name):
        return _err("ATTRIBUTE_WRITE_REJECTED",
                    f"attribute name write to block {block_id} row {row} did not persist",
                    blockId=block_id, attributeName=name, row=row,
                    requested=str(name), actual=str(name_rb))
    if not _num_eq(value_rb, value):
        return _err("ATTRIBUTE_WRITE_REJECTED",
                    f"attribute value write to block {block_id} row {row} did not persist",
                    blockId=block_id, attributeName=name, row=row,
                    requested=str(value), actual=str(value_rb))
    return {"success": True, "blockId": block_id, "attributeName": name,
            "value": value, "valueType": value_type, "row": row,
            "nameActual": str(name_rb), "valueActual": str(value_rb)}


def set_attribute_entry(block_id, name, value, value_type="constant", row=0):
    import simulation_backend as backend
    return set_attribute(backend, block_id, name, value, value_type, row)
