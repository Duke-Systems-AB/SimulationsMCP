# Changelog

All notable changes to the Simulations MCP Server. Versions match the installer
(`installer/SimulationsMCP-Setup-<version>.exe`) and `package.json`.

## 1.22.1 — 2026-07-19

Quality release: a full codebase health review (4 parallel review agents) followed by
three fix waves. Tool count unchanged at 104.

### Fixed — customer-impacting
- **Error reporting**: backend errors (`success: false`) are now correctly flagged as
  errors to the AI client and in telemetry — previously most real failures were
  reported as successes.
- **Equation tools** (`equation_set_formula`, `equation_i_set_formula`,
  `queue_equation_set_config`): now write the real storage variable
  (`EQ_EquationText`) with read-back verification, instead of a broken dialog handle
  that silently discarded the equation. Multi-line equations are normalized to a
  single line; equations compile at the next simulation run. Live-verified.
- **`block_configure` (Queue)**: no longer silently drops `maxLength`,
  `renegeEnabled`, `renegeTime`, `calcWaitCosts`, `shift`, `calcDelay`.
- **`block_add_batch`**: no longer stacks blocks (reuses `block_add`'s verified
  placement) and reports real read-back positions.
- **Portability**: removed hardcoded developer-machine debug-log paths that could
  crash `block_connect` on other machines; debug logging now opt-in
  (`EXTENDSIM_DEBUG`) and written to the system temp directory.
- **ModL string escaping**: database names, save paths, and block/library names are
  now escaped (quotes/backslashes/newlines no longer break generated ModL).
- Added missing COM timeouts for `detect_license` and `model_close`.

### Fixed — robustness
- Retried commands (after a backend restart) get a fresh timeout instead of
  inheriting the dying request's countdown.
- Auto-dismissed ExtendSim dialogs no longer discard a command's real result: a 5 s
  grace window lets the true response win.
- Partial molecule/flow builds now report orphaned block/H-block ids
  (`partialBuild`, `orphanedBlockIds`, `orphanedHblockIds`).
- `queue_set_priority` validates the target is a Queue; popup writes
  (`QueueRank_Pop`, `BatchType_pop`, `AllocRule`, `SelectType_pop`) are read-back
  verified with warnings on mismatch.
- `db_relations_list` returns an honest not-implemented error instead of fake data.
- Molecule validation rejects undeclared `{{placeholders}}` and missing param values
  up front (no more mid-build KeyErrors).

### Changed — internal quality
- Removed ~360 lines of dead TypeScript (32 unused pre-v1.7 wrappers).
- Consolidated duplicated logic (merge/diverge config, connection diagnostics,
  pattern-module helpers).
- Pattern mining now extracts `Set`/`Shutdown`/`Resource Pool Release` parameters,
  carries `setAttributes` through extract → cluster → approve, and infers real
  attribute read/write contracts (previously hardcoded empty).
- Test suite grew from 197 to 239 Python tests; TypeScript suite (151) repaired and
  green, including new backend lifecycle tests.

## 1.22.0 — 2026-07-19

- New tool **`block_introspect`** (104 tools): unified block introspection — live
  dialog items plus internal STAT storage variables parsed from the block's `.lbr`
  (names, types, dimensions), with live values for scalars (arrays are never read —
  wedge-safe). Surfaces variables like `EQ_EquationText` and `dsPythonCode` that the
  dialog API cannot see.
- `lbr_stat.py` offline STAT parser; `copy-files` ships it with the build.

## 1.21.0 — 2026-07-17

- Pattern-mining pipeline complete (103 tools): `extract_psg` (M7),
  `mine_candidates` + WL fingerprint (M8), `cluster_patterns` (M9),
  `approve_pattern` (M10) — learn reusable molecules from existing models, curated
  into the same library `instantiate_pattern`/`compose_flow` build from.
- BUG-009a: `_escape_modl_string` escapes newlines/CR/tab (multi-line strings no
  longer raise "unterminated string" modals).
- Installer: ExtendSim 2026 support notes; robust Inno Setup detection.

## 1.20.0 — 2026-07-15

- `table_get` / `table_set` (dialog tables), `detect_attributes` (M6 attribute
  detection), pattern library foundation (`instantiate_pattern`, `compose_flow`,
  `list_patterns`, `get_pattern`), resource-machine H-block solution.
- BUG-001..004 fixes (license detection without a model, distribution popup codes,
  block_add auto-flow + real positions, GetBlockTypePosition array).

## 1.19.2 and earlier

Initial public releases: core model/block/simulation/database/hierarchy/analysis
tooling, MCP_init guidance system, pattern search over 268 example models, advisor,
telemetry, Windows-service installer with stdio + HTTP transports.
