# Simulations MCP Server — User Manual

**Version:** 1.22.1
**Author:** Duke Systems AB
**Date:** 2026-06-29

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

The Simulations MCP Server is a Model Context Protocol (MCP) server that enables AI assistants to build, configure, run, and analyze ExtendSim simulation models programmatically. It bridges AI clients (Claude Code, Claude Desktop, Gemini CLI, Cursor, ChatGPT) to ExtendSim's full modeling environment through 92 specialized tools.

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
| **Python** | Version 3.x with `pywin32` package |
| **ExtendSim** | Installed with COM component registered |
| **AI Client** | Any MCP-compatible client (see Section 4) |

### Install Python Dependencies

```bash
pip install pywin32
```

### Verify ExtendSim COM Registration

ExtendSim registers its COM component during installation. If needed, run ExtendSim once as administrator to trigger registration.

---

## 3. Installation

### Option A: Installer (Recommended)

1. Run `SimulationsMCP-Setup-1.22.1.exe` as administrator (the prebuilt installer matches the current source)
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
| **stdio** (default) | Claude Code, Claude Desktop, Gemini CLI, Cursor | AI client starts the server as a local subprocess. No network, no port. |
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

The server provides 104 tools organized into categories. Each tool accepts structured parameters (validated with JSON Schema) and returns structured JSON responses.

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

### 6.9 Global Arrays

| Tool | Description |
|------|-------------|
| `ga_list` | List all global arrays |
| `ga_create` | Create a new global array |
| `ga_read` | Read values from a global array |
| `ga_write` | Write values to a global array |

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

### Port Conflict (HTTP Mode)

**Symptom:** Service fails to start
**Fix:** Change the port via `MCP_PORT` environment variable, or stop the conflicting process.

---

## 11. Appendix: Error Codes

All error responses include a structured `errorCode` and human-readable `error` message, plus a `suggestion` field with recovery hints.

| Error Code | Meaning |
|------------|---------|
| `COM_ERROR` | COM communication failure with ExtendSim |
| `BLOCK_NOT_FOUND` | Specified block ID does not exist |
| `CONNECTION_FAILED` | Failed to establish or use a block connection |
| `NOT_CONNECTED` | No active COM connection to ExtendSim |
| `MISSING_PARAMETER` | Required parameter not provided |
| `EXTENDSIM_NOT_RUNNING` | ExtendSim is not running |
| `EXTENDSIM_START_FAILED` | Failed to start ExtendSim |
| `INVALID_JSON` | Invalid JSON response from Python backend |
| `TOOL_ERROR` | Unhandled error in tool execution |
| `TIMEOUT` | Command timed out (see per-command timeout table) |

### Per-Command Timeouts

| Command | Timeout |
|---------|---------|
| Most commands | 10 seconds |
| File I/O (open, save, import, export) | 30 seconds |
| `block_configure` | 60 seconds |
| `extendsim_start` | 2 minutes |
| `simulation_run` (blocking mode) | 5 minutes |
| `block_list` (large models) | 2 minutes |
| `simulation_run_multi`, `scenario_manager_run`, `optimizer_run` | 10 minutes |

---

---

**Trademark Notice:** ExtendSim is a registered trademark of Imagine That, Inc., a subsidiary of ANDRITZ Inc. This product is an independent third-party integration and is not affiliated with, endorsed by, or sponsored by Imagine That, Inc. or ANDRITZ. All other trademarks are the property of their respective owners.

*Copyright (c) 2025–2026 Duke Systems AB*
