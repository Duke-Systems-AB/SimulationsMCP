# M9 clustering + param/interface inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cluster_patterns` MCP tool that groups M8's candidate subgraphs (exact WL bucket + near-miss GED merge) and infers each cluster's parameter schema, interface, and template — the mined pattern candidates M10 will approve.

**Architecture:** A pure, COM-free module (`pattern_cluster.py`) with `graph_edit_distance` (self-contained Hungarian, no scipy), `cluster_candidates` (exact bucket + union-find near-miss merge), and `infer_pattern` (WL-label alignment → params/interface/template), all fixture-tested. A thin `cluster_patterns` entry in `simulation_backend.py` aggregates candidates from offline JSON (`candidatesPaths`), offline PSG (`psgPaths` via M8), and live models (`filePaths` via M7→M8), then calls the core. Wired through backend.ts + index.ts.

**Tech Stack:** Python 3.13 (`statistics`, `collections`), TypeScript (MCP server, zod), pytest, vitest (build check).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-16-m9-cluster-infer-design.md` — every task's requirements implicitly include it.
- Tool count goes **101 → 102** exactly (one new tool: `cluster_patterns`).
- **Any new backend `.py` module MUST be added to `package.json` `copy-files`.**
- **No new third-party dependencies.** Hungarian solver is self-contained (stdlib only). No scipy/numpy.
- Node label = `f"{lib}:{type}"`. Local edges of a node = multiset of `(direction, ownPort, neighborPort)`; a `directionConfident:false` edge contributes both out- and in-views (matches M8's WL).
- GED cost model: node substitution `0` if labels equal else `1`, plus `0.5 * multiset_symdiff(localEdges)`; node deletion/insertion `1 + 0.5*|localEdges|`. GED = optimal assignment cost on the padded `(|A|+|B|)²` matrix. Must be deterministic and symmetric (`ged(a,b)==ged(b,a)`).
- Near-miss merge threshold default `ged_threshold=2`; a cluster formed by any merge → `nearMiss: true`. Union-find (transitive) merge.
- Node alignment across instances uses M8's `wlLabels` (ref → label). Positions labelled by the representative (first) instance's refs. Param keys = `"<repRef>.<paramKey>"`.
- Param inference: constant across instances → `fixed`; varies → `required`, numeric `default`=median + `range`=[min,max], non-numeric `default`=most-common.
- Interface from the representative's `boundaryEdges`: `inlet`→inlet, `outlet`→outlet, `binds`=the edge's `internal`, `role` best-effort (`item`/`value`/null).
- Pure core is COM-free; all COM/aggregation lives in the entry.
- Unit tests in `src/ExtendSimMCP.TypeScript/tests/unit_py/`, each prepends `../../src` to `sys.path`.
- Fail-closed: unreadable `candidatesPath`/`psgPath` → error; `mine_candidates` failure → propagate; no candidates → success with `clusterCount: 0`; candidate missing `wl_fingerprint` → skipped defensively.

---

### Task 1: GED core — Hungarian solver + `graph_edit_distance`

The algorithmic heart: a self-contained min-cost assignment (Kuhn–Munkres) and a bipartite-assignment GED over two candidate subgraphs. Pure, stdlib only.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `_hungarian(cost: list[list[float]]) -> float` — min total cost of a perfect assignment on a square matrix.
  - `_split_ref_port(endpoint: str) -> tuple[str, str]`.
  - `_local_edges(ref: str, edges: list[dict]) -> list[tuple]`.
  - `graph_edit_distance(a: dict, b: dict) -> float` where `a`/`b` have `nodes` (each `ref`,`lib`,`type`) and `edges` (each `from`,`to`, optional `directionConfident`).

- [ ] **Step 1: Write the failing tests**

Create `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pattern_cluster'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_cluster.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py
git commit -m "feat(M9): GED core (self-contained Hungarian + bipartite graph edit distance)"
```

---

### Task 2: `cluster_candidates` (exact bucket + near-miss union-find)

Group candidates by exact WL fingerprint, then union buckets whose representatives are within `ged_threshold` GED. Pure.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`

**Interfaces:**
- Consumes: `graph_edit_distance` (Task 1).
- Produces: `cluster_candidates(candidates: list[dict], ged_threshold: float = 2) -> list[dict]`. Each candidate has `wl_fingerprint`, `nodes`, `edges` (+ carried fields). Returns clusters `{fingerprint, instances: [candidate...], nearMiss: bool}`.

- [ ] **Step 1: Write the failing tests**

Append to `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`:

```python
from pattern_cluster import cluster_candidates


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -k cluster -v`
Expected: FAIL with `ImportError: cannot import name 'cluster_candidates'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`:

```python
def cluster_candidates(candidates, ged_threshold=2):
    """Group candidates by exact WL fingerprint, then union-merge buckets whose
    representatives are within ged_threshold GED. Returns clusters."""
    buckets = {}
    order = []
    for c in candidates:
        fp = c.get("wl_fingerprint")
        if fp is None:
            continue  # malformed -> skip defensively
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -v`
Expected: PASS (13 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_cluster.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py
git commit -m "feat(M9): cluster_candidates (exact WL bucket + near-miss GED union-find)"
```

---

### Task 3: `infer_pattern` (params + interface + template)

Turn a cluster into a mined pattern candidate: align nodes by WL label, infer params (fixed/required + median/range), derive the interface from the representative's boundary edges, and build the placeholder template. Pure.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`

**Interfaces:**
- Consumes: cluster dicts from `cluster_candidates` (Task 2). Each instance candidate carries `nodes` (with `ref`,`lib`,`type`,`isHBlock`,`params`), `edges`, `boundaryEdges`, `wlLabels` (ref→label), `hblockType`, `scopeId`, and optional `source`.
- Produces: `infer_pattern(cluster: dict) -> dict` returning the mined-pattern-candidate shape (`wl_fingerprint`, `support`, `nearMiss`, `hblockType`, `kind`, `params`, `template`, `interface`, `instances`, `example`).

- [ ] **Step 1: Write the failing tests**

Append to `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`:

```python
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
```

Note: constancy requires at least two equal observations. A single observation (one
instance, or a param present on only one instance) cannot establish that the value is
fixed, so it is reported `required` with `default` = the observed value — matching the
spec's "support==1 → required" rule.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -k infer -v`
Expected: FAIL with `ImportError: cannot import name 'infer_pattern'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`:

```python
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


def _role_of(internal):
    port = internal.rpartition(".")[2].lower()
    if "item" in port:
        return "item"
    if "value" in port:
        return "value"
    return None


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
        for node in inst.get("nodes", []):
            lbl = labels.get(node["ref"])
            if lbl is None:
                continue
            for k, v in (node.get("params") or {}).items():
                values[(lbl, k)].append(v)

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
        tnodes.append(tn)
    template = {"nodes": tnodes, "edges": rep.get("edges", [])}

    # Interface from the representative's boundary edges.
    inlets, outlets = [], []
    for be in rep.get("boundaryEdges", []):
        entry = {"binds": be.get("internal", ""), "role": _role_of(be.get("internal", ""))}
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -v`
Expected: PASS (21 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_cluster.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py
git commit -m "feat(M9): infer_pattern (WL-label alignment, param + interface + template)"
```

---

### Task 4: `cluster_patterns` backend entry (aggregate sources)

Add the thin entry that aggregates candidates from `candidatesPaths` (offline JSON), `psgPaths` (offline via M8), and `filePaths` (live via M7→M8), then clusters + infers. The offline `candidatesPaths` path touches NO COM and is unit-tested here.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (add function + `COMMANDS` entry)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_cluster_patterns_offline.py`

**Interfaces:**
- Consumes: `pattern_cluster.cluster_candidates` + `pattern_cluster.infer_pattern` (Tasks 2–3); existing `mine_candidates` (M8), `_com_error`.
- Produces: `cluster_patterns(candidates_paths=None, file_paths=None, psg_paths=None, save_path=None) -> dict`; `COMMANDS["cluster_patterns"]`.

- [ ] **Step 1: Add the entry function**

Add immediately AFTER the `mine_candidates` function definition (added in M8; it ends with `return _com_error(e, "mine_candidates")`), before the `# Dispatch table` / `COMMANDS = {` block, in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`:

```python
def cluster_patterns(candidates_paths=None, file_paths=None, psg_paths=None, save_path=None):
    """Aggregate candidate subgraphs from offline JSON (candidates_paths), offline PSG
    (psg_paths via mine_candidates), and live models (file_paths via mine_candidates),
    then cluster + infer mined pattern candidates."""
    import json as _json
    import pattern_cluster
    try:
        all_candidates = []
        for p in (candidates_paths or []):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = _json.load(f)
            except Exception as e:
                return {"success": False, "errorCode": "CANDIDATES_PATH_UNREADABLE",
                        "error": f"cannot read candidatesPath: {e}", "candidatesPath": p}
            for c in data.get("candidates", []):
                c = dict(c)
                c.setdefault("source", p)
                all_candidates.append(c)
        for p in (psg_paths or []):
            res = mine_candidates(psg_path=p)
            if not res.get("success"):
                return res
            for c in res.get("candidates", []):
                c = dict(c)
                c.setdefault("source", p)
                all_candidates.append(c)
        for p in (file_paths or []):
            res = mine_candidates(file_path=p)
            if not res.get("success"):
                return res
            for c in res.get("candidates", []):
                c = dict(c)
                c.setdefault("source", p)
                all_candidates.append(c)

        clusters = pattern_cluster.cluster_candidates(all_candidates)
        patterns = [pattern_cluster.infer_pattern(cl) for cl in clusters]

        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                _json.dump({"success": True, "clusterCount": len(patterns),
                            "patterns": patterns}, f, indent=2, allow_nan=False)
            return {"success": True, "savedTo": save_path, "clusterCount": len(patterns)}
        return {"success": True, "clusterCount": len(patterns), "patterns": patterns}
    except Exception as e:
        return _com_error(e, "cluster_patterns")
```

- [ ] **Step 2: Register the command**

In the `COMMANDS` dict in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`, add next to the `mine_candidates` entry:

```python
    "cluster_patterns": lambda p: cluster_patterns(
        p.get("candidatesPaths"), p.get("filePaths"), p.get("psgPaths"), p.get("savePath")
    ),
```

- [ ] **Step 3: Write the offline aggregation unit tests**

Create `src/ExtendSimMCP.TypeScript/tests/unit_py/test_cluster_patterns_offline.py`:

```python
# tests/unit_py/test_cluster_patterns_offline.py
"""Unit tests for the cluster_patterns OFFLINE aggregation path (candidatesPaths).
Never touches COM; runs without ExtendSim (pywin32 import only)."""
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import simulation_backend as b


def _cand(fp, ref, d):
    return {"wl_fingerprint": fp, "scopeId": "h1", "hblockType": "pure",
            "nodes": [{"ref": ref, "lib": "Item", "type": "Activity",
                       "isHBlock": False, "params": {"D": d}}],
            "edges": [], "boundaryEdges": [], "wlLabels": {ref: "L1"}}


def _write(tmp_path, name, cands):
    p = tmp_path / name
    p.write_text(json.dumps({"success": True, "candidateCount": len(cands),
                             "candidates": cands}), encoding="utf-8")
    return str(p)


def test_cluster_patterns_aggregates_two_candidate_files(tmp_path):
    p1 = _write(tmp_path, "a.json", [_cand("FP1", "b1", 2)])
    p2 = _write(tmp_path, "b.json", [_cand("FP1", "x1", 8)])
    res = b.cluster_patterns(candidates_paths=[p1, p2])
    assert res["success"] is True
    assert res["clusterCount"] == 1
    pat = res["patterns"][0]
    assert pat["support"] == 2
    assert pat["params"]["b1.D"]["required"] is True
    assert pat["params"]["b1.D"]["range"] == [2, 8]


def test_cluster_patterns_empty_sources_zero_clusters():
    res = b.cluster_patterns()
    assert res["success"] is True and res["clusterCount"] == 0 and res["patterns"] == []


def test_cluster_patterns_save_path(tmp_path):
    p1 = _write(tmp_path, "a.json", [_cand("FP1", "b1", 5)])
    out = tmp_path / "patterns.json"
    res = b.cluster_patterns(candidates_paths=[p1], save_path=str(out))
    assert res["success"] is True and res["savedTo"] == str(out)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["clusterCount"] == 1


def test_cluster_patterns_unreadable_candidates_path():
    res = b.cluster_patterns(candidates_paths=["C:/nonexistent/nope.json"])
    assert res["success"] is False
    assert res["errorCode"] == "CANDIDATES_PATH_UNREADABLE"
```

- [ ] **Step 4: Run the offline tests**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_cluster_patterns_offline.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Verify dispatch wiring (no COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -c "import sys; sys.path.insert(0,'src'); import pattern_cluster, simulation_backend as b; assert 'cluster_patterns' in b.COMMANDS; assert callable(b.cluster_patterns); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_cluster_patterns_offline.py
git commit -m "feat(M9): cluster_patterns entry (aggregate candidatesPaths / psgPaths / filePaths)"
```

---

### Task 5: Wire the MCP tool (backend.ts, index.ts, copy-files)

Expose `cluster_patterns` as an MCP tool and ship the new module to `dist/`. Tool count 101 → 102.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts` (add `clusterPatterns`)
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts` (add `server.tool("cluster_patterns", ...)`)
- Modify: `src/ExtendSimMCP.TypeScript/package.json` (add `pattern_cluster.py` to `copy-files`)

**Interfaces:**
- Consumes: `sendCommand` (backend.ts); `safeToolCall` + `backend` (index.ts); the `cluster_patterns` COMMAND (Task 4).
- Produces: `backend.clusterPatterns({candidatesPaths?, filePaths?, psgPaths?, savePath?})`; MCP tool `cluster_patterns`.

- [ ] **Step 1: Add the backend proxy**

In `src/ExtendSimMCP.TypeScript/src/backend.ts`, immediately after the `mineCandidates` function (ends with `return await sendCommand("mine_candidates", params);` then `}`), add:

```typescript
export async function clusterPatterns(params: {
  candidatesPaths?: string[];
  filePaths?: string[];
  psgPaths?: string[];
  savePath?: string;
}) {
  return await sendCommand("cluster_patterns", params);
}
```

- [ ] **Step 2: Register the tool**

In `src/ExtendSimMCP.TypeScript/src/index.ts`, immediately after the `server.tool("mine_candidates", ... )` block (ends with `);`), add:

```typescript
server.tool(
  "cluster_patterns",
  "Cluster mined candidate subgraphs into pattern candidates: group instances by exact Weisfeiler-Lehman fingerprint, merge near-misses via graph edit distance (flagged for review), and infer each cluster's parameter schema (fixed/required + median/range), interface (from boundary edges), and template. Aggregates candidates from candidatesPaths (offline JSON saved by mine_candidates), psgPaths (offline PSG), and filePaths (live model). Output feeds pattern approval (M10). Use savePath to write JSON.",
  {
    candidatesPaths: z.array(z.string()).optional().describe("Saved mine_candidates JSON files to aggregate (offline)"),
    filePaths: z.array(z.string()).optional().describe("Model .mox files to mine live (opened read-only) then cluster"),
    psgPaths: z.array(z.string()).optional().describe("Saved PSG JSON files to mine (offline) then cluster"),
    savePath: z.string().optional().describe("If set, write JSON to file and return path instead of inline data")
  },
  async ({ candidatesPaths, filePaths, psgPaths, savePath }) => {
    return safeToolCall("cluster_patterns", () => backend.clusterPatterns({ candidatesPaths, filePaths, psgPaths, savePath }), { candidatesPaths, filePaths, psgPaths, savePath });
  }
);
```

- [ ] **Step 3: Add the module to copy-files**

In `src/ExtendSimMCP.TypeScript/package.json`, in the `copy-files` script string, add this immediately after the `pattern_mine.py` copy statement:

```
fs.copyFileSync('src/pattern_cluster.py', 'dist/pattern_cluster.py');
```

- [ ] **Step 4: Build and verify tool count + dist copy**

Run: `cd src/ExtendSimMCP.TypeScript && npm run build`
Expected: `tsc` compiles with no errors; copy-files runs.

Run: `cd src/ExtendSimMCP.TypeScript && grep -c "server.tool(" src/index.ts && test -f dist/pattern_cluster.py && echo "pattern_cluster copied"`
Expected: prints `102` then `pattern_cluster copied`.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/package.json
git commit -m "feat(M9): wire cluster_patterns MCP tool (101->102) + copy-files"
```

---

### Task 6: Live smoke test (deferred run) + backlog

Add a guarded live test (auto-skips without models) and record the deferred live run in the backlog. Do NOT run it against COM.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/live/test_cluster_patterns_live.py`
- Modify: `docs/BACKLOG.md`

**Interfaces:**
- Consumes: the live `cluster_patterns` entry (Task 4).

- [ ] **Step 1: Write the guarded live smoke test**

Create `src/ExtendSimMCP.TypeScript/tests/live/test_cluster_patterns_live.py`:

```python
# tests/live/test_cluster_patterns_live.py
"""Live smoke test for cluster_patterns. Requires ExtendSim 2024 Pro running with a
model open that contains repeated H-block instances. Skips if COM/model unavailable.
Run: python -m pytest tests/live/test_cluster_patterns_live.py -v -s

DEFERRED (M9 Task 6): not yet run live — pairs with M7/M8's deferred verification.
Provide a real model via filePaths, or an active model, to exercise M7->M8->M9.
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_cluster_patterns_live_from_active_model():
    import simulation_backend as b
    # mine the active model, then cluster its candidates via an in-memory round-trip
    mined = b.mine_candidates()
    if not mined.get("success") or not mined.get("candidates"):
        pytest.skip(f"no live model / no candidates: {mined.get('error') or mined.get('errorCode')}")
    # write candidates to a temp file and cluster offline (exercises aggregation too)
    import tempfile, json
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"candidates": mined["candidates"]}, f)
        path = f.name
    res = b.cluster_patterns(candidates_paths=[path])
    assert res["success"] is True
    print("clusterCount:", res["clusterCount"])
    for pat in res["patterns"]:
        print(" ", pat["wl_fingerprint"][:8], "support=", pat["support"],
              "nearMiss=", pat["nearMiss"], "params=", list(pat["params"].keys()))
        assert pat["support"] >= 1
```

- [ ] **Step 2: Verify it collects (do not execute against COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_cluster_patterns_live.py --collect-only -q`
Expected: `1 test collected`.

- [ ] **Step 3: Record the deferral in the backlog**

In `docs/BACKLOG.md`, immediately after the `## M8 mine_candidates — live verification (Task 5)` section's last bullet (starts with `- Deferred 2026-07-16`), add:

```markdown
## M9 cluster_patterns — live verification (Task 6)

`cluster_patterns` shipped (M9, `src/pattern_cluster.py` + entry in
`simulation_backend.py`), pure core fully unit-tested incl. the offline
`candidatesPaths` aggregation, but the live paths (`filePaths` via M7→M8) have not
been run against real ExtendSim. Follow-up (pairs with M7/M8):

- Run `src/ExtendSimMCP.TypeScript/tests/live/test_cluster_patterns_live.py` against a
  live ExtendSim with a model containing repeated H-block instances (safe COM pattern).
- Confirm repeated instances cluster (support > 1), that a varying param is inferred
  `required` with a sensible median/range, and that near-miss merges look right.
- Deferred 2026-07-16.
```

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/live/test_cluster_patterns_live.py docs/BACKLOG.md
git commit -m "test(M9): guarded live smoke test for cluster_patterns (deferred run) + backlog"
```

---

## Self-Review

**Spec coverage:**
- Pure module `pattern_cluster.py` (GED + cluster + infer) → Tasks 1–3. ✅
- Full GED, self-contained Hungarian, no scipy → Task 1. ✅
- Cost model (node sub/del/ins, 0.5×local-edge symdiff) → Task 1. ✅
- Exact WL bucket + near-miss union-find merge, nearMiss flag, threshold 2 → Task 2. ✅
- WL-label alignment; param fixed/required + median/range/most-common → Task 3. ✅
- Interface from representative boundaryEdges, role best-effort → Task 3. ✅
- Template placeholders/literals; hblockType consistency; kind → Task 3. ✅
- Output shape (support/nearMiss/instances/example) → Task 3. ✅
- Three aggregation sources (candidatesPaths/psgPaths/filePaths) → Task 4. ✅
- Offline path unit-tested; empty → 0; unreadable → error → Task 4. ✅
- savePath contract → Task 4. ✅
- Tool count 101→102 + copy-files → Task 5. ✅
- Live deferred + backlog → Task 6. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `graph_edit_distance(a,b)` used in Task 2's `cluster_candidates`; cluster dict shape `{fingerprint, instances, nearMiss}` produced in Task 2 and consumed in Task 3's `infer_pattern`; candidate fields (`wl_fingerprint`, `nodes`, `edges`, `boundaryEdges`, `wlLabels`, `hblockType`, `scopeId`) match M8's output and Task 3/4 usage; `cluster_patterns(candidates_paths, file_paths, psg_paths, save_path)` matches the COMMANDS lambda order and `backend.clusterPatterns` param names (`candidatesPaths`/`filePaths`/`psgPaths`/`savePath`); `mine_candidates(psg_path=..)`/`(file_path=..)` match M8's shipped signature. ✅
