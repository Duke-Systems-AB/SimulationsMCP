# M7 `extract_psg` — Design

**Status:** Approved 2026-07-15
**Module:** Pattern Mining — the "learn from old models" (miner) half, milestone 1 of 4 (M7–M10).
**PRD refs:** FR-4, FR-5 (§6.2); data model §7.4; tool #2 (§11).

## Goal

Read an ExtendSim model and return its **Pattern Structure Graph (PSG)** in
**multi-scale** form: the top level plus a sub-graph for every H-block at every
depth, with boundary-crossing edges marked so the downstream miner (M8) can infer
molecule interfaces. This is the deterministic foundation the rest of the miner
(M8 boundary detection + WL fingerprint, M9 param/interface inference, M10 approve)
builds on.

Non-goals (deferred to later milestones): boundary *detection* / clustering /
fingerprinting (M8), param/interface *inference* (M9), naming/approval (M10),
attribute-contract inference beyond what M6 already provides. M7 only *extracts*
the graph; it does not decide what is a molecule.

## Locked decisions

1. **PSG shape: multi-scale (recursive).** One scope per level — root plus every
   H-block at every depth. Each H-block scope marks the edges that cross its
   boundary. Rationale: the miner's core rule is "a pure/library H-block is always
   a molecule boundary," so M8 needs the sub-graph inside each H-block plus its
   crossing edges. Flat extraction would push that work into M8 and break the
   "molecule = H-block" principle.
2. **Input: open model + optional `filePath`.** Default operates on the
   currently-open model (like every other tool; `modelId` optional). If `filePath`
   is given, open it → read → close it (only if we opened it). Matches the stack's
   "work against the open model" pattern while still letting the miner walk a
   library of old `.mox` files.
3. **Pure core + thin live adapter.** Same architecture as M3–M6: a COM-free
   `build_psg(raw)` transform, unit-tested with fixtures; a thin live reader that
   gathers the raw snapshot via the existing extractors.

## Architecture & components

- **New module `src/psg_extract.py`** — pure function `build_psg(raw_model)` that
  takes a raw snapshot (top-level `blocks`/`connections`/`parameters`/`hierarchies`
  in the exact shape `model_extract` already returns, plus per-H-block internal
  graphs incl. crossing edges) and transforms it into the multi-scale PSG.
  **Zero COM** → fully unit-testable with fixture dicts.
- **Thin live reader** (in `simulation_backend.py`, new `extract_psg(...)` entry +
  a private gatherer) — reuses the existing `_extract_blocks` /
  `_extract_connections` / `_extract_parameters` / `_extract_hierarchies` helpers
  for the top level, and the `hierarchy_get_contents` mechanism
  (`LocalNumBlocks2` / `LocalToGlobal2` / `NodeGetIDIndex` / `GetConName`) to
  descend into each H-block recursively. Unlike `hierarchy_get_contents` (which
  filters to edges with both endpoints internal), the gatherer **keeps crossing
  edges** (exactly one internal endpoint) as boundary raw data.
- **MCP tool `extract_psg`** in `index.ts` — params `filePath?`, `modelId?`,
  `savePath?`. Tool count **99 → 100**.
- **`psg_extract.py` added to `package.json` `copy-files`** — otherwise
  `ModuleNotFoundError` in any packaged `dist/` build (the known build gotcha; any
  new backend `.py` module must be listed).

## Data model (PSG output)

Flat list of scopes (one per level), not deeply nested — easier for M8 to iterate.
Nesting is expressed via `parentScopeId`; an H-block node points to its own scope
via `scopeId`.

```jsonc
{
  "success": true,
  "modelName": "resource-machine-demo.mox",
  "scopes": [
    {
      "scopeId": "root", "kind": "root", "parentScopeId": null,
      "nodes": [
        { "ref": "b101", "blockId": 101, "lib": "Item", "type": "Activity",
          "isHBlock": false,
          "params": { "D": 5, "capacity": 1 } },
        { "ref": "b140", "blockId": 140, "lib": "", "type": "Hierarchical",
          "isHBlock": true, "scopeId": "h140", "hblockType": "pure",
          "label": "Machine" }
      ],
      "edges": [ { "from": "b101.out", "to": "b140.in" } ],
      "boundaryEdges": []
    },
    {
      "scopeId": "h140", "kind": "hblock", "parentScopeId": "root",
      "hblockType": "pure", "label": "Machine",
      "nodes": [ /* blocks inside the H-block, same node shape */ ],
      "edges": [ /* internal edges */ ],
      "boundaryEdges": [
        { "internal": "b141.in",  "crosses": "inlet",  "boundaryConnector": "Con0In" },
        { "internal": "b145.out", "crosses": "outlet", "boundaryConnector": "Con0Out" }
      ]
    }
  ]
}
```

Field rules:

- **`ref`** = `"b" + blockId` — stable local identity. M8's WL fingerprint cares
  only about structure, so the block id suffices.
- **Edge ports** = connector name (`GetConName`); empty name → fallback
  `Con{In|Out}{idx}` (same convention as M3, where connector names vary per build).
- **`edges`** = both endpoints inside the same scope. Direction is normalized
  out→in (`from` is the output side, `to` the input side). When a shared node is
  not a clean out→in pair (e.g. two outs, or an endpoint whose connector name
  carries no "in"/"out"), the edge is still emitted (no wire is dropped) but
  carries **`"directionConfident": false`** so the miner (M8) can treat it as
  undirected in its fingerprint. Confident out→in edges omit the field (default
  true).
- **`boundaryEdges`** = dangling endpoints → M8's interface candidates.
  Two cases produce them: (1) a node with exactly one internal endpoint; (2) a
  node with two or more endpoints that all share one **known** direction (all
  `in` or all `out`) — such endpoints cannot form a valid internal edge (you
  cannot wire two inputs, or two outputs, together), so they tie to the same
  H-block boundary connector and each becomes a boundary edge (a boundary fan-in
  / fan-out). `crosses: inlet|outlet` derived from the internal endpoint's
  direction (input side → `inlet`, output side → `outlet`). `boundaryConnector` =
  the internal connector name (M7 does not resolve the H-block's own connector
  name; M8 does interface inference).
- **`hblockType`** = `pure|physical` best-effort: H-block via
  `GetBlockTypeNumeric == 4`; library origin via `GetLibraryPathName(id, 2)` →
  `pure`, otherwise `physical`. Feeds M8's rule. Undeterminable → `null` (no guess).
- **`params`** = whatever `model_extract`'s parameter extraction yields per block.
  Canonicalization/curation is M8/M9's job, not M7's (YAGNI).

## Data flow

```
extract_psg(filePath?, modelId?, savePath?)
  1. Resolve input
       filePath given?  → model_open(filePath, readOnly=True)   [remember: we opened it]
       else             → _validate_model_open(active/modelId)
  2. Gather raw snapshot (live, COM)
       top level: _extract_blocks / _extract_connections / _extract_parameters / _extract_hierarchies
       for each H-block (recurse, all depths):
         internal blocks + connectors (LocalToGlobal2 / NodeGetIDIndex / GetConName)
         KEEP crossing edges (exactly one internal endpoint) → boundary raw
  3. build_psg(raw)   ← PURE, no COM  → scopes[] with nodes / edges / boundaryEdges
  4. Return JSON  (or write to savePath, mirror model_extract's savePath contract)
  5. If we opened filePath → model_close(saveFirst=False)   [in finally]
```

Step 2 is the only COM-heavy part; step 3 (all transform logic) is pure and
tested without ExtendSim.

## Error handling (fail-closed)

- No model open and no `filePath` → `_validate_model_open` error (reused).
- `filePath` missing / won't open → propagate the `model_open` error unchanged.
- Unreadable block param or connector → param `"?"` / skip that edge, never
  fabricate (same line as M6 attribute detection).
- Undeterminable `hblockType` → `null`, not a guess.
- Never trust COM `success` — effect-verify every read. If we opened the file,
  close it in a `finally` so a mid-read error doesn't leave it open.

## Testing

**Unit (COM-free, `build_psg`)** — fixture snapshots in, PSG out:

- Flat model (root scope only, no boundaryEdges).
- Single H-block → `root` + one `hblock` scope, correct `parentScopeId` / `scopeId`
  linkage, H-block node carries `scopeId`.
- Nested H-blocks (H-block inside H-block) → every depth becomes its own scope.
- Dangling/crossing edge → lands in `boundaryEdges` with correct
  `crosses: inlet|outlet`.
- Empty connector name → `Con{In|Out}{idx}` fallback.
- Pure vs physical → correct `hblockType`; undeterminable → `null`.
- Unreadable param → `"?"`, no crash.
- Edge direction normalized out→in.

**Live (manual, guarded)** — PSG a real H-block model (e.g. the resource-machine
we built), assert scopes / nodes / boundaryEdges match. Run with the safe COM
pattern (one-shot dialog watcher, in-range reads, never kill mid-call).

## Definition of done

- `build_psg` passes all unit tests (COM-free).
- `extract_psg` tool registered, tool count 99 → 100.
- `psg_extract.py` in `copy-files`.
- Live-verified against one real H-block model.
- No new false-success paths; every COM read effect-verified; fail-closed on the
  paths above.
