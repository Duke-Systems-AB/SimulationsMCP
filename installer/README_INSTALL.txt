Simulations MCP Server - Installation Guide
============================================
Duke Systems AB
Version 1.20.0 — 99 tools
(Prebuilt installer SimulationsMCP-Setup-1.19.2.exe predates the 1.20.0 tools; rebuild pending.)

PREREQUISITES
-------------
Before installing, ensure you have:

1. Node.js 18 or higher
   Download: https://nodejs.org

2. Python 3.x with pywin32
   Download: https://python.org
   (Make sure to check "Add Python to PATH" during installation)
   Then install pywin32: pip install pywin32

3. ExtendSim installed and registered
   The ExtendSim COM component must be available.
   ExtendSim must be running before using the MCP server.

INSTALLATION
------------
1. Run this installer with administrator privileges
2. Choose installation directory (default: C:\Program Files\SimulationsMCP)
3. Select whether to install as Windows Service
   NOTE: Most users do NOT need the Windows Service. It is only required
   for ChatGPT integration. Claude Code, Gemini CLI, and Cursor connect
   directly via stdio — no service or port needed.
4. If you selected Windows Service: configure the port (default: 3001)
5. Complete installation

TRANSPORT MODES
---------------
The server supports two transport modes:

- stdio (RECOMMENDED for most users)
  Direct process communication — the AI client starts the server as a
  local subprocess. No network, no port, no service needed. This is the
  simplest and most reliable setup. Used by Claude Code, Claude Desktop,
  Gemini CLI, and Cursor.

- HTTP (only needed for ChatGPT)
  Streamable HTTP on localhost via Windows Service. Only required for
  ChatGPT, which cannot start local processes and needs a remote HTTPS
  endpoint. If you are NOT using ChatGPT, you do NOT need the Windows
  Service or port configuration — just use stdio mode above.
  Configured via MCP_TRANSPORT=http and MCP_PORT environment variables.

MCP CONFIGURATION — AI CLIENTS
-------------------------------

--- Claude Code (CLI) ---

Add to your project's .mcp.json or ~/.claude.json:

{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"]
    }
  }
}

--- Claude Desktop ---

Add to claude_desktop_config.json
(typically at %APPDATA%\Claude\claude_desktop_config.json):

{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"]
    }
  }
}

--- Gemini CLI ---

Add to your Gemini CLI settings.json
(typically at ~/.gemini/settings.json):

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

Note: MCP_SESSION_LOG=1 is optional — enables session logging for debugging.

--- Cursor IDE ---

Add to .cursor/mcp.json (project-level) or ~/.cursor/mcp.json (global):

{
  "mcpServers": {
    "SimulationsMCP": {
      "command": "node",
      "args": ["C:/Program Files/SimulationsMCP/dist/index.js"]
    }
  }
}

--- ChatGPT (requires Windows Service / HTTP mode) ---

ChatGPT only supports remote HTTPS MCP servers. To use with ChatGPT:

1. Install as Windows Service (select during installation)
2. The service runs on http://localhost:3001/mcp
3. You need to expose this endpoint via a reverse proxy with HTTPS
   (e.g., ngrok, Cloudflare Tunnel, or your own domain with SSL)
4. In ChatGPT Settings > Apps & Connectors, add your HTTPS URL

For local testing without HTTPS, use the API Playground at
platform.openai.com with the HTTP endpoint directly.

SESSION LOGGING (for testing & debugging)
-----------------------------------------
Enable detailed tool call logging for cross-AI testing:

Option 1: Environment variable
  Set MCP_SESSION_LOG=1 in your MCP config's env block

Option 2: Marker file
  Create an empty file: [Install Dir]\temp\mcp_session_enable

Logs are written to: [Install Dir]\temp\mcp_session.log
Format: [timestamp] tool_name (duration) status + params + result

FIRST SESSION — IMPORTANT
-------------------------
When your AI client connects, it should call MCP_init first.
This returns critical usage rules, available tools, and workflow guidance.

The server provides 99 tools across these categories:
  Model, Block, Block Layout, Values, Config, Attributes,
  Simulation, Statistics, Multi-run, Database, DB Relations,
  Global Arrays, Hierarchy, Analysis (Optimizer, Scenario Manager),
  Templates, Annotations, Time/Date, Context, Patterns, Advisor,
  Reference, and Status tools.

Key tools for AI assistants:
  - modeling_guide  — Step-by-step guidance for common scenarios
  - pattern_search  — Search 268 verified example models
  - model_advisor   — Analyze model and get warnings/suggestions
  - block_configure — Configure any block type with one call

FIRE-AND-FORGET OPERATIONS
---------------------------
Long-running operations return immediately by default:

  simulation_run         — Poll with simulation_status
  scenario_manager_run   — Poll with scenario_manager_status,
                           collect with scenario_manager_get_results
  optimizer_run          — Poll with simulation_status,
                           collect with optimizer_get_results

Set waitForCompletion=true for blocking mode.

SERVICE MANAGEMENT
------------------
Start service:  net start "SimulationsMCP"
Stop service:   net stop "SimulationsMCP"

Or use the shortcuts in Start Menu > Simulations MCP Server

TROUBLESHOOTING
---------------
- ExtendSim must be running before making MCP calls
- Check logs in: [Install Dir]\logs\
- Session log: [Install Dir]\temp\mcp_session.log (if enabled)
- Verify port is not blocked by firewall (HTTP mode)
- Check Windows Services (services.msc) for service status
- If COM errors occur, restart ExtendSim and retry
- On large models (20k+ blocks), first status poll may take 10-15s

SUPPORT
-------
For issues and feature requests, contact Duke Systems AB

ExtendSim is a registered trademark of Imagine That, Inc., a subsidiary
of ANDRITZ Inc. This product is an independent third-party integration
and is not affiliated with, endorsed by, or sponsored by Imagine That,
Inc. or ANDRITZ.

Copyright (c) 2025-2026 Duke Systems AB
