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
    assert edges == [{"from": "b10.port", "to": "b11.port"}]
    assert boundary == []
