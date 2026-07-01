# Backlog

Future work items, newest first. Not a committed roadmap — a parking lot for
things we've agreed are worth doing but haven't scheduled.

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
