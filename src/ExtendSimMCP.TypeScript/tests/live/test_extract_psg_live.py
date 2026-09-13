# tests/live/test_extract_psg_live.py
"""Live smoke test for extract_psg. Requires ExtendSim 2024 Pro running with a
model open that contains at least one H-block. Skips if COM/model unavailable.
Run: python -m pytest tests/live/test_extract_psg_live.py -v -s

DEFERRED (M7 Task 5): this test has not yet been run against a live model. It
also pins the one live-uncertain field, hblockType (pure vs physical) — see
_psg_hblock_type in simulation_backend.py. Run it when ExtendSim is free with an
H-block model open, confirm the printed hblockType values, and if the
GetLibraryPathName-based signal proves unreliable, change _psg_hblock_type to
return None (fail-closed) rather than a wrong tag.
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_extract_psg_live_has_scopes_and_hblock():
    import simulation_backend as b
    res = b.extract_psg()
    if not res.get("success"):
        pytest.skip(f"no live model / COM: {res.get('error') or res.get('errorCode')}")
    scopes = res["scopes"]
    assert any(s["scopeId"] == "root" and s["kind"] == "root" for s in scopes)
    hblocks = [s for s in scopes if s["kind"] == "hblock"]
    print("scopeCount:", len(scopes), "hblockCount:", len(hblocks))
    for s in hblocks:
        print(" scope", s["scopeId"], "type=", s.get("hblockType"),
              "label=", s.get("label"), "nodes=", len(s["nodes"]),
              "edges=", len(s["edges"]), "boundary=", len(s["boundaryEdges"]))
        assert s["parentScopeId"] is not None
    # every H-block node points at a real scope
    node_child_scopes = {n["scopeId"] for s in scopes for n in s["nodes"]
                         if n.get("isHBlock") and "scopeId" in n}
    scope_ids = {s["scopeId"] for s in scopes}
    assert node_child_scopes <= scope_ids
