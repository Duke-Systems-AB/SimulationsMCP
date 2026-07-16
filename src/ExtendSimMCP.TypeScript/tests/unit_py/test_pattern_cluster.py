# tests/unit_py/test_pattern_cluster.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pattern_cluster import _hungarian, graph_edit_distance, cluster_candidates


def _n(ref, lib, typ):
    return {"ref": ref, "lib": lib, "type": typ}


def test_hungarian_2x2_picks_min_assignment():
    # min cost assignment: row0->col1 (1) + row1->col0 (1) = 2
    assert _hungarian([[5.0, 1.0], [1.0, 5.0]]) == 2.0


def test_hungarian_3x3_known_optimum():
    cost = [[4.0, 1.0, 3.0], [2.0, 0.0, 5.0], [3.0, 2.0, 2.0]]
    # optimal: 1 (r0c1) + 2 (r1c0) + 2 (r2c2) = 5  vs diagonal 4+0+2=6
    assert _hungarian(cost) == 5.0


def test_hungarian_empty_is_zero():
    assert _hungarian([]) == 0.0


def test_ged_identical_graphs_is_zero():
    g = {"nodes": [_n("b1", "Item", "Queue"), _n("b2", "Item", "Activity")],
         "edges": [{"from": "b1.outCon0", "to": "b2.inCon0"}]}
    g2 = {"nodes": [_n("b1", "Item", "Queue"), _n("b2", "Item", "Activity")],
          "edges": [{"from": "b1.outCon0", "to": "b2.inCon0"}]}
    assert graph_edit_distance(g, g2) == 0.0


def test_ged_relabeled_single_node_is_one():
    a = {"nodes": [_n("b1", "Item", "Queue")], "edges": []}
    b = {"nodes": [_n("x1", "Item", "Activity")], "edges": []}
    assert graph_edit_distance(a, b) == 1.0


def test_ged_one_extra_isolated_node_is_one():
    a = {"nodes": [_n("b1", "Item", "Queue")], "edges": []}
    b = {"nodes": [_n("b1", "Item", "Queue"), _n("b2", "Item", "Exit")], "edges": []}
    assert graph_edit_distance(a, b) == 1.0


def test_ged_is_symmetric():
    a = {"nodes": [_n("b1", "Item", "Queue"), _n("b2", "Item", "Activity")],
         "edges": [{"from": "b1.outCon0", "to": "b2.inCon0"}]}
    b = {"nodes": [_n("c1", "Item", "Queue")], "edges": []}
    assert graph_edit_distance(a, b) == graph_edit_distance(b, a)


def test_ged_is_deterministic():
    a = {"nodes": [_n("b1", "Item", "Queue"), _n("b2", "Item", "Activity")],
         "edges": [{"from": "b1.outCon0", "to": "b2.inCon0"}]}
    b = {"nodes": [_n("c1", "Item", "Queue"), _n("c2", "Item", "Activity")],
         "edges": [{"from": "c1.outCon0", "to": "c2.inCon0"}]}
    assert graph_edit_distance(a, b) == graph_edit_distance(a, b) == 0.0


def test_ged_differing_ports_is_nonzero():
    a = {"nodes": [_n("b1", "Item", "Create"), _n("b2", "Item", "Activity")],
         "edges": [{"from": "b1.outCon0", "to": "b2.inCon0"}]}
    b = {"nodes": [_n("c1", "Item", "Create"), _n("c2", "Item", "Activity")],
         "edges": [{"from": "c1.shutdown", "to": "c2.shutdown"}]}
    assert graph_edit_distance(a, b) > 0.0


def _cand(fp, nodes, edges=None):
    return {"wl_fingerprint": fp, "nodes": nodes, "edges": edges or []}


def test_cluster_identical_fingerprints_one_bucket():
    nodes = [_n("b1", "Item", "Queue")]
    cands = [_cand("FP1", nodes), _cand("FP1", nodes)]
    clusters = cluster_candidates(cands)
    assert len(clusters) == 1
    assert clusters[0]["fingerprint"] == "FP1"
    assert len(clusters[0]["instances"]) == 2
    assert clusters[0]["nearMiss"] is False


def test_cluster_far_apart_stay_separate():
    a = _cand("FPA", [_n("b1", "Item", "Queue"), _n("b2", "Item", "Activity"),
                      _n("b3", "Item", "Exit")],
              [{"from": "b1.outCon0", "to": "b2.inCon0"},
               {"from": "b2.outCon0", "to": "b3.inCon0"}])
    b = _cand("FPB", [_n("c1", "Value", "Constant")], [])
    clusters = cluster_candidates([a, b], ged_threshold=2)
    assert len(clusters) == 2


def test_cluster_near_miss_merges_and_flags():
    # differ by one extra isolated node -> GED 1 <= threshold 2 -> merge
    a = _cand("FPA", [_n("b1", "Item", "Queue")], [])
    b = _cand("FPB", [_n("c1", "Item", "Queue"), _n("c2", "Item", "Exit")], [])
    clusters = cluster_candidates([a, b], ged_threshold=2)
    assert len(clusters) == 1
    assert clusters[0]["nearMiss"] is True
    assert len(clusters[0]["instances"]) == 2


def test_cluster_skips_candidate_missing_fingerprint():
    good = _cand("FP1", [_n("b1", "Item", "Queue")])
    bad = {"nodes": [_n("b9", "Item", "Queue")], "edges": []}  # no wl_fingerprint
    clusters = cluster_candidates([good, bad])
    assert len(clusters) == 1
    assert len(clusters[0]["instances"]) == 1
