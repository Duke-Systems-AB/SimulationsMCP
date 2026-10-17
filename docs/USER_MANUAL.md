# Simulations MCP Server — User Manual

**Version:** 1.27.0
**Author:** Duke Systems AB
**Date:** 2026-10-16

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [System Requirements](#2-system-requirements)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [Getting Started](#5-getting-started)
6. [Tool Reference](#6-tool-reference)
7. [Workflows and Best Practices](#7-workflows-and-best-practices)
8. [Fire-and-Forget Operations](#8-fire-and-forget-operations)
9. [Session Logging and Telemetry](#9-session-logging-and-telemetry)
10. [Troubleshooting](#10-troubleshooting)
11. [Appendix: Error Codes](#11-appendix-error-codes)

---

## 1. Introduction

The Simulations MCP Server is a Model Context Protocol (MCP) server that enables AI assistants to build, configure, run, and analyze ExtendSim simulation models programmatically. It bridges AI clients (Claude Code, Claude Desktop, Gemini CLI, Cursor, ChatGPT) to ExtendSim's full modeling environment through 116 specialized tools.

### What is MCP?

The Model Context Protocol is an open standard that allows AI assistants to interact with external tools and data sources. This server implements MCP, exposing ExtendSim's simulation capabilities as structured tool calls that any MCP-compatible AI client can use.

### Key Capabilities

- **Model Management** — Create, open, save, close, validate, and extract simulation models
- **Block Operations** — Add, connect, configure, position, and remove simulation blocks
- **Simulation Control** — Run simulations, monitor status, collect results, perform multi-run analyses
- **Database Operations** — Full CRUD on ExtendSim's internal databases
- **Analysis** — Run the Scenario Manager and Optimizer, collect and interpret results
- **AI Assistance** — Built-in modeling guides, pattern search (268 example models), and model advisor
- **Reference** — Search ModL functions, block libraries, and dialog variables

---

## 2. System Requirements

| Component | Requirement |
|-----------|-------------|
| **Operating System** | Windows 10/11 (64-bit) |
| **Node.js** | Version 18 or higher |
| **Python** | Version 3.9+ (tested on 3.13), with the packages in `requirements.txt` |
| **ExtendSim** | Installed with COM component registered. Run **one** ExtendSim at a time (see 5.3) |
| **AI Client** | Any MCP-compatible client (see Section 4) |

### Install Python Dependencies

```bash
pip install -r src/ExtendSimMCP.TypeScript/requirements.txt
```

That installs `pywin32` (the COM bridge — nothing works without it) and `comtypes`.
`comtypes` is optional in the sense that the server still starts and still dismisses
blocking ExtendSim dialogs without it, but it loses the ability to *read* the dialog
text — which is the difference between a bare `COM_TIMEOUT` and an error that tells you
what ExtendSim complained about.

### Verify ExtendSim COM Registration

ExtendSim registers its COM component during installation. If needed, run ExtendSim once as administrator to trigger registration.

---

## 3. Installation

### Option A: Installer (Recommended)

1. Run `SimulationsMCP-Setup-1.27.0.exe` as administrator (the prebuilt installer matches the current source)
2. Choose installation directory (default: `C:\Program Files\SimulationsMCP`)
3. Select whether to install as a Windows Service (only needed for ChatGPT — see Section 4.5)
4. Complete the installation

### Option B: From Source

```bash
cd src/ExtendSimMCP.TypeScript
npm install
npm run build
```

The built server is at `dist/index.js`.

---

## 4. Configuration

### 4.1 Transport Modes

The server supports two transport modes:

| Mode | Use Case | How it Works |
|------|----------|-------------|
| **stdio** (default) | Claude Code, Claude Desktop, Gemini CLI, Cursor | AI client starts the server as a local subprocess. No port is opened for this transport; the server's only network activity is the monthly guide check described in §4.7. |
| **HTTP** | ChatGPT | Runs as a Windows Service on `localhost:3001/mcp`. Requires HTTPS reverse proxy for ChatGPT. |

Most users need **stdio only**. The server starts automatically when your AI client connects — no manual startup required.

### 4.2 Claude Code

Add to your project's `.mcp.json` or `~/.claude.json`:

```json
{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"]
    }
  }
}
```

### 4.3 Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"]
    }
  }
}
```

### 4.4 Gemini CLI

Add to `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"],
      "env": {
        "MCP_SESSION_LOG": "1"
      }
    }
  }
}
```

### 4.5 Cursor IDE

Add to `.cursor/mcp.json` (project-level) or `~/.cursor/mcp.json` (global):

```json
{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"]
    }
  }
}
```

### 4.6 ChatGPT (HTTP Mode)

ChatGPT requires a remote HTTPS endpoint:

1. Install as Windows Service during setup
2. Service runs on `http://localhost:3001/mcp`
3. Expose via reverse proxy with HTTPS (ngrok, Cloudflare Tunnel, or custom domain with SSL)
4. In ChatGPT Settings > Apps & Connectors, add your HTTPS URL

Service management:
```
net start "SimulationsMCP"
net stop "SimulationsMCP"
```

### 4.7 Guide updates from duke.se

By default, the server checks `https://duke.se/simulationsmcp/v1/modeling_guides.json`
for newer modelling guides than the ones bundled with the install. This is the server's
only network activity outside of ExtendSim COM and whichever transport you use.

**What is fetched and how often:** one JSON file, over HTTPS GET, with a 3-second
timeout, no redirects and no query string. The check runs at most once every 30 days
per user, and only when `modeling_guide`, `model_advisor` or `MCP_init` is called — never
at startup and never in the background. If a check fails, the server waits 24 hours
before trying again and keeps using the guides it already has. Nothing identifying is
sent beyond what any HTTPS GET carries (no cookies, no query string, no custom headers);
an `If-None-Match` ETag is sent for a copy already held. The downloaded file is capped
at 1 MB while streaming and validated against a strict schema (`schemaVersion` 1) that
drops unknown fields and caps every string and list before any of it is used.

**Tool output:** `modeling_guide` and `MCP_init` report `guideSource` (`"bundled"` or
`"web"`) and `guideVersion` (the version of the guide content in use). When the guide
file in use contains guides whose `since` version is higher than this server version,
those guides are hidden from the response, and `newerGuides` reports how many are
waiting — install a newer server to see them.

**Turning it off:**
- Per user or per MCP client: set the environment variable `SIMULATIONSMCP_WEB_LOOKUP`
  to `off`, `0` or `false` (case-insensitive) in the client's server configuration or as
  a system environment variable.
- Machine-wide, for administrators: create `policy.json` in the installation folder
  (default `C:\Program Files\SimulationsMCP`) with exactly:
  ```json
  { "webLookup": false }
  ```
  Creating this file requires administrator rights (it lives in the install directory),
  and it survives upgrades. Without a policy file, the lookup is **on**. A policy file
  that is present but unreadable, not valid JSON, or does not contain
  `"webLookup": true` is treated as **off** — the policy file can only turn the check
  off, never force it on over a user's own setting. If either the environment variable
  or the policy file says off, the check is off.

**Local cache:** a per-user copy of the guide file and its fetch state is kept in
`%LOCALAPPDATA%\SimulationsMCP\guides\`. It can be deleted at any time; the server falls
back to the bundled guides and fetches again on the next eligible check.

Turning the lookup off (either method above) stops all fetching **and** makes the server
serve the guides bundled with it only. A guide file fetched earlier stays in the cache
folder but is not used while the lookup is off; delete the folder if you also want it
gone from disk.

See `SECURITY.md` for the full security analysis of this check, and
`docs/DESIGN_DOCUMENT.md` §5.9 for the architectural detail.

### 4.8 Your own guides

You can turn a model you have built into a modelling guide of your own. The AI then
finds it through `modeling_guide`, `model_advisor` and `MCP_init` exactly like the
official guides, marked `"source": "local"`.

**Making one.** Open the model and ask the AI to make a guide of it. `guide_draft` reads
the blocks, connections and set parameters (the top level, or with `hierarchyBlockId`
the inside of one hierarchical block — at most 50 blocks) and lists in `needsInput` what
only you can tell: the name, when to use it, what each block is for, what to measure and
common mistakes. When that is filled in, `guide_save` checks the guide against the same
schema as the official guides and saves it. It is marked verified only when you have run
the model and confirmed the guide.

A connection that crosses into a hierarchical block is shown as `<block> (via <inner
block> <connector>)`, and `needsInput` then asks you to fill in the real connector names
in `pattern.connections` — the block being drafted only knows the inner block's own port.

**Where they live.** `%APPDATA%\SimulationsMCP\guides\`, one `<key>.json` per guide. You
can copy a file to a colleague's folder or delete it by hand; `guide_delete` does the
same. Nothing in this folder is ever sent anywhere, and turning the duke.se guide check
off (§4.7) does not affect it.

**When something is wrong.** A file that is not valid is skipped and reported in
`localGuideErrors` with the file and field; the official guides keep working. A guide
whose key is already used by an official guide is shown as `<key>_local` and reported in
`localGuideRenames` — it never replaces the official one. Every own guide also carries
`savedAs`, the key it is saved under: to update a renamed guide, save it with that key
and `overwrite: true`.

### 4.9 Your own block guides

`block_profile` asks ExtendSim about a block: **ExtendSim must be running**. It places the
block in a temporary model of its own, reads its dialog items through COM, and closes the
temporary model when done — nothing on the block or your open model is changed. `library` is
a file name (e.g. `Item.lbr`) that ExtendSim can find, or the full path of a library outside
ExtendSim's own Libraries folder (e.g. `C:\Models\Libraries\Mine.lbr`). Given a path,
`block_profile` first tries to place the block; if ExtendSim does not have the library open, it
asks ExtendSim to open it and tries again, and the response says `libraryOpened: true`. The
library stays open in ExtendSim afterwards, as if you had opened it yourself. Without `block`: an inventory of the standard libraries' block names (not a
live read — only the libraries ExtendSim ships with). With `block`: its settings, popup
options, labels, and a summary of its help, taken from ExtendSim's own installed `.chm` help
file for that block (none for a block with no help file, e.g. most third-party blocks).

Some things cannot be known this way, because they live in the block's own compiled code, not
its dialog items: what a setting resets, the order settings must be written in, and whether a
setting's handler opens a dialog box. The profile lists these under `unknownWithoutCode`
rather than guessing. Write settings one at a time and read each back (`block_set_value`
reports what it wrote) — a dialog box may appear that `block_profile` could not predict.

**Making one.** `guide_draft` with `kind: "block"` (`library`, `block`) drafts a block guide
from `block_profile` — nothing in it is marked as proved, because nothing has been run.
Rewrite `useWhen`, `notFor`, and each setting's `meaning` in your own words with the AI, then
`guide_save` with `kind: "block"` saves it.

**Where they live.** `%APPDATA%\SimulationsMCP\guides\blocks\`, one file per block, named
`<library>__<block>.json` (library and block slugged to lower case). `guide_delete` with
`kind: "block"` (`library`, `block`) removes one.

**Cannot claim proof.** A local block guide's `provedOn` and `recipes` must be empty — only a
live run proves a block guide, and `guide_save` rejects a guide that claims otherwise.

**Search.** `block_search` merges your block guides in: a block with only a local guide gets
`guideSource: "local"` and is listed first among matches; a block with both a local guide and
an official one always shows the official guide, with `localGuideIgnored: true` so the
shadowed one isn't silently lost. A block guide file that fails to load (bad JSON, fails the
schema, claims proof, or whose `library`/`block` don't match its file name) is reported in
`localBlockGuideErrors`, never shown as a guide.

### 4.10 Advanced resources (Resource Manager)

`resources_configure` sets up Advanced Resource Management in one call: resource **classes**
(a name, a pool, how many, and optional custom properties such as `Skill`, plus a shift by
name) and resource **requirements** that pick resources by those properties — a quantity with
`where` filters that must all hold (AND), or `anyOf` naming other requirements in the same call
so any one of them will do (OR). Queue blocks are pointed at a requirement; Resource Pool
Release blocks give the resources back.

A shift named on a class must be the `shiftName` of a Shift block that already exists in the
model (`block_configure` on a Shift block) — `resources_configure` does not create shifts
itself.

**Each call replaces the whole configuration.** `resources_configure` rewrites ExtendSim's
Resource Classes and Resource Requirement Expressions tables from what you send, not a diff —
always send every class and requirement you want to keep, not just the one you are changing.
ExtendSim then regenerates all resources and requirements from those tables; nothing from the
previous call is kept. Two details: when a pool from the previous call is no longer used,
ExtendSim asks whether to delete it and the server answers No, so the empty pool stays listed
(it has no resources and is never allocated); and the numbers after a resource's name are not
restarted on a regeneration (`Junior_2` can be the first Junior) — use the names in the result.

The server generates the resources and requirements in ExtendSim and then verifies them: every
filter, every queue and every release is read back, and the call only reports success when all
of it matches what was asked for. A block guide for the Resource Manager ships with the server
(`block_search "Resource Manager" detail: true`) with a proved recipe and pitfalls.

**Activity capacity pitfall.** An Activity block's `maxItems` defaults to 1. With a pool of
several resources but `maxItems` still 1, only one resource is ever busy at a time, however
many the pool has — set `maxItems` (via `block_configure`) to the number of resources that
should be able to work in parallel.

### 4.11 Setting up analysis blocks (Scenario Manager, Optimizer, Analysis Manager)

Three tools set up the Analysis library's blocks in one call each, the same way a person would
in ExtendSim's own dialog — a Shift-click to add a factor/response/variable/output, which pops
up a "User Parameter" box asking for a name. The server answers that box itself, then reads
every row it wrote back, so a call only reports success once ExtendSim has genuinely taken it.

- **`scenario_manager_configure`** — factors (inputs to vary) and responses (results to
  record), plus the scenarios: `"fullFactorial"` (computed from each factor's min/max/step) or
  a list of named scenarios with one value per factor. Run with `scenario_manager_run` and
  `waitForCompletion: true`, then `scenario_manager_get_results`.
- **`optimizer_configure`** — variables the search may change (with min/max), outputs the
  objective may use, and the objective itself, e.g. `"MaxProfit = Exited*10 - Cap*200;"` —
  only the call's own variable/output names, numbers, and `+ - * /` are accepted. The server
  also presses one of ExtendSim's own Quicker/Better default search-strategy buttons. Run with
  `optimizer_run` and `waitForCompletion: true`, then `optimizer_get_results`.
- **`analysis_manager_configure`** — responses whose value is recorded for every replication.
  Run the model with `simulation_setup_set` (`numberOfRuns` = the replications wanted) and then
  one `simulation_run`; ExtendSim's own "Number of runs" batch runs all of them in that single
  call. Read them back with `analysis_manager_get_results`.

**V1 limits.** None of the three configure tools can change a block that already has factors,
responses, variables or outputs — build a new Scenario Manager, Optimizer or Analysis Manager
for a different spec rather than trying to edit one already set up. Each call takes at most 20
items — factors and responses together, variables and outputs together, or responses — because
every factor, response, variable and output costs ExtendSim a "User Parameter" prompt of about
4 seconds and a configure call has 3 minutes in total. A list of named scenarios is capped at 50
(every cell is written and read back); a full factorial is capped at 1000 scenarios. A call over
a limit is refused with `INVALID_PARAMETER` before anything is changed. The Analysis Manager's
responses table belongs to the whole model, so a model that already holds responses is refused
before any block is placed. The objective given to `optimizer_configure` may only reference that same
call's own variable and output names.

**Pitfalls carried over from Advanced Resources.** The same two rules apply here: every
Activity needs a Queue in front of it (`Create → Queue → Activity → Exit`), and an Activity's
`maxItems` defaults to 1. `scenario_manager_run`/`optimizer_run` are fire-and-forget by default
(see §8) — always call them with `waitForCompletion: true` when setting up or reading results,
otherwise nothing about the run is actually checked. Re-running a Scenario Manager with
`waitForCompletion: true` reruns every scenario from scratch and clears the previous results of
the selected scenarios first.

A run ExtendSim itself aborts (an out-of-range reference, a missing resource, …) comes back as
an error with ExtendSim's own message box text in `dialogs`; a box whose text could not be read
is reported as "ExtendSim showed a message box whose text could not be read" rather than left
silent.

### 4.12 Data files of a model (Excel)

Models read and write external files — most often Excel — through ExtendSim's **Data Import
Export** block (Value.lbr). Each block stores one file path; when a model is copied to another
folder or machine, its blocks still point at the old paths, and a run then stops on ExtendSim's
own box ("...does not exist or is not in the current folder. Please locate the input file").

- **`model_files_list`** — read-only. Lists every Data Import Export block in the model
  (including ones inside hierarchical blocks) with its direction (import/export), file type,
  stored and resolved path, whether the file exists on disk, sheet, database/table, and whether
  it imports or exports automatically. File existence is checked on disk in Python, never by
  asking ExtendSim to open the file. A bare file name (no folder, e.g. `Arrivals.xlsx`) is
  resolved against the model's own folder and marked `"location": "model folder"` — ExtendSim
  finds such a file next to the model, and ExtendSim 2024 itself rewrites a path into the model
  folder to a bare name after an import or export. A relative path with a folder
  (`sub/Arrivals.xlsx`) gets a warning — ExtendSim does not find it, so the run guard refuses it;
  repoint it to an absolute path. A model that has never been saved has no folder, so a bare
  name there cannot be found either.
- **`model_files_repoint`** — points one or more blocks at other files, either `changes:
  [{blockId, path}]` for named blocks or `fromFolder` + `toFolder` to move every block whose file
  is under one folder to the same place under another. Every new path must be absolute, at most
  255 characters, and contain no `"`; an import file must exist, an export file's folder must
  exist. Everything is checked before anything is written, and every new path is read back to
  confirm ExtendSim kept it exactly — a path that does not read back returns
  `MODEL_FILES_REPOINT_FAILED` instead of a cheerful success. The tool does not save the model;
  save the new paths with `model_save`.
- **`model_files_configure`** — sets up Data Import Export blocks for Excel or text import/export.
  Each entry in `blocks` (at most 20) needs exactly one of `blockId` (an existing top-level Data
  Import Export block) or `place: {x, y}` (places a new one from `Value.lbr`); `direction`
  (`import`/`export`); `fileType` (`excel`/`text`); `filePath` (absolute, at most 255 characters,
  no `"`; the import file must exist, an export file's folder must exist); `database` and `table`
  (names, resolved to indices and checked to exist — the database/table popups themselves are
  never opened, since they block COM with a modal "Database Selector"); `sheet` (required,
  Excel only); `auto` (import at run start / export at run end, default `true`); and optional
  `verifyImport` (import entries only — presses Import now with the dialog watcher running and
  reports the table's record count; the workbook it opened is closed afterwards). Everything is
  validated before anything is written — `INVALID_PARAMETER`, nothing changed — and a block
  inside a hierarchical block is refused. A value that does not read back is
  `MODEL_FILES_CONFIGURE_FAILED`, with truthful `steps` saying what was written. The intended
  database/table of every configured block is stored in the model's AI context (merged, never
  replacing other context) so `model_files_list` and the run guard can detect a later silent table
  retarget (see below). The tool does not save the model; save the new setup with `model_save`. A
  text export keeps the block's own delimiter (tab by default); the decimal separator for numbers
  follows the Windows regional settings, not the tool call — measured as a comma on this machine.
- **The run guard.** `simulation_run`, `simulation_run_multi`, `simulation_run_scenarios`,
  `optimizer_run` and `scenario_manager_run` all refuse to start with:
  - `DATA_FILE_MISSING` — a file the model would auto-import at run start is missing, has no path
    set, or has a relative path with a folder (even when the file sits under the model's folder,
    ExtendSim does not find it; a bare file name next to the saved model is fine). `missingFiles`
    names the block, the stored path and the `reason`; the suggestion is to repoint it with
    `model_files_repoint` or restore the file.
  - `DATA_FILE_TARGET_CHANGED` — a block configured with `model_files_configure` now points at a
    different table than the one it was set up for (the block keys on the table's INDEX, which a
    deleted-and-recreated table re-uses). `targetChanged` names the block, the intended
    database/table and the one it now resolves to; the suggestion is to run
    `model_files_configure` again.
  - `DATA_FILE_EXCEL_CLOSE_CONFLICT` — an auto Excel import has "close worksheet after use" turned
    on while the model also has an Excel export; that combination hangs ExtendSim on Excel's own
    save prompt at run start. `conflicts` names the blocks; the suggestion is to turn the setting
    off in ExtendSim or remove the Excel export.

  In every case nothing starts, so ExtendSim's own box never appears and the block's path is never
  emptied. If a file disappears mid-run and ExtendSim's box appears anyway, it is still answered
  automatically, and the run's response carries a readable `dataFileNotes` entry — the block has
  emptied its own path by then, so do not save the model before repointing it.
- **Saving Excel exports.** After a run finishes, the run tools (`simulation_run`,
  `simulation_run_multi`, `simulation_run_scenarios`, `optimizer_run`, `scenario_manager_run`) save
  any Excel export workbook the model points at to disk themselves — ExtendSim itself never does,
  on either version — and close workbooks the run opened that have no unsaved changes. The
  response carries `savedExports` and `closedWorkbooks`; pass `saveExports: false` to skip this.
  An export target workbook that was already open in Excel before the run is saved too —
  including any unsaved edits you made in it yourself — but it stays open; only the model's
  own workbooks that the run itself opened are closed. Excel is never started by this step,
  and it runs in a worker process with a 20 s deadline per call (one call before the run, one
  after), so a stuck Excel cannot hang the server; at worst a run returns up to about 40 s
  later. If the worker times out, the warning says so — Excel may then be showing a box of
  its own (a read-only file, a compatibility check), so check Excel.

**Needs Excel installed.** For the Excel file type, ExtendSim starts Excel in the background and
keeps a workbook it has used open and locked until something closes it — this is ExtendSim's own
behaviour, not something these tools control. See the block guide
(`block_search "Data Import Export" detail: true`) for what is and is not proved.

---

## 5. Getting Started

### 5.1 Before Your First Session

1. **Start ExtendSim** — The application must be running before the MCP server can connect. The server communicates with ExtendSim via COM; it does not launch ExtendSim automatically.
2. **Connect your AI client** — Open your MCP-compatible AI tool and ensure it lists the Simulations MCP Server.

### 5.2 First Session Workflow

Every session should begin with `MCP_init`. This returns:
- Critical usage rules (connection direction, queue requirements, etc.)
- Recommended workflow steps
- License information and available libraries
- Fire-and-forget operation instructions

**Recommended first-session flow:**

```
1. MCP_init                           → Get rules and guidance
2. modeling_guide("queuing system")   → Get step-by-step instructions
3. pattern_search("bank teller")      → Find similar example models
4. model_new()                        → Create a new model
5. block_add / block_connect / ...    → Build the model
6. block_configure(...)               → Configure block parameters
7. model_advisor()                    → Check for warnings
8. simulation_run()                   → Run the simulation
9. simulation_get_results()           → Collect results
10. model_save(filePath="...")        → Save your work
```

### 5.3 Critical Rules

These rules prevent common errors that can crash ExtendSim or produce invalid models:

1. **Never invent ModL functions** — Always use `modl_search` to verify a function exists before using it in `execute_command`. Many function names you might guess do not exist.

2. **Connection direction** — Connections always flow from OUT-connector to IN-connector:
   ```
   block_connect(sourceBlock, "ItemOut", targetBlock, "ItemIn")
   ```

3. **Queue blocks are mandatory** — There must be at least one Queue between Create→Activity and between Activity→Activity:
   ```
   Create → Queue → Activity → Queue → Activity → Exit   ✓
   Create → Activity                                       ✗
   ```

4. **Sequential calls only** — ExtendSim's COM interface cannot handle concurrent calls. Always wait for one tool call to complete before issuing the next.

5. **Use `block_configure` for block settings** — Do not try to set Activity delay or other block parameters via `block_set_value` directly. Use `block_configure` which auto-detects the block type and applies the correct internal API.

6. **One ExtendSim at a time** — Do not keep two ExtendSim instances open, for example 2024 and 2026 side by side. The server cannot tell which one ExtendSim's prompts and messages appear in, so while two run it refuses every command that needs ExtendSim with `MULTIPLE_EXTENDSIM_INSTANCES` and lists them; close all but one. `extendsim_status` still answers and shows the same warning.

7. **Save frequently** — ExtendSim may crash on invalid commands. Save your model often with `model_save`.

---

## 6. Tool Reference

The server provides 116 tools organized into categories. Each tool accepts structured parameters (validated with JSON Schema) and returns structured JSON responses.

### 6.1 Model Management

| Tool | Description |
|------|-------------|
| `model_new` | Create a new empty model. Refused with `INVALID_PARAMETER` if `savePath` names a model that is already open — close it first |
| `model_open` | Open an existing model file (.mox). If that same file is already the active model, it says so (`alreadyOpen`); if a model with the same file name is open from another folder, it refuses with `MODEL_OPEN_FAILED` — ExtendSim cannot hold two models with the same name, so close that one first. If a model with that name is open but not the active one, it also refuses with `MODEL_OPEN_FAILED` (`alreadyOpen`, `activeModel`) instead of switching to it — bring it to the front in ExtendSim yourself, or close the active model first. If ExtendSim does not open the file (a path that does not exist, say), it refuses with `MODEL_OPEN_FAILED` and names the model that is still active |
| `model_save` | Save the current model (optional: save-as with new path; a never-saved model needs one). The save is checked - `MODEL_SAVE_FAILED` if it did not take |
| `model_close` | Close the active model. Never saves or discards changes on its own — a model with unsaved changes is refused with `MODEL_HAS_UNSAVED_CHANGES` and stays open, unchanged, unless you pass `saveFirst` (save, then close), `discard` (close without saving), or — for a model that has never been saved — `filePath` (save it there, then close). See "Closing models safely" below. The close is checked: if a dialog of the model was in front it is repeated (`closeAttempts`) |
| `model_list` | List every open model (name, which one is active, the active model's folder); falls back to the active model alone, with a warning, if the window list cannot be read (the warning says so when an earlier window read is still running; after 60 s such a read is given up on and a new one is made) |
| `model_info` | Get model metadata: name, end and current time, simulation phase, optionally run statistics (`includeStatistics`), and `unsavedChanges` — `true`/`false` for a model that has never been saved, `"unknown"` for a saved one (with `unsavedChangesNote`): ExtendSim cannot report a saved model's unsaved changes without closing it, so `model_info` never tries; `model_close` checks for real and refuses with `MODEL_HAS_UNSAVED_CHANGES` |
| `model_files_list` | List every Data Import Export block (also inside hierarchical blocks): its file, whether the file exists on disk, sheet, database/table, and whether it imports/exports automatically. Read-only - use after copying a model or on another machine, before running |
| `model_files_repoint` | Point Data Import Export blocks at other files, either by block or by moving everything under one folder to another. Validated before anything changes (absolute path, file/folder exists) and every new path is read back; the model is not saved - use `model_save`. See §4.12 |
| `model_files_configure` | Set up Data Import Export blocks for Excel or text import/export (direction, file type, path, sheet, database/table, auto, optional verifyImport), verified by read-back; the model is not saved - use `model_save`. See §4.12 |
| `model_validate` | Check model for structural issues (unconnected blocks, missing queues) |
| `model_snapshot` | Capture the full model state as a JSON summary |
| `model_extract` | Deep extraction of model structure, connections, and configurations |
| `model_overview` | High-level summary optimized for large models (24k+ blocks) |

**Closing models safely.** `model_close` never saves or discards a change you did not ask it to.
Called with no choice at all on a model that has unsaved changes, it returns
`MODEL_HAS_UNSAVED_CHANGES` and leaves the model open and exactly as it was — nothing is lost and
nothing is saved behind your back. Tell it what to do: `saveFirst: true` saves then closes;
`discard: true` closes without saving (for a model that already has a file, ExtendSim cannot say
afterwards whether it actually had changes, so the response marks `discardedChanges: "unknown"`);
and for a model that has never been saved, `filePath` saves it there first, then closes. `filePath`
must be a new file: a file that already exists is refused with `INVALID_PARAMETER` (nothing is
overwritten). If a save does not take, the model is left open and the answer is `MODEL_SAVE_FAILED`.
Passing both `saveFirst` and `discard` is `INVALID_PARAMETER`. Measured: about 3-5 s when
`model_close` has to check a saved model for unsaved changes or confirm that a save took
(`saveFirst`, `filePath`), under 2 s otherwise.

**Counting.** `model_overview` reports `totalBlocks` (ordinary blocks, the same number
`block_list` returns), `hierarchicalBlocks` and `textBlocks` separately. ExtendSim's
own `NumBlocks()` counts every object slot - anchor points and empty slots included -
and is not a block count. A section that cannot be read is listed in `sectionErrors`
rather than shown as empty. `model_extract` lists connections it cannot pair (for
example a line into a hierarchical block) under `unresolvedConnectionNodes`.

### 6.2 Block Operations

| Tool | Description |
|------|-------------|
| `block_add` | Add a single block from a library |
| `block_add_batch` | Add multiple blocks in one call |
| `block_connect` | Connect two blocks (source OUT → target IN) |
| `block_disconnect` | Remove a connection between two blocks |
| `connect_chain` | Connect a sequence of blocks in order |
| `connect_graph` | Connect blocks using a graph specification (adjacency list) |
| `block_remove` | Remove a block from the model |
| `block_list` | List all blocks in the model |
| `connection_list` | List all connections between blocks |
| `block_info` | Get detailed information about a specific block |
| `block_discover` | Discover a block's connectors, variables, and capabilities |
| `block_discover_variables` | List all dialog variables for a block |
| `block_introspect` | Unified introspection: dialog items via COM. Read-only. `stat_variables` is always empty with a `stat_warning` — ExtendSim does not expose a block's internal static storage variables (e.g. `EQ_EquationText`) through COM |

### 6.3 Block Layout

| Tool | Description |
|------|-------------|
| `block_move` | Move a block to a specific position |
| `block_get_position` | Get a block's current position |
| `block_align` | Align multiple blocks (horizontal, vertical, or grid) |
| `block_duplicate` | Duplicate a block with its configuration |
| `block_find` | Find blocks by name or library |

### 6.4 Values and Configuration

| Tool | Description |
|------|-------------|
| `block_set_value` | Set a dialog variable value on a block. The value is read back: a variable the block does not have, or a value the block did not keep, returns `SET_VALUE_FAILED` with what the block holds |
| `block_get_value` | Get a dialog variable value from a block |
| `execute_command` | Execute a raw ModL command string (advanced) |
| `block_configure` | Auto-detecting block configurator — handles Activity, Queue, Create, Exit, Select Item In/Out, Gate, Resource Item, Batch/Unbatch, Equation, Queue Matching, Queue Equation, Tank, Valve, and more. Single tool replaces 33 individual config tools. |
| `attribute_set` | Set a value attribute on a Set block (registers a new attribute in the model first; constant values, first row) |
| `attribute_get` | Point a Get block at a value attribute (registers a new attribute in the model first; first row) |
| `table_get` | Read a string-table cell (`*_ttbl` dialog tables such as `IVars_ttbl`/`OVars_ttbl`). Use this where `block_get_value` cannot — it is numeric and returns `ERR` on string cells |
| `table_set` | Write a string-table cell. Read-back verified: fails closed with `TABLE_WRITE_REJECTED` if the cell does not hold the written value (block-controlled cells reject writes silently) |
| `detect_attributes` | Detect which item attributes a block reads and writes (equation blocks: from their in/out variable tables). Returns `{ reads, writes, confidence }` |
| `resources_configure` | Set up Advanced Resource Management (the Resource Manager block) in one call: resource classes, resource requirements, and the Queue/Resource Pool Release blocks that use them. Replaces the whole configuration each call and verifies everything it generated. See 4.10 |

### 6.5 Simulation Control

| Tool | Description |
|------|-------------|
| `simulation_run` | Run the simulation (fire-and-forget by default). A blocking run reports `status: "completed"` only when it reached its end time; a run that stopped earlier says `"stopped early"` with `stoppedAt`, and one that ExtendSim aborted before time passed (or with an error dialog) is `SIMULATION_RUN_FAILED`. Dialogs dismissed during any command are listed in `dialogsDuringCommand` |
| `simulation_stop` | Stop a running simulation |
| `simulation_pause` | Pause a running simulation |
| `simulation_resume` | Resume a paused simulation |
| `simulation_status` | Check if a simulation is running, paused, or complete |
| `simulation_get_results` | Collect simulation results (throughput, utilization, queue stats) |
| `simulation_setup_get` | Get simulation parameters (end time, time units, etc.) |
| `simulation_setup_set` | Set simulation parameters |
| `simulation_step` | Advance the simulation by one step |
| `simulation_get_state` | Get the current simulation state (time, phase, events) |

### 6.6 Statistics

| Tool | Description |
|------|-------------|
| `block_get_stats` | Get statistics from a specific block |
| `simulation_get_block_stats` | Get statistics from all blocks (filterable) |
| `resource_pool_get_stats` | Get resource pool utilization statistics |

### 6.7 Multi-Run Analysis

| Tool | Description |
|------|-------------|
| `simulation_run_multi` | Run the simulation multiple times with different seeds |
| `simulation_run_scenarios` | Run predefined scenarios |
| `scenario_manager_configure` | Set up a Scenario Manager in one call: factors, responses, and the scenarios (full factorial or a named list). See 4.11 |
| `scenario_manager_run` | Run the Scenario Manager; with `waitForCompletion: true` the outcome is checked (every selected scenario needs every response) |
| `scenario_manager_status` | Check Scenario Manager progress |
| `scenario_manager_get_results` | Collect Scenario Manager results |
| `optimizer_configure` | Set up an Optimizer in one call: variables (with min/max), outputs, and the objective equation. See 4.11 |
| `optimizer_run` | Run the Optimizer; with `waitForCompletion: true` the outcome is checked (at least one generation) |
| `optimizer_get_results` | Collect Optimizer results |
| `analysis_manager_configure` | Set up an Analysis Manager: responses recorded for every replication of a run. See 4.11 |
| `analysis_manager_get_results` | Read the Analysis Manager's results: each replication of an experiment with its response values and whether ExtendSim aborted it |

### 6.8 Database Operations

| Tool | Description |
|------|-------------|
| `db_list` | List all databases in the model |
| `db_table_info` | Get table structure (fields, record count) |
| `db_get_value` | Get a single cell value |
| `db_set_value` | Set a single cell value |
| `db_get_records` | Get multiple records (note: endRecord is exclusive) |
| `db_add_records` | Add new records to a table |
| `db_delete_records` | Delete records (note: endRecord is inclusive) |
| `db_create` | Create a new database table |
| `db_import` | Import data into a table from CSV/text |
| `db_export` | Export a table to CSV/text |
| `db_find_record` | Find a record by field value |
| `db_sort` | Sort records in a table |
| `db_relations_list` | List database table relationships |
| `db_relation_create` | Create a relationship between tables |

**Record numbers are 0-based** in every database tool: the first record is `0`, in
parameters (`record`, `startRecord`, `endRecord`, `position`) and in results
(`db_find_record`'s `record`). The server translates to ExtendSim's own numbering, which
starts at 1. The database, table and field *indices* that `db_list`, `db_table_info` and
`model_extract` report are ExtendSim's real ones and so start at 1. No tool takes them
as input; they are there for raw ModL through `execute_command`, where the real index is
the one that works.

### 6.9 Global Arrays

| Tool | Description |
|------|-------------|
| `ga_list` | List all global arrays |
| `ga_create` | Create a new global array |
| `ga_read` | Read values from a global array. A range that runs past the end is clamped to the array's real size and flagged `clamped`; a start cell outside the array is refused |
| `ga_write` | Write values to a global array. A cell outside the array is refused rather than written |

### 6.10 Hierarchy

| Tool | Description |
|------|-------------|
| `hierarchy_list` | List all hierarchy blocks |
| `hierarchy_get_contents` | Get the blocks inside a hierarchy block |

### 6.11 AI Assistance Tools

| Tool | Description |
|------|-------------|
| `MCP_init` | Session initialization — returns rules, workflow, and license info |
| `modeling_guide` | Step-by-step guidance for 12 common scenarios (queuing, manufacturing, logistics, flow, resources, continuous) |
| `pattern_search` | Search 268 verified example models by keyword or domain |
| `model_advisor` | Analyze current model: returns warnings, suggestions, and completions |
| `simulation_type_guide` | Choose the right simulation type for your system |
| `guide_draft` | Draft a guide of your own from the open model (nothing is saved); with `kind: "block"` (`library`, `block`) drafts a block guide from `block_profile` instead |
| `guide_save` | Check and save a guide of your own to your personal guide folder; with `kind: "block"` saves a block guide drafted by `guide_draft` |
| `guide_delete` | Delete one of your own guides; with `kind: "block"` (`library`, `block`) deletes a block guide instead |

### 6.12 Reference Tools

| Tool | Description |
|------|-------------|
| `modl_search` | Search ModL function reference (syntax, arguments, return types) |
| `block_search` | Search block library (connectors, patterns, descriptions). Results for a block with a guide carry `hasGuide: true`; `detail: true` returns that guide - what the block is for, proved recipes, what is not proved, pitfalls |
| `block_profile` | Ask ExtendSim about a block through COM (ExtendSim must be running), read-only. Params: `library` (file name ExtendSim can find, e.g. `Item.lbr`), `block` (optional - omit to list the standard libraries' block names), `detail` (optional). With `block`: its settings and popup options, labels, and a summary of its help from the installed `.chm` file |
| `dialog_search` | Search block dialog variables (name, type, dialogId) |
| `template_list` | List available block templates (26 templates) |
| `block_template` | Get a pre-configured block template |

### 6.13 Annotations

| Tool | Description |
|------|-------------|
| `text_block_add` | Add a text annotation to the model |

### 6.14 Time and Date

| Tool | Description |
|------|-------------|
| `time_convert` | Convert between simulation time units |

### 6.15 Context

| Tool | Description |
|------|-------------|
| `context_set` | Store model context (purpose, assumptions, block roles) |
| `context_get` | Retrieve stored model context |
| `context_clear` | Clear stored model context |

Each context value can be at most **255 characters** - ExtendSim's string limit. A
longer value is refused with `INVALID_PARAMETER` and nothing is written. The same
limit applies to any text stored in an ExtendSim database string field.

### 6.16 Status

| Tool | Description |
|------|-------------|
| `extendsim_status` | Check if ExtendSim is running and connected |
| `extendsim_start` | Attempt to start ExtendSim |
| `extendsim_get_license` | Get ExtendSim license and library information |

### 6.17 Telemetry

| Tool | Description |
|------|-------------|
| `telemetry_control` | Check local telemetry status (event count, error count, file size) |

### 6.18 Patterns and Mining

Two halves of one library. The **use** half builds models from curated patterns; the
**learn** half distils new patterns out of models you already have. A *molecule* is a
reusable sub-model that becomes one H-block; a *flow* is several molecules wired together.

Using patterns:

| Tool | Description |
|------|-------------|
| `list_patterns` | List available molecule and flow patterns. Optional intent substring filter. Returns id, kind, intent, params, interface |
| `get_pattern` | Get the full definition of a molecule or flow pattern by id |
| `instantiate_pattern` | Build a molecule as an H-block in the open model (e.g. `machine-with-breakdowns` with `process_time`/`mtbf`/`mttr`) |
| `compose_flow` | Build a whole process flow from molecule instances plus wiring (`m1.out` → `m2.in`) |

Mining patterns out of existing models — run in this order:

| Tool | Description |
|------|-------------|
| `extract_psg` | Model → Pattern Structure Graph: multi-scale nodes (`lib:blocktype` + params) and edges (`srcPort`→`dstPort`), with boundary-crossing edges marked per H-block. Reads the open model, or opens `filePath` read-only. `savePath` writes JSON |
| `mine_candidates` | One candidate subgraph per H-block scope, each with a stable Weisfeiler–Lehman fingerprint (topology-only, parameter-independent), kind, and boundary edges |
| `cluster_patterns` | Group candidates by exact WL fingerprint, merge near-misses via graph edit distance (flagged for review), and infer each cluster's parameter schema (fixed/required + median/range), interface and template |
| `approve_pattern` | Turn a clustered candidate plus a naming into a validated library entry and write `patterns/molecules/<id>.json`. `dryRun` previews without writing. Settings that choose an attribute from a list (a Queue's sort attribute) are not stored but listed in the entry's `notApplied`, to be set by hand. **Fail-closed — nothing enters the library without deliberate approval** |

Each mining step can run offline from the previous step's saved JSON (`psgPath`,
`candidatesPaths`), so you can mine without ExtendSim open once the PSG is extracted.

> **Scope note:** mining treats a pure/library H-block as the molecule boundary.
> Flat models with no H-blocks are a deliberate non-goal — the miner never guesses
> boundaries from loose blocks.

---

## 7. Workflows and Best Practices

### 7.1 Building a New Model

```
1. MCP_init
2. modeling_guide("<your scenario>")     → Get the right approach
3. pattern_search("<keywords>")          → Find example models
4. model_new()
5. block_add(library, blockType)         → Add blocks
6. connect_chain([blockId1, blockId2, ...])  → Connect in sequence
7. block_configure(blockId, config)      → Configure each block
8. model_advisor()                       → Check for issues
9. model_save(filePath)
```

### 7.2 Analyzing an Existing Model

```
1. MCP_init
2. model_open(filePath)
3. model_overview()                      → Quick summary
4. model_info()                          → Detailed metadata
5. block_list()                          → All blocks
6. connection_list()                     → All connections
7. model_advisor()                       → Warnings and suggestions
8. model_extract()                       → Deep extraction (JSON)
```

### 7.3 Running Experiments

```
1. simulation_setup_set(endTime=1000)    → Set run parameters
2. simulation_run()                      → Start (non-blocking)
3. simulation_status()                   → Poll until complete
4. simulation_get_results()              → Collect results
5. simulation_get_block_stats()          → Per-block statistics
```

### 7.4 Scenario Manager Workflow

```
1. scenario_manager_run(waitForCompletion: true)  → Run and check every scenario has its results
2. scenario_manager_get_results()                 → Collect all scenario results
```

### 7.5 Using block_configure

`block_configure` is the single most important tool for setting up blocks. It auto-detects the block type and applies the correct internal API. Examples:

**Activity with fixed delay:**
```json
{ "blockId": 5, "config": { "delayType": "fixed", "value": 10 } }
```

**Activity with distribution:**
```json
{ "blockId": 5, "config": { "delayType": "distribution", "distribution": "exponential", "arg1": 5.0 } }
```

**Queue with priority ordering:**
```json
{ "blockId": 3, "config": { "sortRule": "priority", "maxContents": 100 } }
```

**Create with interarrival time:**
```json
{ "blockId": 2, "config": { "createType": "interarrival", "interarrivalTime": 5.0 } }
```

**Queue Matching** — release a set only when every input has an item with the same key value:
```json
{ "blockId": 7, "config": { "matchAttribute": "orderId" } }
```
Params: `numQueues` (number of internal queues; each item input is one queue), `groupType`
(`variable` — only value supported: a group per attribute value), `matchAttribute` (numeric item
attribute to match on; sets variable groups first), `releaseOptions` (`matchedOnly` | `allInGroup`).
Items leave on their own queue's output. Only `matchAttribute` is exercised by a proved recipe
today; `numQueues` and `releaseOptions` are write/read-back only. Full guide: `block_search`
query `"Queue Matching"` with `detail: true`.

**Queue Equation** — rank waiting items with your own equation:
```json
{ "blockId": 8, "config": { "inputAttribute": "dueDate", "equation": "iRank_0 = dueDate;", "releaseRule": "lowestRank" } }
```
Params: `equation` (ModL equation using the block's variable names, e.g. `iRank_0 = dueDate;`),
`releaseRule` (`highestRank` | `lowestRank` | `firstTrue` | `allTrue`), `inputAttribute` (turns the
block's single input row into this item attribute, name used in the equation). Only one input row
can be set from COM. The output-name cells are filled automatically. Full guide: `block_search`
query `"Queue Equation"` with `detail: true`.

---

## 8. Fire-and-Forget Operations

Three long-running tools default to non-blocking (fire-and-forget) mode. `simulation_run` starts the run and returns immediately, so you poll for completion. `scenario_manager_run` and `optimizer_run` are different: ExtendSim finishes the whole run before the call returns, so use `waitForCompletion: true` with them to have the outcome checked.

### 8.1 simulation_run

```
simulation_run()                → Starts simulation, returns immediately
simulation_status()             → Poll: { running: true/false }
simulation_get_results()        → Collect results when complete
```

Set `waitForCompletion: true` to block until the simulation finishes (legacy behavior).

### 8.2 scenario_manager_run

```
scenario_manager_run()          → Runs all scenarios (ExtendSim finishes before the call returns)
scenario_manager_status()       → { running, currentScenario, totalScenarios }
scenario_manager_get_results()  → Collect full results matrix
```

The server selects all scenarios only when none are selected; a selection you made is kept. With `waitForCompletion: true` it clears the previous results of the selected scenarios first, so only results from this run count, and the run succeeds only when every selected scenario has every response (this needs at least one block response); otherwise `OPTIMIZER_FAILED` lists the scenarios without results (`scenariosWithoutResults`) and ExtendSim's own messages (`dialogs`). A box saying the run was aborted also makes it `OPTIMIZER_FAILED`, even when every scenario has values (without `waitForCompletion` that is the only check). An auto-dialog-dismisser runs in the background to handle error dialogs and reports their texts. A Scenarios table that holds one row with every cell blank counts as no scenarios.

**`timeout` does not limit the run.** ExtendSim runs all scenarios before the call returns, with or without `waitForCompletion`; `timeout` only bounds the wait for ExtendSim to report idle afterwards (`waitForCompletion: true`). After the server's 10-minute command limit the call answers `COM_TIMEOUT` while ExtendSim may still be running.

**One analysis block per model.** When the model holds both a Scenario Manager and an Optimizer, `scenario_manager_run` and `optimizer_run` refuse with `INVALID_PARAMETER` before anything runs or changes: ExtendSim starts both with the same command and picks the block itself, so the other block could run and old results would be read.

### 8.3 optimizer_run

```
optimizer_run()                 → Runs the optimization (ExtendSim finishes before the call returns)
optimizer_get_results()         → Collect optimization results
```

With `waitForCompletion: true` the run succeeds only when at least one generation completed and no box ExtendSim showed says the run was aborted, and reports the best value, generations and convergence; an optimization that ExtendSim stopped or aborted (an error box, or Stop) is `OPTIMIZER_FAILED`, with ExtendSim's own messages in `dialogs`. Without `waitForCompletion` only an abort box is checked. As for the Scenario Manager, `timeout` does not limit the run, and a model holding both analysis blocks is refused (`INVALID_PARAMETER`); a model without an Optimizer is `BLOCK_NOT_FOUND`.

---

## 9. Session Logging and Telemetry

### 9.1 Session Logging

Opt-in detailed logging of all tool calls. Useful for debugging and cross-AI client testing.

**Enable via environment variable:**
```json
"env": { "MCP_SESSION_LOG": "1" }
```

**Enable via marker file:**
Create an empty file at `[Install Dir]/temp/mcp_session_enable`

**Log location:** `[Install Dir]/temp/mcp_session.log`

Params and results are each truncated at 2000 characters — a long entry ends in
`...[truncated]`. Logging never fails a tool call: if the write fails, it is dropped
silently.

**Log format:**
```
[2026-03-13T14:22:01.123Z] block_add (245ms) status=success
  params: {"library":"Item","blockType":"Activity"}
  result: {"status":"success","blockId":5,"label":"Activity"}
```

### 9.2 Telemetry

Local-only telemetry is always active. It records which tools were called, in what order, and error codes. It **never** records:
- Model data, file paths, or file names
- Block labels or parameter values
- Any user-defined names

**File location:** `[Install Dir]/temp/telemetry/telemetry.jsonl`
**Rotation:** Automatic at 10 MB
**Check status:** Use the `telemetry_control` tool

Telemetry data never leaves the machine automatically. You can inspect the file and optionally share it with Duke Systems AB for support purposes.

### 9.3 Backend Debug Logging

The Python COM backend can write low-level diagnostics — connector resolution, array
slot selection, block discovery — when you need to see why a connection or a block
lookup behaved unexpectedly.

**Enable:**
```json
"env": { "EXTENDSIM_DEBUG": "1" }
```

**Log location:** your system temp directory (`%TEMP%`), as
`simulationsmcp_connector_debug.log` and `simulationsmcp_block_discover_debug.log`.

Debug logging is off by default and never affects tool results: if the write fails,
it is silently skipped.

---

## 10. Troubleshooting

### ExtendSim Must Be Running

The server communicates with ExtendSim via COM. ExtendSim must be running and responsive before making any MCP calls.

**Symptom:** `EXTENDSIM_NOT_RUNNING` or `NOT_CONNECTED` errors
**Fix:** Start ExtendSim, then retry.

### COM Connection Lost

If ExtendSim crashes or is closed, the COM connection is lost.

**Symptom:** `COM_ERROR` or `CONNECTION_FAILED` errors
**Fix:** Restart ExtendSim. The server automatically attempts to reconnect (up to 2 retries).

### Slow new models and failed block copies (Windows clipboard)

ExtendSim copies blocks through the Windows clipboard. While another program holds the
clipboard (a clipboard manager, a remote-desktop session, a stuck application), every new
model takes several seconds and `block_duplicate` cannot copy.

**Symptom:** `model_new` is slow; `block_duplicate` fails
**Fix:** close the program that holds the clipboard (or restart it). Attribute tools
(`attribute_set`, `attribute_get`, `tag-items`) do not depend on the clipboard since 1.22.8.

### ExtendSim 2026.1.0.39: two hierarchical blocks connected directly stall

In ExtendSim 2026.1.0.39 a run stalls at time 1 when two hierarchical blocks are connected
directly (for example two machines from `compose_flow` wired `m1.out -> m2.in`). This is an
ExtendSim bug; ExtendSim 2024 runs the same model.

**Fix:** put a Queue between the two hierarchical blocks, or use ExtendSim 2024.

### Dialog Blocking ExtendSim

ExtendSim may display a modal dialog (e.g., COM error `sCode: 80004003`) that blocks all further COM calls.

**Symptom:** Tool calls time out
**Fix:** The server includes an auto-dialog-dismisser that detects and closes these dialogs. If manual intervention is needed, click OK/Close in the ExtendSim dialog.

### When ExtendSim gets stuck

ExtendSim sometimes stops to show a message box (an error in the model, or a message
about a block), or takes longer than expected over a command.

- **The server clicks OK on ExtendSim's own blocking message boxes automatically** (never on
  another program's) and passes the message
  to the AI as `EXTENDSIM_ERROR_DIALOG`, with the text in `dialog.text`. It does this so a
  single message box does not stop the session until someone comes to the computer.
  It looks for message boxes shortly after each command starts and every 10 seconds while a
  long command (such as a simulation run) is going. During Scenario Manager and optimizer
  runs it only reports what it sees and never clicks.
- **A few boxes get one fixed, safe answer.** ExtendSim's save prompt ("The model '...' has
  been modified. Do you want to save your changes?") is only ever answered **Cancel**, so the
  model stays open and unchanged — the server never saves your model or throws your changes
  away through it. "Already Open" ("A model with the same name is already open.") is answered
  OK. Excel's own "Save your changes to this file?" is answered **Cancel** too, but only in the
  background Excel that a Data Import Export block uses (an Excel started for automation) —
  the same prompt in an Excel you started yourself is left for you. When the server has to fall back to a key (Enter, or Escape for a box with a known answer),
  the key goes only to that box and only while ExtendSim responds; while ExtendSim is busy the
  box is reported, not pressed, and it counts as closed only when it has really gone.
- **ExtendSim's start-up reminders are closed too** - ExtendSim 2024's "Maintenance & Support
  Expired ..." and ExtendSim 2026's "Subscription Renewal" box. Until one of them is closed
  ExtendSim does not answer the server at all. Their text is never passed to the AI or
  recorded: the 2026 box shows your licence's activation key.
- **While ExtendSim has not finished a command**, every further command is answered at once
  with `EXTENDSIM_BUSY` instead of being queued. The answer says which command ExtendSim is
  still busy with, for how long, and what to do: click OK in a message box that could not be
  closed automatically, or wait for a long run. A long command can make Windows show ExtendSim
  as "Not responding" for as long as it works - that is normal; restart ExtendSim only if it
  stays that way for many minutes.
  `extendsim_status` answers even in this state.
- **The server carries on by itself** as soon as ExtendSim answers again. After ExtendSim
  has been restarted by hand it carries on by itself as well, and connects to the new ExtendSim. It never closes or
  restarts ExtendSim itself.
- **If ExtendSim never answers** (it has crashed or hangs for good), close ExtendSim, start
  it again, and restart your AI client (which restarts the server).

### Large Model Performance

Models with 20,000+ blocks may cause slower response times.

**Expected behavior:**
- `block_list` on 24k blocks: up to 2 minutes (5 COM calls per block)
- First `simulation_status` poll: 10–15 seconds
- `model_overview` is optimized for large models and should be used instead of `model_info`

### Python Backend Issues

The server spawns a Python subprocess for COM communication. If the Python process dies, the server attempts automatic restart (up to 2 retries).

**Check Python startup log:** `[Install Dir]/temp/python_startup.log`
**Verify pywin32:** `python -c "import win32com.client; print('OK')"`
**Deeper diagnostics:** set `EXTENDSIM_DEBUG=1` (see §9.3) to log connector resolution and block discovery to your temp directory.

### Port Conflict (HTTP Mode)

**Symptom:** Service fails to start
**Fix:** Change the port via `MCP_PORT` environment variable, or stop the conflicting process.

---

## 11. Appendix: Error Codes

Every failure returns `success: false` together with a structured `errorCode` and a
human-readable `error` message. Some errors also carry a `suggestion` field with a
recovery hint — treat it as a bonus, not a guarantee.

**Connection and process**

| Error Code | Meaning |
|------------|---------|
| `NOT_CONNECTED` | No active COM connection to ExtendSim |
| `COM_ERROR` | COM communication failure with ExtendSim, including a connection dropped mid-operation |
| `EXTENDSIM_NOT_RUNNING` | ExtendSim is not running — start it and retry |
| `EXTENDSIM_START_FAILED` | Failed to start ExtendSim |
| `MULTIPLE_EXTENDSIM_INSTANCES` | More than one ExtendSim is running; `instances` lists them — close all but one and retry |
| `LICENSE_DETECTION_FAILED` | Could not read the ExtendSim license or library set |

**Model**

| Error Code | Meaning |
|------------|---------|
| `MODEL_NOT_OPEN` | The operation needs an open model; none is open |
| `MODEL_OPEN_FAILED` | The model file could not be opened (ExtendSim did not make it the active model) — also returned by `model_open` when a model with that file name is already open from another folder, or is open but not the active model (ExtendSim cannot hold two models with the same name, and switching to an open model is not safe through COM; see §6.1) |
| `MODEL_CLOSE_FAILED` | `model_close` closed a window but the model is still open after three attempts - close the model's open dialogs in ExtendSim and try again |
| `MODEL_SAVE_FAILED` | The model could not be saved: the file did not change (read-only, locked, open elsewhere, or a file system with coarse timestamps), ExtendSim said it cannot save there, or a never-saved model was given no file path. The model is left open with its changes. Returned by `model_save`, `model_new` with `savePath`, and `block_configure`'s save-and-reopen step too. Also returned by `model_close` when `saveFirst` is given for a model that has never been saved (pass `filePath` instead: `model_close` saves it there, then closes), and when the save did not take - closing then still asked to save the changes, so the model is left open and unchanged |
| `MODEL_HAS_UNSAVED_CHANGES` | `model_close` refuses to close a model with unsaved changes unless you pass `saveFirst`, `discard`, or (a model that has never been saved) `filePath`; the model stays open and unchanged. See "Closing models safely" in §6.1 |
| `MODEL_QUERY_FAILED` | Reading model metadata failed |

**Blocks and connections**

| Error Code | Meaning |
|------------|---------|
| `BLOCK_NOT_FOUND` | Specified block ID does not exist |
| `INVALID_BLOCK_ID` | Block ID is malformed or out of range |
| `WRONG_BLOCK_TYPE` | The tool requires a different block type than the one given |
| `NOT_AN_HBLOCK` | The target is not a hierarchy block |
| `BLOCK_ADD_FAILED` / `BLOCK_REMOVE_FAILED` | Placing or deleting the block failed |
| `BLOCK_QUERY_FAILED` | Reading the block's properties failed |
| `BLOCK_PROFILE_FAILED` | `block_profile` placed the block but reading its dialog items through COM failed |
| `CONNECTION_FAILED` | Failed to establish or use a block connection. This also covers a connector that does not exist on the block — the error message names the connector, but the code does not distinguish the two cases |

**Values, tables and databases**

| Error Code | Meaning |
|------------|---------|
| `GET_VALUE_FAILED` / `SET_VALUE_FAILED` | Reading or writing a dialog variable failed - including a write the block did not keep, or a variable the block does not have (`readBack` shows what it holds; use `block_introspect` to find the real name) |
| `TABLE_NOT_FOUND` | The named table does not exist on the block |
| `TABLE_WRITE_REJECTED` | `table_set` read the cell back and it did not hold the written value — block-controlled cells reject writes silently |
| `DATABASE_NOT_FOUND` / `FIELD_NOT_FOUND` | No such database table or field |
| `DB_OPERATION_FAILED` | The database operation failed |

**Simulation**

| Error Code | Meaning |
|------------|---------|
| `SIMULATION_RUN_FAILED` | The run could not be started or completed - also when ExtendSim aborted it (it stopped at time 0, or an error dialog appeared during the run; the dialog text is in `dialogsDuringCommand`) |
| `SIMULATION_TIMEOUT` | The run exceeded its timeout |
| `MULTI_RUN_FAILED` | A multi-run or scenario sweep failed |
| `OPTIMIZER_FAILED` | An Optimizer **or Scenario Manager** run failed: ExtendSim stopped or aborted it (a box saying "aborted" counts, also after results were written), a scenario has no results, the run could not be checked, or the Scenario Manager has no (selected) scenarios. Scenario Manager failures use this code too |
| `OPTIMIZER_TIMEOUT` | ExtendSim did not report idle within `timeout` seconds after an Optimizer or Scenario Manager run returned (`waitForCompletion: true`) |

**Advanced resources**

| Error Code | Meaning |
|------------|---------|
| `ARM_GENERATION_FAILED` | ExtendSim did not generate the resources or requirements that were described - its own message is in `dialogsDuringCommand` - or a call to ExtendSim failed after generation had started (pressing Auto-Generate, or setting the queues and releases). `steps` shows how far the call got; call `resources_configure` again once the cause is fixed |
| `ARM_VERIFY_FAILED` | Something ExtendSim generated or a queue/release setting did not read back as written |

**Analysis blocks (Scenario Manager, Optimizer, Analysis Manager)**

| Error Code | Meaning |
|------------|---------|
| `ANALYSIS_CONFIG_FAILED` | Something ExtendSim generated did not read back, or a prompt was not answered; `steps` says how far it got |

**Data files (Data Import Export blocks)**

| Error Code | Meaning |
|------------|---------|
| `MODEL_FILES_REPOINT_FAILED` | `model_files_repoint` wrote a new path that did not read back exactly as written; `steps` says which blocks were changed |
| `MODEL_FILES_CONFIGURE_FAILED` | `model_files_configure` wrote or verified a block and a value did not read back exactly as written (or the intent could not be stored in the model's AI context); `steps` says what was written so far |
| `DATA_FILE_MISSING` | A run tool found a file the model would auto-import at run start missing, unset or given as a relative path with a folder, so nothing was started; `missingFiles` names the block, the path and the `reason` |
| `DATA_FILE_TARGET_CHANGED` | A run tool found a block configured with `model_files_configure` now pointing at a different table than the one it was set up for, so nothing was started; `targetChanged` names the block, the intended database/table and the one it now resolves to |
| `DATA_FILE_EXCEL_CLOSE_CONFLICT` | A run tool found an auto Excel import with "close worksheet after use" on while the model also has an Excel export - that combination hangs ExtendSim on Excel's own save prompt - so nothing was started; `conflicts` names the blocks |

**Request validation**

| Error Code | Meaning |
|------------|---------|
| `MISSING_PARAMETER` | Required parameter not provided |
| `INVALID_PARAMETER` | Parameter present but not valid for this tool |
| `UNKNOWN_COMMAND` | The backend has no handler for the command |
| `COMMAND_FAILED` | A raw ModL command failed |
| `TEMPLATE_NOT_FOUND` | No such block template |

**Raised by the TypeScript layer, not the backend**

| Error Code | Meaning |
|------------|---------|
| `EXTENDSIM_ERROR_DIALOG` | ExtendSim raised an error dialog while the command ran — almost always an error in the model, such as a missing resource pool or an index out of range in a block. The dialog text is in `dialog.text` and usually names the block (e.g. `[62]Queue`). **Retrying the same call will hit the same error; fix the cause.** If `dialog.dismissed` is false the dialog is still open and a person must close it. If a simulation is running, the dialog may come from the run rather than from this command |
| `COM_TIMEOUT` | Command timed out with **no** dialog to explain why (see the per-command timeout table below). ExtendSim may be busy or unresponsive; a retry can succeed |
| `EXTENDSIM_BUSY` | ExtendSim has not finished an earlier command; nothing is sent to it until it answers. Wait, follow `suggestion`, check `extendsim_status` |
| `INVALID_JSON` | Invalid JSON response from the Python backend |
| `TOOL_ERROR` | Unhandled error in tool execution |

**Own guides (`guide_draft`, `guide_save`, `guide_delete`; §4.8)**

| Error Code | Meaning |
|------------|---------|
| `GUIDE_INVALID_KEY` | The key is not 1-64 characters of a-z, 0-9 and _, is a reserved name, or is only the display name (`<key>_local`) of a guide saved under another key |
| `GUIDE_INVALID` | The guide does not match the guide schema, lacks content every guide needs, or would exceed 100 KB; every problem is listed in `issues` and nothing was saved |
| `GUIDE_EXISTS` | You already have a guide with this key; pass `overwrite: true` to replace it |
| `GUIDE_NOT_FOUND` | You have no own guide saved under this key |
| `GUIDE_TOO_LARGE_MODEL` | More than 50 blocks at this level of the model; draft the inside of one hierarchical block with `hierarchyBlockId` |
| `GUIDE_WRITE_FAILED` | The guide file could not be written or deleted; nothing was changed |
| `GUIDE_NOTHING_TO_DRAFT` | There are no blocks at this level of the model to draft a guide from |

### Per-Command Timeouts

Timeouts are compiled into `backend.ts` and cannot be changed without rebuilding.

| Timeout | Commands |
|---------|----------|
| 10 s (default) | Everything not listed below |
| 30 s | `model_open`, `model_list`, `model_save`, `model_new`, `model_validate`, `detect_license`, `block_template`, `block_add_batch`, `block_discover`, `block_discover_variables`, `block_introspect`, `simulation_get_block_stats`, `db_get_records`, `db_import`, `db_export`, `db_create`, `hierarchy_list`, `hierarchy_get_contents`, `scenario_manager_status`, `scenario_manager_get_results` |
| 60 s | `model_close` (probes a saved model for unsaved changes when no choice is given), `model_files_list`, `model_files_repoint`, `block_configure` (save/close/reopen cycle), `simulation_get_results` |
| 2 min | `extendsim_start`, `block_list`, `model_extract`, `block_profile`, `block_guide_draft`, `resources_configure`, `analysis_manager_get_results`, `model_files_configure` |
| 3 min | `scenario_manager_configure`, `optimizer_configure`, `analysis_manager_configure` |
| 5 min | `simulation_run` (blocking mode only) |
| 10 min | `simulation_run_multi`, `simulation_run_scenarios`, `scenario_manager_run`, `optimizer_run` — the last two in both modes, since ExtendSim finishes the run before they return |

A separate **early dialog check** fires 1 second into any command: a modal dialog
appearing that fast always means a configuration error, never a long-running
operation, so you get the real message instead of waiting out the full timeout.
`scenario_manager_run`, `scenario_manager_status` and `optimizer_run` skip it —
they open dialogs as part of normal operation.

---

---

**Trademark Notice:** ExtendSim is a registered trademark of Imagine That, Inc., a subsidiary of ANDRITZ Inc. This product is an independent third-party integration and is not affiliated with, endorsed by, or sponsored by Imagine That, Inc. or ANDRITZ. All other trademarks are the property of their respective owners.

*Copyright (c) 2025–2026 Duke Systems AB*
