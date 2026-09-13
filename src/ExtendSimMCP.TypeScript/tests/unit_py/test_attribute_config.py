# tests/unit_py/test_attribute_config.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import attribute_config
from attribute_config import set_attribute, ATTR_NAME_COL, ATTR_VALUE_COL


class FakeBackend:
    """Test double for the simulation_backend surface attribute_config needs."""
    def __init__(self, name_rb=None, value_rb="2", model_open=True,
                 block_ok=True, raise_on=None):
        self.calls = []
        self._name_rb = name_rb          # what the name cell reads back (defaults to written)
        self._value_rb = value_rb
        self._model_open = model_open
        self._block_ok = block_ok
        self._raise_on = raise_on         # None | "set" | "get"
        self._written_name = None
        self.app = object()

    def get_extendsim_app(self):
        return self.app

    def _validate_model_open(self, app):
        return {"success": True} if self._model_open else \
            {"success": False, "errorCode": "MODEL_NOT_OPEN", "error": "no model"}

    def _validate_block_type(self, app, block_id, expected):
        return {"success": True} if self._block_ok else \
            {"success": False, "errorCode": "WRONG_BLOCK_TYPE", "error": expected}

    def _set_var_string(self, app, block_id, var, value, row, col):
        self.calls.append(("set_str", block_id, var, value, row, col))
        if self._raise_on == "set":
            raise RuntimeError("com boom")
        self._written_name = value

    def _set_var(self, app, block_id, var, value, row, col):
        self.calls.append(("set_num", block_id, var, value, row, col))
        if self._raise_on == "set":
            raise RuntimeError("com boom")

    def _get_var(self, app, block_id, var, row, col):
        self.calls.append(("get", block_id, var, row, col))
        if self._raise_on == "get":
            raise RuntimeError("com boom")
        if col == ATTR_NAME_COL:
            return self._written_name if self._name_rb is None else self._name_rb
        return self._value_rb


def test_set_attribute_writes_name_and_value_and_verifies():
    be = FakeBackend(value_rb="2")
    res = set_attribute(be, 5, "partType", 2, "constant", 0)
    assert res["success"] is True
    assert res["attributeName"] == "partType"
    assert res["blockId"] == 5
    assert ("set_str", 5, "AttribsTable_ttbl", "partType", 0, ATTR_NAME_COL) in be.calls
    assert ("set_num", 5, "AttribsTable_ttbl", 2, 0, ATTR_VALUE_COL) in be.calls
    assert any(c[0] == "get" for c in be.calls)


def test_set_attribute_rejected_when_name_readback_differs():
    be = FakeBackend(name_rb="")
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_WRITE_REJECTED"
    assert res["requested"] == "partType"
    assert res["actual"] == ""


def test_set_attribute_write_failure_is_fail_closed():
    be = FakeBackend(raise_on="set")
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_WRITE_FAILED"


def test_set_attribute_readback_failure_is_distinct():
    be = FakeBackend(raise_on="get")
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_READ_FAILED"
    assert be.calls[0][0] == "set_str"


def test_set_attribute_unsupported_value_type_no_com_write():
    be = FakeBackend()
    res = set_attribute(be, 3, "partType", 1, "connector", 0)
    assert res["success"] is False
    assert res["errorCode"] == "ATTRIBUTE_VALUETYPE_UNSUPPORTED"
    assert be.calls == []


def test_set_attribute_propagates_wrong_block_type():
    be = FakeBackend(block_ok=False)
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "WRONG_BLOCK_TYPE"


def test_set_attribute_propagates_model_not_open():
    be = FakeBackend(model_open=False)
    res = set_attribute(be, 3, "partType", 1, "constant", 0)
    assert res["success"] is False
    assert res["errorCode"] == "MODEL_NOT_OPEN"


def test_entry_is_callable_with_expected_arity():
    import inspect
    assert callable(attribute_config.set_attribute_entry)
    params = list(inspect.signature(attribute_config.set_attribute_entry).parameters)
    assert params == ["block_id", "name", "value", "value_type", "row"]
