# tests/unit_py/test_psg_extract.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from psg_extract import _port, _pair


def _blk(bid, connectors):
    return {"blockId": bid, "connectors": connectors}


def _c(idx, name, direction, node):
    return {"idx": idx, "connName": name, "direction": direction, "nodeIndex": node}


def test_port_uses_connector_name_when_present():
    assert _port(_c(0, "outCon0", "out", 5)) == "outCon0"


def test_port_falls_back_to_con_direction_index():
    assert _port(_c(2, "", "in", 7)) == "ConIn2"
    assert _port(_c(3, "", "out", 7)) == "ConOut3"
    assert _port(_c(1, "", "unknown", 7)) == "Con1"


def test_pair_makes_one_internal_edge_out_to_in():
    blocks = [
        _blk(10, [_c(0, "outCon0", "out", 5)]),
        _blk(11, [_c(0, "inCon0", "in", 5)]),
    ]
    edges, boundary = _pair(blocks)
    assert edges == [{"from": "b10.outCon0", "to": "b11.inCon0"}]
    assert boundary == []


def test_pair_normalizes_direction_regardless_of_block_order():
    blocks = [
        _blk(11, [_c(0, "inCon0", "in", 5)]),
        _blk(10, [_c(0, "outCon0", "out", 5)]),
    ]
    edges, _ = _pair(blocks)
    assert edges == [{"from": "b10.outCon0", "to": "b11.inCon0"}]


def test_pair_fan_out_one_source_two_targets():
    blocks = [
        _blk(10, [_c(0, "outCon0", "out", 5)]),
        _blk(11, [_c(0, "inCon0", "in", 5)]),
        _blk(12, [_c(0, "inCon0", "in", 5)]),
    ]
    edges, _ = _pair(blocks)
    assert {"from": "b10.outCon0", "to": "b11.inCon0"} in edges
    assert {"from": "b10.outCon0", "to": "b12.inCon0"} in edges
    assert len(edges) == 2


def test_pair_skips_unconnected_node_index_zero():
    blocks = [_blk(10, [_c(0, "outCon0", "out", 0)])]
    edges, boundary = _pair(blocks)
    assert edges == [] and boundary == []


def test_pair_single_internal_endpoint_is_boundary_inlet():
    blocks = [_blk(10, [_c(0, "inCon0", "in", 9)])]
    edges, boundary = _pair(blocks)
    assert edges == []
    assert boundary == [{"internal": "b10.inCon0", "crosses": "inlet",
                         "boundaryConnector": "inCon0"}]


def test_pair_single_internal_endpoint_out_is_boundary_outlet():
    blocks = [_blk(10, [_c(0, "outCon0", "out", 9)])]
    _, boundary = _pair(blocks)
    assert boundary == [{"internal": "b10.outCon0", "crosses": "outlet",
                         "boundaryConnector": "outCon0"}]


def test_pair_unknown_direction_two_endpoints_emits_edge_as_listed():
    blocks = [
        _blk(10, [_c(0, "port", "unknown", 5)]),
        _blk(11, [_c(0, "port", "unknown", 5)]),
    ]
    edges, boundary = _pair(blocks)
    assert edges == [{"from": "b10.port", "to": "b11.port",
                      "directionConfident": False}]
    assert boundary == []


def test_pair_confident_out_in_edge_has_no_direction_flag():
    blocks = [
        _blk(10, [_c(0, "outCon0", "out", 5)]),
        _blk(11, [_c(0, "inCon0", "in", 5)]),
    ]
    edges, _ = _pair(blocks)
    assert "directionConfident" not in edges[0]


def test_pair_two_outs_on_one_node_emits_unconfident_edge():
    blocks = [
        _blk(10, [_c(0, "outA", "out", 5)]),
        _blk(11, [_c(0, "outB", "out", 5)]),
    ]
    edges, _ = _pair(blocks)
    assert edges == [{"from": "b10.outA", "to": "b11.outB",
                      "directionConfident": False}]


from psg_extract import build_psg


def _raw_block(bid, lib, btype, is_h=False, child=None, params=None, connectors=None):
    return {"blockId": bid, "lib": lib, "type": btype, "isHBlock": is_h,
            "childScopeId": child, "params": params or {},
            "connectors": connectors or []}


def test_build_psg_flat_model_single_root_scope():
    raw = {"modelName": "flat.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None, "blocks": [
            _raw_block(10, "Item", "Create", connectors=[_c(0, "outCon0", "out", 5)]),
            _raw_block(11, "Item", "Exit", connectors=[_c(0, "inCon0", "in", 5)]),
        ]},
    ]}
    psg = build_psg(raw)
    assert psg["modelName"] == "flat.mox"
    assert len(psg["scopes"]) == 1
    root = psg["scopes"][0]
    assert root["scopeId"] == "root" and root["kind"] == "root"
    assert root["nodes"][0] == {"ref": "b10", "blockId": 10, "lib": "Item",
                                "type": "Create", "isHBlock": False, "params": {}}
    assert root["edges"] == [{"from": "b10.outCon0", "to": "b11.inCon0"}]
    assert root["boundaryEdges"] == []


def test_build_psg_hblock_node_carries_child_scope_id():
    raw = {"modelName": "h.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None, "blocks": [
            _raw_block(140, "", "Hierarchical", is_h=True, child="h140"),
        ]},
        {"scopeId": "h140", "kind": "hblock", "parentScopeId": "root",
         "hblockType": "pure", "label": "Machine", "blocks": [
            _raw_block(141, "Item", "Activity", connectors=[_c(0, "inCon0", "in", 3)]),
        ]},
    ]}
    psg = build_psg(raw)
    root, hb = psg["scopes"]
    node = root["nodes"][0]
    assert node["isHBlock"] is True and node["scopeId"] == "h140"
    assert hb["kind"] == "hblock" and hb["parentScopeId"] == "root"
    assert hb["hblockType"] == "pure" and hb["label"] == "Machine"
    # the dangling internal inlet becomes a boundary edge
    assert hb["boundaryEdges"] == [{"internal": "b141.inCon0", "crosses": "inlet",
                                    "boundaryConnector": "inCon0"}]


def test_build_psg_passes_params_through_including_question_mark():
    raw = {"modelName": "p.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None, "blocks": [
            _raw_block(10, "Item", "Activity", params={"D": 5, "capacity": "?"}),
        ]},
    ]}
    node = build_psg(raw)["scopes"][0]["nodes"][0]
    assert node["params"] == {"D": 5, "capacity": "?"}


def test_build_psg_root_scope_omits_hblock_only_fields():
    raw = {"modelName": "f.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None, "blocks": []},
    ]}
    root = build_psg(raw)["scopes"][0]
    assert "hblockType" not in root and "label" not in root


def test_build_psg_nested_hblocks_every_depth_is_a_scope():
    raw = {"modelName": "n.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None, "blocks": [
            _raw_block(140, "", "Hierarchical", is_h=True, child="h140")]},
        {"scopeId": "h140", "kind": "hblock", "parentScopeId": "root",
         "hblockType": "physical", "label": "Outer", "blocks": [
            _raw_block(150, "", "Hierarchical", is_h=True, child="h150")]},
        {"scopeId": "h150", "kind": "hblock", "parentScopeId": "h140",
         "hblockType": "pure", "label": "Inner", "blocks": []},
    ]}
    psg = build_psg(raw)
    ids = [s["scopeId"] for s in psg["scopes"]]
    assert ids == ["root", "h140", "h150"]
    assert psg["scopes"][2]["parentScopeId"] == "h140"
