# Changelog

All notable changes to the Simulations MCP Server. Versions match the installer
(`installer/SimulationsMCP-Setup-<version>.exe`) and `package.json`.

## Unreleased

### Changed — client-visible
- **New error code `EXTENDSIM_ERROR_DIALOG`.** A command blocked by an ExtendSim error
  dialog used to come back as `COM_TIMEOUT`, even when the dialog was caught within two
  seconds. On 1.22.1 every single `COM_TIMEOUT` in the telemetry (24 of 24) had a dialog
  behind it — none was a real timeout. The label mattered: "timeout" reads as "transient,
  try again", and a client re-ran `simulation_run` six times against the identical
  `[62]Queue ... out of Range` error. The new code says what happened, and its suggestion
  says to fix the cause rather than retry. `COM_TIMEOUT` now means only what it says: no
  answer, and no dialog to explain why. **Clients that matched `COM_TIMEOUT` to detect
  dialogs need to match `EXTENDSIM_ERROR_DIALOG` instead.** This narrows `COM_TIMEOUT` to
  its intended meaning rather than redefining it.

### Fixed
- **`ga_read` and `ga_write` no longer raise ExtendSim dialogs themselves.** Neither
  checked the array's size, so reading a range past the last row — or any cell outside the
  array — made ExtendSim raise a modal "Row or column reference out of range" dialog that
  blocked COM. Observed on 2026-09-14, reading `_AttributeList`. Both now read the array's
  rows and columns first: a range read is clamped to what exists (as `db_get_records`
  already did) and flagged `clamped` with a warning; an out-of-range start cell or write
  fails closed with `INVALID_PARAMETER` and the array's real dimensions, without the call
  ever reaching ExtendSim.

- **G2 wave 4** — offline tests for the eleven layout and simulation-control tools that
  had none: `block_move`, `block_get_position`, `block_find`, `block_align`,
  `block_duplicate`, `simulation_pause`, `simulation_resume`, `simulation_step`,
  `simulation_status`, `simulation_setup_get`, `simulation_get_state`. Python suite
  275 → 292. No behaviour change.

## 1.22.2 — 2026-09-15

A gap-sweep release: one real bug fix, the project scaffolding the repo never had,
and 36 new tests. Tool count unchanged at 104.

### Fixed — customer-impacting
- **`ga_list` reported no global arrays at all** when the model's only global array sat
  at index 0. It read `GALastUsedIndex` as `int(parse_float(...) or -1)`, and `0.0 or -1`
  is `-1`, so the enumeration loop never ran. Found by writing the first test the tool
  had ever had.
- **`workstation_set_config` now warns** when a delay type maps to one of the
  never-verified popup indices. ExtendSim exposes no way to read that popup's labels over
  COM, and the block's own help text suggests the mapping may be off by one — which would
  silently select a neighbouring option. The doubt now reaches the caller instead of
  sitting in a source comment. See `WORKSTATION_DELAY_OPTIONS`.

### Added — testing
- 36 offline tests covering tools that had none: `time_convert`, `template_list`,
  `context_clear`, all four `ga_*`, and the nine untested `db_*` plus their shared
  `_resolve_db_indices` gateway. Python suite 239 → 275.
- The `endRecord` asymmetry is now pinned from both sides — **exclusive** for
  `db_get_records`, **inclusive** for `db_delete_records`. Both are intended and
  documented; the tests stop someone "correcting" one into a data-loss bug.

### Changed — verified assumptions
- The distribution numbering is **confirmed** to apply to Activity, not just Create: a
  fresh Create reads `10` (exponential) and a fresh Activity reads `34` while the block's
  own change log records a switch to triangular on creation. The "assumed for Activity"
  caveat is gone.
- Corrected a source comment that claimed these popup indices had been verified through
  `GetDialogItemLabel`. That call returns empty for every popup on every block tested, so
  the code can never self-verify them.

### Changed — the installer is 4.0 MB instead of 12.9 MB
The `.iss` bundles all of `node_modules`, so every installer ever built also shipped the
dev toolchain — typescript, vitest, ts-node, `@types` — to customers. Adding eslint as a
devDependency this release made it visible (12.9 → 14.9 MB) rather than causing it.
`build-installer.bat` now runs `npm prune --omit=dev` before packaging and restores the
dev dependencies afterwards. Smoke-tested: the server still starts and loads every
runtime dependency from the pruned tree.

### Added — project infrastructure
- **`requirements.txt`** — the Python dependencies were never declared. `pywin32` was
  documented in the README; **`comtypes` was not**, and its import in
  `dialog_watcher.py` is guarded, so installing without it left the server running and
  still dismissing dialogs but unable to *read* dialog text — silently turning a
  useful error into a bare `COM_TIMEOUT`. Also `requirements-dev.txt`.
- **Linting**: `eslint.config.mjs` (ESLint 9 flat config + typescript-eslint) and
  `ruff.toml`. Both are at zero errors. Run with `npm run lint:all`.
- **`pytest.ini`** — bare `pytest` now runs the offline suite only; the live suite is
  opted into explicitly. New `npm run test:py` and `npm run test:all` (every offline
  test in one command — 426 of them now — which is what CI needs).
- **`azure-pipelines.yml`** — CI on every push and PR to `main`: build, lint both
  languages, run both offline suites, publish JUnit results.
- **`SECURITY.md`** — how to report a vulnerability privately, and what is in scope
  versus a documented design decision.
- **`.gitattributes`** — normalizes line endings. The index was already uniformly LF,
  so nothing was corrupted, but mixed endings made every commit warn and silently broke
  text comparisons during review work.

### Fixed — found by the new linters on their first run
- Removed dead telemetry code: `updateEnvInfo()` was imported by `index.ts` but never
  called, and the `envInfo` map it wrote was never read by anything.
- Unused bindings and loop variables, an unused local in `simulation_run`'s stop path, a
  lambda assignment, 20 f-strings with no placeholders, and `raise ... from err` in
  `compose.py` so a molecule-load failure keeps its cause.

### Changed
- README: Python is pinned at **3.9+** (tested on 3.13; the backend uses no syntax newer
  than 3.7) and points at `requirements.txt`.

### Documentation accuracy pass
No code changes; the 104-tool count is unchanged.

- **User Manual**: the tool reference documented only 92 of the 104 tools. Added
  `block_introspect`, `table_get`, `table_set`, `detect_attributes` and a new
  "Patterns and Mining" section covering all eight pattern tools. Error-code appendix
  expanded from 10 to the full set, grouped by area — it also named a `TIMEOUT` code
  that does not exist (the real one is `COM_TIMEOUT`) and promised a `suggestion`
  field on every error, which is optional. Timeout table now matches
  `backend.ts` command for command. New section on `EXTENDSIM_DEBUG` logging.
- **Design Document**: was still stamped v1.19.1 / 92 tools / 17 categories, with
  component line counts up to 25% low. Refreshed against the code, plus a new §3.7
  describing the 13 auxiliary Python modules and the injected-backend pattern that
  makes them offline-testable.
- **README**: the test command claimed `npm test` runs all 390 offline tests; it runs
  the 151 TypeScript ones. The 239 Python tests need `python -m pytest tests/unit_py`.

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
