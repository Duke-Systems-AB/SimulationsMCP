# Changelog

All notable changes to the Simulations MCP Server. Versions match the installer
(`installer/SimulationsMCP-Setup-<version>.exe`) and `package.json`.

## 1.26.0 — 2026-10-11

A model's data files, end to end: the AI can see which Excel and text files a model reads and
writes, repoint them after a copy, set up imports and exports, and Excel exports now reach the
file on disk after every run (ExtendSim never saves them itself). Runs no longer start into a
missing or retargeted file. Model handling is safe by default: closing a changed model is an
error unless the AI says save or discard, and the server never presses Save on ExtendSim's or
Excel's save prompt. 116 tools. Verified live on ExtendSim 2024 and 2026.

### Changed
- **Closing a model with unsaved changes is now an error, not a silent discard.** `model_close`
  called with no `saveFirst`, `discard` or (for a model that has never been saved) `filePath`
  returns `MODEL_HAS_UNSAVED_CHANGES` and leaves the model open and exactly as it was - nothing is
  lost. Pass `saveFirst: true` to save then close, `discard: true` to close without saving, or
  `filePath` to save a never-saved model there first. `saveFirst` and `discard` together is
  `INVALID_PARAMETER`. Verified live on ExtendSim 2024 and 2026. `filePath` must name a new
  file - an existing one is refused with `INVALID_PARAMETER`, never overwritten - and a save that
  does not take (closing still asks to save) returns `MODEL_SAVE_FAILED` with the model left open.
- **`model_open` checks that the file really opened.** When ExtendSim does not open it (a path
  that does not exist, say) the answer is `MODEL_OPEN_FAILED` naming the model that is still
  active, instead of reporting that model as opened. `extract_psg` (and the mining tools built on
  it) with a `filePath` therefore never reads - or closes - the model you had open.
- **`model_list` has a 30 s timeout**, and its open-window read is bounded at 5 s: during a
  running simulation it falls back to the active model with a warning instead of timing out.

### Added
- **`model_list` lists every open model**, not only the active one: name, which one is active, and
  the active model's folder. Falls back to the active model alone, with a warning, if the window
  list cannot be read.
- **`model_open` refuses to switch to a model that is open but not active**, instead of silently
  activating it (`MODEL_OPEN_FAILED`, `alreadyOpen`, `activeModel`, with a suggestion to bring it
  to the front in ExtendSim or close the active model first). Measured live: activating it through
  ExtendSim's own "Already Open" box couples the two models together - closing one closes both,
  and if the hidden one had changes its save prompt could not be answered Cancel.
- **`model_new` refuses a `savePath` that names a model already open** (`INVALID_PARAMETER`)
  instead of letting ExtendSim collide two models with the same name.
- **`model_files_list` and `model_files_repoint`** (new tools, 115 in all): see which files a
  model reads and writes through its Data Import Export blocks (Value.lbr) - direction, file
  type, stored and resolved path, whether the file exists on disk, sheet, database/table, and
  whether it imports/exports automatically - and point blocks at other files, either one by one
  or by moving everything under one folder to another. Every new path is validated (absolute, at
  most 255 characters, no `"`; an import file must exist, an export file's folder must exist)
  before anything is written, and read back afterwards - a path that does not read back exactly
  returns `MODEL_FILES_REPOINT_FAILED` instead of a cheerful success. File existence is always
  checked on disk, never by asking ExtendSim to open the file.
- **Run guard against missing data files.** `simulation_run`, `simulation_run_multi`,
  `simulation_run_scenarios`, `optimizer_run` and `scenario_manager_run` now refuse with
  `DATA_FILE_MISSING` (nothing starts) when a file the model would auto-import at run start is
  missing, instead of letting ExtendSim show its own "does not exist or is not in the current
  folder" box - which, for a block inside a hierarchical block with a relative path, could crash
  ExtendSim. If the box appears anyway (a file removed mid-run), it is still dismissed
  automatically and the run's response carries a readable `dataFileNotes` entry. A block guide
  for Data Import Export ships with the server (`block_search`). Verified live on ExtendSim 2024
  and 2026.
- **`model_files_configure`** (new tool, 116 in all): sets up Data Import Export blocks for Excel
  or text import/export in one call - up to 20 entries, each naming an existing top-level block or
  a place to put a new one, direction, file type, file path, database/table by name, and whether
  it imports/exports automatically. Everything is validated before anything is written
  (`INVALID_PARAMETER`, nothing changed) and every value is read back; a value that does not take
  returns `MODEL_FILES_CONFIGURE_FAILED` with truthful `steps`. An optional `verifyImport` presses
  Import now and reports the record count. The intended database/table is stored in the model's AI
  context (merged, never replacing other context) so a later silent table retarget can be
  detected. Verified live on ExtendSim 2024 and 2026.
- **Run tools now save Excel exports to disk and release the workbooks they opened.**
  `simulation_run`, `simulation_run_multi`, `simulation_run_scenarios`, `optimizer_run` and
  `scenario_manager_run` save every Excel export workbook the model points at after a finished run
  - ExtendSim itself never writes it to disk, measured on both versions - and close workbooks the
  run opened that have no unsaved changes; the response carries `savedExports` and
  `closedWorkbooks` (`saveExports: false` skips this). The work runs in a worker process with a
  20 s deadline and never starts Excel, so a stuck Excel cannot hang the server.
  `dialog_watcher` answers Excel's own "Save your changes to this file?" prompt with Cancel -
  only in the background Excel that ExtendSim's block uses (started with `/automation`), never
  in an Excel you opened yourself.
- **Run guard against a retargeted table or an Excel close conflict.** The run tools above also
  refuse with `DATA_FILE_TARGET_CHANGED` when a block configured with `model_files_configure` now
  points at a different table than the one it was set up for, and with
  `DATA_FILE_EXCEL_CLOSE_CONFLICT` when an auto Excel import has "close worksheet after use" on
  while the model also has an Excel export - a combination that hangs ExtendSim on Excel's own
  save prompt at run start. Verified live on ExtendSim 2024 and 2026.

### Fixed
- **A data file next to the model no longer stops the next run.** A Data Import Export block
  whose file is a bare name (no folder, e.g. `Arrivals.xlsx`) is now found in the model's own
  folder, as ExtendSim itself does. ExtendSim 2024 rewrites a path into the model folder to such
  a bare name after an import or export, so the run after it used to be refused with
  `DATA_FILE_MISSING`. `model_files_list` shows the bare name resolved against the model folder.
  A relative path with a folder (`sub/Arrivals.xlsx`), and a bare name in a model that was never
  saved, are still refused.
- **ExtendSim's save prompt is answered Cancel, never Save.** "The model '...' has been
  modified. Do you want to save your changes?" now gets Cancel automatically - the model stays
  open and unchanged - instead of blocking the server. It is never answered Yes or No. The
  fallback key for a box that cannot be read (Enter, or Escape for a box with a known answer) now
  goes only to the box it was meant for, only while ExtendSim responds, and the box counts as
  closed only when it has really gone; it is no longer a keystroke to whatever window has the
  focus.
- **`model_open` compares the whole path.** Opening a model whose file name matches the active
  model but lives in another folder used to report `alreadyOpen` without opening anything; it now
  refuses with `MODEL_OPEN_FAILED` and says which folder the open model came from - close it
  first. ExtendSim's "Already Open" box no longer blocks the server; it is answered OK.

## 1.25.0 — 2026-10-08

Advanced blocks your AI can set up itself: the Resource Manager (`resources_configure`) and the
analysis blocks - Scenario Manager, Optimizer and Analysis Manager - each in one call, verified
against what ExtendSim actually took. Optimizer and Scenario Manager runs now report their real
outcome, and the server works with one ExtendSim at a time. 113 tools. Verified live on
ExtendSim 2024 and 2026.

### Added
- **`resources_configure`** (new tool, 109 in all): sets up Advanced Resource Management in one call -
  resource classes (pool, count, custom properties such as Skill, a shift), requirements (a quantity
  with filters, or any one of other requirements), and the queues and releases that use them. The
  server generates the resources and requirements in ExtendSim and verifies them: it reports success
  only when every filter, queue and release reads back. A block guide for the Resource Manager comes
  with it (`block_search`).
- **`scenario_manager_configure`, `optimizer_configure`, `analysis_manager_configure` and
  `analysis_manager_get_results`** (new tools, 113 in all): set up a Scenario Manager, an Optimizer
  or an Analysis Manager in one call, the same way a person would in ExtendSim's own dialog. The
  server adds every factor, response, variable, output or objective itself - including answering
  the "User Parameter" name prompt ExtendSim pops up for each one - and reads everything back, so
  success is only reported once ExtendSim has genuinely taken it. `scenario_manager_configure` takes
  factors and responses plus a full factorial or a named list of scenarios; `optimizer_configure`
  takes variables, outputs and an objective such as `"MaxProfit = Exited*10 - Cap*200;"`;
  `analysis_manager_configure` takes the responses recorded on every replication of a run, read
  back afterwards with `analysis_manager_get_results`. Block guides for all three ship with the
  server (`block_search`).

### Fixed
- **One ExtendSim at a time.** With two ExtendSim instances open (for example 2024 and 2026),
  ExtendSim's prompts and messages could appear in the instance the server is not connected to, and
  a call could hang. The server now refuses every ExtendSim command while more than one runs
  (`MULTIPLE_EXTENDSIM_INSTANCES`, listing them); `extendsim_status` shows the same warning.
- **A timed-out command no longer loses the message ExtendSim showed.** When the check for blocking
  boxes ran out of time after closing a box, the server answered "no dialog"; it now reports the box
  and its text (or that its text could not be read).
- **Analysis set-up keeps within its time limit on large models.** The time for answering
  ExtendSim's name prompts now counts from the start of the call, so a slow model gives a clear
  `ANALYSIS_CONFIG_FAILED` instead of a timeout.
- Running a Scenario Manager or an Optimizer with `waitForCompletion: true` no longer loses the text
  of ExtendSim's own message boxes that were closed automatically while the run was still in
  progress (including the "Optimization converged to X%." box) - that text now always reaches
  `dialogs`, even when the box is dismissed right as the run finishes.
- `optimizer_run` and `scenario_manager_run` (with `wait_for_completion`) now report the run's real
  outcome. The optimizer reported "did not start within 30 seconds" for an optimization that had
  finished, and the Scenario Manager reported "completed" for a run ExtendSim had aborted. Both now
  look at what the run left behind: the optimizer succeeds only after at least one generation (and
  reports the best value, generations and convergence); the Scenario Manager succeeds only when every
  selected scenario has every response, and otherwise lists the scenarios without results. With
  `wait_for_completion` the previous results of the selected scenarios are cleared before the run, so a
  rerun that ExtendSim aborts can no longer show the last run's results as its own (a Scenario Manager
  without block responses cannot be checked and is refused in that mode). `scenario_manager_run` now
  selects all scenarios only when none are selected, as documented; a selection you made is kept. ExtendSim's own messages about an aborted run are returned in `dialogs`. An aborted Scenario
  Manager run no longer waits for the full timeout, and all scenarios are collected (only the first was
  when scenarios had just been created). A Scenario Manager with an empty scenario table is refused
  ("no scenarios") instead of selecting rows that do not exist, which raised ExtendSim error boxes.
- Shift blocks configured through `block_configure` (`schedule`) never followed their schedule: the
  periods were written into the wrong columns of the block's table. The server now writes the block's
  own change rows (time and On/Off, or the number for a numbers shift, `statusType` 2) and reads
  them back. Refused now instead of written wrong: a schedule with more changes than the block's
  table holds, a period without `endTime` (it used to be taken as 8), and a capacity other than
  0 or 1 on an on/off shift (it used to become "On" silently - use `statusType` 2 for numbers).

## 1.24.0 — 2026-10-02

Blocks your AI has never seen: `block_profile` reads any block - your own, third-party or locked -
by asking ExtendSim, and `guide_draft` turns the result into a block guide of your own. The server
no longer reads ExtendSim library files directly. `simulation_run` no longer calls an aborted run
"completed". 108 tools. Verified live on ExtendSim 2024 and 2026.

### Added
- **`block_profile`** (new tool, 108 in all): asks ExtendSim about a block (ExtendSim must be
  running) by placing it in a temporary model of its own and reading its dialog items through
  COM; nothing on the block or the user's model is changed, and the temporary model is closed
  when done. Without `block`: the standard libraries' block names (an inventory, not a live
  read). With `block`: its settings, popup options, labels, and a summary of its help from the
  installed `.chm` file. Meant for blocks that have no guide - your own libraries and
  third-party blocks. A library outside ExtendSim's Libraries folder can be given by its full
  path: when the block cannot be placed, ExtendSim is asked to open the library
  (`libraryOpened: true`). Some things are not knowable this way - what a setting resets, the order
  settings must be written in, whether a setting's handler opens a dialog box - and the profile
  says so rather than guessing.
- **Your own block guides:** `guide_draft`, `guide_save` and `guide_delete` take `kind: "block"`.
  A draft comes from `block_profile` with nothing marked as proved; after you and the AI have
  written it in your words it is saved in your guide folder, and `block_search` shows it
  (`guideSource: "local"`). An official guide for the same block always wins.

### Changed
- `block_introspect` no longer lists a block's static variables: ExtendSim does not expose
  their names through COM, and the server no longer reads library files directly.
  `stat_variables` is empty with a `stat_warning`.

### Fixed
- `simulation_run` reported "completed" for a run that ExtendSim had aborted (an error in a
  block's settings, e.g. an advanced-resource queue with no valid requirement): the error
  dialog was dismissed and then forgotten. A run stopped at time 0 is now `SIMULATION_RUN_FAILED`;
  a run that stops before its end time says `status: "stopped early"` with `stoppedAt`, and
  becomes `SIMULATION_RUN_FAILED` when an error dialog appeared. Any dialog dismissed while a
  command ran is now reported in `dialogsDuringCommand` instead of being dropped.
- ExtendSim 2026 asks "Unsigned Blocks Detected" when it opens a library made before 2026.R1
  (e.g. a third-party library). The box blocked every further command until someone answered
  it. The server now answers **Cancel**, which leaves the library files unchanged (the blocks
  still work), and reports the box. It never presses "Sign Blocks", which would rewrite and
  save the library.

## 1.23.0 — 2026-09-30

Queue blocks for your AI: it can now build a matching queue and an attribute-ranked queue, and
it can read a guide for the blocks it does not know - what they are for, what has been proved
to work with the server's tools, and the pitfalls. Guides are also delivered from duke.se
between releases. Tool count unchanged at 107. Verified live on ExtendSim 2024 and 2026.

**Upgrade first if you use** Queue Equation through `block_configure` (such models never ran),
or `model_close` in scripts that must know the model is really closed.

### Added
- `block_configure` for **Queue Matching**: `matchAttribute` (numeric attribute; variable groups),
  `numQueues`, `releaseOptions`. Items with different key values are never released together
  (verified on ExtendSim 2024 and 2026).
- `block_configure` for **Queue Equation**: `inputAttribute` turns the block's input row into an
  item attribute the equation can use.
- **Block guides**: `block_search` marks blocks that have a guide (`hasGuide`); `detail: true`
  returns it - what the block is for, proved recipes written as tool calls, what is not proved,
  pitfalls. Guides travel with the modelling guides and are updated from duke.se between
  releases. First guides: Queue Matching, Queue Equation, Query Equation (I).

### Fixed
- ExtendSim 2026 could stay blocked at start-up once a subscription had passed its renewal
  date: the grace-period notice ("ExtendSim Subscription Expired") was not recognised as a
  start-up reminder, so it was not closed and the server could not reach ExtendSim. It is now
  closed like the other start-up reminders, and its text is never passed on.
- A Queue Equation configured with `block_configure` never ran: the run stopped with "A name
  needs to be specified for the output variable in row 0", because a new block's output name
  is only filled in when its dialog is opened. The name is now filled whenever the block is
  configured. `releaseRule` now rejects unknown values (they silently became "highestRank")
  and is read back.
- `model_close` could report success while the model stayed open. ExtendSim's close command
  closes the front window, and when one of the model's block dialogs was in front (after an
  equation was written, for example) the dialog closed instead. The close is now checked
  against the model's name and repeated, at most three times; the result carries
  `closeAttempts` when more than one was needed, and `MODEL_CLOSE_FAILED` if the model is
  still open. With no model open, `model_close` now returns `MODEL_NOT_OPEN` instead of
  closing whatever window is in front.

## 1.22.8 — 2026-09-29

A fix release: models built with the pattern library now behave as designed, wiring stays
right across models, and the server no longer reports success for a value that did not take
or quietly discards a model's changes. Tool count unchanged at 107. Verified live on
ExtendSim 2024 and 2026.

**Upgrade first if you use** the machine patterns (`instantiate_pattern`, `compose_flow`), or
build more than one model per session.

### Fixed
- After the first model in a session, a new model could be wired wrong without any error.
  The server remembered which slots of a block's array connector (a Create's item output,
  for example) it had used, keyed by block number - and ExtendSim numbers blocks from the
  start again in every model. In the next model it skipped the real, free output, so
  the Create stayed unconnected and a run returned 0 items. The server now decides only
  from what ExtendSim reports about each connector.
- The machine patterns (`simple-machine`, `resource-machine`, `machine-with-breakdowns`)
  never set their process time: they wrote a variable the Activity block does not have.
  They now set the Activity's delay. `machine-with-breakdowns` also ticks the Activity's
  "Enable Shutdown", without which the Shutdown block never stopped the machine.
- `block_set_value` said success for a variable the block does not have, and for a value
  the block did not keep. It now reads the value back and returns `SET_VALUE_FAILED` with
  what the block holds instead.
- `attribute_set`, `attribute_get` and the `tag-items` pattern could fail with
  `ATTRIBUTE_REGISTER_FAILED` for a new attribute. Registering it duplicated a helper block,
  which goes through the Windows clipboard - while another program held the clipboard,
  nothing was registered. The server now asks the helper block to register the name
  directly, without copying anything.
- A pattern approved with `approve_pattern` never got its block settings when it was built:
  mining names settings with the server's own short names (`rankType`, `arg1`, ...), and
  `instantiate_pattern` wrote those as if they were ExtendSim variables, which the block
  ignored. `approve_pattern` now stores ExtendSim's own variable names. Settings that choose an
  attribute from a list (a Queue's sort attribute) are not written - doing so can open an
  ExtendSim error box - and are listed in the pattern's `notApplied` instead, to be set by hand.

### Changed
- `model_close` without `saveFirst` still closes the model, but now says when changes were
  lost: the result has `unsavedChanges` (true/false, or null when ExtendSim cannot tell -
  always the case for a model that has been saved) and a `warning`. `saveFirst` on a model that
  has never been saved now returns `MODEL_SAVE_FAILED` with a suggestion, instead of opening
  ExtendSim's "Save As" box, which froze the server.

### Known ExtendSim issue
- In ExtendSim 2026.1.0.39, two hierarchical blocks connected directly (for example two
  machines from `compose_flow` wired `m1.out -> m2.in`) make the run stall at time 1. This
  is an ExtendSim bug, being reported to Imagine That; 2024 runs the same model. Workaround: put a
  Queue between the two hierarchical blocks.

## 1.22.7 — 2026-09-26

A fix release. `attribute_set` and `attribute_get` (and the `tag-items` pattern) work for the first time,
`block_configure` can no longer close your model, `approve_pattern` no longer saves a
pattern that cannot be built, and ExtendSim's start-up reminders no longer keep the server
out. Tool count unchanged at 107. Verified live on ExtendSim 2024
and 2026.

**Upgrade first if you use** `attribute_set` or `tag-items` (they always failed before), or
`block_configure` on Activity and Create blocks.

### Fixed
- `attribute_set` now works. It used to write the Set block's attribute table, which ExtendSim
  overwrites (the name came back as "None"), so it always failed - and the `tag-items`
  molecule with it. It now registers a new attribute in the model when needed and sets the
  Set block's attribute and value the way ExtendSim stores them, then reads them back.
  Verified on ExtendSim 2024 and 2026: a Get block further down reads the value from the
  items in a run. Constant values in the block's first row only; names 1-15 characters,
  no spaces. The tool description no longer promises connector or distribution values.
- `attribute_get` now really points a Get block at an attribute. It wrote a setting the Get
  block no longer has and reported success without checking. It now works like
  `attribute_set` (registers a new attribute, sets it, reads it back); its description says
  what it does - configure the block, not read a value.
- Pattern mining (`extract_psg`) reads a Set block's attributes the way ExtendSim stores them,
  so attributes set through the server are seen, and it no longer reads one row past the end
  of the block's attribute table - a read that could open a dialog and freeze ExtendSim.
- `approve_pattern` no longer saves a molecule that `instantiate_pattern` cannot build. The
  `seed` must be the last block of the item flow (the one the outlet binds to); any other
  seed now fails validation instead of failing half-way through a later build. The same
  check runs before every `instantiate_pattern`.
- `block_configure` could close your model. When a popup change on an Activity or Create
  block needed its save-close-reopen fallback, the model was reopened from a wrong path, so
  it stayed closed while the tool reported success. It now reopens from the right path,
  checks that the model is back, and says so if it is not. On a model that has never been
  saved it now returns a clear error instead of opening "Save As", which froze ExtendSim.

### Fixed — start-up reminders
- ExtendSim's start-up reminders are now clicked away: ExtendSim 2024's "Maintenance & Support
  Expired ..." box was missed because its title does not say "ExtendSim", and ExtendSim 2026's
  "Subscription Renewal" box was taken for the main window. Until one of them is closed
  ExtendSim does not answer the server at all. Only boxes of ExtendSim's own process are
  touched.
- The text of these reminders is never passed on to the AI or to usage statistics - the 2026
  renewal box shows the licence's activation key.

### Verified
- Pattern mining, end to end, against a real model on ExtendSim 2026: `extract_psg`,
  `mine_candidates`, `cluster_patterns`, and `approve_pattern` followed by
  `instantiate_pattern`, which rebuilds the mined H-block with the same blocks, the same
  connections and the same fingerprint.

## 1.22.6 — 2026-09-25

A robustness and security release. One ExtendSim call that does not come back no longer
ruins the session, message boxes during long commands are found in about 10 seconds, and
HTTP mode (the Windows Service used for ChatGPT) can no longer send one client's answer to
another. Tool count unchanged at 107. Verified live on ExtendSim 2024.

**Upgrade first if you use** ChatGPT through the Windows Service (HTTP mode), or long
simulations that sometimes show a message box.

### Changed — one stuck ExtendSim call no longer ruins the session
- While ExtendSim has not finished a command, further commands are answered at once with
  `EXTENDSIM_BUSY` (what it is busy with, for how long, what to do) instead of queuing behind
  it and timing out one after another. The server resumes by itself when ExtendSim answers.
- Message boxes during long commands (simulation runs) are found within about 10 seconds
  instead of at the end of the command's time limit (up to 10 minutes).
- `extendsim_status` answers while ExtendSim is busy and reports `state`.
- `COM_TIMEOUT` now tells the AI to wait until `extendsim_status` reports `idle` before
  retrying, and to run long simulations with `waitForCompletion=false`, instead of
  retrying at once into `EXTENDSIM_BUSY`.
- Usage statistics now record which message boxes occur, with names, paths and numbers
  removed.
- The automatic OK-click now only ever touches ExtendSim's own message boxes; before, a
  fallback could also click a standard Windows message box of another program.

### Security — HTTP mode could answer the wrong client
- In HTTP mode (the Windows Service used for ChatGPT) every session was connected to one
  shared MCP server. With the MCP SDK in use until now (1.25.3) that server followed the
  newest session, so with two clients connected a response could reach the other client
  (GHSA-345p-7cg4-v4c7). Each HTTP session now gets its own server. stdio mode (Claude
  Code, Claude Desktop, Cursor, Gemini CLI) was not affected.
- MCP SDK 1.25.3 -> 1.30.1, and the SDK's own dependencies (hono, path-to-regexp,
  fast-uri, ajv, body-parser, qs) updated within their ranges: `npm audit --omit=dev`
  now reports no known vulnerabilities in what the installer ships.

## 1.22.5 — 2026-09-24

Two features and a fix. Your own modelling guides: the AI drafts a guide from a model
you built, you fill in what only you know, and it is found next to the official guides.
The server can pick up newer official guides from duke.se without an upgrade (at most
once a month; off switch below). `modl_search` no longer gives the AI wrong ModL facts.
107 tools (was 104). Verified live on ExtendSim 2024, including Bank.mox.

**Upgrading:** this is the first release that makes a network call (the monthly guide
check). Set `SIMULATIONSMCP_WEB_LOOKUP=off`, or create `policy.json` in the install
folder, to keep the server fully offline - see below.

### Added — your own modelling guides
- Three tools: `guide_draft` drafts a guide from the open model (blocks, connections, set
  parameters; the top level or one hierarchical block, at most 50 blocks) and lists what
  only a person can fill in; `guide_save` checks it against the official guide schema,
  listing every problem, and saves it; `guide_delete` removes one. 107 tools.
- Own guides live in `%APPDATA%\SimulationsMCP\guides\` (one file per guide) and appear in
  `modeling_guide`, `model_advisor` and `MCP_init` marked `"source": "local"`. A broken
  file is skipped and reported (`localGuideErrors`), never takes the official guides down;
  a key clash is shown as `<key>_local` (`localGuideRenames`). The duke.se off switch does
  not affect own guides.
- `model_extract` blocks now carry `parentBlockId` (the enclosing hierarchical block, or
  null at top level).
- Fixed: `guide_draft` was silently dropping every connection that crossed a hierarchical-
  block boundary (found live on Bank.mox, where it left 5 top-level H-blocks with 0
  connections). It now climbs from each endpoint to its ancestor at the drafted level and
  keeps the connection, rendered `<block> (via <inner block> <connector>)` when the level
  block only contains the endpoint; `needsInput` then asks for the real connector names.

### Fixed — modl_search gave the AI wrong ModL facts
- `modl_reference.json` listed **`GAGetCols`**, which ExtendSim does not have, and
  described `GAGetRows` and `GAGetType` as taking an array *index* when they take its
  *name*. An AI following `modl_search` into `execute_command` got a compile-error dialog
  that blocks COM. The reference now lists `GAGetRows` / `GAGetColumns` / `GAGetType`
  (by name) and `GAGetRowsByIndex` / `GAGetColumnsByIndex` / `GAGetTypeByIndex` (by index),
  with the five array types. `MCP_init`'s example no longer mentions `GAGetCols`.
- A full audit of `modl_reference.json` against ExtendSim's own function list, its
  shipped ModL and the 2026 binary found more:
  - **Database indices were described as 0-based.** ExtendSim's are 1-based (measured
    live) - very likely where the server's own 0/1 bug came from. `DBDatabasesGetNum`
    is now described as the highest index in use (not a count) and `DBTablesGetNum` as
    possibly counting empty slots.
  - **Field types were given as 0-3**; the real codes are the `DB_FIELDTYPE_*` values
    (4096 integer, 8192 real, 16384 string, ...).
  - **`DBTableCreate` / `DBFieldCreate` take names**, not indices; the `...ByIndex` forms
    are now listed too. `DBRecordFind` searches for a *string*, returns a 1-based record,
    and 0 or less means not found.
  - Argument forms corrected for `ConArraySendMsgToAllCons/Inputs/Outputs`,
    `FindInHierarchy2` and `Get/SetDialogItemEColor`.
  - **`ModelSettingsGet`, `ModelSettingsSet` and `RunSetup` removed** - found in none of
    ExtendSim's sources.

### Changed — the server now checks duke.se for newer guides (on by default)

Existing installations pick up this behaviour on upgrade: the default changes from no
outbound network activity to one HTTPS check per month.
Turning the check off also makes the server serve its bundled guides only; a copy
fetched earlier stays on disk but is not used.

- **What is fetched:** one file, `https://duke.se/simulationsmcp/v1/modeling_guides.json`.
  Nothing else.
- **When:** at most once every 30 days per user, checked only when `modeling_guide`,
  `model_advisor` or `MCP_init` is called — never at startup, never in the background.
  After a failed check, the next attempt waits 24 hours.
- **What is sent:** nothing identifying beyond what any HTTPS GET carries — no cookies,
  no query string, no custom headers. An `If-None-Match` ETag is sent for a copy
  already held.
- **Turning it off:** set `SIMULATIONSMCP_WEB_LOOKUP=off` (also accepts `0`/`false`), or
  create `policy.json` in the installation folder (default
  `C:\Program Files\SimulationsMCP`) with `{ "webLookup": false }` machine-wide
  (administrator rights required; any "off" wins over a user setting).
- **New in tool output:** `modeling_guide` and `MCP_init` now report `guideSource`
  (`"bundled"` or `"web"`) and `guideVersion`, and `newerGuides` when the guide file in
  use contains guides newer than this server version — they are hidden until the
  server is upgraded.
- Guide file `schemaVersion` 1; bundled content version 1.13.0.
- README, `SECURITY.md`, `docs/DESIGN_DOCUMENT.md` and `docs/USER_MANUAL.md` are
  rewritten in this same release to reflect the change (FR-N7).

## 1.22.4 — 2026-09-23

A correctness and safety release. Everything in it was found by testing the tools
against a real ExtendSim - 2024 and 2026, on empty models and on a full model (Bank.mox)
- rather than against fakes. Tool count unchanged at 104.

**Upgrade first if you use any of these:** long text (128+ characters) could crash
ExtendSim; text containing a double quote could freeze ExtendSim behind a dialog, or run
as ModL; `model_extract`, `db_create`, the AI context tools and the global-array tools
did not work live at all.

### Changed — client-visible
- **Database listings use ExtendSim's own 1-based indices.** `db_list`, `db_table_info`
  and `model_extract` no longer show a phantom `table_0` / `field_0`, and no longer drop
  the last table or field. Record numbers in tool parameters and results stay 0-based.
- **`db_add_records` without `position` appends.** It used to insert before the last
  record.
- **`model_overview`: `totalBlocks` now counts blocks.** It used to report every object
  slot (703 for a model with 50 blocks). New fields `hierarchicalBlocks`, `textBlocks`,
  and `sectionErrors` for any section that could not be read.
- **`model_extract`:** `hierarchies` is now filled (it was always empty); a connection
  fanned out to several inputs gives one connection per input; unpairable nodes appear
  in `unresolvedConnectionNodes`; `modelPath` is the full path.
- **Connector direction** (in `connection_list`, `model_extract`, block details) follows
  ExtendSim's rule: a name ending in "Out" is an output, anything else an input. Names
  such as `NumInBatchOut` change from input to output; names with neither ("D", "TR")
  change from `unknown` to `in`.
- **Field and array type names:** database fields report `string`, `real`, `integer`,
  `boolean`, `currency`, `date_time` and so on instead of `unknown(16384)`; global arrays
  can report `string15` and `string31`.
- **Errors where there used to be a false success:** `db_create` when a table or field
  is not created, `context_clear` when the database survives, `model_snapshot` when
  blocks or connections cannot be read, `context_set` for a value over 255 characters.
  Scenario Manager failures now return `MULTI_RUN_FAILED`, not `OPTIMIZER_FAILED`.

### Fixed — pattern mining missed every hierarchical block
Measured live on Bank.mox (ExtendSim 2024). `extract_psg`, the input to pattern mining,
started from `ObjectIDNext(id, 0)`, which visits ordinary blocks only, so it never saw a
top-level H-block and never descended into one: it returned **one scope of 6 blocks**
for a model with 10 H-blocks and 50 blocks. It now returns all **11 scopes** with all 50
blocks and 10 H-blocks in the right tree. Inside an H-block it also stopped treating
text blocks and anchor points as blocks - `LocalToGlobal2` returns them too (one H-block
held 12 blocks, 7 text blocks and 72 anchor points), and their names would have been
mined as block types such as "Tellers available" or "ItemOut". The shipped pattern
library was checked and contains no such entries.

### Fixed — counting blocks, finding H-blocks and text blocks
Measured live on a real model (Bank.mox, ExtendSim 2024): `NumBlocks()` counts every
object slot - 703 of them, for 50 ordinary blocks, 10 hierarchical blocks and 54 text
blocks (the rest are anchor points and empty slots). And `ObjectIDNext(id, 0)`, which
the server used everywhere, visits ordinary blocks only - never H-blocks or text.
- **`model_overview`** reported `NumBlocks()` as `totalBlocks` (703 for Bank.mox). It now
  gives `totalBlocks` (50, matching `block_list` and `model_snapshot`),
  `hierarchicalBlocks` and `textBlocks`, each counted by one ModL loop inside
  ExtendSim - still fast on a large model.
- **`text_block_add`** always reported failure: it looked for the new text block with
  `ObjectIDNext`, which never visits text blocks. (The fail-closed check added earlier in
  this release made that visible; before it, the call returned success with
  `blockId: -1`.) It now uses the block number `PlaceTextBlock` returns and checks that
  it is a text block.
- **`model_extract`** never found a hierarchy: it walked ordinary blocks looking for
  H-blocks. It now walks H-blocks (Bank.mox: 10, as `hierarchy_list` reports).
- **`hierarchy_list`** started its walk at block 0 instead of before it, so an H-block
  numbered 0 would have been skipped.
- **`block_add` and `block_duplicate`** can now find a newly placed or copied H-block.

### Fixed — connector direction follows ExtendSim's own rule
The server guessed a connector's direction by looking for "in" anywhere in its name,
before "out" - in ten places, four variants. `WaitingOut`, `LinkOut` or Bank.mox's
`NumInBatchOut` read as inputs, so connections through them were built backwards or
lost. It now uses the rule from Imagine That's own `isOutputCon`: strip any `[...]`
array suffix; a name ending in "Out" is an output, anything else an input. One function
decides, and a test fails if a private copy comes back.

### Fixed — calls to ModL functions that do not exist
Checked every ModL call the server makes against ExtendSim's own function list, its
shipped ModL code and the ExtendSim 2026 binary, then verified live on ExtendSim 2026.
Each of these raised a compile-error modal that blocks COM:
- **`model_extract`** called `GetModelPathName()`, which has never existed - so every
  `model_extract`, in every version, stopped on a modal. It now uses
  `GetModelPath(name)`.
- **`ga_list`, `ga_read`, `ga_write` and `model_extract`** called `GAGetCols`, which does
  not exist, and passed an array *index* to `GAGetRows`/`GAGetType`, which take a
  *name*. They now use `GAGetRowsByIndex`, `GAGetColumnsByIndex`, `GAGetTypeByIndex`.
  Arrays of type `GAString15`/`GAString31` (codes 4 and 5) are read and written as
  strings; they were treated as reals.
- **`optimizer_get_results`** read its two text results with `GetDialogVariableString`,
  which does not exist. It now uses `GetDialogVariable`.
A test now fails if any of these names comes back.

### Fixed — model summaries that hid failures or lost data
- **`model_overview`** never showed the AI context: it read `purpose`, `notes`, `tags`
  and `assumptions` from the wrong level of `context_get`'s answer, so a model with
  context looked like one without. A section that fails to read is now listed in
  `sectionErrors` instead of appearing empty.
- **`model_snapshot`** turned a COM failure in `block_list` or `connection_list` into a
  successful snapshot of an *empty* model. It now fails closed.
- **`model_extract`** dropped every connection from an output wired to more than one
  input, and every second line into an array input (such as a Queue's `ItemIn`). It now
  reads connectors the same way `connection_list` does, gives one connection per
  target, and lists anything it cannot pair under `unresolvedConnectionNodes`.

### Fixed — text with a quote in it could freeze ExtendSim, or run as code
Every string the server puts into a ModL command goes through one escaper, and it assumed
C rules. ModL has none of them - measured live with `StrLen`: a backslash is always an
ordinary character, and a double quote always ends the string.
- **A double quote** was written as `\"`, which ModL reads as a backslash and the end of
  the string. Any text containing a quote - JSON, a note, a label, a search string - raised
  a compile-error modal that blocks COM until someone clicks it away. Worse, text shaped
  like `x" ; Evil("` ran as ModL: the injection fix in 1.22.1 (W1-6) never actually held.
  Quotes are now spliced in as `StrPutAscii(34)`, the way ExtendSim's own blocks do it.
  The injection tests now check what matters - that nothing lands outside a string
  literal - against a small lexer built from the measured rules.
- **Line breaks and tabs** were written as `\n`/`\r`/`\t` and stored as those two
  characters. They are now real line breaks and tabs (`StrPutAscii(10/13/9)`).
- **Backslashes** were doubled, so `C:\tmp` arrived as `C:\\tmp`. They now pass through.
- **Text over 255 characters** raised "String literals cannot be larger than 255
  characters", another COM-blocking modal. Long text is now split into joined literals.
  Note that a ModL string *value* still cannot exceed 255 characters.

### Fixed — reading a string of 128+ characters crashed ExtendSim
- Found live: reading a string of **128 or more characters** back over COM crashed
  ExtendSim 2024.1 (access violation in `VCRUNTIME140.dll`); 127 was fine, and longer
  values sometimes came back empty instead. A ModL string holds up to 255, so any long
  block label, database text, dialog text or context value could take ExtendSim down.
- All 98 string reads now go through one function that copies the value out in pieces
  of at most 100 characters with `StrPart`. A short string still costs a single COM
  read. Verified live at 127, 128, 200, 254 and 255 characters, and a test pins that no
  code reads the string global directly any more.
- `context_set` refuses values over 255 characters (ExtendSim's string limit) **before
  writing anything**. It used to report success for a 397-character note that was never
  stored.

### Fixed — db_create and AI context storage never created anything
- `DBTableCreate`, `DBFieldCreate`, `DBTableDelete` and `DBDatabaseDelete` take **names**;
  the server passed **indices**, which fails silently. `db_create` created the database
  but no table - and still answered success. The AI context tables (`context_set`) could
  never be created, and `context_clear` could not delete. All now use the `...ByIndex`
  forms, and `db_create` and `context_clear` fail closed.
- **Field types** were mapped as 0=real, 1=integer, 2=string, 3=boolean. The real
  `DB_FIELDTYPE_*` values, read live, are 4096 integer, 4097 boolean, 8192 real, 16384
  string (plus currency, date/time and more). `db_table_info` and `model_extract`
  reported every field as `unknown(...)`, and `db_create` asked for types that do not
  exist. Both directions now use the real constants.
- Verified live end to end: create, add, append, insert at a position, set, get, find,
  delete, and context set/get/history/clear all round-trip correctly.

### Fixed — the database tools counted from 0; ExtendSim counts from 1
ExtendSim's database API numbers databases, tables, fields and records from **1**; 0 is
never a valid index (`DBRecordsInsert` even uses 0 to mean "append"). The server counted
from 0. What that did, all of it visible in real sessions:
- **`db_list`** showed a phantom `table_0` first and **dropped the last table**. A model
  with exactly one database listed **no databases at all**.
- **`db_table_info`** showed a phantom `field_0` and dropped the last field;
  **`db_get_records`** without `fields` did the same and returned a blank first row.
- **`db_get_value` / `db_set_value` / `db_get_records` / `db_delete_records`** were
  one record off: asking for record `n` touched the record the documented 0-based
  numbering calls `n-1`, and record `0` - the first record - did not exist at all.
- **`db_add_records`** without `position` inserted the new records *before* the old
  last one instead of appending.
- **`db_find_record`** could report a miss as a hit on record 0.
- **AI context storage (`context_set` / `context_get`)** wrote a new key over the
  previous last key, and on an empty table wrote to record 0, which does not exist.
- **Lookups** (`db_create`, `db_relations_list`, the context tools) treated an index of 0
  as "found".
- **`db_list`** listed a table slot that does not exist: `DBTablesGetNum` can count an
  empty slot (it said 7 for ExtendSim's own `_RightClickConnect`, which has 6 tables).
  Slots with no name and no records are now skipped, as unnamed database slots already
  were.

**Record numbers stay 0-based in the tool API**, as the tool schemas have always said;
the server now adds 1 on the way to ExtendSim and subtracts 1 on the way back. The
database/table/field indices in `db_list`, `db_table_info` and `model_extract` are now
ExtendSim's real ones and start at 1. `docs/USER_MANUAL.md` §6.8 documents both.

### Fixed — a wrong warning shipped in 1.22.2 and 1.22.3
- **`workstation_set_config` no longer warns that its delay indices may be off by one.**
  They are not. Reading the `DELAY_IS_*` constants out of the Workstation block's own
  source in `Item.lbr` gives CONSTANT=1, ATTRIBUTE=2, DISTRIBUTION=3, TABLE=4 — exactly
  the map the tool uses — and CONNECTOR=1000, a sentinel rather than a popup row,
  because connecting the D connector overrides the delay automatically. The warning
  came from reading the block's help text, which lists the *ways* a delay can
  be set, as if it listed the popup's *rows*. The warning was itself misinformation:
  it could have led a client to "correct" a right index into a wrong one.
- The Activity map is confirmed the same way (its source has CONNECTOR=2 as a real
  popup row). Both maps are now pinned by tests to the source constants.

### Fixed — a COM failure no longer looks like an empty model
- **`model_list`, `block_list` and `connection_list`** answered a COM failure with an
  empty list, an error code and **no `success: false`**. The TypeScript layer flags an
  error only on `success === false`, so "ExtendSim could not be read" reached the client
  as a successful "the model is empty" — the same contract hole W1-1 closed everywhere
  else in 1.22.1. A client trusting an empty `block_list` could start building a new
  model on top of one that was really there. All three now fail closed.
  `extendsim_status` keeps its behaviour on purpose: "not running" is its answer, not a
  failure of the tool.

### Added — testing
- **Unit tests can no longer reach a real ExtendSim.** A new `conftest.py` replaces
  `win32com.client.GetActiveObject` and `Dispatch` with functions that fail loudly for
  every offline test. Five places in the backend call COM directly, and `Dispatch`
  *launches* ExtendSim if none is running, so a test that forgot to patch could connect
  to — or start — the developer's live ExtendSim. One did, briefly, while this wave was
  being written (read-only, no harm). The full suite was re-run under the guard and no
  existing test had been relying on real COM.
- **G2 wave 5** — `model_list`, `model_info`, `model_close`, plus the list tools above.
  Python suite 302 → 310.

### Fixed — two more tools that reported success for something that did not happen
- **`text_block_add`** returned success with `blockId: -1` when nothing was placed. It
  now fails closed (`COMMAND_FAILED`) - and finds the block from `PlaceTextBlock`'s own
  return value; see "counting blocks" above for why the first version of this fix
  failed every time.
- **`block_info`** in live mode swallows the error from each read and substitutes an
  empty string, so a block ID that does not exist came back as success with every field
  blank. It now returns `BLOCK_NOT_FOUND` — but only when label, type *and* name are all
  empty, so an object with any identity (a text block may have a label and no block
  name) is never rejected.
- **Scenario Manager failures were reported as `OPTIMIZER_FAILED`** — three places across
  `scenario_manager_status` and `scenario_manager_get_results`, evidently copied from the
  optimizer. A client told the optimizer failed goes looking in the wrong block. They now
  return `MULTI_RUN_FAILED`, which the manual already defines as "a multi-run or scenario
  sweep failed". Clients matching `OPTIMIZER_FAILED` from these two tools need the new code.

## 1.22.3 — 2026-09-23

A friction-driven release: the first fixes chosen from telemetry rather than by hand.
Tool count unchanged at 104. **One client-visible change** — a new error code, below.

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

### Added — testing
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
