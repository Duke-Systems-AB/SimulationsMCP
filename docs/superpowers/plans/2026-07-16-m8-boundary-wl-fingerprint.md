# M8 boundary detection + WL fingerprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `mine_candidates` MCP tool that turns M7's multi-scale PSG into candidate molecule subgraphs, each tagged with a stable Weisfeiler–Lehman fingerprint.

**Architecture:** A pure, COM-free module (`pattern_mine.py`) with `wl_fingerprint` (§9.1, stable `blake2b` hash) and `detect_candidates`, unit-tested with fixtures. A thin `mine_candidates` entry in `simulation_backend.py` resolves a PSG three ways (offline `psgPath` JSON / `filePath` via M7's `extract_psg` / active model) then calls the core. Wired through `backend.ts` + `index.ts` + Python `COMMANDS`.

**Tech Stack:** Python 3.13 (`hashlib`), TypeScript (MCP server, zod), pytest, vitest (build check).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-16-m8-boundary-wl-fingerprint-design.md` — every task's requirements implicitly include it.
- Tool count goes **100 → 101** exactly (one new tool: `mine_candidates`).
- **Any new backend `.py` module MUST be added to `package.json` `copy-files`** or it `ModuleNotFoundError`s in `dist/`.
- **Stable hashing only:** `hashlib.blake2b(repr(value).encode("utf-8"), digest_size=16).hexdigest()` (32 hex chars). NEVER Python's builtin `hash()` (per-process salted).
- WL node label = `f"{lib}:{blocktype}"` (topology, NOT params). Edge signature = `(direction, ownPort, neighborPort, neighborLabel)`. `k=4` iterations. Interior edges only; boundary edges carried, not fingerprinted.
- A `directionConfident:false` edge contributes BOTH an out- and an in-view (orientation-invariant); a normal edge is `from`=out side, `to`=in side.
- Candidate = one per H-block scope; root scope excluded. `kind="composite"` if any interior node `isHBlock` else `"molecule"`. `confidence="high"` if `hblockType=="pure"` else `"candidate"`.
- Pure core is COM-free; all COM/PSG-resolution lives in the `mine_candidates` entry.
- Unit tests live in `src/ExtendSimMCP.TypeScript/tests/unit_py/`; each prepends `../../src` to `sys.path`.
- Fail-closed: unreadable `psgPath` → error; `extract_psg` failure → propagate; no H-block scopes → success with `candidateCount: 0`.

---

### Task 1: WL fingerprint core (`wl_fingerprint`)

The algorithmic heart: a stable Weisfeiler–Lehman fingerprint of a subgraph's interior, param-independent, with the orientation-invariance rule for uncertain edges. Pure, no COM.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/pattern_mine.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_mine.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `_stable_hash(value) -> str` (32-hex-char blake2b digest).
  - `_split_ref_port(endpoint: str) -> tuple[str, str]` splitting `"b141.inCon0"` → `("b141", "inCon0")`.
  - `wl_fingerprint(nodes: list[dict], edges: list[dict], k: int = 4) -> tuple[str, dict]` returning `(fingerprint, labels)` where `labels` maps node `ref` → final WL label. `nodes` items have `ref`, `lib`, `type`; `edges` items have `from`, `to`, optional `directionConfident`.

- [ ] **Step 1: Write the failing tests**

Create `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_mine.py`:

```python
# tests/unit_py/test_pattern_mine.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pattern_mine import wl_fingerprint, _split_ref_port, _stable_hash


def _n(ref, lib, typ):
    return {"ref": ref, "lib": lib, "type": typ}


def test_split_ref_port_rightmost_dot():
    assert _split_ref_port("b141.inCon0") == ("b141", "inCon0")


def test_stable_hash_is_deterministic_and_32_hex():
    h1 = _stable_hash(("a", 1))
    h2 = _stable_hash(("a", 1))
    assert h1 == h2
    assert len(h1) == 32 and all(c in "0123456789abcdef" for c in h1)


def test_fingerprint_is_deterministic():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    f1, _ = wl_fingerprint(nodes, edges)
    f2, _ = wl_fingerprint(nodes, edges)
    assert f1 == f2
    assert len(f1) == 32


def test_isomorphic_subgraphs_same_fingerprint_regardless_of_ids_and_params():
    # same topology, different block ids (refs) and no params in labels
    a_nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    a_edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    b_nodes = [_n("b20", "Item", "Queue"), _n("b30", "Item", "Activity")]
    b_edges = [{"from": "b20.outCon0", "to": "b30.inCon0"}]
    fa, _ = wl_fingerprint(a_nodes, a_edges)
    fb, _ = wl_fingerprint(b_nodes, b_edges)
    assert fa == fb


def test_different_topology_different_fingerprint():
    q_to_a = ([_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")],
              [{"from": "b2.outCon0", "to": "b3.inCon0"}])
    a_to_q = ([_n("b2", "Item", "Activity"), _n("b3", "Item", "Queue")],
              [{"from": "b2.outCon0", "to": "b3.inCon0"}])
    fa, _ = wl_fingerprint(*q_to_a)
    fb, _ = wl_fingerprint(*a_to_q)
    assert fa != fb


def test_port_names_matter_shutdown_vs_flow():
    nodes = [_n("b2", "Item", "Create"), _n("b3", "Item", "Activity")]
    flow = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    shutdown = [{"from": "b2.shutdown", "to": "b3.shutdown"}]
    ff, _ = wl_fingerprint(nodes, flow)
    fs, _ = wl_fingerprint(nodes, shutdown)
    assert ff != fs


def test_confident_edge_direction_matters():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    fwd = [{"from": "b2.p", "to": "b3.p"}]
    rev = [{"from": "b3.p", "to": "b2.p"}]
    ff, _ = wl_fingerprint(nodes, fwd)
    fr, _ = wl_fingerprint(nodes, rev)
    assert ff != fr


def test_unconfident_edge_is_orientation_invariant():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    fwd = [{"from": "b2.p", "to": "b3.p", "directionConfident": False}]
    rev = [{"from": "b3.p", "to": "b2.p", "directionConfident": False}]
    ff, _ = wl_fingerprint(nodes, fwd)
    fr, _ = wl_fingerprint(nodes, rev)
    assert ff == fr


def test_labels_returned_for_each_node():
    nodes = [_n("b2", "Item", "Queue"), _n("b3", "Item", "Activity")]
    edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    _, labels = wl_fingerprint(nodes, edges)
    assert set(labels.keys()) == {"b2", "b3"}


def test_missing_lib_type_does_not_crash():
    nodes = [{"ref": "b2"}, {"ref": "b3", "lib": "Item", "type": "Exit"}]
    edges = [{"from": "b2.outCon0", "to": "b3.inCon0"}]
    f, labels = wl_fingerprint(nodes, edges)
    assert len(f) == 32 and set(labels) == {"b2", "b3"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_mine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pattern_mine'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ExtendSimMCP.TypeScript/src/pattern_mine.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_mine.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_mine.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_mine.py
git commit -m "feat(M8): WL fingerprint core (stable blake2b, param-independent)"
```

---

### Task 2: `detect_candidates`

Walk M7's multi-scale PSG and emit one candidate per H-block scope, each with its WL fingerprint, kind, confidence, and carried boundary edges. Pure.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/pattern_mine.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_mine.py`

**Interfaces:**
- Consumes: `wl_fingerprint` (Task 1).
- Produces: `detect_candidates(psg: dict) -> list[dict]`. Input `psg` = `{"modelName": str, "scopes": [...]}` in M7's shape (scopes have `scopeId`, `kind`, `hblockType?`, `label?`, `nodes`, `edges`, `boundaryEdges`). Output candidate dict: `{scopeId, hblockType, kind, label, wl_fingerprint, nodeCount, nodes, edges, boundaryEdges, wlLabels, confidence}`.

- [ ] **Step 1: Write the failing tests**

Append to `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_mine.py`:

```python
from pattern_mine import detect_candidates


def _scope(scope_id, kind, nodes, edges=None, boundary=None, parent=None,
           hblock_type=None, label=""):
    s = {"scopeId": scope_id, "kind": kind, "parentScopeId": parent,
         "nodes": nodes, "edges": edges or [], "boundaryEdges": boundary or []}
    if kind == "hblock":
        s["hblockType"] = hblock_type
        s["label"] = label
    return s


def _hnode(ref, lib, typ, is_h=False, child=None):
    n = {"ref": ref, "blockId": int(ref[1:]), "lib": lib, "type": typ,
         "isHBlock": is_h, "params": {}}
    if is_h and child:
        n["scopeId"] = child
    return n


def test_detect_candidates_one_per_hblock_root_excluded():
    psg = {"modelName": "m.mox", "scopes": [
        _scope("root", "root", [_hnode("b1", "", "Hierarchical", is_h=True, child="h1")]),
        _scope("h1", "hblock", [_hnode("b2", "Item", "Queue"), _hnode("b3", "Item", "Activity")],
               edges=[{"from": "b2.outCon0", "to": "b3.inCon0"}],
               hblock_type="pure", label="Machine"),
    ]}
    cands = detect_candidates(psg)
    assert len(cands) == 1
    c = cands[0]
    assert c["scopeId"] == "h1"
    assert c["kind"] == "molecule"
    assert c["confidence"] == "high"
    assert c["hblockType"] == "pure"
    assert c["nodeCount"] == 2
    assert c["label"] == "Machine"
    assert len(c["wl_fingerprint"]) == 32
    assert set(c["wlLabels"].keys()) == {"b2", "b3"}


def test_detect_candidates_physical_and_null_are_candidate_confidence():
    psg = {"modelName": "m.mox", "scopes": [
        _scope("h1", "hblock", [_hnode("b2", "Item", "Queue")], hblock_type="physical"),
        _scope("h2", "hblock", [_hnode("b3", "Item", "Queue")], hblock_type=None),
    ]}
    cands = detect_candidates(psg)
    assert cands[0]["confidence"] == "candidate"
    assert cands[1]["confidence"] == "candidate"


def test_detect_candidates_composite_when_interior_has_hblock():
    psg = {"modelName": "m.mox", "scopes": [
        _scope("h1", "hblock",
               [_hnode("b2", "Item", "Queue"),
                _hnode("b3", "", "Hierarchical", is_h=True, child="h3")],
               hblock_type="pure"),
        _scope("h3", "hblock", [_hnode("b4", "Item", "Activity")], hblock_type="pure"),
    ]}
    cands = detect_candidates(psg)
    by_id = {c["scopeId"]: c for c in cands}
    assert by_id["h1"]["kind"] == "composite"
    assert by_id["h3"]["kind"] == "molecule"


def test_detect_candidates_carries_boundary_edges_untouched():
    b = [{"internal": "b2.inCon0", "crosses": "inlet", "boundaryConnector": "inCon0"}]
    psg = {"modelName": "m.mox", "scopes": [
        _scope("h1", "hblock", [_hnode("b2", "Item", "Queue")], boundary=b, hblock_type="pure"),
    ]}
    assert detect_candidates(psg)[0]["boundaryEdges"] == b


def test_detect_candidates_flat_model_yields_empty():
    psg = {"modelName": "flat.mox", "scopes": [
        _scope("root", "root", [_hnode("b1", "Item", "Create"), _hnode("b2", "Item", "Exit")],
               edges=[{"from": "b1.outCon0", "to": "b2.inCon0"}]),
    ]}
    assert detect_candidates(psg) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_mine.py -k detect_candidates -v`
Expected: FAIL with `ImportError: cannot import name 'detect_candidates'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ExtendSimMCP.TypeScript/src/pattern_mine.py`:

```python
def detect_candidates(psg):
    """Emit one candidate molecule subgraph per H-block scope of a multi-scale PSG.

    Root scope (the model's flow) is excluded. Each candidate carries its interior
    subgraph, boundary edges, WL fingerprint, and per-node WL labels.
    """
    candidates = []
    for scope in psg.get("scopes", []):
        if scope.get("kind") != "hblock":
            continue
        nodes = scope.get("nodes", [])
        edges = scope.get("edges", [])
        fingerprint, labels = wl_fingerprint(nodes, edges)
        hblock_type = scope.get("hblockType")
        is_composite = any(n.get("isHBlock") for n in nodes)
        candidates.append({
            "scopeId": scope["scopeId"],
            "hblockType": hblock_type,
            "kind": "composite" if is_composite else "molecule",
            "label": scope.get("label", ""),
            "wl_fingerprint": fingerprint,
            "nodeCount": len(nodes),
            "nodes": nodes,
            "edges": edges,
            "boundaryEdges": scope.get("boundaryEdges", []),
            "wlLabels": labels,
            "confidence": "high" if hblock_type == "pure" else "candidate",
        })
    return candidates
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_mine.py -v`
Expected: PASS (15 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_mine.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_mine.py
git commit -m "feat(M8): detect_candidates from multi-scale PSG (one per H-block)"
```

---

### Task 3: `mine_candidates` backend entry (three input paths)

Add the thin entry that resolves a PSG (offline `psgPath` JSON / `filePath` via `extract_psg` / active model) and runs `detect_candidates`. The offline `psgPath` path touches NO COM, so it is unit-tested end-to-end here; the live paths are verified in Task 5.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (add function + `COMMANDS` entry)
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_mine_candidates_offline.py`

**Interfaces:**
- Consumes: `pattern_mine.detect_candidates` (Task 2); existing `extract_psg` (M7), `_com_error`.
- Produces: `mine_candidates(file_path=None, psg_path=None, save_path=None, model_id=None) -> dict`; `COMMANDS["mine_candidates"]`.

- [ ] **Step 1: Add the entry function**

Add immediately AFTER the `extract_psg` function definition (it ends with its `finally:` block, just before the `# Dispatch table` / `COMMANDS = {` block) in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`:

```python
def mine_candidates(file_path=None, psg_path=None, save_path=None, model_id=None):
    """Detect candidate molecule subgraphs + WL fingerprints from a model's PSG.

    PSG source (priority): psg_path (offline JSON, no COM) -> file_path (extract_psg
    opens it) -> active model (extract_psg). Pure mining runs on the resolved PSG.
    """
    import json as _json
    import pattern_mine
    try:
        if psg_path:
            try:
                with open(psg_path, "r", encoding="utf-8") as f:
                    psg = _json.load(f)
            except Exception as e:
                return {"success": False, "errorCode": "PSG_PATH_UNREADABLE",
                        "error": f"cannot read psgPath: {e}", "psgPath": psg_path}
        else:
            res = extract_psg(file_path=file_path, model_id=model_id)
            if not res.get("success"):
                return res
            psg = {"modelName": res.get("modelName", ""), "scopes": res.get("scopes", [])}

        model_name = psg.get("modelName", "")
        candidates = pattern_mine.detect_candidates(psg)

        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                _json.dump({"success": True, "modelName": model_name,
                            "candidateCount": len(candidates), "candidates": candidates},
                           f, indent=2, allow_nan=False)
            return {"success": True, "savedTo": save_path, "modelName": model_name,
                    "candidateCount": len(candidates)}
        return {"success": True, "modelName": model_name,
                "candidateCount": len(candidates), "candidates": candidates}
    except Exception as e:
        return _com_error(e, "mine_candidates")
```

- [ ] **Step 2: Register the command**

In the `COMMANDS` dict in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`, add next to the `extract_psg` entry:

```python
    "mine_candidates": lambda p: mine_candidates(
        p.get("filePath"), p.get("psgPath"), p.get("savePath"), p.get("modelId")
    ),
```

- [ ] **Step 3: Write the offline-path unit tests**

Create `src/ExtendSimMCP.TypeScript/tests/unit_py/test_mine_candidates_offline.py`:

```python
# tests/unit_py/test_mine_candidates_offline.py
"""Unit tests for the mine_candidates OFFLINE path (psgPath JSON -> candidates).
This path never touches COM, so it runs without ExtendSim. Importing
simulation_backend requires pywin32 installed but no running ExtendSim.
"""
import os, sys, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import simulation_backend as b


def _fixture_psg():
    return {"modelName": "demo.mox", "scopes": [
        {"scopeId": "root", "kind": "root", "parentScopeId": None,
         "nodes": [{"ref": "b1", "blockId": 1, "lib": "", "type": "Hierarchical",
                    "isHBlock": True, "scopeId": "h1", "params": {}}],
         "edges": [], "boundaryEdges": []},
        {"scopeId": "h1", "kind": "hblock", "parentScopeId": "root",
         "hblockType": "pure", "label": "Machine",
         "nodes": [
            {"ref": "b2", "blockId": 2, "lib": "Item", "type": "Queue",
             "isHBlock": False, "params": {}},
            {"ref": "b3", "blockId": 3, "lib": "Item", "type": "Activity",
             "isHBlock": False, "params": {"D": 5}},
         ],
         "edges": [{"from": "b2.outCon0", "to": "b3.inCon0"}],
         "boundaryEdges": [{"internal": "b2.inCon0", "crosses": "inlet",
                            "boundaryConnector": "inCon0"}]},
    ]}


def test_mine_candidates_offline_from_psg_path(tmp_path):
    p = tmp_path / "psg.json"
    p.write_text(json.dumps(_fixture_psg()), encoding="utf-8")
    res = b.mine_candidates(psg_path=str(p))
    assert res["success"] is True
    assert res["candidateCount"] == 1
    c = res["candidates"][0]
    assert c["scopeId"] == "h1"
    assert c["kind"] == "molecule"
    assert c["confidence"] == "high"
    assert c["nodeCount"] == 2
    assert len(c["wl_fingerprint"]) == 32
    assert c["boundaryEdges"][0]["crosses"] == "inlet"


def test_mine_candidates_offline_save_path_writes_file(tmp_path):
    p = tmp_path / "psg.json"
    p.write_text(json.dumps(_fixture_psg()), encoding="utf-8")
    out = tmp_path / "cands.json"
    res = b.mine_candidates(psg_path=str(p), save_path=str(out))
    assert res["success"] is True and res["savedTo"] == str(out)
    assert res["candidateCount"] == 1
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["candidates"][0]["scopeId"] == "h1"


def test_mine_candidates_offline_unreadable_psg_path():
    res = b.mine_candidates(psg_path="C:/nonexistent/nope.json")
    assert res["success"] is False
    assert res["errorCode"] == "PSG_PATH_UNREADABLE"
```

- [ ] **Step 4: Run the offline tests**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_mine_candidates_offline.py -v`
Expected: PASS (3 passed). (Imports `simulation_backend`; no ExtendSim needed — the offline path never calls COM.)

- [ ] **Step 5: Verify dispatch wiring (no COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -c "import sys; sys.path.insert(0,'src'); import pattern_mine, simulation_backend as b; assert 'mine_candidates' in b.COMMANDS; assert callable(b.mine_candidates); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_mine_candidates_offline.py
git commit -m "feat(M8): mine_candidates entry (offline psgPath / filePath / active model)"
```

---

### Task 4: Wire the MCP tool (backend.ts, index.ts, copy-files)

Expose `mine_candidates` as an MCP tool and ship the new Python module to `dist/`. Tool count 100 → 101.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts` (add `mineCandidates`)
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts` (add `server.tool("mine_candidates", ...)`)
- Modify: `src/ExtendSimMCP.TypeScript/package.json` (add `pattern_mine.py` to `copy-files`)

**Interfaces:**
- Consumes: `sendCommand` (backend.ts); `safeToolCall` + `backend` (index.ts); the `mine_candidates` COMMAND (Task 3).
- Produces: `backend.mineCandidates({filePath?, psgPath?, savePath?, modelId?})`; MCP tool `mine_candidates`.

- [ ] **Step 1: Add the backend proxy**

In `src/ExtendSimMCP.TypeScript/src/backend.ts`, immediately after the `extractPsg` function (ends with `return await sendCommand("extract_psg", params);` then `}`), add:

```typescript
export async function mineCandidates(params: {
  filePath?: string;
  psgPath?: string;
  savePath?: string;
  modelId?: string;
}) {
  return await sendCommand("mine_candidates", params);
}
```

- [ ] **Step 2: Register the tool**

In `src/ExtendSimMCP.TypeScript/src/index.ts`, immediately after the `server.tool("extract_psg", ... )` block (ends with `);`), add:

```typescript
server.tool(
  "mine_candidates",
  "Mine candidate molecule subgraphs from a model's Pattern Structure Graph: one candidate per H-block scope, each with a stable Weisfeiler-Lehman fingerprint (topology-only, param-independent), kind (molecule/composite), hblockType, confidence, and its boundary edges. Foundation for pattern clustering (M9). PSG source: psgPath (offline JSON saved by extract_psg), else filePath (opened read-only), else the active model. Use savePath to write JSON.",
  {
    filePath: z.string().optional().describe("If set, open this .mox read-only and extract its PSG first"),
    psgPath: z.string().optional().describe("If set, load a previously-saved PSG JSON from disk (offline, no ExtendSim)"),
    savePath: z.string().optional().describe("If set, write JSON to file and return path instead of inline data"),
    modelId: z.string().optional().describe("Model ID (defaults to the active model)")
  },
  async ({ filePath, psgPath, savePath, modelId }) => {
    return safeToolCall("mine_candidates", () => backend.mineCandidates({ filePath, psgPath, savePath, modelId }), { filePath, psgPath, savePath, modelId });
  }
);
```

- [ ] **Step 3: Add the module to copy-files**

In `src/ExtendSimMCP.TypeScript/package.json`, in the `copy-files` script string, add this immediately after the `psg_extract.py` copy statement:

```
fs.copyFileSync('src/pattern_mine.py', 'dist/pattern_mine.py');
```

- [ ] **Step 4: Build and verify tool count + dist copy**

Run: `cd src/ExtendSimMCP.TypeScript && npm run build`
Expected: `tsc` compiles with no errors; copy-files runs.

Run: `cd src/ExtendSimMCP.TypeScript && grep -c "server.tool(" src/index.ts && test -f dist/pattern_mine.py && echo "pattern_mine copied"`
Expected: prints `101` then `pattern_mine copied`.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/package.json
git commit -m "feat(M8): wire mine_candidates MCP tool (100->101) + copy-files"
```

---

### Task 5: Live smoke test (deferred run) + backlog

Add a guarded live test (auto-skips without a model) and record the deferred live run in the backlog alongside M7 Task 5. Do NOT run it here (ExtendSim coordination handled separately).

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/live/test_mine_candidates_live.py`
- Modify: `docs/BACKLOG.md`

**Interfaces:**
- Consumes: the live `mine_candidates` entry (Task 3).

- [ ] **Step 1: Write the guarded live smoke test**

Create `src/ExtendSimMCP.TypeScript/tests/live/test_mine_candidates_live.py`:

```python
# tests/live/test_mine_candidates_live.py
"""Live smoke test for mine_candidates. Requires ExtendSim 2024 Pro running with a
model open that contains at least one H-block. Skips if COM/model unavailable.
Run: python -m pytest tests/live/test_mine_candidates_live.py -v -s

DEFERRED (M8 Task 5): not yet run against a live model — pairs with M7's deferred
extract_psg live verification. Confirms the full M7->M8 chain against real COM.
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_mine_candidates_live_returns_candidates():
    import simulation_backend as b
    res = b.mine_candidates()
    if not res.get("success"):
        pytest.skip(f"no live model / COM: {res.get('error') or res.get('errorCode')}")
    assert "candidates" in res
    print("candidateCount:", res["candidateCount"])
    for c in res["candidates"]:
        print(" ", c["scopeId"], c["kind"], c["hblockType"], c["confidence"],
              "wl=", c["wl_fingerprint"][:8], "nodes=", c["nodeCount"])
        assert len(c["wl_fingerprint"]) == 32
        assert c["kind"] in ("molecule", "composite")
```

- [ ] **Step 2: Verify it collects (do not execute against COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_mine_candidates_live.py --collect-only -q`
Expected: `1 test collected`.

- [ ] **Step 3: Record the deferral in the backlog**

In `docs/BACKLOG.md`, immediately after the `## M7 extract_psg — live verification (Task 5)` section's last bullet, add a new section:

```markdown
## M8 mine_candidates — live verification (Task 5)

`mine_candidates` shipped (M8, `src/pattern_mine.py` + entry in `simulation_backend.py`),
pure core fully unit-tested incl. the offline `psgPath` path, but the live paths
(`filePath` / active model, which drive M7's `extract_psg`) have not been run against
real ExtendSim. Follow-up (pairs with M7's deferred live run):

- Run `src/ExtendSimMCP.TypeScript/tests/live/test_mine_candidates_live.py` against a
  live ExtendSim with an H-block model open (safe COM pattern: single driver).
- Confirm candidates, kinds, and WL fingerprints match the model; sanity-check that
  two instances of the same molecule produce the same fingerprint.
- Deferred 2026-07-16.
```

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/live/test_mine_candidates_live.py docs/BACKLOG.md
git commit -m "test(M8): guarded live smoke test for mine_candidates (deferred run) + backlog"
```

---

## Self-Review

**Spec coverage:**
- Pure module `pattern_mine.py` (WL + detect) → Tasks 1–2. ✅
- Stable blake2b hash, not `hash()` → Task 1 `_stable_hash` + determinism test. ✅
- Node label `lib:blocktype`, param-independent → Task 1 isomorphism test. ✅
- Edge signature `(direction, ownPort, neighborPort, neighborLabel)`, k=4 → Task 1. ✅
- Direction/port matters; directionConfident orientation-invariance → Task 1 tests. ✅
- Interior only; boundary carried → Task 1 (no boundary in fingerprint) + Task 2 carries. ✅
- Candidate = per H-block scope, root excluded → Task 2 tests. ✅
- kind molecule/composite; confidence high/candidate → Task 2 tests. ✅
- Three input paths (psgPath/filePath/active) → Task 3. ✅
- Offline path unit-tested → Task 3 tests. ✅
- Empty model → candidateCount 0 → Task 2 flat test + Task 3 (empty scopes). ✅
- savePath contract → Task 3 save test. ✅
- Fail-closed unreadable psgPath / propagate extract_psg error → Task 3. ✅
- Tool count 100→101 + copy-files → Task 4. ✅
- Live deferred + backlog → Task 5. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `wl_fingerprint(nodes, edges, k=4) -> (str, dict)` used identically in Task 2's `detect_candidates`. Candidate dict keys in Task 2's implementation match Task 2's tests and Task 3's assertions (`scopeId`, `kind`, `confidence`, `nodeCount`, `wl_fingerprint`, `boundaryEdges`, `wlLabels`). `mine_candidates(file_path, psg_path, save_path, model_id)` matches the `COMMANDS` lambda argument order and `backend.mineCandidates` param names (`filePath`/`psgPath`/`savePath`/`modelId`). `extract_psg(file_path=..., model_id=...)` matches M7's shipped signature and its `{success, modelName, scopes}` return. ✅
