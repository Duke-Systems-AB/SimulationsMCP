# tests/live/test_mine_candidates_live.py
"""Live smoke test for mine_candidates. Requires ExtendSim 2024 Pro running with a
model open that contains at least one H-block. Skips if COM/model unavailable.
Run: python -m pytest tests/live/test_mine_candidates_live.py -v -s

DEFERRED (M8 Task 5): not yet run against a live model — pairs with M7's deferred
extract_psg live verification. Confirms the full M7->M8 chain against real COM.
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_mine_candidates_live_returns_candidates():
    import simulation_backend as b
    res = b.mine_candidates()
    if not res.get("success"):
        pytest.skip(f"no live model / COM: {res.get('error') or res.get('errorCode')}")
    assert "candidates" in res
    print("candidateCount:", res["candidateCount"])
    for c in res["candidates"]:
        print(" ", c["scopeId"], c["kind"], c["hblockType"], c["confidence"],
              "wl=", c["wl_fingerprint"][:8], "nodes=", c["nodeCount"])
        assert len(c["wl_fingerprint"]) == 32
        assert c["kind"] in ("molecule", "composite")
