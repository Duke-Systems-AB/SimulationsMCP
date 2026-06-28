# tests/unit_py/test_dialog_table.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from dialog_table import table_get, table_set


class FakeBackend:
    """Test double for the simulation_backend module surface that
    dialog_table depends on. Records calls and scripts return values."""

    def __init__(self, get_returns="", model_open=True, raise_on=None):
        self.calls = []
        self._get_returns = get_returns      # str OR list of strs (consumed in order)
        self._model_open = model_open
        self._raise_on = raise_on            # None | "get" | "set"
        self.app = object()

    def get_extendsim_app(self):
        return self.app

    def _validate_model_open(self, app):
        if self._model_open:
            return {"success": True}
        return {"success": False, "errorCode": "MODEL_NOT_OPEN", "error": "no model"}

    def _get_var(self, app, block_id, var_name, row, col):
        self.calls.append(("get", block_id, var_name, row, col))
        if self._raise_on == "get":
            raise RuntimeError("com boom")
        if isinstance(self._get_returns, list):
            return self._get_returns.pop(0)
        return self._get_returns

    def _set_var_string(self, app, block_id, var_name, value, row, col):
        self.calls.append(("set", block_id, var_name, value, row, col))
        if self._raise_on == "set":
            raise RuntimeError("com boom")


def test_table_get_returns_string_cell():
    be = FakeBackend(get_returns="inCon0")
    res = table_get(be, 3, "IVars_ttbl", 0, 1)
    assert res["success"] is True
    assert res["value"] == "inCon0"
    assert res["blockId"] == 3
    assert res["variableName"] == "IVars_ttbl"
    assert res["row"] == 0 and res["col"] == 1
    assert be.calls == [("get", 3, "IVars_ttbl", 0, 1)]


def test_table_get_propagates_model_not_open():
    be = FakeBackend(model_open=False)
    res = table_get(be, 3, "IVars_ttbl", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "MODEL_NOT_OPEN"


def test_table_get_com_failure_is_fail_closed():
    be = FakeBackend(raise_on="get")
    res = table_get(be, 3, "IVars_ttbl", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_READ_FAILED"
    assert res["blockId"] == 3


def test_table_set_succeeds_when_readback_matches():
    # _get_var is only called once (the verification read) and returns the written value
    be = FakeBackend(get_returns="partType")
    res = table_set(be, 5, "AttribsTable_ttbl", "partType", 0, 0)
    assert res["success"] is True
    assert res["value"] == "partType"
    # set happened, then a verification read happened
    assert be.calls == [
        ("set", 5, "AttribsTable_ttbl", "partType", 0, 0),
        ("get", 5, "AttribsTable_ttbl", 0, 0),
    ]


def test_table_set_rejected_when_readback_differs():
    be = FakeBackend(get_returns="outCon0")  # block-controlled cell ignored the write
    res = table_set(be, 3, "OVars_ttbl", "testAttr", 0, 1)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_WRITE_REJECTED"
    assert res["requested"] == "testAttr"
    assert res["actual"] == "outCon0"


def test_table_set_com_failure_is_fail_closed():
    be = FakeBackend(raise_on="set")
    res = table_set(be, 3, "OVars_ttbl", "x", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_WRITE_FAILED"
    assert res["blockId"] == 3


def test_table_set_forwards_value_to_set_var_string():
    # Escaping is _set_var_string's responsibility; the core must forward the
    # raw value so the backend can escape it. Verify the forwarded argument.
    be = FakeBackend(get_returns='a"b')
    res = table_set(be, 7, "Equation_dtxt", 'a"b', 1, 2)
    assert res["success"] is True
    set_call = [c for c in be.calls if c[0] == "set"][0]
    assert set_call == ("set", 7, "Equation_dtxt", 'a"b', 1, 2)


def test_table_set_readback_failure_is_distinct_from_write_failure():
    # Write succeeds, but the verification read raises. The write may have
    # persisted, so this must NOT be reported as TABLE_WRITE_FAILED.
    be = FakeBackend(raise_on="get")
    res = table_set(be, 3, "OVars_ttbl", "x", 0, 0)
    assert res["success"] is False
    assert res["errorCode"] == "TABLE_READ_FAILED"
    # the write was attempted before the failing readback
    assert be.calls[0] == ("set", 3, "OVars_ttbl", "x", 0, 0)
    assert res["blockId"] == 3
