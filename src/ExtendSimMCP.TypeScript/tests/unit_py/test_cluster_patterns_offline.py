# tests/unit_py/test_cluster_patterns_offline.py
"""Unit tests for the cluster_patterns OFFLINE aggregation path (candidatesPaths).
Never touches COM; runs without ExtendSim (pywin32 import only)."""
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import simulation_backend as b


def _cand(fp, ref, d):
    return {"wl_fingerprint": fp, "scopeId": "h1", "hblockType": "pure",
            "nodes": [{"ref": ref, "lib": "Item", "type": "Activity",
                       "isHBlock": False, "params": {"D": d}}],
            "edges": [], "boundaryEdges": [], "wlLabels": {ref: "L1"}}


def _write(tmp_path, name, cands):
    p = tmp_path / name
    p.write_text(json.dumps({"success": True, "candidateCount": len(cands),
                             "candidates": cands}), encoding="utf-8")
    return str(p)


def test_cluster_patterns_aggregates_two_candidate_files(tmp_path):
    p1 = _write(tmp_path, "a.json", [_cand("FP1", "b1", 2)])
    p2 = _write(tmp_path, "b.json", [_cand("FP1", "x1", 8)])
    res = b.cluster_patterns(candidates_paths=[p1, p2])
    assert res["success"] is True
    assert res["clusterCount"] == 1
    pat = res["patterns"][0]
    assert pat["support"] == 2
    assert pat["params"]["b1.D"]["required"] is True
    assert pat["params"]["b1.D"]["range"] == [2, 8]


def test_cluster_patterns_empty_sources_zero_clusters():
    res = b.cluster_patterns()
    assert res["success"] is True and res["clusterCount"] == 0 and res["patterns"] == []


def test_cluster_patterns_save_path(tmp_path):
    p1 = _write(tmp_path, "a.json", [_cand("FP1", "b1", 5)])
    out = tmp_path / "patterns.json"
    res = b.cluster_patterns(candidates_paths=[p1], save_path=str(out))
    assert res["success"] is True and res["savedTo"] == str(out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["clusterCount"] == 1


def test_cluster_patterns_unreadable_candidates_path():
    res = b.cluster_patterns(candidates_paths=["C:/nonexistent/nope.json"])
    assert res["success"] is False
    assert res["errorCode"] == "CANDIDATES_PATH_UNREADABLE"
