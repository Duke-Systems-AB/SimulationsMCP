# M10 approve_pattern + naming + library persistence — Design

**Status:** Approved 2026-07-16
**Module:** Pattern Mining — the "learn from old models" (miner) half, milestone 4 of 4 (M7–M10, FINAL).
**PRD refs:** FR-11, FR-12 (§6.3); library entry §7.1; tool #4 `approve_pattern`.

## Goal

Close the miner loop: turn M9's mined pattern candidates into **approved, M3-instantiable
library entries**. A human/LLM reviews a candidate and supplies the naming and structural
decisions (id, intent, seed, port/param names); `approve_pattern` deterministically
assembles a §7.1 molecule entry, validates it (fail-closed), and — with approval — writes
it into `patterns/molecules/`, the same library `list_patterns`/`get_pattern` read and
`instantiate_pattern` (M3) consumes. Nothing enters the library without approval (FR-12).

This milestone also folds in two Minor findings from M9's review (below).

Non-goals: LLM auto-naming (the caller supplies naming); flow (§7.3 / composite) entries;
attribute-contract inference beyond M6; the live COM instantiation round-trip (§9.5),
deferred with M7–M9's live verification.

## Locked decisions

1. **M3-instantiable, validated.** The written entry MUST pass
   `molecule_schema.validate_molecule` (exactly one `seed`, every edge tagged
   `kind: flow|side`, ≤1 inlet and ≤1 outlet, binds reference known nodes, required
   params present). Structural decisions come from the caller's `naming` input plus simple
   default inference; M10 assembles deterministically and validates fail-closed.
2. **Approval = the deliberate call.** Calling `approve_pattern` with a chosen
   id/intent/seed/ports IS the approval act (FR-12). `dryRun` previews the assembled entry
   without writing; a write requires no dryRun. Existing id → refuse unless `overwrite`.
3. **Pure + file-I/O module** (like `patterns.py`), no COM. `pattern_approve.py` holds the
   assembly, validation, and write; the backend `COMMANDS` delegates to it.
4. **Fold in M9 minors** on this branch: `cluster_candidates` skips candidates missing
   `wl_fingerprint` OR `wlLabels`; `infer_pattern` set-merges values of nodes that share a
   WL label (locked decision #4 from the M9 spec).

## Architecture & components

- **New module `src/pattern_approve.py`** (pure + file I/O, no COM):
  - `build_library_entry(candidate, naming) -> dict` — assemble a §7.1 molecule entry.
  - `approve_pattern_entry(candidate=None, patterns_path=None, pattern_fingerprint=None,
    naming=None, dry_run=False, overwrite=False, molecules_dir=None) -> dict` — resolve →
    build → validate → (preview | write).
  - Helpers: edge-kind inference, name sanitization, role lookup, library-dir resolution.
  - Imports `molecule_schema.validate_molecule` for validation.
- **Small fix in `src/pattern_cluster.py`** (M9 minors): `cluster_candidates` and
  `infer_pattern` as described above.
- **MCP tool `approve_pattern`** in `index.ts` via `backend.ts` proxy + Python `COMMANDS`.
  Tool count **102 → 103**.
- **`pattern_approve.py` added to `package.json` `copy-files`.**

## Naming / approval input

The caller reviews the M9 candidate and supplies the structural decisions:

```jsonc
naming = {
  "id": "machine-with-breakdowns",
  "intent": "Machine that processes items with stochastic breakdowns",
  "seed": "b3",                                   // template node ref that is the seed (flow tail) — REQUIRED
  "params": { "b3.D": "process_time" },           // M9 key -> friendly name (unmapped -> sanitized key)
  "inlet":  { "binds": "b2.inCon0", "port": "in"  },   // selects + names the single inlet (null if none)
  "outlet": { "binds": "b3.outCon0", "port": "out" },  // and the single outlet (null if none)
  "edgeKinds": { "b3.SDV_In": "side" }            // optional per-edge override, keyed by the edge's "to" endpoint
}
```

## `build_library_entry(candidate, naming)`

Deterministic assembly (raises a clear error on any structural problem):

- **kind guard:** `candidate.kind == "composite"` → error (composites are flows §7.3, not
  molecule entries — unsupported in v1).
- **params:** the entry `params` dict lists only the *tunable* params (those M9 marked
  `required`), keyed by friendly name from `naming.params` (missing → `_sanitize(m9key)`),
  carrying `{type, required: true, default?, range?}`. Fixed params are NOT listed here —
  they are baked into the nodes.
- **nodes:** from `candidate.template.nodes`; rewrite each placeholder `"{{m9key}}"` →
  `"{{friendlyName}}"`; set `"seed": true` on the node whose ref == `naming.seed` (error if
  that ref is not a template node); **normalize lib** — non-empty `lib` not ending in
  `.lbr` gets `.lbr` appended (toward the hand-authored library convention; flagged for
  live verification). Node refs are kept as-is (M9's `bID`), consistent throughout.
- **edges:** from `candidate.template.edges`; add `kind` — `naming.edgeKinds[edge.to]` if
  present, else inferred: `"flow"` if either endpoint port name contains "item"
  (case-insensitive), else `"side"`. (Default leans `flow`/`side` on a name heuristic; the
  human reviews, since it's approval-gated.)
- **interface:** `{inlets: [...], outlets: [...]}` built from `naming.inlet`/`naming.outlet`
  (each → `{port, binds, role}`, role looked up from the candidate's `boundaryEdges`/
  `interface` for that `binds`, else `_role_of(binds)`); `null` → omit. ≤1 each (enforced
  by validation).
- **envelope:** `version: "1.0"`, `kind: "molecule"`, `intent`, `id`, `attributes:
  {reads: [], writes: []}`, `provenance: {mined_from: support, wl_fingerprint, sources:
  [instance.source...], nearMiss}`, `example` (candidate.example with keys renamed to
  friendly names).

## Validation, write, approval gate

- **Validate (fail-closed):** `molecule_schema.validate_molecule(entry, entry["example"])`
  — checks exactly one seed, edge kinds, ≤1 inlet/outlet, binds to known nodes, required
  params present. On failure → error, NO write.
- **Approval gate (FR-12):** the deliberate `approve_pattern` call is the approval. `dryRun:
  true` → return `{success, preview: entry}` without writing.
- **Overwrite guard:** if `patterns/molecules/<id>.json` already exists → error unless
  `overwrite: true`.

## M9 minors folded in (`pattern_cluster.py`)

- `cluster_candidates`: skip a candidate when `wl_fingerprint` is None **or** `wlLabels` is
  missing/empty (currently only `wl_fingerprint` is checked).
- `infer_pattern`: when collecting values per `(WL label, paramKey)`, **set-merge** across
  nodes that share a label within one instance so duplicates don't over-weight the median /
  most-common (locked decision #4: "merge their values as a set (v1)").

## Data flow

```
approve_pattern(candidate | (patternsPath + patternFingerprint), naming, dryRun?, overwrite?)
  1. Resolve candidate: inline `candidate`, else load `patternsPath` (cluster_patterns
     output JSON) and pick the pattern whose wl_fingerprint == patternFingerprint.
  2. build_library_entry(candidate, naming)            [pure]
  3. validate_molecule(entry, entry.example)           [fail-closed; no write on failure]
  4. dryRun -> {success, preview: entry}
     else   -> write patterns/molecules/<id>.json (overwrite guard) -> {success, written, id}
```

## Error handling (fail-closed)

- `composite` candidate → error (flows unsupported in v1).
- `naming.seed` missing / not a template node ref → error.
- >1 inlet or >1 outlet after naming → validation error.
- Unknown `patternFingerprint` in `patternsPath` (or unreadable path) → error.
- Existing `id` without `overwrite` → error.
- Any `validate_molecule` failure → error, no file written.
- Never write a guessed entry into the library.

## Testing

**Unit (COM-free, fixtures)** — write into a temp `molecules_dir` (injected), never the real
`patterns/`:

- Assembly: M9 candidate + naming → §7.1 entry with tunable params (friendly-named),
  `{{placeholder}}` rewrite, `seed: true` on the right node, edge `kind`s, interface
  `{port, binds, role}`, provenance, lib normalization (`Item` → `Item.lbr`).
- Validation: the assembled entry passes `validate_molecule`; and is round-trip readable by
  `get_pattern`/`list_patterns` after writing.
- Errors: invalid seed / >1 inlet / composite / existing id without overwrite → error, no
  file written.
- `dryRun` → preview, no file.
- `overwrite` → writes only with the flag.
- M9 fixes: candidate missing `wlLabels` skipped; symmetric-label values set-merged
  (duplicates don't skew the median).

**Live (deferred with M7–M9)** — actually `instantiate_pattern` an approved mined molecule
against ExtendSim (§9.5 round-trip); auto-skips without ExtendSim.

## Definition of done

- `build_library_entry` + `approve_pattern_entry` pass all unit tests (COM-free).
- Written entries pass `validate_molecule` and are readable by `get_pattern`/`list_patterns`.
- `approve_pattern` tool registered, tool count 102 → 103.
- `pattern_approve.py` in `copy-files`.
- M9 minors fixed in `pattern_cluster.py` with tests.
- Fail-closed on every error path above; nothing written unless valid + approved.
- Live instantiation round-trip deferred to BACKLOG.
