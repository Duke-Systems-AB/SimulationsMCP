import os, sys, inspect
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def test_simulation_run_uses_setrunparameter():
    import importlib
    try:
        be = importlib.import_module("simulation_backend")
    except Exception:
        import pytest; pytest.skip("no pywin32")
    src = inspect.getsource(be.simulation_run)
    assert "SetRunParameter" in src, "end time must be set via SetRunParameter"
