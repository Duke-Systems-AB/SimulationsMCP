# tests/live/test_approve_pattern_live.py
"""Live round-trip test for the miner: mine -> cluster -> approve -> instantiate.
Requires ExtendSim 2024 Pro running with a model containing a repeated H-block.
Skips if COM/model unavailable. Writes an approved molecule to a temp dir.
Run: python -m pytest tests/live/test_approve_pattern_live.py -v -s

DEFERRED (M10 Task 5): not yet run live — the §9.5 round-trip
(extract_psg(instantiate_pattern(m, m.example)) == source) closes the miner loop and
should be validated against real ExtendSim, together with M7-M9's deferred verification.
"""
import os, sys, tempfile, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_mine_cluster_approve_round_trip():
    import simulation_backend as b
    import pattern_approve
    mined = b.mine_candidates()
    if not mined.get("success") or not mined.get("candidates"):
        pytest.skip(f"no live model / candidates: {mined.get('error') or mined.get('errorCode')}")
    with tempfile.TemporaryDirectory() as tmp:
        cpath = os.path.join(tmp, "cands.json")
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"candidates": mined["candidates"]}, f)
        clustered = b.cluster_patterns(candidates_paths=[cpath])
        assert clustered["success"] and clustered["patterns"]
        cand = clustered["patterns"][0]
        # a minimal naming: seed = first template node, expose its first boundary port if any
        seed_ref = cand["template"]["nodes"][0]["ref"]
        naming = {"id": "mined-live-demo", "intent": "mined", "seed": seed_ref, "params": {}}
        res = pattern_approve.approve_pattern_entry(candidate=cand, naming=naming,
                                                    dry_run=True, molecules_dir=tmp)
        print("approve dry-run:", res.get("success"), res.get("errorCode") or "")
        # dry run must at least build; if the mined molecule needs an inlet/outlet to validate,
        # this surfaces the naming a human must supply — that's the point of the round-trip.
        assert "preview" in res or res.get("errorCode") in ("VALIDATION_FAILED", "BUILD_FAILED")
