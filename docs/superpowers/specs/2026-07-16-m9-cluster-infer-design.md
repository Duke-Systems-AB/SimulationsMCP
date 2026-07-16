# M9 clustering + param/interface inference — Design

**Status:** Approved 2026-07-16
**Module:** Pattern Mining — the "learn from old models" (miner) half, milestone 3 of 4 (M7–M10).
**PRD refs:** FR-8, FR-9, FR-10 (§6.3); algorithms §9.2 (near-miss), §9.3 (param), §9.4 (interface); library entry §7.1. Partial of tool #3 `mine_patterns`.

## Goal

Turn M8's per-model candidate subgraphs into **mined pattern candidates**: group
instances of the same molecule (exact WL bucket + near-miss GED merge), then infer
each cluster's parameter schema and interface. Output is shaped toward the library
entry (§7.1) but pre-approval — M10 adds id/intent/port names and the human gate.

Non-goals (later): naming, human approval, and library persistence are M10. Flow
mining (composites as flows) beyond the `kind` flag is out of scope. Attribute
contract inference beyond what M6 already provides is out of scope.

## Locked decisions

1. **Full GED for near-miss.** Real, deterministic graph edit distance between bucket
   representatives (bipartite node-assignment / Riesen–Bunke), self-contained
   Hungarian — NO scipy dependency. Conservative merge threshold; merged clusters
   flagged `nearMiss: true` for M10 review.
2. **Input = offline + live.** `cluster_patterns` aggregates candidates from
   `candidatesPaths` (offline M8 output JSON), `psgPaths` (offline PSG → M8), and
   `filePaths` (live model → M7→M8). Live paths' end-to-end run is deferred with
   M7/M8's live verification; the offline aggregation path is unit-tested.
3. **Pure module + thin adapter** (same as M7/M8): `pattern_cluster.py` is COM-free
   and fixture-tested; the `cluster_patterns` entry resolves sources then calls it.
4. **Node alignment across instances via WL labels.** M8's `wlLabels` (ref → final WL
   label) identify structural positions, so params/interface align without trusting
   block ids. Label collisions (symmetry) merge their values as a set (v1).
5. **M9 produces bindings, not names.** Interface entries carry `binds` + best-effort
   `role`; port names and pattern id/intent are assigned by M10.

## Architecture & components

- **New pure module `src/pattern_cluster.py`** (zero COM, fixture-tested):
  - `graph_edit_distance(a, b) -> float` — bipartite-assignment GED between two
    candidate subgraphs, with a self-contained Hungarian solver.
  - `cluster_candidates(candidates, ged_threshold=2) -> list[dict]` — exact WL
    bucketing then near-miss GED merge; returns clusters.
  - `infer_pattern(cluster) -> dict` — param + interface inference + template build.
- **Thin entry `cluster_patterns(candidates_paths=None, file_paths=None,
  psg_paths=None, save_path=None)`** in `simulation_backend.py` — aggregates
  candidates from all sources then calls the core.
- **MCP tool `cluster_patterns`** in `index.ts` via `backend.ts` proxy + Python
  `COMMANDS` entry. Tool count **101 → 102**.
- **`pattern_cluster.py` added to `package.json` `copy-files`.**

## Graph edit distance (§9.2, full GED)

Bipartite node-assignment approximation (Riesen–Bunke), deterministic, for small
graphs (3–10 nodes). Given candidate subgraphs A and B (each `nodes` + `edges`):

- **Node label** = `f"{lib}:{type}"`.
- **Local edges of a node** = multiset of `(direction, ownPort, neighborPort)` over
  its incident edges (direction: `out` for the `from` side, `in` for the `to` side;
  a `directionConfident:false` edge contributes both, matching M8's WL treatment).
- **Substitution cost** `C[i][j]` (node i of A → node j of B) =
  `nodeSub(i,j) + 0.5 * multiset_symdiff(localEdges(i), localEdges(j))`, where
  `nodeSub = 0` if labels equal else `1`.
- **Deletion cost** of node i = `1 + 0.5 * |localEdges(i)|`; **insertion** of node j =
  `1 + 0.5 * |localEdges(j)|`.
- Build the padded `(|A|+|B|) × (|A|+|B|)` cost matrix (substitution block +
  deletion diagonal + insertion diagonal + zero dummy block) and solve the optimal
  assignment with a self-contained Hungarian (Kuhn–Munkres), O(n³). **GED = total
  assignment cost.**
- Deterministic: costs are integers/half-integers; ties broken by index order in the
  Hungarian implementation. `graph_edit_distance(a, b) == graph_edit_distance(b, a)`.

## Clustering (§9.2)

1. **Exact bucket:** group candidates by `wl_fingerprint` (identical instances group
   for free; pure H-blocks cluster perfectly).
2. **Near-miss merge:** for each pair of bucket representatives, compute
   `graph_edit_distance`; union buckets whose GED ≤ `ged_threshold` (default 2). A
   cluster formed by any merge is flagged `nearMiss: true` (→ M10 review). Merge is
   transitive (union-find over the pairwise ≤-threshold relation) but only within the
   candidate set at hand.

Each cluster = `{fingerprint, instances: [candidate...], nearMiss: bool}`.

## Param inference (§9.3)

Align nodes across a cluster's instances by WL label (M8 `wlLabels`). For each
aligned position × each param key present on that position:

- collect the value across instances;
- **all equal** → `fixed` (value baked into the template);
- **varies** → `required`; `default` = median for numeric values, else the most
  common value; numeric also carries `range: [min, max]`.

A cluster's **representative** is its first instance (deterministic by aggregation
order); positions are labelled by the representative's node refs (M8's `bID` style).
Param keys are generated positional names `"<repRef>.<paramKey>"` — e.g. `"b3.D"` —
which M10 renames to friendly names. The template is built from the representative's
`nodes`/`edges`. A `support == 1` cluster yields a valid candidate: every param
becomes `required` with no `fixed`/range inference possible (noted, not an error).

## Interface inference (§9.4)

From each instance's `boundaryEdges`, aligned by the internal node's WL label + port:

- `crosses: inlet` → an inlet; `crosses: outlet` → an outlet; `binds` =
  `"<position>.<port>"`.
- `role` = best-effort from the connector name (`Item…` → `item`, `Value…` →
  `value`) else `null`. Port **names** are assigned by M10.

## Output — mined pattern candidate

Shaped toward the §7.1 library entry, pre-approval:

```jsonc
{
  "wl_fingerprint": "b7d3…",
  "support": 3,
  "nearMiss": false,
  "hblockType": "pure",              // if consistent across the cluster, else null
  "kind": "molecule",               // or "composite"
  "params": {                        // keys use the representative's refs; M10 renames
    "b3.D": { "type": "number", "required": true, "default": 5, "range": [2, 8] },
    "b2.capacity": { "type": "number", "required": false, "fixed": 1 }
  },
  "template": { "nodes": [ /* {{b3.D}} for required, 1 baked for fixed */ ],
                "edges": [ /* interior edges */ ] },
  "interface": { "inlets":  [ { "binds": "b2.inCon0",  "role": "item" } ],
                 "outlets": [ { "binds": "b3.outCon0", "role": "item" } ] },
  "instances": [ { "scopeId": "h140", "source": "modelA.mox" } ],
  "example": { "b3.D": 5, "b2.capacity": 1 }
}
```

The template's node params carry `{{<paramKey>}}` for required params and the baked
literal for fixed ones; `hblockType` is set only when identical across all instances
(else `null`).

## Data flow

```
cluster_patterns(candidatesPaths?, filePaths?, psgPaths?, savePath?)
  1. Aggregate candidates from all provided sources:
       candidatesPaths[] -> read JSON, take .candidates            [OFFLINE]
       psgPaths[]        -> mine_candidates(psg_path=..).candidates [OFFLINE via M8]
       filePaths[]       -> mine_candidates(file_path=..).candidates[LIVE via M7->M8]
       (tag each candidate with its source for provenance)
  2. cluster_candidates(all, ged_threshold=2)   <- PURE
  3. infer_pattern(cluster) for each cluster     <- PURE
  4. Return {success, clusterCount, patterns[]}  (or write to savePath)
```

## Error handling (fail-closed)

- Unreadable / invalid `candidatesPath` / `psgPath` → error unchanged, no guess.
- `mine_candidates` (live/psg) failure → propagate verbatim.
- No candidates from any source → `{success: true, clusterCount: 0, patterns: []}`.
- `support == 1` cluster → a valid candidate (all params `required`, no fixed/range).
- A candidate missing `wl_fingerprint`/`wlLabels` (malformed) → skipped defensively,
  never crashes the run.

## Testing

**Unit (COM-free, `pattern_cluster`)** — fixtures in, results out:

- **GED:** identical graphs → 0; one extra node → ~1; one relabeled node
  (`lib:type` differs) → 1; differing ports → nonzero; symmetry
  (`ged(a,b) == ged(b,a)`) and determinism (same pair → same value).
- **Clustering:** two identical WL → one bucket; two near (GED ≤ threshold) → merged
  with `nearMiss: true`; two far (GED > threshold) → separate clusters.
- **Param inference:** constant across instances → `fixed`; varying → `required` +
  median default + range; non-numeric → most-common value.
- **Interface:** boundaryEdges → correct inlets/outlets + `binds`; role best-effort.
- **Alignment:** nodes align by WL label, not block id (different ids, same structure
  → same positions).
- **Offline aggregation:** `cluster_patterns` with two `candidatesPaths` → candidates
  merged and clustered (COM-free, unit-tested end-to-end).
- `support == 1` → valid candidate with all-required params.

**Live (guarded, deferred with M7/M8)** — `cluster_patterns(filePaths=[...])` against
real models; auto-skips without a model.

## Definition of done

- `graph_edit_distance`, `cluster_candidates`, `infer_pattern` pass all unit tests.
- `cluster_patterns` tool registered, tool count 101 → 102.
- `pattern_cluster.py` in `copy-files`.
- Offline aggregation path unit-tested; live path deferred to BACKLOG.
- Fail-closed on the paths above; GED deterministic and symmetric.
