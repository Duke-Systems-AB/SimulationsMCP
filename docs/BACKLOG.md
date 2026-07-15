# Backlog

Future work items, newest first. Not a committed roadmap — a parking lot for
things we've agreed are worth doing but haven't scheduled.

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
