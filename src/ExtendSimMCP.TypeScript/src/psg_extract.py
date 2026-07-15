# src/psg_extract.py
"""Pattern Structure Graph (PSG) extraction — pure core (M7).

build_psg() transforms a raw per-scope model snapshot (gathered by the live COM
reader in simulation_backend.py) into a multi-scale PSG: one scope per level
(root + every H-block at every depth), each with nodes, internal edges, and
boundary-crossing edges. Zero COM here; unit-tested with fixtures.
See spec 2026-07-15-m7-extract-psg-design.md.
"""
from collections import defaultdict

_DIR_TAG = {"in": "In", "out": "Out"}
_CROSSES = {"in": "inlet", "out": "outlet"}


def _port(conn):
    """Port name for an edge endpoint: connector name, or Con{In|Out}{idx} fallback."""
    name = conn.get("connName")
    if name:
        return name
    return f"Con{_DIR_TAG.get(conn.get('direction'), '')}{conn.get('idx', 0)}"


def _pair(scope_blocks):
    """Pair a scope's connectors by shared nodeIndex.

    Two-or-more internal endpoints on a node -> internal edge(s), out->in.
    Exactly one internal endpoint -> boundary (dangling) edge.
    Returns (edges, boundaryEdges).
    """
    by_node = defaultdict(list)
    for blk in scope_blocks:
        bid = blk["blockId"]
        for c in blk.get("connectors", []):
            if c.get("nodeIndex", 0) == 0:
                continue  # unconnected
            by_node[c["nodeIndex"]].append((bid, c))

    edges, boundary = [], []
    for _, eps in by_node.items():
        if len(eps) == 1:
            bid, c = eps[0]
            boundary.append({
                "internal": f"b{bid}.{_port(c)}",
                "crosses": _CROSSES.get(c.get("direction"), "unknown"),
                "boundaryConnector": _port(c),
            })
            continue
        outs = [e for e in eps if e[1].get("direction") == "out"]
        ins = [e for e in eps if e[1].get("direction") == "in"]
        if outs and ins:
            for o in outs:
                for i in ins:
                    edges.append({"from": f"b{o[0]}.{_port(o[1])}",
                                  "to": f"b{i[0]}.{_port(i[1])}"})
        else:
            # direction indeterminate: keep every wire (first endpoint as source),
            # but flag the edge so the miner (M8) can treat it as undirected.
            src = eps[0]
            for tgt in eps[1:]:
                edges.append({"from": f"b{src[0]}.{_port(src[1])}",
                              "to": f"b{tgt[0]}.{_port(tgt[1])}",
                              "directionConfident": False})
    return edges, boundary
