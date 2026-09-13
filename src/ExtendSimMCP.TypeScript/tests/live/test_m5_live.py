# tests/live/test_m5_live.py
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

def _smoke_through(sb, in_hid, in_outer, out_hid, out_outer):
    cS = sb.block_add("Item.lbr", "Create")["blockId"]
    eS = sb.block_add("Item.lbr", "Exit")["blockId"]
    try:
        sb.execute_command(f"MakeConnection({cS}, 0, {in_hid}, {in_outer});")
        sb.execute_command(f"MakeConnection({out_hid}, {out_outer}, {eS}, 0);")
        out = sb.simulation_run(end_time=100, include_stats=True)
        return next(e["itemsExited"] for e in out["statistics"]["exitStatistics"] if e["blockId"] == eS)
    finally:
        sb.block_remove(eS); sb.block_remove(cS)

def test_simple_machine_instantiates_and_runs():
    import simulation_backend as sb
    from instantiate import instantiate_pattern
    sb.execute_command("ActivateApplication();")
    res = instantiate_pattern("simple-machine", {"process_time": 1})
    assert res.get("success"), res
    hid = res["hblockId"]
    try:
        exited = _smoke_through(sb, hid, res["interfaceMap"]["in"]["outerCon"],
                                hid, res["interfaceMap"]["out"]["outerCon"])
        assert exited > 0
    finally:
        sb.block_remove(hid)

def test_two_stage_line_composes_and_runs():
    import simulation_backend as sb
    from compose import compose_flow
    sb.execute_command("ActivateApplication();")
    p = os.path.join(_SRC, "..", "patterns", "flows", "two-stage-line.json")
    with open(p, encoding="utf-8") as f:
        flow = json.load(f)
    res = compose_flow(flow)
    assert res.get("success"), res
    s1, s2 = res["instances"]["s1"], res["instances"]["s2"]
    try:
        exited = _smoke_through(sb, s1["hblockId"], s1["interfaceMap"]["in"]["outerCon"],
                                s2["hblockId"], s2["interfaceMap"]["out"]["outerCon"])
        assert exited > 0
    finally:
        sb.block_remove(s2["hblockId"]); sb.block_remove(s1["hblockId"])
