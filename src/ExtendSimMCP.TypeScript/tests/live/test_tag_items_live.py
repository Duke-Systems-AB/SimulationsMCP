# tests/live/test_tag_items_live.py
import os, sys
import pytest
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    import simulation_backend as be
    from instantiate import instantiate_pattern
    from attribute_config import ATTR_NAME_COL, ATTR_VALUE_COL
    _HAVE_COM = be.get_extendsim_app() is not None
except Exception:
    _HAVE_COM = False

pytestmark = pytest.mark.skipif(not _HAVE_COM, reason="ExtendSim COM not available")


def test_tag_items_writes_partType_live():
    res = instantiate_pattern("tag-items", {"partType": 2})
    assert res["success"], res
    set_id = res["internalBlockIds"]["set"]
    name = be.block_get_value(set_id, "AttribsTable_ttbl", 0, ATTR_NAME_COL, as_string=True)
    assert name["success"] and str(name["value"]).strip() == "partType", name
