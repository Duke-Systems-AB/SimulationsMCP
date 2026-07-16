# M8 boundary detection + WL fingerprint — Design

**Status:** Approved 2026-07-16
**Module:** Pattern Mining — the "learn from old models" (miner) half, milestone 2 of 4 (M7–M10).
**PRD refs:** FR-6, FR-7 (§6.3); algorithm §9.1 (WL); §9.2 (pure vs physical); tool #3 `mine_patterns` (partial — M8 does boundary detection + fingerprint; clustering/near-miss = M9).

## Goal

Consume M7's multi-scale PSG and emit **candidate molecule subgraphs**, each tagged
with a **Weisfeiler–Lehman (WL) fingerprint** that canonicalizes its topology. This
is the second miner milestone: it turns "here is the graph" (M7) into "here are the
reusable-unit candidates and their structural identities" — the input M9 needs for
clustering, near-miss merging, and param/interface inference.

Exposed as a diagnostic MCP tool `mine_candidates` so the whole M7→M8 pipeline can be
run against a real model (which also exercises M7's still-unverified live reader).

Non-goals (later milestones): clustering / bucketing / near-miss GED merge, param
inference, interface *naming*, and human approval are M9/M10. Flat (non-H-block)
models are a permanent non-goal — a flat model simply yields zero candidates.

## Locked decisions

1. **Diagnostic tool now.** Build the pure module `pattern_mine.py` AND expose a thin
   `mine_candidates` MCP tool (tool count 100 → 101). Rationale: incremental value +
   lets us test the M7→M8 chain live (doubling as M7's deferred live verification).
2. **Pure module + thin adapter** (same as M3–M7): `pattern_mine.py` is COM-free and
   fixture-tested; the live `mine_candidates` entry resolves a PSG then calls the core.
3. **Candidate = one per H-block scope.** Root scope is the model's flow, not a
   molecule candidate — excluded. Nested H-blocks are each candidates (multi-scale).
4. **WL over the interior only**, with a **stable hash** (not Python's salted
   `hash()`). Boundary edges are carried alongside for M9, not folded into the
   fingerprint (§9.2: "cluster on interior core + explicit interface").
5. **WL for every candidate uniformly.** Robust to M7's still-best-effort `hblockType`,
   which is carried so M9 can cluster pure by identity (their WL fingerprints coincide).

## Architecture & components

- **New pure module `src/pattern_mine.py`** (zero COM, unit-tested with fixtures):
  - `wl_fingerprint(nodes, edges, k=4) -> str` — §9.1 WL canonicalization over a
    subgraph's interior, returning a stable hex digest.
  - `detect_candidates(psg) -> list[dict]` — walk M7's multi-scale PSG, emit one
    candidate per H-block scope, compute WL, tag kind/hblockType/confidence, and
    carry the subgraph + boundaryEdges.
- **Thin live entry `mine_candidates(file_path=None, psg_path=None, save_path=None,
  model_id=None)`** in `simulation_backend.py` — resolves a PSG (three input paths,
  below) then calls `detect_candidates`.
- **MCP tool `mine_candidates`** in `index.ts` via `backend.ts` proxy + Python
  `COMMANDS` entry. Tool count **100 → 101**.
- **`pattern_mine.py` added to `package.json` `copy-files`** (build gotcha — any new
  backend `.py` module must be listed or it `ModuleNotFoundError`s in `dist/`).

## Candidate model (output)

One candidate per H-block scope (root excluded):

```jsonc
{
  "scopeId": "h140",
  "hblockType": "pure",              // pure | physical | null (carried from M7)
  "kind": "molecule",                // "composite" if the subgraph itself contains an H-block node
  "label": "Machine",
  "wl_fingerprint": "b7d3…",         // stable hex digest (see WL section)
  "nodeCount": 3,
  "nodes": [ /* the scope's interior nodes, verbatim from the PSG */ ],
  "edges": [ /* interior edges, verbatim */ ],
  "boundaryEdges": [ /* carried untouched -> M9 interface inference */ ],
  "wlLabels": { "b141": "…", "b145": "…" },   // per-node final WL labels (M9 near-miss / debug)
  "confidence": "high"               // pure -> high; physical/null -> candidate
}
```

Field rules:

- **`kind`** = `"composite"` when any interior node has `isHBlock == true` (the
  candidate references sub-molecules → flow-like), else `"molecule"`.
- **`confidence`** = `"high"` when `hblockType == "pure"`, else `"candidate"`.
- **`nodes`/`edges`/`boundaryEdges`** are passed through from the PSG scope unchanged.
- No `definitionRef` field in M8: pure instances of the same library block are
  structurally identical, so their WL fingerprint already serves as the identity M9
  clusters on. `hblockType` + `wl_fingerprint` are sufficient; a separate definition
  reference is deferred to M9 if it turns out to be needed.

## WL fingerprint (§9.1) — with a stability correction

The PRD pseudocode writes `hash(...)`. Python's builtin `hash()` is **per-process
salted** → non-deterministic across runs, which would break cross-run/cross-instance
comparison (M9 clusters instances possibly from separate mine runs). Therefore:

- **Stable hash:** `hashlib.blake2b(canonical_bytes, digest_size=16).hexdigest()`
  (32-hex-char digest), not builtin `hash()`. Every hashed value goes through a
  canonical string (e.g. `repr` of the tuple) encoded to UTF-8 bytes.
- **Node label init** = `f"{lib}:{blocktype}"` (topology, NOT params).
- **Per iteration** (default `k=4`, enough for 3–10 block molecules): a node's
  signature is the **sorted** list of `(direction, ownPort, neighborPort,
  neighborLabel)` over its in/out interior edges; new label = stable-hash of
  `(currentLabel, signature)`.
- **Fingerprint** = stable-hash of the sorted final node labels.
- **Interior only.** Boundary edges are excluded from the fingerprint (carried
  separately). Ports come from the edge `from`/`to` (`"bID.port"`); direction from
  which side the node is on (`from` side = out, `to` side = in). A
  `directionConfident:false` edge is treated as undirected for signature purposes
  (contributes both an out- and an in-view) so an uncertain wire doesn't split
  otherwise-identical subgraphs.
- **Determinism:** a node missing `lib`/`type` uses `""` (no crash; topology just
  less distinct). Same input → identical fingerprint string, always.

## Data flow

```
mine_candidates(filePath?, psgPath?, savePath?, modelId?)
  1. Obtain PSG (priority order):
       psgPath given?  -> load JSON from disk            [OFFLINE, no COM — replay/debug]
       filePath given? -> extract_psg(filePath)          [open->read->close, via M7]
       else            -> extract_psg(modelId)           [active model, via M7]
  2. detect_candidates(psg)   <- PURE, no COM
       for each scope where kind == "hblock":
         interior = scope nodes + edges
         wl = wl_fingerprint(nodes, edges)
         kind = "composite" if any interior node isHBlock else "molecule"
         confidence = "high" if hblockType == "pure" else "candidate"
         -> candidate (carries boundaryEdges + wlLabels)
       (root scope skipped — it is the model's flow)
  3. Return {success, modelName, candidateCount, candidates[]}  (or write to savePath)
```

The `psgPath` path makes the whole mining step runnable **offline** against a saved
PSG (from M7's `savePath`) — fixture/replay debugging without ExtendSim.

## Error handling (fail-closed)

- `psgPath` missing / invalid JSON → error unchanged, no guess.
- `extract_psg` fails (no model, etc.) → propagate M7's error verbatim.
- **No H-block scopes** → `{success: true, candidateCount: 0, candidates: []}` (empty
  is not an error — flat models are a non-goal and just yield zero candidates).
- `hblockType == null` → carry null, `confidence: "candidate"` (cannot assert pure).
- Fingerprint always computed deterministically; a node missing `lib`/`type` uses
  `""` rather than crashing.

## Testing

**Unit (COM-free, `pattern_mine`)** — fixture PSGs in, candidates out:

- Two isomorphic subgraphs with **different params** → **same** fingerprint (params
  don't affect topology).
- Different topology → different fingerprint.
- Direction/port matters: `shutdown→Activity` ≠ `out→in` yields a different
  fingerprint.
- **Determinism/stability:** same input twice → identical fingerprint string (guards
  the `hash()` trap).
- `pure` → confidence `high`; `physical`/`null` → confidence `candidate`.
- `composite` flagged when the subgraph contains an H-block node; root scope excluded.
- Empty model (no H-blocks) → `[]`.
- A `directionConfident:false` edge is orientation-invariant: the same subgraph with
  that edge stored `from→to` vs `to→from` yields the SAME fingerprint (both views
  contributed). A confident edge is NOT orientation-invariant (direction matters).

**Live (guarded, deferred like M7 Task 5)** — `mine_candidates` against a real
H-block model; auto-skips without an open model. Run alongside M7's deferred live
verification.

## Definition of done

- `wl_fingerprint` and `detect_candidates` pass all unit tests (COM-free).
- `mine_candidates` tool registered, tool count 100 → 101.
- `pattern_mine.py` in `copy-files`.
- Three input paths work (psgPath offline; filePath; active model).
- No new false-success paths; fail-closed on the paths above; stable hashing verified.
- Live verification deferred to BACKLOG alongside M7 Task 5.
