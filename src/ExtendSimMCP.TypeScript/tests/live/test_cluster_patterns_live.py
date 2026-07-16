# tests/live/test_cluster_patterns_live.py
"""Live smoke test for cluster_patterns. Requires ExtendSim 2024 Pro running with a
model open that contains repeated H-block instances. Skips if COM/model unavailable.
Run: python -m pytest tests/live/test_cluster_patterns_live.py -v -s

DEFERRED (M9 Task 6): not yet run live — pairs with M7/M8's deferred verification.
Provide a real model via filePaths, or an active model, to exercise M7->M8->M9.
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_cluster_patterns_live_from_active_model():
    import simulation_backend as b
    # mine the active model, then cluster its candidates via an in-memory round-trip
    mined = b.mine_candidates()
    if not mined.get("success") or not mined.get("candidates"):
        pytest.skip(f"no live model / no candidates: {mined.get('error') or mined.get('errorCode')}")
    # write candidates to a temp file and cluster offline (exercises aggregation too)
    import tempfile, json
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"candidates": mined["candidates"]}, f)
        path = f.name
    res = b.cluster_patterns(candidates_paths=[path])
    assert res["success"] is True
    print("clusterCount:", res["clusterCount"])
    for pat in res["patterns"]:
        print(" ", pat["wl_fingerprint"][:8], "support=", pat["support"],
              "nearMiss=", pat["nearMiss"], "params=", list(pat["params"].keys()))
        assert pat["support"] >= 1
