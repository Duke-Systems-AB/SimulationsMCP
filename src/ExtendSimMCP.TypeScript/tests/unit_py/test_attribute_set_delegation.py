# tests/unit_py/test_attribute_set_delegation.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _load_backend():
    import importlib
    try:
        return importlib.import_module("simulation_backend")
    except Exception:
        import pytest
        pytest.skip("simulation_backend not importable (no pywin32 in this env)")


def test_attribute_set_delegates_to_core(monkeypatch):
    be = _load_backend()
    import attribute_config
    captured = {}

    def fake_core(backend, block_id, name, value, value_type="constant", row=0):
        captured.update(dict(block_id=block_id, name=name, value=value,
                             value_type=value_type, row=row))
        return {"success": True, "blockId": block_id, "attributeName": name,
                "value": value, "valueType": value_type}

    monkeypatch.setattr(attribute_config, "set_attribute", fake_core)
    res = be.attribute_set(7, "partType", value_type="constant", value=2)
    assert res["success"] is True
    assert res["attributeName"] == "partType"
    assert captured == dict(block_id=7, name="partType", value=2,
                            value_type="constant", row=0)


def _attribute_set_source():
    """Text of just the attribute_set function, read from the source file.

    Uses no COM import, so this regression guard runs in every environment
    (including headless CI without pywin32). Slices to the next top-level def
    so attribute_get (which legitimately still references AttributeName_prm) is
    not scanned.
    """
    path = os.path.join(_SRC, "simulation_backend.py")
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("def attribute_set("))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("def ")), len(lines))
    return "".join(lines[start:end])


def test_attribute_set_no_longer_references_dead_dialog_vars():
    src = _attribute_set_source()
    for dead in ("AttributeName_prm", "ValueType_pop", "ConstantValue_prm"):
        assert dead not in src, f"{dead} still referenced in attribute_set"
