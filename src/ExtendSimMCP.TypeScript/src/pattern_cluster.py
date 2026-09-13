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

from patterns import split_ref_port as _split_ref_port, infer_port_role


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


def cluster_candidates(candidates, ged_threshold=2):
    """Group candidates by exact WL fingerprint, then union-merge buckets whose
    representatives are within ged_threshold GED. Returns clusters."""
    buckets = {}
    order = []
    for c in candidates:
        fp = c.get("wl_fingerprint")
        if fp is None or not c.get("wlLabels"):
            continue  # malformed (no fingerprint or no WL labels) -> skip defensively
        if fp not in buckets:
            buckets[fp] = []
            order.append(fp)
        buckets[fp].append(c)

    parent = {fp: fp for fp in order}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    reps = {fp: buckets[fp][0] for fp in order}
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            if find(order[i]) == find(order[j]):
                continue
            if graph_edit_distance(reps[order[i]], reps[order[j]]) <= ged_threshold:
                union(order[i], order[j])

    groups = defaultdict(list)
    for fp in order:
        groups[find(fp)].append(fp)

    clusters = []
    for root, member_fps in groups.items():
        instances = []
        for fp in member_fps:
            instances.extend(buckets[fp])
        clusters.append({
            "fingerprint": root,
            "instances": instances,
            "nearMiss": len(member_fps) > 1,
        })
    return clusters


def _infer_param(values):
    """Classify a param's values across instances into fixed / required + default."""
    if not values:
        return {"type": "number", "required": True}
    is_num = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
    typ = "number" if is_num else "string"
    if len(values) >= 2 and len(set(values)) == 1:
        return {"type": typ, "required": False, "fixed": values[0]}
    if len(set(values)) == 1:
        # single observation -> cannot conclude constant -> required (default = the value)
        return {"type": typ, "required": True, "default": values[0]}
    info = {"type": typ, "required": True}
    if is_num:
        median = statistics.median(values)
        info["default"] = int(median) if float(median).is_integer() else median
        info["range"] = [min(values), max(values)]
    else:
        info["default"] = Counter(values).most_common(1)[0][0]
    return info


def infer_pattern(cluster):
    """Turn a cluster into a mined pattern candidate (params/interface/template)."""
    instances = cluster["instances"]
    rep = instances[0]
    rep_nodes = rep.get("nodes", [])
    rep_labels = rep.get("wlLabels", {})

    # Collect each param value across instances, keyed by (WL label, paramKey).
    values = defaultdict(list)
    for inst in instances:
        labels = inst.get("wlLabels", {})
        seen = set()  # per-instance (label, key, value) -> set-merge symmetric nodes
        for node in inst.get("nodes", []):
            lbl = labels.get(node["ref"])
            if lbl is None:
                continue
            for k, v in (node.get("params") or {}).items():
                marker = (lbl, k, v)
                if marker in seen:
                    continue
                seen.add(marker)
                values[(lbl, k)].append(v)

    # Collect each instance's setAttributes (whole list, verbatim) per WL
    # label — structural, not a scalar param, so no varies/fixed treatment;
    # just majority/first + a disagreement flag (W3-6b).
    sa_by_label = defaultdict(list)  # label -> [(signature, original_list), ...]
    for inst in instances:
        labels = inst.get("wlLabels", {})
        seen_sa = set()
        for node in inst.get("nodes", []):
            lbl = labels.get(node["ref"])
            if lbl is None or not node.get("setAttributes"):
                continue
            sa = node["setAttributes"]
            sig = tuple(tuple(sorted(e.items())) for e in sa)
            marker = (lbl, sig)
            if marker in seen_sa:
                continue
            seen_sa.add(marker)
            sa_by_label[lbl].append((sig, sa))

    # Params + example keyed by the representative's refs.
    params, example = {}, {}
    for node in rep_nodes:
        lbl = rep_labels.get(node["ref"])
        for k, v in (node.get("params") or {}).items():
            key = f"{node['ref']}.{k}"
            params[key] = _infer_param(values.get((lbl, k), [v]))
            example[key] = v

    # Template from the representative: placeholder for required, literal for fixed.
    tnodes = []
    for node in rep_nodes:
        tn = {"ref": node["ref"], "lib": node.get("lib", ""), "type": node.get("type", "")}
        p = {}
        for k, v in (node.get("params") or {}).items():
            key = f"{node['ref']}.{k}"
            p[k] = "{{" + key + "}}" if params.get(key, {}).get("required") else v
        if p:
            tn["params"] = p
        if node.get("isHBlock"):
            tn["isHBlock"] = True
        sa_entries = sa_by_label.get(rep_labels.get(node["ref"]))
        if sa_entries:
            counts = Counter(sig for sig, _ in sa_entries)
            top_sig, _ = counts.most_common(1)[0]
            tn["setAttributes"] = next(sa for sig, sa in sa_entries if sig == top_sig)
            if len(counts) > 1:
                tn["setAttributesVaries"] = True
        tnodes.append(tn)
    template = {"nodes": tnodes, "edges": rep.get("edges", [])}

    # Interface from the representative's boundary edges.
    inlets, outlets = [], []
    for be in rep.get("boundaryEdges", []):
        entry = {"binds": be.get("internal", ""), "role": infer_port_role(be.get("internal", ""))}
        if be.get("crosses") == "inlet":
            inlets.append(entry)
        elif be.get("crosses") == "outlet":
            outlets.append(entry)
    interface = {"inlets": inlets, "outlets": outlets}

    hblock_types = {inst.get("hblockType") for inst in instances}
    hblock_type = instances[0].get("hblockType") if len(hblock_types) == 1 else None
    kind = "composite" if any(n.get("isHBlock") for n in rep_nodes) else "molecule"

    return {
        "wl_fingerprint": cluster.get("fingerprint"),
        "support": len(instances),
        "nearMiss": cluster.get("nearMiss", False),
        "hblockType": hblock_type,
        "kind": kind,
        "params": params,
        "template": template,
        "interface": interface,
        "instances": [{"scopeId": i.get("scopeId"), "source": i.get("source")}
                      for i in instances],
        "example": example,
    }
