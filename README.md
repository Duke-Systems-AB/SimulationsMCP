# Simulations MCP Server

**AI-powered simulation modeling for ExtendSim**

An MCP (Model Context Protocol) server that enables AI assistants to build, configure, run, and analyze [ExtendSim](https://extendsim.com) simulation models. Works with Claude Code, Claude Desktop, Gemini CLI, Cursor, and ChatGPT.

> **Note:** This is an independent third-party tool by Duke Systems AB. It is not affiliated with or endorsed by Imagine That, Inc. (a subsidiary of ANDRITZ Inc.), the makers of ExtendSim.

## Overview

```
AI Client (Claude, Gemini, Cursor, ChatGPT)
    │
    │  MCP Protocol (JSON-RPC 2.0)
    ▼
TypeScript MCP Server (92 tools)
    │
    │  JSON over stdin/stdout
    ▼
Python COM Backend (pywin32)
    │
    │  COM/DCOM
    ▼
ExtendSim Application
```

The server exposes **92 tools** across 17 categories:

| Category | Tools |
|----------|-------|
| Model Management | `model_open`, `model_save`, `model_new`, `model_close`, `model_info`, `model_validate`, `model_extract`, `model_overview`, `model_snapshot`, `model_list` |
| Block Operations | `block_add`, `block_add_batch`, `block_connect`, `block_disconnect`, `connect_chain`, `connect_graph`, `block_remove`, `block_list`, `connection_list`, `block_info`, `block_discover`, `block_discover_variables` |
| Block Layout | `block_move`, `block_get_position`, `block_align`, `block_duplicate`, `block_find` |
| Configuration | `block_configure`, `block_set_value`, `block_get_value`, `execute_command`, `attribute_set`, `attribute_get` |
| Simulation | `simulation_run`, `simulation_stop`, `simulation_pause`, `simulation_resume`, `simulation_status`, `simulation_get_results`, `simulation_setup_get`, `simulation_setup_set`, `simulation_step`, `simulation_get_state` |
| Statistics | `block_get_stats`, `simulation_get_block_stats`, `resource_pool_get_stats` |
| Multi-Run | `simulation_run_multi`, `simulation_run_scenarios`, `scenario_manager_run`, `scenario_manager_status`, `scenario_manager_get_results`, `optimizer_run`, `optimizer_get_results` |
| Database | `db_list`, `db_table_info`, `db_get_value`, `db_set_value`, `db_get_records`, `db_add_records`, `db_delete_records`, `db_create`, `db_import`, `db_export`, `db_find_record`, `db_sort`, `db_relations_list`, `db_relation_create` |
| Global Arrays | `ga_list`, `ga_create`, `ga_read`, `ga_write` |
| Hierarchy | `hierarchy_list`, `hierarchy_get_contents` |
| AI Assistance | `MCP_init`, `modeling_guide`, `pattern_search`, `model_advisor`, `simulation_type_guide` |
| Reference | `modl_search`, `block_search`, `dialog_search`, `template_list`, `block_template` |
| Templates | `text_block_add` |
| Time/Date | `time_convert` |
| Context | `context_set`, `context_get`, `context_clear` |
| Status | `extendsim_status`, `extendsim_start`, `extendsim_get_license` |
| Telemetry | `telemetry_control` |

## Requirements

- **Windows 10/11** (64-bit)
- **Node.js** 18+
- **Python 3.x** with pywin32: `pip install pywin32`
- **ExtendSim** installed with COM component registered

## Quick Start

### Install from Source

```bash
cd src/ExtendSimMCP.TypeScript
npm install
npm run build
```

### Install from Installer

Download `SimulationsMCP-Setup-1.19.2.exe` from the `installer/` directory and run it.

### Configure Your AI Client

Add to your MCP configuration (e.g., `.mcp.json` for Claude Code):

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

For other clients (Claude Desktop, Gemini CLI, Cursor, ChatGPT), see the [User Manual](docs/USER_MANUAL.md).

### First Session

1. Start ExtendSim
2. Open your AI client
3. Run `MCP_init` to get rules and guidance
4. Use `modeling_guide` or `pattern_search` to find the right approach
5. Build, configure, run, and analyze your simulation

## Transport Modes

| Mode | Use Case | Setup |
|------|----------|-------|
| **stdio** (default) | Claude Code, Claude Desktop, Gemini CLI, Cursor | Zero config — AI client starts the server as a subprocess |
| **HTTP** | ChatGPT | Windows Service on localhost:3001, requires HTTPS reverse proxy |

## Key Features

- **block_configure** — Single tool that auto-detects block type and configures Activity, Queue, Create, Exit, Select Item, Gate, Tank, Valve, and 25+ more block types
- **Fire-and-forget** — `simulation_run`, `scenario_manager_run`, and `optimizer_run` start non-blocking by default; poll with status tools
- **268 example models** — Search verified patterns via `pattern_search`
- **12 modeling guides** — Step-by-step guidance for queuing, manufacturing, logistics, resources, flow, and continuous systems
- **Model advisor** — Automatic warnings, suggestions, and completions for your model
- **Auto-dialog-dismisser** — Handles ExtendSim COM error dialogs during long-running operations

## Documentation

- [User Manual](docs/USER_MANUAL.md) — Installation, configuration, tool reference, workflows, troubleshooting
- [Design Document](docs/DESIGN_DOCUMENT.md) — Architecture, security analysis, threat model, data flows

## Build and Test

```bash
cd src/ExtendSimMCP.TypeScript

# Build
npm run build

# Run tests (142 tests, no ExtendSim required)
npm test

# Run live COM tests (requires running ExtendSim)
npm run test:live
```

## Security

- **stdio mode**: Zero network attack surface — all communication via process-local pipes
- **No credentials stored** — COM uses Windows process-level trust
- **Local-only telemetry** — Usage patterns logged to disk, never transmitted
- **Input validation** — Zod schemas on all tool parameters, ModL string sanitization
- **No cloud dependencies** — Everything runs locally on your machine

See the [Design Document](docs/DESIGN_DOCUMENT.md) for the full security architecture and threat analysis.

## License

MIT — see [LICENSE](LICENSE)

## Author

Duke Systems AB

---

*Built with the [Model Context Protocol](https://modelcontextprotocol.io)*

---

**Trademark Notice:** ExtendSim is a registered trademark of Imagine That, Inc., a subsidiary of ANDRITZ Inc. This project is an independent third-party integration and is not affiliated with, endorsed by, or sponsored by Imagine That, Inc. or ANDRITZ. All other trademarks are the property of their respective owners.
