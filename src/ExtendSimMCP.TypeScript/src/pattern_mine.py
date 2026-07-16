# src/pattern_mine.py
"""Pattern mining — boundary detection + Weisfeiler-Lehman fingerprint (M8).

Pure core over M7's multi-scale PSG (from extract_psg). Emits one candidate
molecule subgraph per H-block scope, each tagged with a stable WL fingerprint that
canonicalizes topology (lib:blocktype + port/direction), independent of parameter
values. Clustering / near-miss / param inference is M9. Zero COM here.
See spec 2026-07-16-m8-boundary-wl-fingerprint-design.md.
"""
import hashlib


def _stable_hash(value):
    """Deterministic 32-hex-char digest of a value (NOT Python's salted hash())."""
    return hashlib.blake2b(repr(value).encode("utf-8"), digest_size=16).hexdigest()


def _split_ref_port(endpoint):
    """'b141.inCon0' -> ('b141', 'inCon0'); rightmost dot separates ref from port."""
    ref, _, port = endpoint.rpartition(".")
    return ref, port


def wl_fingerprint(nodes, edges, k=4):
    """Weisfeiler-Lehman fingerprint of a subgraph interior (PRD §9.1).

    Node label init = 'lib:blocktype' (topology, not params). Each round a node's
    signature is the sorted (direction, ownPort, neighborPort, neighborLabel) over
    its incident edges; a directionConfident:false edge contributes both views so an
    uncertain wire is orientation-invariant. Returns (fingerprint, labels) where
    labels maps node ref -> final label.
    """
    label = {n["ref"]: f"{n.get('lib', '')}:{n.get('type', '')}" for n in nodes}
    incidence = {n["ref"]: [] for n in nodes}

    for e in edges:
        src_ref, src_port = _split_ref_port(e["from"])
        dst_ref, dst_port = _split_ref_port(e["to"])
        undirected = e.get("directionConfident") is False
        if src_ref in incidence:
            incidence[src_ref].append(("out", src_port, dst_ref, dst_port))
            if undirected:
                incidence[src_ref].append(("in", src_port, dst_ref, dst_port))
        if dst_ref in incidence:
            incidence[dst_ref].append(("in", dst_port, src_ref, src_port))
            if undirected:
                incidence[dst_ref].append(("out", dst_port, src_ref, src_port))

    for _ in range(k):
        new = {}
        for ref, lbl in label.items():
            sig = [(direction, own_port, nbr_port, label.get(nbr_ref, ""))
                   for direction, own_port, nbr_ref, nbr_port in incidence[ref]]
            new[ref] = _stable_hash((lbl, tuple(sorted(sig))))
        label = new

    fingerprint = _stable_hash(tuple(sorted(label.values())))
    return fingerprint, label
