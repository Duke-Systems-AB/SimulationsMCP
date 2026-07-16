# tests/unit_py/test_pattern_mine.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pattern_mine import wl_fingerprint, _split_ref_port, _stable_hash


def _n(ref, lib, typ):
    return {"ref": ref, "lib": lib, "type": typ}


def test_split_ref_port_rightmost_dot():
    assert _split_ref_port("b141.inCon0") == ("b141", "inCon0")


def test_stable_hash_is_deterministic_and_32_hex():
    h1 = _stable_hash(("a", 1))
    h2 = _stable_hash(("a", 1))
    assert h1 == h2
    assert len(h1) == 32 and all(c in "0123456789abcdef" for c in h1)


def test_fingerprint_is_deterministic():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    f1, _ = wl_fingerprint(nodes, edges)
    f2, _ = wl_fingerprint(nodes, edges)
    assert f1 == f2
    assert len(f1) == 32


def test_isomorphic_subgraphs_same_fingerprint_regardless_of_ids_and_params():
    # same topology, different block ids (refs) and no params in labels
    a_nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    a_edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    b_nodes = [_n("b20", "Item", "Queue"), _n("b30", "Item", "Activity")]
    b_edges = [{"from": "b20.outCon0", "to": "b30.inCon0"}]
    fa, _ = wl_fingerprint(a_nodes, a_edges)
    fb, _ = wl_fingerprint(b_nodes, b_edges)
    assert fa == fb


def test_different_topology_different_fingerprint():
    q_to_a = ([_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")],
              [{"from": "b2.outCon0", "to": "b3.inCon0"}])
    a_to_q = ([_n("b2", "Item", "Activity"), _n("b3", "Item", "Queue")],
              [{"from": "b2.outCon0", "to": "b3.inCon0"}])
    fa, _ = wl_fingerprint(*q_to_a)
    fb, _ = wl_fingerprint(*a_to_q)
    assert fa != fb


def test_port_names_matter_shutdown_vs_flow():
    nodes = [_n("b2", "Item", "Create"), _n("b3", "Item", "Activity")]
    flow = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    shutdown = [{"from": "b2.shutdown", "to": "b3.shutdown"}]
    ff, _ = wl_fingerprint(nodes, flow)
    fs, _ = wl_fingerprint(nodes, shutdown)
    assert ff != fs


def test_confident_edge_direction_matters():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    fwd = [{"from": "b2.p", "to": "b3.p"}]
    rev = [{"from": "b3.p", "to": "b2.p"}]
    ff, _ = wl_fingerprint(nodes, fwd)
    fr, _ = wl_fingerprint(nodes, rev)
    assert ff != fr


def test_unconfident_edge_is_orientation_invariant():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    fwd = [{"from": "b2.p", "to": "b3.p", "directionConfident": False}]
    rev = [{"from": "b3.p", "to": "b2.p", "directionConfident": False}]
    ff, _ = wl_fingerprint(nodes, fwd)
    fr, _ = wl_fingerprint(nodes, rev)
    assert ff == fr


def test_labels_returned_for_each_node():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    _, labels = wl_fingerprint(nodes, edges)
    assert set(labels.keys()) == {"b2", "b3"}


def test_missing_lib_type_does_not_crash():
    nodes = [{"ref": "b2"}, {"ref": "b3", "lib": "Item", "type": "Exit"}]
    edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    f, labels = wl_fingerprint(nodes, edges)
    assert len(f) == 32 and set(labels) == {"b2", "b3"}


from pattern_mine import detect_candidates


def _scope(scope_id, kind, nodes, edges=None, boundary=None, parent=None,
           hblock_type=None, label=""):
    s = {"scopeId": scope_id, "kind": kind, "parentScopeId": parent,
         "nodes": nodes, "edges": edges or [], "boundaryEdges": boundary or []}
    if kind == "hblock":
        s["hblockType"] = hblock_type
        s["label"] = label
    return s


def _hnode(ref, lib, typ, is_h=False, child=None):
    n = {"ref": ref, "blockId": int(ref[1:]), "lib": lib, "type": typ,
         "isHBlock": is_h, "params": {}}
    if is_h and child:
        n["scopeId"] = child
    return n


def test_detect_candidates_one_per_hblock_root_excluded():
    psg = {"modelName": "m.mox", "scopes": [
        _scope("root", "root", [_hnode("b1", "", "Hierarchical", is_h=True, child="h1")]),
        _scope("h1", "hblock", [_hnode("b2", "Item", "Queue"), _hnode("b3", "Item", "Activity")],
               edges=[{"from": "b2.outCon0", "to": "b3.inCon0"}],
               hblock_type="pure", label="Machine"),
    ]}
    cands = detect_candidates(psg)
    assert len(cands) == 1
    c = cands[0]
    assert c["scopeId"] == "h1"
    assert c["kind"] == "molecule"
    assert c["confidence"] == "high"
    assert c["hblockType"] == "pure"
    assert c["nodeCount"] == 2
    assert c["label"] == "Machine"
    assert len(c["wl_fingerprint"]) == 32
    assert set(c["wlLabels"].keys()) == {"b2", "b3"}


def test_detect_candidates_physical_and_null_are_candidate_confidence():
    psg = {"modelName": "m.mox", "scopes": [
        _scope("h1", "hblock", [_hnode("b2", "Item", "Queue")], hblock_type="physical"),
        _scope("h2", "hblock", [_hnode("b3", "Item", "Queue")], hblock_type=None),
    ]}
    cands = detect_candidates(psg)
    assert cands[0]["confidence"] == "candidate"
    assert cands[1]["confidence"] == "candidate"


def test_detect_candidates_composite_when_interior_has_hblock():
    psg = {"modelName": "m.mox", "scopes": [
        _scope("h1", "hblock",
               [_hnode("b2", "Item", "Queue"),
                _hnode("b3", "", "Hierarchical", is_h=True, child="h3")],
               hblock_type="pure"),
        _scope("h3", "hblock", [_hnode("b4", "Item", "Activity")], hblock_type="pure"),
    ]}
    cands = detect_candidates(psg)
    by_id = {c["scopeId"]: c for c in cands}
    assert by_id["h1"]["kind"] == "composite"
    assert by_id["h3"]["kind"] == "molecule"


def test_detect_candidates_carries_boundary_edges_untouched():
    b = [{"internal": "b2.inCon0", "crosses": "inlet", "boundaryConnector": "inCon0"}]
    psg = {"modelName": "m.mox", "scopes": [
        _scope("h1", "hblock", [_hnode("b2", "Item", "Queue")], boundary=b, hblock_type="pure"),
    ]}
    assert detect_candidates(psg)[0]["boundaryEdges"] == b


def test_detect_candidates_flat_model_yields_empty():
    psg = {"modelName": "flat.mox", "scopes": [
        _scope("root", "root", [_hnode("b1", "Item", "Create"), _hnode("b2", "Item", "Exit")],
               edges=[{"from": "b1.outCon0", "to": "b2.inCon0"}]),
    ]}
    assert detect_candidates(psg) == []
