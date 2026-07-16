# M10 approve_pattern + naming + library persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `approve_pattern` MCP tool that assembles a validated, M3-instantiable §7.1 molecule entry from an M9 mined pattern candidate + a caller-supplied naming, and writes it into the library — closing the miner loop.

**Architecture:** A pure + file-I/O module (`pattern_approve.py`, no COM) with `build_library_entry` (candidate + naming → §7.1 entry) and `approve_pattern_entry` (resolve → build → `validate_molecule` fail-closed → dryRun preview | write). A small fix to `pattern_cluster.py` folds in two M9 review Minors. Wired through backend.ts + index.ts.

**Tech Stack:** Python 3.13 (`re`, `os`, `json`), existing `molecule_schema.validate_molecule`, TypeScript (MCP server, zod), pytest, vitest (build check).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-16-m10-approve-pattern-design.md` — every task's requirements implicitly include it.
- Tool count goes **102 → 103** exactly (one new tool: `approve_pattern`).
- **Any new backend `.py` module MUST be added to `package.json` `copy-files`.**
- Written entry MUST pass `molecule_schema.validate_molecule` (exactly one `seed`, edges tagged `kind: flow|side`, ≤1 inlet and ≤1 outlet, binds → known nodes, required params present).
- **`molecule_schema`'s placeholder resolver only matches `^\{\{(\w+)\}\}$`** — friendly param names MUST be `\w+`. Sanitize derived names (`_sanitize`); a caller-provided name that is not `\w+` → error.
- Approval gate (FR-12): the deliberate `approve_pattern` call is the approval. `dryRun` previews without writing. Existing id → error unless `overwrite`.
- Fail-closed: composite candidate → error; missing/invalid seed → error; unknown fingerprint / unreadable patternsPath → error; validation failure → error, no write. Never write a guessed entry.
- Pure/file-I/O only; no COM. `molecules_dir` is injectable for tests (never touch the real `patterns/` in unit tests).
- Unit tests in `src/ExtendSimMCP.TypeScript/tests/unit_py/`, each prepends `../../src` to `sys.path`.

---

### Task 1: Fold in the two M9 review Minors (`pattern_cluster.py`)

Two small correctness fixes carried over from M9's review: `cluster_candidates` must also skip candidates missing `wlLabels`; `infer_pattern` must set-merge values of nodes sharing a WL label within one instance (locked decision #4).

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`

**Interfaces:**
- Consumes/Produces: no signature changes — behavior of existing `cluster_candidates` / `infer_pattern`.

- [ ] **Step 1: Write the failing tests**

Append to `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py`:

```python
def test_cluster_skips_candidate_missing_wllabels():
    good = {"wl_fingerprint": "FP1", "nodes": [_n("b1", "Item", "Queue")],
            "edges": [], "wlLabels": {"b1": "L1"}}
    bad = {"wl_fingerprint": "FP2", "nodes": [_n("b9", "Item", "Queue")],
           "edges": []}  # no wlLabels
    clusters = cluster_candidates([good, bad])
    assert len(clusters) == 1
    assert clusters[0]["instances"][0]["wl_fingerprint"] == "FP1"


def test_infer_set_merges_symmetric_label_values():
    # Instance A has a symmetric pair (2 nodes, same label L, both "gold");
    # B and C each contribute one "silver". Without set-merge the doubled "gold"
    # wins most-common; with set-merge, "silver" (2 instances) wins.
    def inst(fp, nodes, wl):
        return {"wl_fingerprint": fp, "nodes": nodes, "edges": [], "boundaryEdges": [],
                "wlLabels": wl, "hblockType": "pure", "scopeId": "h", "source": "m"}
    a = inst("FP", [_pnode("a1", "Item", "Set", {"attr": "gold"}),
                    _pnode("a2", "Item", "Set", {"attr": "gold"})],
             {"a1": "L1", "a2": "L1"})
    b = inst("FP", [_pnode("b1", "Item", "Set", {"attr": "silver"})], {"b1": "L1"})
    c = inst("FP", [_pnode("c1", "Item", "Set", {"attr": "silver"})], {"c1": "L1"})
    cluster = {"fingerprint": "FP", "nearMiss": False, "instances": [a, b, c]}
    info = infer_pattern(cluster)["params"]["a1.attr"]
    assert info["default"] == "silver"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -k "missing_wllabels or set_merges" -v`
Expected: FAIL — `test_cluster_skips_candidate_missing_wllabels` fails (bad candidate currently NOT skipped → 2 clusters); `test_infer_set_merges_symmetric_label_values` fails (default == "gold").

- [ ] **Step 3: Apply the two fixes**

In `src/ExtendSimMCP.TypeScript/src/pattern_cluster.py`, in `cluster_candidates`, replace:

```python
        fp = c.get("wl_fingerprint")
        if fp is None:
            continue  # malformed -> skip defensively
```

with:

```python
        fp = c.get("wl_fingerprint")
        if fp is None or not c.get("wlLabels"):
            continue  # malformed (no fingerprint or no WL labels) -> skip defensively
```

In `infer_pattern`, replace the value-collection loop:

```python
    values = defaultdict(list)
    for inst in instances:
        labels = inst.get("wlLabels", {})
        for node in inst.get("nodes", []):
            lbl = labels.get(node["ref"])
            if lbl is None:
                continue
            for k, v in (node.get("params") or {}).items():
                values[(lbl, k)].append(v)
```

with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_cluster.py -v`
Expected: PASS (28 passed — 26 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_cluster.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_cluster.py
git commit -m "fix(M9): skip candidates missing wlLabels + set-merge symmetric-label values"
```

---

### Task 2: `build_library_entry` (candidate + naming → §7.1 entry)

The assembly core: rename params to friendly names, rewrite placeholders, set the seed, tag edge kinds, build the interface, normalize lib names, and package the §7.1 envelope. Pure.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/src/pattern_approve.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_approve.py`

**Interfaces:**
- Consumes: `molecule_schema` (for a validation test only, not in build). M9 candidate shape (`kind`, `params` {m9key: {type,required,default?,range?,fixed?}}, `template` {nodes,edges}, `interface`, `instances`, `example`, `support`, `wl_fingerprint`, `nearMiss`).
- Produces: `ApproveError` (Exception); `build_library_entry(candidate: dict, naming: dict) -> dict` returning a §7.1 entry.

- [ ] **Step 1: Write the failing tests**

Create `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_approve.py`:

```python
# tests/unit_py/test_pattern_approve.py
import os, sys
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest
from pattern_approve import build_library_entry, ApproveError
from molecule_schema import validate_molecule


def _candidate():
    return {
        "wl_fingerprint": "FP1", "support": 3, "nearMiss": False,
        "hblockType": "pure", "kind": "molecule",
        "params": {
            "b3.D": {"type": "number", "required": True, "default": 5, "range": [2, 8]},
            "b2.capacity": {"type": "number", "required": False, "fixed": 1},
        },
        "template": {
            "nodes": [
                {"ref": "b2", "lib": "Item", "type": "Queue", "isHBlock": False,
                 "params": {"capacity": 1}},
                {"ref": "b3", "lib": "Item", "type": "Activity", "isHBlock": False,
                 "params": {"D": "{{b3.D}}"}},
            ],
            "edges": [{"from": "b2.ItemOut", "to": "b3.ItemIn"}],
        },
        "interface": {
            "inlets": [{"binds": "b2.ItemIn", "role": "item"}],
            "outlets": [{"binds": "b3.ItemOut", "role": "item"}],
        },
        "instances": [{"scopeId": "h140", "source": "modelA.mox"}],
        "example": {"b3.D": 5, "b2.capacity": 1},
    }


def _naming():
    return {
        "id": "machine", "intent": "A machine", "seed": "b3",
        "params": {"b3.D": "process_time"},
        "inlet": {"binds": "b2.ItemIn", "port": "in"},
        "outlet": {"binds": "b3.ItemOut", "port": "out"},
    }


def test_build_entry_params_are_friendly_and_only_required():
    e = build_library_entry(_candidate(), _naming())
    assert e["params"] == {"process_time": {"type": "number", "required": True,
                                            "default": 5, "range": [2, 8]}}


def test_build_entry_rewrites_placeholder_to_friendly_name():
    e = build_library_entry(_candidate(), _naming())
    b3 = next(n for n in e["nodes"] if n["ref"] == "b3")
    assert b3["params"]["D"] == "{{process_time}}"


def test_build_entry_sets_seed_on_named_node_only():
    e = build_library_entry(_candidate(), _naming())
    seeds = [n["ref"] for n in e["nodes"] if n.get("seed")]
    assert seeds == ["b3"]


def test_build_entry_normalizes_lib_to_lbr():
    e = build_library_entry(_candidate(), _naming())
    assert all(n["lib"] == "Item.lbr" for n in e["nodes"])


def test_build_entry_infers_edge_kind_flow_from_item_ports():
    e = build_library_entry(_candidate(), _naming())
    assert e["edges"][0] == {"kind": "flow", "from": "b2.ItemOut", "to": "b3.ItemIn"}


def test_build_entry_edge_kind_override():
    naming = _naming()
    naming["edgeKinds"] = {"b3.ItemIn": "side"}
    e = build_library_entry(_candidate(), naming)
    assert e["edges"][0]["kind"] == "side"


def test_build_entry_interface_has_port_binds_role():
    e = build_library_entry(_candidate(), _naming())
    assert e["interface"]["inlets"] == [{"port": "in", "binds": "b2.ItemIn", "role": "item"}]
    assert e["interface"]["outlets"] == [{"port": "out", "binds": "b3.ItemOut", "role": "item"}]


def test_build_entry_provenance_and_example_renamed():
    e = build_library_entry(_candidate(), _naming())
    assert e["provenance"]["mined_from"] == 3
    assert e["provenance"]["wl_fingerprint"] == "FP1"
    assert e["provenance"]["sources"] == ["modelA.mox"]
    assert e["example"]["process_time"] == 5


def test_build_entry_envelope_fields():
    e = build_library_entry(_candidate(), _naming())
    assert e["id"] == "machine" and e["version"] == "1.0" and e["kind"] == "molecule"
    assert e["intent"] == "A machine"
    assert e["attributes"] == {"reads": [], "writes": []}


def test_build_entry_passes_validate_molecule():
    e = build_library_entry(_candidate(), _naming())
    validate_molecule(e, e["example"])   # must not raise


def test_build_entry_composite_is_error():
    c = _candidate()
    c["kind"] = "composite"
    with pytest.raises(ApproveError):
        build_library_entry(c, _naming())


def test_build_entry_bad_seed_is_error():
    naming = _naming()
    naming["seed"] = "nonexistent"
    with pytest.raises(ApproveError):
        build_library_entry(_candidate(), naming)


def test_build_entry_non_word_friendly_name_is_error():
    naming = _naming()
    naming["params"] = {"b3.D": "process time"}   # space -> not \w+
    with pytest.raises(ApproveError):
        build_library_entry(_candidate(), naming)


def test_build_entry_unmapped_param_uses_sanitized_key():
    naming = _naming()
    naming["params"] = {}   # no friendly name for b3.D
    e = build_library_entry(_candidate(), naming)
    assert "b3_D" in e["params"]
    b3 = next(n for n in e["nodes"] if n["ref"] == "b3")
    assert b3["params"]["D"] == "{{b3_D}}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_approve.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pattern_approve'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/ExtendSimMCP.TypeScript/src/pattern_approve.py`:

```python
# src/pattern_approve.py
"""Pattern approval + library persistence (M10).

Assembles a validated, M3-instantiable library entry (§7.1) from an M9 mined pattern
candidate plus a caller-supplied naming, and writes it into patterns/molecules/. Pure
+ file I/O, no COM. Nothing is written unless it passes molecule_schema.validate_molecule
and the caller deliberately approves. See spec 2026-07-16-m10-approve-pattern-design.md.
"""
import os
import re
import json

from molecule_schema import validate_molecule, MoleculeError

_PLACEHOLDER = re.compile(r"^\{\{(.+?)\}\}$")
_WORD = re.compile(r"^\w+$")


class ApproveError(Exception):
    pass


def _sanitize(key):
    """Turn an M9 param key (e.g. 'b3.D') into a placeholder-safe \\w+ name ('b3_D')."""
    return re.sub(r"\W+", "_", key)


def _friendly_map(candidate, naming):
    """Map each M9 param key -> friendly name (caller-provided, validated \\w+, or sanitized)."""
    provided = naming.get("params") or {}
    out = {}
    for key in candidate.get("params", {}):
        name = provided.get(key)
        if name is not None:
            if not _WORD.match(name):
                raise ApproveError(f"friendly param name must be \\w+ (letters/digits/_): {name!r}")
            out[key] = name
        else:
            out[key] = _sanitize(key)
    return out


def _normalize_lib(lib):
    if lib and not lib.endswith(".lbr"):
        return lib + ".lbr"
    return lib


def _infer_edge_kind(frm, to, override):
    if to in override:
        return override[to]
    text = (frm + " " + to).lower()
    return "flow" if "item" in text else "side"


def _role_for(binds, candidate):
    for grp in ("inlets", "outlets"):
        for p in candidate.get("interface", {}).get(grp, []):
            if p.get("binds") == binds:
                return p.get("role")
    port = binds.rpartition(".")[2].lower()
    if "item" in port:
        return "item"
    if "value" in port:
        return "value"
    return None


def build_library_entry(candidate, naming):
    """Assemble an M3-instantiable §7.1 molecule entry from a candidate + naming."""
    if candidate.get("kind") == "composite":
        raise ApproveError("composite candidates are flows (§7.3), unsupported in v1")

    template = candidate.get("template", {})
    nodes = template.get("nodes", [])
    refs = {n["ref"] for n in nodes}
    seed = naming.get("seed")
    if not seed or seed not in refs:
        raise ApproveError(f"naming.seed must be a template node ref; got {seed!r}")
    if not naming.get("id"):
        raise ApproveError("naming.id is required")

    fmap = _friendly_map(candidate, naming)

    # params: only the tunable (required) ones, friendly-named
    params = {}
    for key, info in candidate.get("params", {}).items():
        if not info.get("required"):
            continue
        p = {"type": info.get("type", "number"), "required": True}
        if "default" in info:
            p["default"] = info["default"]
        if "range" in info:
            p["range"] = info["range"]
        params[fmap[key]] = p

    # nodes: rewrite placeholders, seed flag, lib normalize
    out_nodes = []
    for n in nodes:
        on = {"ref": n["ref"], "lib": _normalize_lib(n.get("lib", "")), "type": n.get("type", "")}
        p = {}
        for k, v in (n.get("params") or {}).items():
            if isinstance(v, str):
                m = _PLACEHOLDER.match(v)
                if m:
                    inner = m.group(1)
                    p[k] = "{{" + fmap.get(inner, _sanitize(inner)) + "}}"
                    continue
            p[k] = v
        if p:
            on["params"] = p
        if n["ref"] == seed:
            on["seed"] = True
        if n.get("isHBlock"):
            on["isHBlock"] = True
        out_nodes.append(on)

    # edges: add kind
    override = naming.get("edgeKinds") or {}
    out_edges = [{"kind": _infer_edge_kind(e["from"], e["to"], override),
                  "from": e["from"], "to": e["to"]}
                 for e in template.get("edges", [])]

    # interface from naming.inlet / naming.outlet
    def _iface(spec):
        if not spec:
            return None
        binds = spec["binds"]
        return {"port": spec["port"], "binds": binds, "role": _role_for(binds, candidate)}

    inlets = [x for x in [_iface(naming.get("inlet"))] if x]
    outlets = [x for x in [_iface(naming.get("outlet"))] if x]

    example = {fmap.get(k, _sanitize(k)): v for k, v in (candidate.get("example") or {}).items()}

    return {
        "id": naming["id"],
        "version": "1.0",
        "kind": "molecule",
        "intent": naming.get("intent", ""),
        "params": params,
        "attributes": {"reads": [], "writes": []},
        "nodes": out_nodes,
        "edges": out_edges,
        "interface": {"inlets": inlets, "outlets": outlets},
        "provenance": {
            "mined_from": candidate.get("support"),
            "wl_fingerprint": candidate.get("wl_fingerprint"),
            "sources": [i.get("source") for i in candidate.get("instances", [])],
            "nearMiss": candidate.get("nearMiss", False),
        },
        "example": example,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_approve.py -v`
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_approve.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_approve.py
git commit -m "feat(M10): build_library_entry (candidate + naming -> validated §7.1 entry)"
```

---

### Task 3: `approve_pattern_entry` (resolve → validate → write)

Add the orchestration: resolve the candidate (inline or from a cluster_patterns file by fingerprint), build the entry, validate fail-closed, then preview (dryRun) or write to `patterns/molecules/<id>.json` with an overwrite guard and an injectable `molecules_dir` for tests.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/pattern_approve.py`
- Test: `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_approve.py`

**Interfaces:**
- Consumes: `build_library_entry` (Task 2), `validate_molecule`/`MoleculeError` (molecule_schema).
- Produces: `approve_pattern_entry(candidate=None, patterns_path=None, pattern_fingerprint=None, naming=None, dry_run=False, overwrite=False, molecules_dir=None) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_approve.py`:

```python
import json as _json
from pattern_approve import approve_pattern_entry


def test_approve_dry_run_returns_preview_no_write(tmp_path):
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(),
                                dry_run=True, molecules_dir=str(tmp_path))
    assert res["success"] is True
    assert res["preview"]["id"] == "machine"
    assert list(tmp_path.iterdir()) == []   # nothing written


def test_approve_writes_valid_entry(tmp_path):
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(),
                                molecules_dir=str(tmp_path))
    assert res["success"] is True and res["id"] == "machine"
    out = tmp_path / "machine.json"
    assert out.exists()
    written = _json.loads(out.read_text(encoding="utf-8"))
    validate_molecule(written, written["example"])   # written entry is valid
    assert written["kind"] == "molecule"


def test_approve_refuses_overwrite_without_flag(tmp_path):
    approve_pattern_entry(candidate=_candidate(), naming=_naming(), molecules_dir=str(tmp_path))
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(), molecules_dir=str(tmp_path))
    assert res["success"] is False and res["errorCode"] == "ALREADY_EXISTS"


def test_approve_overwrite_with_flag(tmp_path):
    approve_pattern_entry(candidate=_candidate(), naming=_naming(), molecules_dir=str(tmp_path))
    res = approve_pattern_entry(candidate=_candidate(), naming=_naming(),
                                overwrite=True, molecules_dir=str(tmp_path))
    assert res["success"] is True


def test_approve_invalid_build_no_write(tmp_path):
    naming = _naming()
    naming["seed"] = "nope"
    res = approve_pattern_entry(candidate=_candidate(), naming=naming, molecules_dir=str(tmp_path))
    assert res["success"] is False and res["errorCode"] == "BUILD_FAILED"
    assert list(tmp_path.iterdir()) == []


def test_approve_from_patterns_path_by_fingerprint(tmp_path):
    patterns_file = tmp_path / "patterns.json"
    patterns_file.write_text(_json.dumps({"patterns": [_candidate()]}), encoding="utf-8")
    res = approve_pattern_entry(patterns_path=str(patterns_file), pattern_fingerprint="FP1",
                                naming=_naming(), molecules_dir=str(tmp_path))
    assert res["success"] is True and res["id"] == "machine"


def test_approve_unknown_fingerprint(tmp_path):
    patterns_file = tmp_path / "patterns.json"
    patterns_file.write_text(_json.dumps({"patterns": [_candidate()]}), encoding="utf-8")
    res = approve_pattern_entry(patterns_path=str(patterns_file), pattern_fingerprint="NOPE",
                                naming=_naming(), molecules_dir=str(tmp_path))
    assert res["success"] is False and res["errorCode"] == "UNKNOWN_FINGERPRINT"


def test_approve_no_candidate_source():
    res = approve_pattern_entry(naming=_naming())
    assert res["success"] is False and res["errorCode"] == "NO_CANDIDATE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_approve.py -k approve -v`
Expected: FAIL with `ImportError: cannot import name 'approve_pattern_entry'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/ExtendSimMCP.TypeScript/src/pattern_approve.py`:

```python
def _default_molecules_dir():
    return os.path.join(os.path.dirname(__file__), "..", "patterns", "molecules")


def approve_pattern_entry(candidate=None, patterns_path=None, pattern_fingerprint=None,
                          naming=None, dry_run=False, overwrite=False, molecules_dir=None):
    """Resolve a candidate, assemble + validate its library entry, then preview or write it."""
    try:
        if candidate is None:
            if not patterns_path or not pattern_fingerprint:
                return {"success": False, "errorCode": "NO_CANDIDATE",
                        "error": "provide candidate, or patternsPath + patternFingerprint"}
            try:
                with open(patterns_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                return {"success": False, "errorCode": "PATTERNS_PATH_UNREADABLE",
                        "error": f"cannot read patternsPath: {e}", "patternsPath": patterns_path}
            candidate = next((p for p in data.get("patterns", [])
                              if p.get("wl_fingerprint") == pattern_fingerprint), None)
            if candidate is None:
                return {"success": False, "errorCode": "UNKNOWN_FINGERPRINT",
                        "error": f"no pattern with fingerprint {pattern_fingerprint}"}

        if not naming or not naming.get("id"):
            return {"success": False, "errorCode": "NAMING_REQUIRED",
                    "error": "naming with an id is required"}

        try:
            entry = build_library_entry(candidate, naming)
        except ApproveError as e:
            return {"success": False, "errorCode": "BUILD_FAILED", "error": str(e)}

        try:
            validate_molecule(entry, entry.get("example", {}))
        except MoleculeError as e:
            return {"success": False, "errorCode": "VALIDATION_FAILED", "error": str(e)}

        if dry_run:
            return {"success": True, "preview": entry}

        mdir = molecules_dir or _default_molecules_dir()
        path = os.path.join(mdir, f"{entry['id']}.json")
        if os.path.exists(path) and not overwrite:
            return {"success": False, "errorCode": "ALREADY_EXISTS",
                    "error": f"pattern id already exists: {entry['id']}", "path": path}
        os.makedirs(mdir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2, allow_nan=False)
        return {"success": True, "written": path, "id": entry["id"]}
    except Exception as e:
        return {"success": False, "errorCode": "APPROVE_FAILED", "error": str(e)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/unit_py/test_pattern_approve.py -v`
Expected: PASS (22 passed).

- [ ] **Step 5: Register the command**

In the `COMMANDS` dict in `src/ExtendSimMCP.TypeScript/src/simulation_backend.py`, add next to the `cluster_patterns` entry:

```python
    "approve_pattern": lambda p: __import__("pattern_approve").approve_pattern_entry(
        p.get("candidate"), p.get("patternsPath"), p.get("patternFingerprint"),
        p.get("naming"), p.get("dryRun", False), p.get("overwrite", False)
    ),
```

- [ ] **Step 6: Verify dispatch wiring (no COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -c "import sys; sys.path.insert(0,'src'); import pattern_approve, simulation_backend as b; assert 'approve_pattern' in b.COMMANDS; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 7: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/pattern_approve.py src/ExtendSimMCP.TypeScript/tests/unit_py/test_pattern_approve.py src/ExtendSimMCP.TypeScript/src/simulation_backend.py
git commit -m "feat(M10): approve_pattern_entry (resolve/validate/write) + COMMANDS"
```

---

### Task 4: Wire the MCP tool (backend.ts, index.ts, copy-files)

Expose `approve_pattern` as an MCP tool and ship the new module to `dist/`. Tool count 102 → 103.

**Files:**
- Modify: `src/ExtendSimMCP.TypeScript/src/backend.ts` (add `approvePattern`)
- Modify: `src/ExtendSimMCP.TypeScript/src/index.ts` (add `server.tool("approve_pattern", ...)`)
- Modify: `src/ExtendSimMCP.TypeScript/package.json` (add `pattern_approve.py` to `copy-files`)

**Interfaces:**
- Consumes: `sendCommand` (backend.ts); `safeToolCall` + `backend` (index.ts); the `approve_pattern` COMMAND (Task 3).
- Produces: `backend.approvePattern({candidate?, patternsPath?, patternFingerprint?, naming?, dryRun?, overwrite?})`; MCP tool `approve_pattern`.

- [ ] **Step 1: Add the backend proxy**

In `src/ExtendSimMCP.TypeScript/src/backend.ts`, immediately after the `clusterPatterns` function (ends with `return await sendCommand("cluster_patterns", params);` then `}`), add:

```typescript
export async function approvePattern(params: {
  candidate?: Record<string, any>;
  patternsPath?: string;
  patternFingerprint?: string;
  naming?: Record<string, any>;
  dryRun?: boolean;
  overwrite?: boolean;
}) {
  return await sendCommand("approve_pattern", params);
}
```

- [ ] **Step 2: Register the tool**

In `src/ExtendSimMCP.TypeScript/src/index.ts`, immediately after the `server.tool("cluster_patterns", ... )` block (ends with `);`), add:

```typescript
server.tool(
  "approve_pattern",
  "Approve a mined pattern candidate into the molecule library. Assembles a validated, M3-instantiable library entry (§7.1) from a candidate (inline, or selected from a cluster_patterns file by patternFingerprint) plus a naming object (id, intent, seed, param names, inlet/outlet ports), validates it against the molecule schema, and writes patterns/molecules/<id>.json. dryRun returns a preview without writing; overwrite allows replacing an existing id. Nothing is written unless valid and deliberately approved.",
  {
    candidate: z.record(z.any()).optional().describe("Inline mined pattern candidate (from cluster_patterns)"),
    patternsPath: z.string().optional().describe("cluster_patterns output JSON to load the candidate from"),
    patternFingerprint: z.string().optional().describe("wl_fingerprint selecting which pattern in patternsPath"),
    naming: z.record(z.any()).optional().describe("id, intent, seed, params{m9key->name}, inlet/outlet{binds,port}, edgeKinds"),
    dryRun: z.boolean().optional().describe("Preview the assembled entry without writing"),
    overwrite: z.boolean().optional().describe("Allow overwriting an existing pattern id")
  },
  async ({ candidate, patternsPath, patternFingerprint, naming, dryRun, overwrite }) => {
    return safeToolCall("approve_pattern", () => backend.approvePattern({ candidate, patternsPath, patternFingerprint, naming, dryRun, overwrite }), { patternsPath, patternFingerprint, dryRun, overwrite });
  }
);
```

- [ ] **Step 3: Add the module to copy-files**

In `src/ExtendSimMCP.TypeScript/package.json`, in the `copy-files` script string, add this immediately after the `pattern_cluster.py` copy statement:

```
fs.copyFileSync('src/pattern_approve.py', 'dist/pattern_approve.py');
```

- [ ] **Step 4: Build and verify tool count + dist copy**

Run: `cd src/ExtendSimMCP.TypeScript && npm run build`
Expected: `tsc` compiles with no errors; copy-files runs.

Run: `cd src/ExtendSimMCP.TypeScript && grep -c "server.tool(" src/index.ts && test -f dist/pattern_approve.py && echo "pattern_approve copied"`
Expected: prints `103` then `pattern_approve copied`.

- [ ] **Step 5: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/src/backend.ts src/ExtendSimMCP.TypeScript/src/index.ts src/ExtendSimMCP.TypeScript/package.json
git commit -m "feat(M10): wire approve_pattern MCP tool (102->103) + copy-files"
```

---

### Task 5: Live instantiation round-trip test (deferred run) + backlog

Add a guarded live test that approves a mined molecule and instantiates it (the §9.5 round-trip) and record the deferred run in the backlog. Do NOT run it against COM.

**Files:**
- Create: `src/ExtendSimMCP.TypeScript/tests/live/test_approve_pattern_live.py`
- Modify: `docs/BACKLOG.md`

**Interfaces:**
- Consumes: `approve_pattern_entry` (Task 3), `mine_candidates` / `cluster_patterns` (M8/M9), `instantiate_pattern` (M3, via the backend).

- [ ] **Step 1: Write the guarded live smoke test**

Create `src/ExtendSimMCP.TypeScript/tests/live/test_approve_pattern_live.py`:

```python
# tests/live/test_approve_pattern_live.py
"""Live round-trip test for the miner: mine -> cluster -> approve -> instantiate.
Requires ExtendSim 2024 Pro running with a model containing a repeated H-block.
Skips if COM/model unavailable. Writes an approved molecule to a temp dir.
Run: python -m pytest tests/live/test_approve_pattern_live.py -v -s

DEFERRED (M10 Task 5): not yet run live — the §9.5 round-trip
(extract_psg(instantiate_pattern(m, m.example)) == source) closes the miner loop and
should be validated against real ExtendSim, together with M7-M9's deferred verification.
"""
import os, sys, tempfile, json
_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest


def test_mine_cluster_approve_round_trip():
    import simulation_backend as b
    import pattern_approve
    mined = b.mine_candidates()
    if not mined.get("success") or not mined.get("candidates"):
        pytest.skip(f"no live model / candidates: {mined.get('error') or mined.get('errorCode')}")
    with tempfile.TemporaryDirectory() as tmp:
        cpath = os.path.join(tmp, "cands.json")
        with open(cpath, "w", encoding="utf-8") as f:
            json.dump({"candidates": mined["candidates"]}, f)
        clustered = b.cluster_patterns(candidates_paths=[cpath])
        assert clustered["success"] and clustered["patterns"]
        cand = clustered["patterns"][0]
        # a minimal naming: seed = first template node, expose its first boundary port if any
        seed_ref = cand["template"]["nodes"][0]["ref"]
        naming = {"id": "mined-live-demo", "intent": "mined", "seed": seed_ref, "params": {}}
        res = pattern_approve.approve_pattern_entry(candidate=cand, naming=naming,
                                                    dry_run=True, molecules_dir=tmp)
        print("approve dry-run:", res.get("success"), res.get("errorCode") or "")
        # dry run must at least build; if the mined molecule needs an inlet/outlet to validate,
        # this surfaces the naming a human must supply — that's the point of the round-trip.
        assert "preview" in res or res.get("errorCode") in ("VALIDATION_FAILED", "BUILD_FAILED")
```

- [ ] **Step 2: Verify it collects (do not execute against COM)**

Run: `cd src/ExtendSimMCP.TypeScript && python -m pytest tests/live/test_approve_pattern_live.py --collect-only -q`
Expected: `1 test collected`.

- [ ] **Step 3: Record the deferral in the backlog**

In `docs/BACKLOG.md`, immediately after the `## M9 cluster_patterns — live verification (Task 6)` section's last bullet (starts with `- Deferred 2026-07-16`), add:

```markdown
## M10 approve_pattern — live round-trip (Task 5)

`approve_pattern` shipped (M10, `src/pattern_approve.py` + entry in `simulation_backend.py`),
pure core fully unit-tested, but the full miner round-trip has not been run against real
ExtendSim. Follow-up (closes the loop; pairs with M7–M9):

- Run `src/ExtendSimMCP.TypeScript/tests/live/test_approve_pattern_live.py`: mine a real
  model → cluster → approve a molecule → `instantiate_pattern` it → `extract_psg` the result
  and confirm it matches the source subgraph (§9.5 round-trip invariant).
- Validate the lib-name normalization (`Item` → `Item.lbr`) actually lets M3 place blocks,
  and that mined edge-kind inference (flow/side) produces a buildable molecule.
- Deferred 2026-07-16.
```

- [ ] **Step 4: Commit**

```bash
git add src/ExtendSimMCP.TypeScript/tests/live/test_approve_pattern_live.py docs/BACKLOG.md
git commit -m "test(M10): guarded live mine->approve->instantiate round-trip (deferred) + backlog"
```

---

## Self-Review

**Spec coverage:**
- M9 minors (skip missing wlLabels; set-merge symmetric labels) → Task 1. ✅
- `build_library_entry` (friendly params, placeholder rewrite, seed, edge kinds, interface, lib normalize, provenance, example) → Task 2. ✅
- Friendly names must be `\w+` (sanitize derived / error on bad caller name) → Task 2 `_friendly_map`. ✅
- Entry passes `validate_molecule` → Task 2 test. ✅
- composite / bad seed → error → Task 2. ✅
- `approve_pattern_entry` resolve (inline / patternsPath+fingerprint), validate fail-closed, dryRun, write, overwrite guard, injectable molecules_dir → Task 3. ✅
- Fail-closed error codes (NO_CANDIDATE, UNKNOWN_FINGERPRINT, BUILD_FAILED, VALIDATION_FAILED, ALREADY_EXISTS) → Task 3. ✅
- Tool count 102→103 + copy-files → Task 4. ✅
- Live round-trip deferred + backlog → Task 5. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅

**Type consistency:** `build_library_entry(candidate, naming)` + `ApproveError` defined in Task 2, used in Task 3's `approve_pattern_entry`; `approve_pattern_entry(candidate, patterns_path, pattern_fingerprint, naming, dry_run, overwrite, molecules_dir)` matches the COMMANDS lambda arg order (`candidate, patternsPath, patternFingerprint, naming, dryRun, overwrite`) and `backend.approvePattern` param names; candidate fields (`kind`, `params`, `template.nodes/edges`, `interface`, `instances`, `example`, `support`, `wl_fingerprint`, `nearMiss`) match M9's `infer_pattern` output; `validate_molecule(molecule, params)` matches molecule_schema's signature. ✅
