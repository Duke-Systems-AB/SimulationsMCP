# tests/unit_py/test_resource_pool_config.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import resource_pool_config as rpc
from resource_pool_config import configure_pool, configure_queue_pool, configure_release


class FakeBackend:
    """Models the live COM surface the core needs. known_pools maps a pool name
    to the Resource Pool block id find_resource_pool should return."""
    def __init__(self, known_pools=None, model_open=True, block_ok=True, raise_on=None):
        self.calls = []
        self.cells = {}
        self.numeric = {}
        self._known_pools = dict(known_pools) if known_pools is not None else {"Pool1": 53}
        self._model_open = model_open
        self._block_ok = block_ok
        self._raise_on = raise_on
        self.app = object()

    def find_resource_pool(self, app, pool_name):
        return self._known_pools.get(pool_name, -1)

    def get_extendsim_app(self): return self.app
    def _validate_model_open(self, app):
        return {"success": True} if self._model_open else \
            {"success": False, "errorCode": "MODEL_NOT_OPEN", "error": "no model"}
    def _validate_block_type(self, app, block_id, expected):
        return {"success": True} if self._block_ok else \
            {"success": False, "errorCode": "WRONG_BLOCK_TYPE", "error": expected}

    def _set_var(self, app, block_id, var, value, row=0, col=0, msg=1):
        self.calls.append(("num", block_id, var, value, row, col))
        if self._raise_on == "set": raise RuntimeError("com boom")
        self.numeric[(block_id, var)] = value

    def _set_dialog_var(self, app, block_id, var, value, row=0, col=0):
        self.calls.append(("dlg", block_id, var, value, row, col))
        if self._raise_on == "set": raise RuntimeError("com boom")
        self.cells[(block_id, var, row, col)] = str(value)

    def _get_dialog_string(self, app, block_id, var, row=0, col=0):
        if self._raise_on == "get": raise RuntimeError("com boom")
        return self.cells.get((block_id, var, row, col), "")

    def _get_var(self, app, block_id, var, row=0, col=0):
        return self.numeric.get((block_id, var), "")


def test_configure_pool_sets_name_and_capacity_verified():
    be = FakeBackend()
    res = configure_pool(be, 62, "Pool1", 2)
    assert res["success"] is True
    assert be.cells[(62, "ResourcePoolName", 0, 0)] == "Pool1"
    assert be.numeric[(62, "NumServ")] == 2


def test_configure_pool_rejected_when_name_readback_differs():
    be = FakeBackend()
    orig = be._get_dialog_string
    be._get_dialog_string = lambda app, b, v, row=0, col=0: "Other" if v == "ResourcePoolName" else orig(app, b, v, row, col)
    res = configure_pool(be, 62, "Pool1", 2)
    assert res["success"] is False
    assert res["errorCode"] == "POOL_CONFIG_REJECTED"


def test_configure_queue_pool_sets_mode_table_verified():
    be = FakeBackend()
    res = configure_queue_pool(be, 10, "Pool1", 1)
    assert res["success"] is True
    assert be.numeric[(10, "QueueType_pop")] == 2
    assert be.cells[(10, "ResourceTable", 0, 0)] == "Pool1"
    assert be.cells[(10, "ResourceTable", 0, 1)] == "1"


def test_configure_queue_pool_rejected_when_table_readback_differs():
    be = FakeBackend()
    be._get_dialog_string = lambda app, b, v, row=0, col=0: "" if v == "ResourceTable" else "x"
    res = configure_queue_pool(be, 10, "Pool1", 1)
    assert res["success"] is False
    assert res["errorCode"] == "QUEUE_POOL_REJECTED"


def test_configure_release_links_pool_verified():
    be = FakeBackend()
    res = configure_release(be, 46, "Pool1", pool_block_id=62, qty=1)
    assert res["success"] is True
    assert res["poolBlock"] == 62
    # the real link vars are set directly (not the Serverblocks_pop popup)
    assert be.cells[(46, "ResourcePoolName", 0, 0)] == "Pool1"
    assert be.cells[(46, "ServerBlockNum", 0, 0)] == "62"
    assert be.numeric[(46, "NumReleased_PRM")] == 1


def test_configure_release_resolves_pool_by_name_when_id_omitted():
    be = FakeBackend(known_pools={"Pool1": 53})
    res = configure_release(be, 46, "Pool1", qty=1)   # pool_block_id defaults to None -> scan
    assert res["success"] is True
    assert res["poolBlock"] == 53
    assert be.cells[(46, "ServerBlockNum", 0, 0)] == "53"


def test_configure_release_fails_when_pool_absent():
    be = FakeBackend(known_pools={})   # find_resource_pool returns -1
    res = configure_release(be, 46, "Pool1", qty=1)
    assert res["success"] is False
    assert res["errorCode"] == "RELEASE_POOL_NOT_FOUND"


def test_configure_release_rejected_when_name_readback_differs():
    be = FakeBackend()
    # force the ResourcePoolName readback to differ from what was written
    be._get_dialog_string = lambda app, b, v, row=0, col=0: "Other"
    res = configure_release(be, 46, "Pool1", pool_block_id=62, qty=1)
    assert res["success"] is False
    assert res["errorCode"] == "RELEASE_CONFIG_REJECTED"


def test_cores_propagate_model_and_block_checks():
    assert configure_pool(FakeBackend(model_open=False), 1, "P", 1)["errorCode"] == "MODEL_NOT_OPEN"
    assert configure_pool(FakeBackend(block_ok=False), 1, "P", 1)["errorCode"] == "WRONG_BLOCK_TYPE"


def test_entries_exist_with_expected_arity():
    import inspect
    for fn, params in [
        (rpc.configure_pool_entry, ["block_id", "name", "capacity"]),
        (rpc.configure_queue_pool_entry, ["block_id", "pool_name", "qty"]),
        (rpc.configure_release_entry, ["block_id", "pool_name", "qty"]),
    ]:
        assert list(inspect.signature(fn).parameters) == params
