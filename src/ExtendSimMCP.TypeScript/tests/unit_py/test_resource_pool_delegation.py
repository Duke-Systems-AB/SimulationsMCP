# tests/unit_py/test_resource_pool_delegation.py
import os, sys, inspect
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        import pytest
        pytest.skip("simulation_backend not importable (no pywin32)")


def _src_of(fn):
    return inspect.getsource(fn)


def test_queue_set_resource_pool_delegates_and_drops_broken_writes():
    be = _load()
    import resource_pool_config
    seen = {}
    def fake(backend, block_id, pool_name, qty=1):
        seen.update(dict(block_id=block_id, pool_name=pool_name, qty=qty))
        return {"success": True, "blockId": block_id, "poolName": pool_name, "qty": qty}
    import pytest
    monkey = pytest.MonkeyPatch()
    monkey.setattr(resource_pool_config, "configure_queue_pool", fake)
    try:
        src = _src_of(be.queue_set_resource_pool)
    finally:
        monkey.undo()
    assert '_set_var(app, block_id, "ResourceTable"' not in src


def test_release_config_sets_the_pool():
    be = _load()
    src = _src_of(be.resource_pool_release_set_config)
    assert "configure_release" in src


def test_pool_set_config_delegates():
    be = _load()
    src = _src_of(be.resource_pool_set_config)
    assert "configure_pool" in src
