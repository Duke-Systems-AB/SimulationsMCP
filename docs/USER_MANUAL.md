# Simulations MCP Server — User Manual

**Version:** 1.22.5
**Author:** Duke Systems AB
**Date:** 2026-09-23

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

The Simulations MCP Server is a Model Context Protocol (MCP) server that enables AI assistants to build, configure, run, and analyze ExtendSim simulation models programmatically. It bridges AI clients (Claude Code, Claude Desktop, Gemini CLI, Cursor, ChatGPT) to ExtendSim's full modeling environment through 107 specialized tools.

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
| **ExtendSim** | Installed with COM component registered |
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

1. Run `SimulationsMCP-Setup-1.22.5.exe` as administrator (the prebuilt installer matches the current source)
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

6. **Save frequently** — ExtendSim may crash on invalid commands. Save your model often with `model_save`.

---

## 6. Tool Reference

The server provides 107 tools organized into categories. Each tool accepts structured parameters (validated with JSON Schema) and returns structured JSON responses.

### 6.1 Model Management

| Tool | Description |
|------|-------------|
| `model_new` | Create a new empty model |
| `model_open` | Open an existing model file (.mox) |
| `model_save` | Save the current model (optional: save-as with new path) |
| `model_close` | Close the current model |
| `model_list` | List all open models |
| `model_info` | Get model metadata (blocks, connections, databases) |
| `model_validate` | Check model for structural issues (unconnected blocks, missing queues) |
| `model_snapshot` | Capture the full model state as a JSON summary |
| `model_extract` | Deep extraction of model structure, connections, and configurations |
| `model_overview` | High-level summary optimized for large models (24k+ blocks) |

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
| `block_introspect` | Unified introspection: live dialog items **plus** the block's internal STAT storage variables (names and types read from the `.lbr`, with live values for scalars). Read-only. Surfaces variables such as `EQ_EquationText` that the dialog API cannot see |

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
| `block_set_value` | Set a dialog variable value on a block |
| `block_get_value` | Get a dialog variable value from a block |
| `execute_command` | Execute a raw ModL command string (advanced) |
| `block_configure` | Auto-detecting block configurator — handles Activity, Queue, Create, Exit, Select Item In/Out, Gate, Resource Item, Batch/Unbatch, Equation, Tank, Valve, and more. Single tool replaces 33 individual config tools. |
| `attribute_set` | Set an item attribute value |
| `attribute_get` | Get an item attribute value |
| `table_get` | Read a string-table cell (`*_ttbl` dialog tables such as `IVars_ttbl`/`OVars_ttbl`). Use this where `block_get_value` cannot — it is numeric and returns `ERR` on string cells |
| `table_set` | Write a string-table cell. Read-back verified: fails closed with `TABLE_WRITE_REJECTED` if the cell does not hold the written value (block-controlled cells reject writes silently) |
| `detect_attributes` | Detect which item attributes a block reads and writes (equation blocks: from their in/out variable tables). Returns `{ reads, writes, confidence }` |

### 6.5 Simulation Control

| Tool | Description |
|------|-------------|
| `simulation_run` | Run the simulation (fire-and-forget by default) |
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
| `scenario_manager_run` | Run the Scenario Manager (fire-and-forget by default) |
| `scenario_manager_status` | Check Scenario Manager progress |
| `scenario_manager_get_results` | Collect Scenario Manager results |
| `optimizer_run` | Run the Optimizer (fire-and-forget by default) |
| `optimizer_get_results` | Collect Optimizer results |

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
| `guide_draft` | Draft a guide of your own from the open model (nothing is saved) |
| `guide_save` | Check and save a guide of your own to your personal guide folder |
| `guide_delete` | Delete one of your own guides |

### 6.12 Reference Tools

| Tool | Description |
|------|-------------|
| `modl_search` | Search ModL function reference (syntax, arguments, return types) |
| `block_search` | Search block library (connectors, patterns, descriptions) |
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
| `approve_pattern` | Turn a clustered candidate plus a naming into a validated library entry and write `patterns/molecules/<id>.json`. `dryRun` previews without writing. **Fail-closed — nothing enters the library without deliberate approval** |

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
1. scenario_manager_run()                → Start (non-blocking)
2. scenario_manager_status()             → Poll until complete
3. scenario_manager_get_results()        → Collect all scenario results
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

---

## 8. Fire-and-Forget Operations

Three long-running tools default to non-blocking (fire-and-forget) mode. They start the operation and return immediately, allowing you to poll for completion.

### 8.1 simulation_run

```
simulation_run()                → Starts simulation, returns immediately
simulation_status()             → Poll: { running: true/false }
simulation_get_results()        → Collect results when complete
```

Set `waitForCompletion: true` to block until the simulation finishes (legacy behavior).

### 8.2 scenario_manager_run

```
scenario_manager_run()          → Starts all scenarios, returns immediately
scenario_manager_status()       → Poll: { running, currentScenario, totalScenarios }
scenario_manager_get_results()  → Collect full results matrix
```

The server automatically selects all scenarios before running. An auto-dialog-dismisser runs in the background to handle COM error dialogs that may appear on large models.

### 8.3 optimizer_run

```
optimizer_run()                 → Starts optimizer, returns immediately
simulation_status()             → Poll for completion
optimizer_get_results()         → Collect optimization results
```

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

### Dialog Blocking ExtendSim

ExtendSim may display a modal dialog (e.g., COM error `sCode: 80004003`) that blocks all further COM calls.

**Symptom:** Tool calls time out
**Fix:** The server includes an auto-dialog-dismisser that detects and closes these dialogs. If manual intervention is needed, click OK/Close in the ExtendSim dialog.

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
| `LICENSE_DETECTION_FAILED` | Could not read the ExtendSim license or library set |

**Model**

| Error Code | Meaning |
|------------|---------|
| `MODEL_NOT_OPEN` | The operation needs an open model; none is open |
| `MODEL_OPEN_FAILED` | The model file could not be opened |
| `MODEL_SAVE_FAILED` | The model could not be saved |
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
| `CONNECTION_FAILED` | Failed to establish or use a block connection. This also covers a connector that does not exist on the block — the error message names the connector, but the code does not distinguish the two cases |

**Values, tables and databases**

| Error Code | Meaning |
|------------|---------|
| `GET_VALUE_FAILED` / `SET_VALUE_FAILED` | Reading or writing a dialog variable failed |
| `TABLE_NOT_FOUND` | The named table does not exist on the block |
| `TABLE_WRITE_REJECTED` | `table_set` read the cell back and it did not hold the written value — block-controlled cells reject writes silently |
| `DATABASE_NOT_FOUND` / `FIELD_NOT_FOUND` | No such database table or field |
| `DB_OPERATION_FAILED` | The database operation failed |

**Simulation**

| Error Code | Meaning |
|------------|---------|
| `SIMULATION_RUN_FAILED` | The run could not be started or completed |
| `SIMULATION_TIMEOUT` | The run exceeded its timeout |
| `MULTI_RUN_FAILED` | A multi-run or scenario sweep failed |
| `OPTIMIZER_FAILED` / `OPTIMIZER_TIMEOUT` | The Optimizer failed or exceeded its timeout |

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
| 30 s | `model_open`, `model_save`, `model_close`, `model_new`, `model_validate`, `detect_license`, `block_template`, `block_add_batch`, `block_discover`, `block_discover_variables`, `block_introspect`, `simulation_get_block_stats`, `db_get_records`, `db_import`, `db_export`, `db_create`, `hierarchy_list`, `hierarchy_get_contents`, `scenario_manager_status`, `scenario_manager_get_results` |
| 60 s | `block_configure` (save/close/reopen cycle), `simulation_get_results` |
| 2 min | `extendsim_start`, `block_list`, `model_extract` |
| 5 min | `simulation_run` (blocking mode only) |
| 10 min | `simulation_run_multi`, `simulation_run_scenarios`, `scenario_manager_run`, `optimizer_run` — the last two only with `waitForCompletion=true` |

A separate **early dialog check** fires 1 second into any command: a modal dialog
appearing that fast always means a configuration error, never a long-running
operation, so you get the real message instead of waiting out the full timeout.
`scenario_manager_run`, `scenario_manager_status` and `optimizer_run` skip it —
they open dialogs as part of normal operation.

---

---

**Trademark Notice:** ExtendSim is a registered trademark of Imagine That, Inc., a subsidiary of ANDRITZ Inc. This product is an independent third-party integration and is not affiliated with, endorsed by, or sponsored by Imagine That, Inc. or ANDRITZ. All other trademarks are the property of their respective owners.

*Copyright (c) 2025–2026 Duke Systems AB*
