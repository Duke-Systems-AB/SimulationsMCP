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
    wl = {n["ref"]: f"L{i+1}" for i, n in enumerate(nodes)}
    return {"wl_fingerprint": fp, "nodes": nodes, "edges": edges or [], "wlLabels": wl}


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


def test_cluster_transitive_merge_across_three_buckets():
    # A-B within threshold and B-C within threshold, but A-C not: union-find must
    # still merge all three into ONE cluster (transitivity).
    a = _cand("FPA", [_n("b1", "Item", "Queue")], [])
    b = _cand("FPB", [_n("c1", "Item", "Queue"), _n("c2", "Item", "Exit")], [])
    c = _cand("FPC", [_n("d1", "Item", "Queue"), _n("d2", "Item", "Exit"),
                      _n("d3", "Item", "Create")], [])
    clusters = cluster_candidates([a, b, c], ged_threshold=1)
    assert len(clusters) == 1
    assert clusters[0]["nearMiss"] is True
    assert len(clusters[0]["instances"]) == 3


def test_cluster_skips_candidate_missing_fingerprint():
    good = _cand("FP1", [_n("b1", "Item", "Queue")])
    bad = {"nodes": [_n("b9", "Item", "Queue")], "edges": []}  # no wl_fingerprint
    clusters = cluster_candidates([good, bad])
    assert len(clusters) == 1
    assert len(clusters[0]["instances"]) == 1


from pattern_cluster import infer_pattern


def _inst(fp, nodes, edges=None, boundary=None, wl=None, hbt="pure",
          scope="h1", source="m.mox"):
    return {"wl_fingerprint": fp, "nodes": nodes, "edges": edges or [],
            "boundaryEdges": boundary or [], "wlLabels": wl or {},
            "hblockType": hbt, "scopeId": scope, "source": source}


def _pnode(ref, lib, typ, params=None, is_h=False):
    n = {"ref": ref, "lib": lib, "type": typ, "isHBlock": is_h, "params": params or {}}
    return n


def test_infer_constant_param_is_fixed():
    n1 = [_pnode("b1", "Item", "Activity", {"D": 5})]
    n2 = [_pnode("x1", "Item", "Activity", {"D": 5})]
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", n1, wl={"b1": "L1"}), _inst("FP", n2, wl={"x1": "L1"})]}
    pat = infer_pattern(cluster)
    assert pat["params"]["b1.D"] == {"type": "number", "required": False, "fixed": 5}
    assert pat["support"] == 2


def test_infer_varying_numeric_is_required_with_median_and_range():
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", [_pnode("b1", "Item", "Activity", {"D": 2})], wl={"b1": "L1"}),
        _inst("FP", [_pnode("x1", "Item", "Activity", {"D": 8})], wl={"x1": "L1"}),
        _inst("FP", [_pnode("y1", "Item", "Activity", {"D": 5})], wl={"y1": "L1"})]}
    info = infer_pattern(cluster)["params"]["b1.D"]
    assert info["required"] is True
    assert info["default"] == 5
    assert info["range"] == [2, 8]


def test_infer_varying_non_numeric_uses_most_common():
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", [_pnode("b1", "Item", "Set", {"attr": "gold"})], wl={"b1": "L1"}),
        _inst("FP", [_pnode("x1", "Item", "Set", {"attr": "gold"})], wl={"x1": "L1"}),
        _inst("FP", [_pnode("y1", "Item", "Set", {"attr": "silver"})], wl={"y1": "L1"})]}
    info = infer_pattern(cluster)["params"]["b1.attr"]
    assert info["required"] is True and info["type"] == "string"
    assert info["default"] == "gold"


def test_infer_template_uses_placeholder_for_required_literal_for_fixed():
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", [_pnode("b1", "Item", "Activity", {"D": 2, "cap": 1})], wl={"b1": "L1"}),
        _inst("FP", [_pnode("x1", "Item", "Activity", {"D": 8, "cap": 1})], wl={"x1": "L1"})]}
    tnode = infer_pattern(cluster)["template"]["nodes"][0]
    assert tnode["params"]["D"] == "{{b1.D}}"   # varies -> placeholder
    assert tnode["params"]["cap"] == 1          # constant -> literal


def test_infer_interface_from_representative_boundary_edges():
    b = [{"internal": "b1.ItemIn", "crosses": "inlet", "boundaryConnector": "ItemIn"},
         {"internal": "b1.ItemOut", "crosses": "outlet", "boundaryConnector": "ItemOut"}]
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", [_pnode("b1", "Item", "Activity")], boundary=b, wl={"b1": "L1"})]}
    iface = infer_pattern(cluster)["interface"]
    assert iface["inlets"] == [{"binds": "b1.ItemIn", "role": "item"}]
    assert iface["outlets"] == [{"binds": "b1.ItemOut", "role": "item"}]


def test_infer_aligns_by_wl_label_not_block_id():
    # different refs, same label -> params align into one position
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", [_pnode("aaa", "Item", "Activity", {"D": 3})], wl={"aaa": "L1"}),
        _inst("FP", [_pnode("zzz", "Item", "Activity", {"D": 7})], wl={"zzz": "L1"})]}
    pat = infer_pattern(cluster)
    # rep is first instance -> key uses rep ref "aaa"; both values aligned via L1
    assert pat["params"]["aaa.D"]["required"] is True
    assert pat["params"]["aaa.D"]["range"] == [3, 7]


def test_infer_support_one_param_is_required_not_fixed():
    # a single instance cannot establish constancy -> required (default = the value)
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [
        _inst("FP", [_pnode("b1", "Item", "Activity", {"D": 5})], wl={"b1": "L1"})]}
    pat = infer_pattern(cluster)
    assert pat["support"] == 1
    assert pat["params"]["b1.D"] == {"type": "number", "required": True, "default": 5}


def test_infer_hblocktype_null_when_mixed():
    cluster = {"fingerprint": "FP", "nearMiss": True, "instances": [
        _inst("FP", [_pnode("b1", "Item", "Activity")], wl={"b1": "L1"}, hbt="pure"),
        _inst("FP", [_pnode("x1", "Item", "Activity")], wl={"x1": "L1"}, hbt="physical")]}
    assert infer_pattern(cluster)["hblockType"] is None


def test_cluster_skips_candidate_missing_wllabels():
    good = {"wl_fingerprint": "FP1", "nodes": [_n("b1", "Item", "Queue")],
            "edges": [], "wlLabels": {"b1": "L1"}}
    bad = {"wl_fingerprint": "FP2", "nodes": [_n("b9", "Item", "Activity")],
           "edges": []}  # no wlLabels -> must be skipped
    clusters = cluster_candidates([good, bad], ged_threshold=0)
    assert len(clusters) == 1
    assert len(clusters[0]["instances"]) == 1   # bad candidate dropped, not merged
    assert clusters[0]["instances"][0]["wl_fingerprint"] == "FP1"


def test_infer_set_merges_symmetric_label_values():
    # Instance A has a symmetric pair (2 nodes, same label L, both "gold");
    # B and C each contribute one "silver". Without set-merge the doubled "gold"
    # wins most-common; with set-merge, "silver" (2 instances) wins.
    def inst(fp, nodes, wl):
        return {"wl_fingerprint": fp, "nodes": nodes, "edges": [], "boundaryEdges": [],
                "wlLabels": wl, "hblockType": "pure", "scopeId": "h", "source": "m"}
    a = inst("FP", [_pnode("a1", "Item", "Set", {"attr": "gold"}),
                    _pnode("a2", "Item", "Set", {"attr": "gold"})],
             {"a1": "L1", "a2": "L1"})
    b = inst("FP", [_pnode("b1", "Item", "Set", {"attr": "silver"})], {"b1": "L1"})
    c = inst("FP", [_pnode("c1", "Item", "Set", {"attr": "silver"})], {"c1": "L1"})
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [a, b, c]}
    info = infer_pattern(cluster)["params"]["a1.attr"]
    assert info["default"] == "silver"
