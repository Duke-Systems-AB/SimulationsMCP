# src/pattern_cluster.py
"""Pattern clustering + param/interface inference (M9).

Pure core over M8's candidate subgraphs. Groups instances (exact WL bucket +
near-miss graph-edit-distance merge) and infers each cluster's parameter schema,
interface, and template — the mined pattern candidates M10 approves. Zero COM;
stdlib only (self-contained Hungarian, no scipy). See spec
2026-07-16-m9-cluster-infer-design.md.
"""
import statistics
from collections import Counter, defaultdict


def _hungarian(cost):
    """Minimum-cost perfect assignment on a square cost matrix (Kuhn-Munkres, O(n^3)).

    Returns the total cost. Deterministic. Based on the standard potentials method.
    """
    n = len(cost)
    if n == 0:
        return 0.0
    INF = float("inf")
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)      # p[j] = row assigned to column j (1-indexed; 0 = none)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    total = 0.0
    for j in range(1, n + 1):
        if p[j] != 0:
            total += cost[p[j] - 1][j - 1]
    return total


def _split_ref_port(endpoint):
    ref, _, port = endpoint.rpartition(".")
    return ref, port


def _node_label(node):
    return f"{node.get('lib', '')}:{node.get('type', '')}"


def _local_edges(ref, edges):
    """Multiset (list) of (direction, ownPort, neighborPort) incident to a node."""
    out = []
    for e in edges:
        s_ref, s_port = _split_ref_port(e["from"])
        d_ref, d_port = _split_ref_port(e["to"])
        undirected = e.get("directionConfident") is False
        if s_ref == ref:
            out.append(("out", s_port, d_port))
            if undirected:
                out.append(("in", s_port, d_port))
        if d_ref == ref:
            out.append(("in", d_port, s_port))
            if undirected:
                out.append(("out", d_port, s_port))
    return out


def _multiset_symdiff(a, b):
    ca, cb = Counter(a), Counter(b)
    return sum((ca - cb).values()) + sum((cb - ca).values())


def graph_edit_distance(a, b):
    """Bipartite-assignment graph edit distance (Riesen-Bunke) between two subgraphs.

    Deterministic and symmetric. Node sub cost 0 if lib:type equal else 1, plus half
    the local-edge multiset symmetric difference; node del/ins = 1 + half local edges.
    """
    na, nb = a.get("nodes", []), b.get("nodes", [])
    ea, eb = a.get("edges", []), b.get("edges", [])
    n, m = len(na), len(nb)
    if n == 0 and m == 0:
        return 0.0
    la = [_local_edges(x["ref"], ea) for x in na]
    lb = [_local_edges(y["ref"], eb) for y in nb]
    dim = n + m
    INF = float(10 ** 9)
    cost = [[0.0] * dim for _ in range(dim)]
    for i in range(n):
        for j in range(m):
            sub = (0.0 if _node_label(na[i]) == _node_label(nb[j]) else 1.0)
            cost[i][j] = sub + 0.5 * _multiset_symdiff(la[i], lb[j])
        for k in range(n):
            cost[i][m + k] = (1.0 + 0.5 * len(la[i])) if k == i else INF
    for k in range(m):
        for j in range(m):
            cost[n + k][j] = (1.0 + 0.5 * len(lb[k])) if k == j else INF
        for l in range(n):
            cost[n + k][m + l] = 0.0
    return _hungarian(cost)
