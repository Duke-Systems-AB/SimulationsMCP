# tests/live/test_resource_machine_live.py
import os, sys
import pytest
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

def _es():
    try:
        import win32com.client
        win32com.client.GetActiveObject("ExtendSim.Application").Request("System", "global0+:0:0:0")
        return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _es(), reason="ExtendSim not running")


def test_resource_machine_runs_and_flows():
    import simulation_backend as sb
    from instantiate import instantiate_pattern
    sb.execute_command("ActivateApplication();")
    res = instantiate_pattern("resource-machine", {"process_time": 1, "capacity": 2, "pool_name": "Pool1"})
    assert res.get("success"), res
    hid = res["hblockId"]
    cS = eS = None
    try:
        cS = sb.block_add("Item.lbr", "Create")["blockId"]
        eS = sb.block_add("Item.lbr", "Exit")["blockId"]
        sb.execute_command(f"MakeConnection({cS}, 0, {hid}, {res['interfaceMap']['in']['outerCon']});")
        sb.execute_command(f"MakeConnection({hid}, {res['interfaceMap']['out']['outerCon']}, {eS}, 0);")
        out = sb.simulation_run(end_time=50, include_stats=True)
        exited = next(e["itemsExited"] for e in out["statistics"]["exitStatistics"] if e["blockId"] == eS)
        assert exited > 0, f"no items flowed: {out}"
    finally:
        if eS is not None: sb.block_remove(eS)
        if cS is not None: sb.block_remove(cS)
        sb.block_remove(hid)
