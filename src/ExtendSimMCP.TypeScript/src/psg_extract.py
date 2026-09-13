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


def _boundary_edge(bid, conn):
    """Boundary (dangling) edge for one internal endpoint."""
    port = _port(conn)
    return {"internal": f"b{bid}.{port}",
            "crosses": _CROSSES.get(conn.get("direction"), "unknown"),
            "boundaryConnector": port}


def _pair(scope_blocks):
    """Pair a scope's connectors by shared nodeIndex.

    - Exactly one internal endpoint on a node -> boundary (dangling) edge.
    - A clean out+in split -> internal edge(s), out->in.
    - Two-or-more endpoints all sharing one KNOWN direction (all in / all out)
      cannot be a valid internal edge; they tie to the same boundary connector,
      so each is a boundary edge (boundary fan-in / fan-out).
    - Otherwise (direction indeterminate) -> keep every wire (first endpoint as
      source) flagged directionConfident:false so the miner treats it undirected.
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
            boundary.append(_boundary_edge(bid, c))
            continue
        outs = [e for e in eps if e[1].get("direction") == "out"]
        ins = [e for e in eps if e[1].get("direction") == "in"]
        if outs and ins:
            for o in outs:
                for i in ins:
                    edges.append({"from": f"b{o[0]}.{_port(o[1])}",
                                  "to": f"b{i[0]}.{_port(i[1])}"})
        elif len(outs) == len(eps) or len(ins) == len(eps):
            # all endpoints share one known direction -> boundary fan-in/out
            for bid, c in eps:
                boundary.append(_boundary_edge(bid, c))
        else:
            # direction indeterminate: keep every wire (first endpoint as source),
            # flag so the miner (M8) can treat it as undirected.
            src = eps[0]
            for tgt in eps[1:]:
                edges.append({"from": f"b{src[0]}.{_port(src[1])}",
                              "to": f"b{tgt[0]}.{_port(tgt[1])}",
                              "directionConfident": False})
    return edges, boundary


def _node(blk):
    node = {
        "ref": f"b{blk['blockId']}",
        "blockId": blk["blockId"],
        "lib": blk.get("lib", ""),
        "type": blk.get("type", ""),
        "isHBlock": bool(blk.get("isHBlock")),
        "params": blk.get("params", {}),
    }
    if blk.get("setAttributes"):
        # Structural (e.g. a Set block's attribute assignments) — carried
        # verbatim, not treated as a scalar param (W3-6a).
        node["setAttributes"] = blk["setAttributes"]
    if node["isHBlock"] and blk.get("childScopeId"):
        node["scopeId"] = blk["childScopeId"]
    return node


def build_psg(raw):
    """Transform a raw per-scope snapshot into a multi-scale PSG."""
    out_scopes = []
    for scope in raw.get("scopes", []):
        blocks = scope.get("blocks", [])
        edges, boundary = _pair(blocks)
        out = {
            "scopeId": scope["scopeId"],
            "kind": scope["kind"],
            "parentScopeId": scope.get("parentScopeId"),
            "nodes": [_node(b) for b in blocks],
            "edges": edges,
            "boundaryEdges": boundary,
        }
        if scope["kind"] == "hblock":
            out["hblockType"] = scope.get("hblockType")
            out["label"] = scope.get("label", "")
        out_scopes.append(out)
    return {"modelName": raw.get("modelName", ""), "scopes": out_scopes}
