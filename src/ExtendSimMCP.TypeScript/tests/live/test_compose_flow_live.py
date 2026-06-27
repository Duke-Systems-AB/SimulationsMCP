# tests/live/test_compose_flow_live.py
import os, sys
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

def test_two_machine_flow_builds_and_runs():
    import simulation_backend as sb
    from compose import compose_flow
    sb.execute_command("ActivateApplication();")
    flow = {
        "id": "line2",
        "instances": [
            {"ref": "m1", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
            {"ref": "m2", "pattern": "machine-with-breakdowns", "params": {"process_time": 1, "mtbf": 30, "mttr": 10}},
        ],
        "wiring": [{"from": "m1.out", "to": "m2.in"}],
    }
    res = compose_flow(flow)
    assert res.get("success"), f"compose_flow failed: {res}"
    m1, m2 = res["instances"]["m1"], res["instances"]["m2"]
    cS = eS = None
    try:
        cS = sb.block_add("Item.lbr", "Create")["blockId"]
        eS = sb.block_add("Item.lbr", "Exit")["blockId"]
        sb.execute_command(f"MakeConnection({cS}, 0, {m1['hblockId']}, {m1['interfaceMap']['in']['outerCon']});")
        sb.execute_command(f"MakeConnection({m2['hblockId']}, {m2['interfaceMap']['out']['outerCon']}, {eS}, 0);")
        out = sb.simulation_run(end_time=100, include_stats=True)
        exited = next(e["itemsExited"] for e in out["statistics"]["exitStatistics"]
                      if e["blockId"] == eS)
        assert exited > 0
    finally:
        if eS is not None:
            sb.block_remove(eS)
        if cS is not None:
            sb.block_remove(cS)
        sb.block_remove(m2["hblockId"])
        sb.block_remove(m1["hblockId"])
