# src/dialog_table.py
"""String-table cell read/write for ExtendSim dialog tables (*_ttbl).

Pure cores take an injected `backend` (the simulation_backend module in
production, a FakeBackend in tests) and reuse its MODL helpers
_get_var / _set_var_string. table_set is read-back verified and fail-closed.
"""


def _err(code, message, **extra):
    result = {"success": False, "errorCode": code, "error": message}
    result.update(extra)
    return result


def table_get(backend, block_id, var_name, row=0, col=0):
    app = backend.get_extendsim_app()
    model_check = backend._validate_model_open(app)
    if not model_check.get("success"):
        return model_check
    try:
        value = backend._get_var(app, block_id, var_name, row, col)
    except Exception as e:
        return _err("TABLE_READ_FAILED", str(e),
                    blockId=block_id, variableName=var_name, row=row, col=col)
    return {"success": True, "blockId": block_id, "variableName": var_name,
            "row": row, "col": col, "value": str(value)}


def table_set(backend, block_id, var_name, value, row=0, col=0):
    raise NotImplementedError("table_set is implemented in Task 2")
