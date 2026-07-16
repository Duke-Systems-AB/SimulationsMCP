# tests/unit_py/test_pattern_cluster.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pattern_cluster import _hungarian, graph_edit_distance


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
