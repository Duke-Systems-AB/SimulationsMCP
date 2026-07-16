# tests/unit_py/test_mine_candidates_offline.py
"""Unit tests for the mine_candidates OFFLINE path (psgPath JSON -> candidates).
This path never touches COM, so it runs without ExtendSim. Importing
simulation_backend requires pywin32 installed but no running ExtendSim.
"""
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import simulation_backend as b


def _fixture_psg():
    return {"modelName": "demo.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None,
         "nodes": [{"ref": "b1", "blockId": 1, "lib": "", "type": "Hierarchical",
                    "isHBlock": True, "scopeId": "h1", "params": {}}],
         "edges": [], "boundaryEdges": []},
        {"scopeId": "h1", "kind": "hblock", "parentScopeId": "root",
         "hblockType": "pure", "label": "Machine",
         "nodes": [
            {"ref": "b2", "blockId": 2, "lib": "Item", "type": "Queue",
             "isHBlock": False, "params": {}},
            {"ref": "b3", "blockId": 3, "lib": "Item", "type": "Activity",
             "isHBlock": False, "params": {"D": 5}},
         ],
         "edges": [{"from": "b2.outCon0", "to": "b3.inCon0"}],
         "boundaryEdges": [{"internal": "b2.inCon0", "crosses": "inlet",
                            "boundaryConnector": "inCon0"}]},
    ]}


def test_mine_candidates_offline_from_psg_path(tmp_path):
    p = tmp_path / "psg.json"
    p.write_text(json.dumps(_fixture_psg()), encoding="utf-8")
    res = b.mine_candidates(psg_path=str(p))
    assert res["success"] is True
    assert res["candidateCount"] == 1
    c = res["candidates"][0]
    assert c["scopeId"] == "h1"
    assert c["kind"] == "molecule"
    assert c["confidence"] == "high"
    assert c["nodeCount"] == 2
    assert len(c["wl_fingerprint"]) == 32
    assert c["boundaryEdges"][0]["crosses"] == "inlet"


def test_mine_candidates_offline_save_path_writes_file(tmp_path):
    p = tmp_path / "psg.json"
    p.write_text(json.dumps(_fixture_psg()), encoding="utf-8")
    out = tmp_path / "cands.json"
    res = b.mine_candidates(psg_path=str(p), save_path=str(out))
    assert res["success"] is True and res["savedTo"] == str(out)
    assert res["candidateCount"] == 1
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["candidates"][0]["scopeId"] == "h1"


def test_mine_candidates_offline_unreadable_psg_path():
    res = b.mine_candidates(psg_path="C:/nonexistent/nope.json")
    assert res["success"] is False
    assert res["errorCode"] == "PSG_PATH_UNREADABLE"
