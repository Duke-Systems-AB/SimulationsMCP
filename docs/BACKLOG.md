# Backlog

Future work items, newest first. Not a committed roadmap — a parking lot for
things we've agreed are worth doing but haven't scheduled.

## Deferred review minors (from 2026-07-19 codebase review)

See docs/superpowers/reviews/2026-07-19-codebase-review-findings.md section W3-7:
watcher-spawn-per-early-check load behavior, block_configure inline doc drift,
hierarchy_list O(n^2) depth walk, NUMERIC_FIELD_TYPES duplicate source of truth,
_get_array_connector_index 256-slot magic, dialog_watcher classification-helper tests,
wider *_set_config consolidation via a _simple_var_setter factory.

## Unified runtime block introspection (`block_introspect`) — MUST-HAVE

Today block introspection is split and incomplete: `block_discover_variables`
(runtime, live, COM) only sees what COM exposes — dialog items, connectors, popups —
NOT a block's internal ModL **STAT storage variables**, whose names live only in the
compiled `.lbr` blob. `block_inspect` (the authoring MCP, offline) parses that blob and
sees the STAT layer, but it's a separate MCP reading a file, not a live block.

This gap bit us on the 2026 Python Bridge block: `block_discover_variables` surfaced the
textframe *widget* `PythonScript_frm` (which doesn't round-trip), while the real script
storage variable `dsPythonCode` (a STAT var) was invisible at runtime — we had to
cross-reference the `.lbr` blob by hand.

**Build:** a runtime `block_introspect` that UNIFIES both layers — live COM discovery +
parsing the block's `.lbr` STAT section (locate the library via `GetLibraryPathName`,
read the blob like the authoring MCP does) — so any block, any time, reports its dialog
items AND its underlying storage variables. Bonus: `block_get_value`/`block_set_value`
could then auto-resolve "textframe widget → bound STAT variable" instead of hardcoding
names like `dsPythonCode`. Requested 2026-07-17, flagged a MUST by Jonas.


## M7 extract_psg — live verification (Task 5)

`extract_psg` shipped (M7, `src/psg_extract.py` + reader in `simulation_backend.py`),
pure core fully unit-tested, but the recursive live COM reader has **not yet been
run against a real model**. Follow-up:

- Run `src/ExtendSimMCP.TypeScript/tests/live/test_extract_psg_live.py` against a
  live ExtendSim with an H-block model open (safe COM pattern: single driver, no
  concurrent runtime server, in-range reads, never kill mid-call).
- Confirm scopes / nodes / boundaryEdges match the model, and **pin `hblockType`**:
  if the `GetLibraryPathName`-based pure/physical signal is unreliable, change
  `_psg_hblock_type` to return `None` (fail-closed) instead of a wrong tag.
- Deferred 2026-07-15 (ExtendSim was in use). Everything else in M7 is merged.

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

## Pattern Mining — the "learn from old models" half (miner, tools 1–4)

We shipped the *use* half of the Pattern Mining module (PRD §11): `instantiate_pattern`
(M3), `compose_flow` (M4), `list_patterns`/`get_pattern` (M5), attribute detection
(M6). The *learn* half — reading existing `.mox` models and distilling reusable
molecules into the library — is **not built**. That's PRD §11 tools 1–4 / FR-1..12.

Build order (each an independent spec → plan → build, brainstorming first):

- **M7 `extract_psg`** (FR-4/5) — read a `.mox` → PSG: nodes (`lib:blocktype` +
  params) + edges (`srcPort→dstPort`), H-blocks/named regions kept as boundary
  metadata. Reuses `hierarchy_get_contents` / `connection_list` / `model_extract`.
  Deterministic foundation everything else builds on.
- **M8 boundary detection + WL fingerprint** (FR-6/7) — offline over PSGs. MVP
  leans on the locked decision that **a pure/library H-block is always a molecule
  boundary**: pull each pure/named H-block out of a real model, canonicalize
  (Weisfeiler–Lehman), offer as a candidate. Flat (non-H-block) models are a
  non-goal — never guess boundaries from loose blocks.
- **M9 clustering + param/interface inference** (FR-8/9/10) — near-miss merge
  (graph edit distance), param varies→required / constant→fixed, interface =
  edges crossing the cut.
- **M10 `approve_pattern` + naming** (FR-11/12) — LLM proposes intent/port names,
  human approves, record written to the **same library** M5 already reads. Nothing
  enters the library without approval (fail-closed).
- `extract_port_registry` (tool 1, FR-1..3) — mostly a prebuilt standard registry
  for validation; fold into M7 or ship as a small standalone piece.

MVP path (M7 → simple M8) already delivers value: point at an existing H-block
model, get a candidate molecule out. Full near-miss/param inference (M9) follows.
Requested 2026-07-15. Not scheduled. See `docs/SimulationsMCP_Pattern_Mining_PRD.md`
§6.2–6.3, §9, §11 for the detailed design.

## Parallel REST/HTTP API (alongside MCP)

Build a standalone REST/HTTP API that runs in parallel with the MCP server,
exposing the same ExtendSim capabilities, so a client (AI or otherwise) can
**choose the interface that suits it best** — MCP for MCP-native clients, plain
REST for everything else.

- Reuse the existing Python COM backend (`simulation_backend.py`) as the shared
  core: MCP server and REST API should be two thin adapters over one backend,
  not duplicated logic.
- This is a *different surface* from the existing Express HTTP transport (which
  is MCP-over-HTTP / JSON-RPC for ChatGPT). REST = resource-style endpoints.
- Follow the normal brainstorming → spec → plan flow before building.
- Requested 2026-07-01. Not scheduled.

## tag-items — pin live column constants + live-verify

The tag-items attribute-write feature (fixed `attribute_set` + `setAttributes`
molecule config) shipped 2026-07-01 with **provisional** `AttribsTable_ttbl`
column constants in `src/ExtendSimMCP.TypeScript/src/attribute_config.py`
(`ATTR_NAME_COL` / `ATTR_VALUE_COL` / `ATTR_TYPE_COL`). Follow-up:

- Live-discover the real Set-block `AttribsTable_ttbl` layout (which column holds
  the attribute name vs. value, whether a value-source popup column exists, and
  whether the attribute must pre-exist in the model). Pin the constants.
- Run `tests/live/test_tag_items_live.py` against a live ExtendSim to confirm the
  molecule tags items end-to-end.
- Use the safe COM pattern (one-shot dialog watcher, in-range reads, never kill
  mid-call) — see the design spec's operative note.

## resource-machine — functional named-pool config

Still deferred. Needs the Resource Pool block configured with a defined named
pool (table-based config, same shape as the now-solved tag-items table write)
plus the Queue's `QueueType_pop = 2` (Resource Pool mode, already the confirmed
workaround). With the string-table + attribute-table capability now in place,
this is unblocked technically; needs its own spec → plan → build.
