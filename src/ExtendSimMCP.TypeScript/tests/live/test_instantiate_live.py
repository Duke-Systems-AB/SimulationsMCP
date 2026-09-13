# tests/live/test_instantiate_live.py
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

def _es_available():
    try:
        import win32com.client
        win32com.client.GetActiveObject("ExtendSim.Application").Request("System", "global0+:0:0:0")
        return True
    except Exception:
        return False

pytestmark = pytest.mark.skipif(not _es_available(), reason="ExtendSim not running")

def test_machine_with_breakdowns_builds_and_runs():
    import simulation_backend as sb
    from instantiate import instantiate_pattern
    sb.execute_command("ActivateApplication();")
    res = instantiate_pattern("machine-with-breakdowns",
                              {"process_time": 1, "mtbf": 30, "mttr": 10})
    assert res.get("success"), f"instantiate_pattern failed: {res}"
    hid = res["hblockId"]
    cS = eS = None
    try:
        contents = [b["blockName"] for b in sb.hierarchy_get_contents(hid)["blocks"] if b.get("library")]
        assert {"Queue", "Activity", "Shutdown"}.issubset(set(contents))
        # smoke: attach Create/Exit to the H-block outer connectors and run
        cS = sb.block_add("Item.lbr", "Create")["blockId"]
        eS = sb.block_add("Item.lbr", "Exit")["blockId"]
        sb.execute_command(f"MakeConnection({cS}, 0, {hid}, {res['interfaceMap']['in']['outerCon']});")
        sb.execute_command(f"MakeConnection({hid}, {res['interfaceMap']['out']['outerCon']}, {eS}, 0);")
        out = sb.simulation_run(end_time=100, include_stats=True)
        # Target the Exit WE connected (the model may hold other Exit blocks).
        exited = next(e["itemsExited"] for e in out["statistics"]["exitStatistics"]
                      if e["blockId"] == eS)
        assert exited > 0
    finally:
        if eS is not None:
            sb.block_remove(eS)
        if cS is not None:
            sb.block_remove(cS)
        sb.block_remove(hid)
