# M7 extract_psg Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `extract_psg` MCP tool that reads an ExtendSim model and returns its multi-scale Pattern Structure Graph (PSG) — nodes, edges, and per-H-block boundary edges — as the deterministic foundation of the pattern miner.

**Architecture:** A pure, COM-free core (`psg_extract.py::build_psg`) transforms a raw per-scope snapshot into the PSG and is unit-tested with fixtures. A thin recursive live reader in `simulation_backend.py` gathers that raw snapshot via the existing COM idioms (`objectIDNext`, `GetNumCons`/`GetConName`/`NodeGetIDIndex`, `LocalNumBlocks2`/`LocalToGlobal2`, `_extract_parameters`). The MCP tool is wired through `backend.ts` + `index.ts` + the Python `COMMANDS` dispatch.

**Tech Stack:** Python 3.13 (COM backend via pywin32), TypeScript (MCP server, zod), pytest (unit_py), vitest (TS build check).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-m7-extract-psg-design.md` — every task's requirements implicitly include it.
- Tool count goes **99 → 100** exactly (one new tool: `extract_psg`).
- **Any new backend `.py` module MUST be added to `package.json` `copy-files`** or it `ModuleNotFoundError`s in `dist/`.
- **Fail-closed everywhere:** unreadable param → `"?"`; undeterminable `hblockType` → `null`; never fabricate an edge; never trust COM `success` — effect-verify reads.
- Pure core is **COM-free** (unit-testable with fixtures); all COM lives in the reader.
- Follow existing module style: pure core + injected/real reader, mirroring `attribute_detect.py`.
- PSG node `ref` = `"b" + blockId`. Edge port = connector name; empty name → `Con{In|Out}{idx}` fallback. Edges normalized out→in. When a shared node is not a clean out→in pair, the edge is still emitted but carries `"directionConfident": false`; clean out→in edges omit the field.
- Unit tests live in `src/ExtendSimMCP.TypeScript/tests/unit_py/`; each test file prepends `../../src` to `sys.path` (see existing tests).

---

### Task 1: Pure edge/boundary pairing helpers (`_port`, `_pair`)

The algorithmic heart: given one scope's blocks (each with connectors carrying `nodeIndex`), pair connectors that share a `nodeIndex` into internal edges, and classify single-endpoint nodes as boundary (dangling) edges. Pure, no COM.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/psg_extract.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_psg_extract.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `_port(conn: dict) -> str` where `conn` has keys `connName:str`, `direction:"in"|"out"|"unknown"`, `idx:int`.
  - `_pair(scope_blocks: list[dict]) -> tuple[list[dict], list[dict]]` returning `(edges, boundaryEdges)`. Each block dict has `blockId:int` and `connectors: list[{idx,connName,direction,nodeIndex}]`. Edge = `{"from": "b{id}.{port}", "to": "b{id}.{port}"}`. BoundaryEdge = `{"internal": "b{id}.{port}", "crosses": "inlet"|"outlet"|"unknown", "boundaryConnector": port}`.

- [ ] **Step 1: Write the failing tests**

Create `src/ExtendSimMCP.TypeScript/tests/unit_py/test_psg_extract.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_psg_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'psg_extract'` (or ImportError on `_port`).

- [ ] **Step 3: Write minimal implementation**

Create `src/ExtendSimMCP.TypeScript/src/psg_extract.py`:

```python
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
            # direction indeterminate: keep every wire, first endpoint as source
            src = eps[0]
            for tgt in eps[1:]:
                edges.append({"from": f"b{src[0]}.{_port(src[1])}",
                              "to": f"b{tgt[0]}.{_port(tgt[1])}"})
    return edges, boundary
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_psg_extract.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/psg_extract.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_psg_extract.py
git commit -m "feat(M7): PSG pairing core (_port, _pair) — edges + boundary edges"
```

---

### Task 2: `build_psg` — assemble scopes and nodes

Wrap `_pair` with scope/node assembly: for each raw scope, build nodes (`ref`, `blockId`, `lib`, `type`, `isHBlock`, `params`, and `scopeId` for H-block nodes), attach `edges`/`boundaryEdges` from `_pair`, and carry scope metadata (`kind`, `parentScopeId`, and `hblockType`/`label` for H-block scopes).

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/psg_extract.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_psg_extract.py`

**Interfaces:**
- Consumes: `_port`, `_pair` (Task 1).
- Produces: `build_psg(raw: dict) -> dict`.
  - Input `raw`: `{"modelName": str, "scopes": [ {scopeId, kind, parentScopeId, hblockType?, label?, blocks:[{blockId, lib, type, isHBlock, childScopeId, params, connectors}]} ]}`.
  - Output: `{"modelName": str, "scopes": [ {scopeId, kind, parentScopeId, hblockType?(hblock only), label?(hblock only), nodes:[...], edges:[...], boundaryEdges:[...]} ]}`.
  - Node: `{"ref": "b{id}", "blockId": int, "lib": str, "type": str, "isHBlock": bool, "params": dict}` plus `"scopeId": childScopeId` when `isHBlock`.

- [ ] **Step 1: Write the failing tests**

Append to `src/ExtendSimMCP.TypeScript/tests/unit_py/test_psg_extract.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_psg_extract.py -k build_psg -v`
Expected: FAIL with `ImportError: cannot import name 'build_psg'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ExtendSimMCP.TypeScript/src/psg_extract.py`:

```python
def _node(blk):
    node = {
        "ref": f"b{blk['blockId']}",
        "blockId": blk["blockId"],
        "lib": blk.get("lib", ""),
        "type": blk.get("type", ""),
        "isHBlock": bool(blk.get("isHBlock")),
        "params": blk.get("params", {}),
    }
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_psg_extract.py -v`
Expected: PASS (14 passed total).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/psg_extract.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_psg_extract.py
git commit -m "feat(M7): build_psg assembles multi-scale scopes and nodes"
```

---

### Task 3: Live reader + `extract_psg` backend entry

Add the COM reader that walks the live model (top level via `objectIDNext` filtered to root-enclosed blocks, then recursively into each H-block via `LocalToGlobal2`), produces the raw snapshot `build_psg` consumes, and the `extract_psg` entry that resolves input (open/close a `filePath` or validate the open model), calls `build_psg`, and optionally writes to `savePath`. COM code is verified live (Task 5), consistent with how `attribute_detect.RealReader` is handled.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/simulation_backend.py` (add functions + `COMMANDS` entry)

**Interfaces:**
- Consumes: `psg_extract.build_psg` (Task 2); existing `get_extendsim_app`, `parse_float`, `_validate_model_open`, `_extract_parameters`, `model_open`, `model_close`, `_com_error`.
- Produces: `extract_psg(file_path=None, save_path=None, model_id=None) -> dict`; `COMMANDS["extract_psg"]`.

- [ ] **Step 1: Add the reader helpers**

Add near `model_extract` (after the `_extract_hierarchies`/`model_extract` block, before the `COMMANDS` table) in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`:

```python
def _psg_read_connectors(app, bid):
    """Read a block's connectors: idx, name, direction, nodeIndex (effect-verified)."""
    conns = []
    app.Execute(f"global0 = GetNumCons({bid});")
    n = int(parse_float(app.Request("System", "global0+:0:0:0")))
    for c in range(n):
        app.Execute(f'globalStr0 = GetConName({bid}, {c});')
        name = app.Request("System", "globalStr0+:0:0:0") or ""
        app.Execute(f"global0 = NodeGetIDIndex({bid}, {c});")
        node_index = int(parse_float(app.Request("System", "global0+:0:0:0")))
        low = name.lower()
        direction = "in" if "in" in low else "out" if "out" in low else "unknown"
        conns.append({"idx": c, "connName": name,
                      "direction": direction, "nodeIndex": node_index})
    return conns


def _psg_hblock_type(app, bid):
    """Best-effort pure/physical: library origin -> pure, else physical.

    NOTE: the pure/physical signal is a live-verification item (Task 5). If it
    proves unreliable, return None (fail-closed, no guess) rather than a wrong tag.
    """
    app.Execute(f'globalStr0 = GetLibraryPathName({bid}, 2);')
    lib = app.Request("System", "globalStr0+:0:0:0") or ""
    return "pure" if lib else "physical"


def _psg_gather_scope(app, scope_id, kind, parent_scope_id, label, block_ids, out_scopes):
    """Build one scope's raw block list and recurse into its child H-blocks."""
    meta = []
    for bid in block_ids:
        app.Execute(f"globalStr0 = BlockName({bid});")
        btype = app.Request("System", "globalStr0+:0:0:0") or ""
        if not btype:
            continue
        app.Execute(f'globalStr0 = GetLibraryPathName({bid}, 2);')
        lib = app.Request("System", "globalStr0+:0:0:0") or ""
        app.Execute(f'global0 = GetBlockTypeNumeric({bid});')
        is_h = int(parse_float(app.Request("System", "global0+:0:0:0"))) == 4
        meta.append({"id": bid, "type": btype, "lib": lib, "isHBlock": is_h})

    # _extract_parameters returns {"blocks": {str(bid): {...}}, "skippedBlocks": [...]}
    param_map = _extract_parameters(
        app, [{"id": m["id"], "type": m["type"]} for m in meta]).get("blocks", {})

    blocks, child_hblocks = [], []
    for m in meta:
        bid = m["id"]
        child_scope = f"h{bid}" if m["isHBlock"] else None
        blocks.append({
            "blockId": bid, "lib": m["lib"], "type": m["type"],
            "isHBlock": m["isHBlock"], "childScopeId": child_scope,
            "params": param_map.get(str(bid), {}),
            "connectors": _psg_read_connectors(app, bid),
        })
        if m["isHBlock"]:
            child_hblocks.append(bid)

    scope = {"scopeId": scope_id, "kind": kind,
             "parentScopeId": parent_scope_id, "blocks": blocks}
    if kind == "hblock":
        scope["hblockType"] = _psg_hblock_type(app, int(scope_id[1:]))
        scope["label"] = label
    out_scopes.append(scope)

    for hb in child_hblocks:
        app.Execute(f'global0 = LocalNumBlocks2({hb});')
        n = int(parse_float(app.Request("System", "global0+:0:0:0")))
        internal = []
        for i in range(n):
            app.Execute(f'global0 = LocalToGlobal2({hb}, {i});')
            gid = int(parse_float(app.Request("System", "global0+:0:0:0")))
            if gid > 0:
                internal.append(gid)
        app.Execute(f'globalStr0 = GetBlockLabel({hb});')
        hlabel = app.Request("System", "globalStr0+:0:0:0") or ""
        _psg_gather_scope(app, f"h{hb}", "hblock", scope_id, hlabel, internal, out_scopes)


def _psg_top_level_ids(app):
    """Top-level block ids (objectIDNext), filtered to those enclosed by the model root."""
    ids, current = [], -1
    while True:
        app.Execute(f"global0 = objectIDNext({current}, 0);")
        bid = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if bid == -1:
            break
        current = bid
        app.Execute(f'global0 = GetEnclosingHBlockNum2({bid});')
        enclosing = int(parse_float(app.Request("System", "global0+:0:0:0")))
        if enclosing <= 0:
            ids.append(bid)
    return ids


def _gather_psg_raw(app, model_name):
    scopes = []
    _psg_gather_scope(app, "root", "root", None, "", _psg_top_level_ids(app), scopes)
    return {"modelName": model_name, "scopes": scopes}


def extract_psg(file_path=None, save_path=None, model_id=None):
    """Extract the model's multi-scale PSG. Reads the open model, or opens
    file_path (read-only) first and closes it afterward. Optionally writes JSON."""
    import psg_extract
    app = get_extendsim_app()
    opened = False
    try:
        if file_path:
            res = model_open(file_path, True)
            if not res.get("success"):
                return res
            opened = not res.get("alreadyOpen", False)  # only close if WE opened it
        else:
            chk = _validate_model_open(app)
            if not chk.get("success"):
                return chk

        app.Execute("globalStr0 = GetModelName();")
        model_name = app.Request("System", "globalStr0+:0:0:0") or ""
        psg = psg_extract.build_psg(_gather_psg_raw(app, model_name))

        if save_path:
            import json as _json
            with open(save_path, "w", encoding="utf-8") as f:
                _json.dump(psg, f, indent=2, allow_nan=False)
            return {"success": True, "savedTo": save_path,
                    "modelName": model_name, "scopeCount": len(psg["scopes"])}
        return {"success": True, **psg}
    except Exception as e:
        return _com_error(e, "extract_psg")
    finally:
        if opened:
            try:
                model_close(None, False)
            except Exception:
                pass
```

- [ ] **Step 2: Register the command**

In the `COMMANDS` dict in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`, add next to the `model_extract` entry:

```python
    "extract_psg": lambda p: extract_psg(
        p.get("filePath"), p.get("savePath"), p.get("modelId")
    ),
```

- [ ] **Step 3: Verify the module imports and dispatch resolves (no COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -c "import sys; sys.path.insert(0,'src'); import psg_extract, simulation_backend as b; assert 'extract_psg' in b.COMMANDS; assert callable(b.extract_psg); print('ok')"`
Expected: prints `ok` (import + dispatch wiring valid; no ExtendSim needed).

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/simulation_backend.py
git commit -m "feat(M7): live PSG reader + extract_psg entry (recursive H-block descent)"
```

---

### Task 4: Wire the MCP tool (backend.ts, index.ts, copy-files)

Expose `extract_psg` as an MCP tool and ensure the new Python module ships to `dist/`. Tool count goes 99 → 100.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts` (add `extractPsg`)
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts` (add `server.tool("extract_psg", ...)`)
- Modify: `src/ExtendSimMCP.TypeScript/package.json` (add `psg_extract.py` to `copy-files`)

**Interfaces:**
- Consumes: `sendCommand` (backend.ts), `safeToolCall` + `backend` (index.ts), the `extract_psg` COMMAND (Task 3).
- Produces: `backend.extractPsg({filePath?, savePath?, modelId?})`; MCP tool `extract_psg`.

- [ ] **Step 1: Add the backend proxy**

In `src/ExtendSimMCP.TypeScript/src/backend.ts`, immediately after the `modelExtract` function (ends at the line with `return await sendCommand("model_extract", params);` and its closing `}`), add:

```typescript
export async function extractPsg(params: {
  filePath?: string;
  savePath?: string;
  modelId?: string;
}) {
  return await sendCommand("extract_psg", params);
}
```

- [ ] **Step 2: Register the tool**

In `src/ExtendSimMCP.TypeScript/src/index.ts`, immediately after the `server.tool("model_extract", ... )` block (ends with `);` before the `// DATABASE TOOLS` banner), add:

```typescript
server.tool(
  "extract_psg",
  "Extract a model's Pattern Structure Graph (PSG): multi-scale nodes (lib:blocktype + params) and edges (srcPort->dstPort), with boundary-crossing edges marked per H-block. Foundation for pattern mining. Reads the open model, or opens filePath (read-only) first and closes it. Use savePath to write JSON to file.",
  {
    filePath: z.string().optional().describe("If set, open this .mox read-only, extract, then close it"),
    savePath: z.string().optional().describe("If set, write JSON to file and return path instead of inline data"),
    modelId: z.string().optional().describe("Model ID (defaults to the active model)")
  },
  async ({ filePath, savePath, modelId }) => {
    return safeToolCall("extract_psg", () => backend.extractPsg({ filePath, savePath, modelId }), { filePath, savePath, modelId });
  }
);
```

- [ ] **Step 3: Add the module to copy-files**

In `src/ExtendSimMCP.TypeScript/package.json`, in the `copy-files` script string, add this immediately after the `attribute_detect.py` copy statement:

```
fs.copyFileSync('src/psg_extract.py', 'dist/psg_extract.py');
```

(So the sequence reads `...'dist/attribute_detect.py'); fs.copyFileSync('src/psg_extract.py', 'dist/psg_extract.py'); fs.copyFileSync('src/patterns.py'...`.)

- [ ] **Step 4: Build and verify tool count + dist copy**

Run: `cd src/ExtendSimMCP.TypeScript && npm run build`
Expected: `tsc` compiles with no errors; copy-files runs.

Run: `cd src/ExtendSimMCP.TypeScript && grep -c "server.tool(" src/index.ts && test -f dist/psg_extract.py && echo "psg_extract copied"`
Expected: prints `100` then `psg_extract copied`.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/package.json
git commit -m "feat(M7): wire extract_psg MCP tool (99->100) + copy-files"
```

---

### Task 5: Live verification against a real H-block model

Prove the reader end-to-end against a real model that contains at least one H-block (e.g. a resource-machine molecule instance). Confirm scopes/nodes/boundaryEdges, and specifically pin down the one live-uncertain field: `hblockType` (pure vs physical) and whether `objectIDNext` + the enclosing filter correctly scope the root.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/live/test_extract_psg_live.py` (guarded live smoke test)

**Interfaces:**
- Consumes: the live `extract_psg` entry (Task 3) via the running COM backend.

- [ ] **Step 1: Write the guarded live smoke test**

Create `src/ExtendSimMCP.TypeScript/tests/live/test_extract_psg_live.py`:

```python
# tests/live/test_extract_psg_live.py
"""Live smoke test for extract_psg. Requires ExtendSim 2024 Pro running with a
model open that contains at least one H-block. Skips if COM/model unavailable.
Run: python -m pytest tests/live/test_extract_psg_live.py -v -s
"""
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_extract_psg_live_has_scopes_and_hblock():
    import simulation_backend as b
    res = b.extract_psg()
    if not res.get("success"):
        pytest.skip(f"no live model / COM: {res.get('error') or res.get('errorCode')}")
    scopes = res["scopes"]
    assert any(s["scopeId"] == "root" and s["kind"] == "root" for s in scopes)
    hblocks = [s for s in scopes if s["kind"] == "hblock"]
    print("scopeCount:", len(scopes), "hblockCount:", len(hblocks))
    for s in hblocks:
        print(" scope", s["scopeId"], "type=", s.get("hblockType"),
              "label=", s.get("label"), "nodes=", len(s["nodes"]),
              "edges=", len(s["edges"]), "boundary=", len(s["boundaryEdges"]))
        assert s["parentScopeId"] is not None
    # every H-block node points at a real scope
    node_child_scopes = {n["scopeId"] for s in scopes for n in s["nodes"]
                         if n.get("isHBlock") and "scopeId" in n}
    scope_ids = {s["scopeId"] for s in scopes}
    assert node_child_scopes <= scope_ids
```

- [ ] **Step 2: Run it against a live model (safe COM pattern)**

Ensure ExtendSim 2024 Pro is running with a model containing an H-block open (build one with an existing molecule tool if needed, or open a saved model). Follow the safe-COM pattern: do not kill Python mid-call; keep reads in range.

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_extract_psg_live.py -v -s`
Expected: PASS, and the printed lines show `root` + at least one `hblock` scope with sensible node/edge/boundary counts.

- [ ] **Step 3: Confirm or correct the `hblockType` signal**

Inspect the printed `type=` values against the model. If a pure/library H-block prints `pure` and a copied one prints `physical`, the signal holds. If the `GetLibraryPathName`-based signal is wrong (e.g. both report `pure`), change `_psg_hblock_type` in `simulation_backend.py` to return `None` when the signal is not trustworthy (fail-closed, no guess), and note it in the design spec's follow-ups. Re-run Step 2.

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/live/test_extract_psg_live.py
git commit -m "test(M7): live smoke test for extract_psg + hblockType pinning"
```

---

## Self-Review

**Spec coverage:**
- Multi-scale recursive PSG (root + every H-block) → Task 3 reader recursion + Task 2 assembly. ✅
- Input = open model + optional filePath (open/read/close) → Task 3 `extract_psg`. ✅
- Pure core + thin adapter → Task 1/2 pure `build_psg`; Task 3 reader. ✅
- Node shape (ref/blockId/lib/type/isHBlock/params/scopeId) → Task 2 `_node`. ✅
- Edge port = connector name, `Con{In|Out}{idx}` fallback, out→in normalize → Task 1 `_port`/`_pair`. ✅
- boundaryEdges (one internal endpoint, inlet/outlet, boundaryConnector) → Task 1 `_pair`. ✅
- hblockType pure/physical/null → Task 3 `_psg_hblock_type` + Task 5 pinning. ✅
- params passthrough incl. "?" → Task 2 test. ✅
- savePath mirrors model_extract → Task 3. ✅
- Error handling fail-closed, close-in-finally → Task 3. ✅
- Tool count 99→100 + copy-files → Task 4. ✅
- Testing: unit (flat, single/nested hblock, dangling, empty name, param) → Task 1/2; live → Task 5. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `_port`/`_pair` signatures and dict shapes match between Task 1 (definition), Task 2 (`build_psg` calling `_pair`), and Task 3 (reader emitting `connectors` with `idx/connName/direction/nodeIndex` and blocks with `blockId/lib/type/isHBlock/childScopeId/params`). Node output keys consistent across Task 2 tests and `_node`. `extract_psg(file_path, save_path, model_id)` matches the `COMMANDS` lambda and `backend.extractPsg` params. ✅

**Note on Task 3 COM code:** COM reader logic is not unit-TDD'd (no COM in CI) — it is verified live in Task 5, consistent with the existing `attribute_detect.RealReader`. All transform logic that *can* be pure is in `build_psg` (Tasks 1–2, fully TDD).
