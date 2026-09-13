# Simulations MCP Server — Architecture and Design Document

**Version:** 1.19.1
**Author:** Duke Systems AB
**Date:** 2026-03-13
**Classification:** Technical — for IT security specialists, software architects, and power users

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [System Architecture](#2-system-architecture)
3. [Component Design](#3-component-design)
4. [Communication Protocols](#4-communication-protocols)
5. [Security Architecture](#5-security-architecture)
6. [Data Flow Analysis](#6-data-flow-analysis)
7. [Error Handling and Resilience](#7-error-handling-and-resilience)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Performance Characteristics](#9-performance-characteristics)
10. [Trust Model and Threat Analysis](#10-trust-model-and-threat-analysis)
11. [Configuration Reference](#11-configuration-reference)
12. [Dependencies and Supply Chain](#12-dependencies-and-supply-chain)

---

## 1. Executive Summary

The Simulations MCP Server is a Model Context Protocol (MCP) server that bridges AI assistants to the ExtendSim discrete-event simulation platform. It exposes 92 tools across 17 categories, enabling AI-driven model construction, simulation execution, and result analysis.

**Key architectural properties:**

- **Single-machine deployment** — All components run locally on the same Windows machine
- **No cloud dependencies** — Zero external API calls, no data exfiltration
- **Two-process architecture** — Node.js (TypeScript) for MCP protocol + Python for COM integration
- **Dual transport** — stdio (default, zero network) or HTTP (localhost only, for ChatGPT)
- **Fire-and-forget** — Long-running operations are non-blocking with status polling
- **Local-only telemetry** — Usage patterns logged to disk, never transmitted

---

## 2. System Architecture

### 2.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  AI Client (Claude Code, Gemini CLI, Cursor, ChatGPT)          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  MCP Protocol (JSON-RPC 2.0 over stdio or HTTP)          │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
└────────────────────────────┼────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Node.js Process (TypeScript MCP Server)                       │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  MCP SDK     │  │  Tool        │  │  Reference Data      │  │
│  │  Protocol    │  │  Definitions │  │  (JSON files, lazy)  │  │
│  │  Handler     │  │  (92 tools)  │  │                      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────────────────┘  │
│         │                 │                                     │
│         │    ┌────────────┴─────────────┐                      │
│         │    │  Backend Bridge          │                      │
│         │    │  (backend.ts)            │                      │
│         │    │  - Request queue         │                      │
│         │    │  - Per-command timeouts  │                      │
│         │    │  - Heartbeat monitor     │                      │
│         │    │  - Dialog watcher        │                      │
│         │    │  - Retry logic           │                      │
│         │    └────────────┬─────────────┘                      │
│         │                 │ JSON over stdin/stdout              │
└─────────┼─────────────────┼────────────────────────────────────┘
          │                 │
          │                 ▼
          │  ┌──────────────────────────────────────────────┐
          │  │  Python Process (simulation_backend.py)      │
          │  │                                              │
          │  │  ┌────────────────┐  ┌───────────────────┐  │
          │  │  │  Command       │  │  COM Integration  │  │
          │  │  │  Dispatcher    │  │  (pywin32)        │  │
          │  │  │  (dispatch     │  │                   │  │
          │  │  │   table)       │  │  GetActiveObject  │  │
          │  │  └────────┬───────┘  └───────┬───────────┘  │
          │  │           │                  │ COM/DCOM     │
          │  └───────────┼──────────────────┼──────────────┘
          │              │                  │
          │              │                  ▼
          │              │  ┌───────────────────────────────┐
          │              │  │  ExtendSim Application       │
          │              │  │  (COM server)                 │
          │              │  │  - Simulation engine          │
          │              │  │  - Model workspace            │
          │              │  │  - Database engine             │
          │              │  └───────────────────────────────┘
          │              │
          │  ┌───────────┴──────────────────────────────────┐
          │  │  Dialog Watcher (dialog_watcher.py)          │
          │  │  Separate Python process                     │
          │  │  - Windows UI Automation                     │
          │  │  - Detects and dismisses modal dialogs       │
          │  │  - Spawned on-demand during timeouts/SM/opt  │
          │  └──────────────────────────────────────────────┘
          │
          ▼
  ┌──────────────────┐
  │  Telemetry       │
  │  (telemetry.ts)  │
  │  Local JSONL     │
  │  file only       │
  └──────────────────┘
```

### 2.2 Process Model

| Process | Language | Lifecycle | Purpose |
|---------|----------|-----------|---------|
| MCP Server | TypeScript (Node.js) | Started by AI client | Protocol handling, tool routing, reference data |
| COM Backend | Python | Singleton subprocess | COM communication with ExtendSim |
| Dialog Watcher | Python | Spawned on-demand | UI Automation to dismiss blocking dialogs |

The Python process is a long-lived singleton — spawned once and kept alive for the entire MCP session. This avoids the overhead of per-call COM initialization.

---

## 3. Component Design

### 3.1 MCP Server (index.ts, ~2380 lines)

**Responsibilities:**
- MCP protocol handling via `@modelcontextprotocol/sdk`
- Tool registration with Zod schema validation (92 tools)
- Reference data management (lazy-loaded JSON files)
- Search engines (ModL functions, blocks, dialog variables)
- Session logging and telemetry integration
- Transport selection (stdio vs HTTP)

**Design decisions:**
- All reference data is lazy-loaded on first access, not at startup
- Tool responses are wrapped in a consistent format with `isError` flag for MCP clients
- `safeToolCall()` wrapper provides uniform error handling, telemetry, and session logging
- Server version is read dynamically from `package.json`, not hardcoded

### 3.2 Backend Bridge (backend.ts, ~1220 lines)

**Responsibilities:**
- Python subprocess lifecycle management
- JSON-over-stdin/stdout protocol with the Python process
- Request queue with sequential processing (COM is single-threaded)
- Per-command timeout configuration (10s default, up to 10min for long ops)
- Heartbeat monitoring (60-second interval)
- Auto-retry on Python process death (up to 2 retries)
- Dialog watcher integration (spawns `dialog_watcher.py` on timeout)
- Stale response detection and discard

**Design decisions:**
- Sequential request queue enforces COM single-threading constraint
- Early dialog check (1s) fires before main timeout for fast error feedback
- Certain commands (SM, optimizer) skip early dialog check — they manage their own lifecycle
- Stale response counter ensures late responses from timed-out commands don't corrupt subsequent results

### 3.3 Python COM Backend (simulation_backend.py, ~9100 lines)

**Responsibilities:**
- COM communication with ExtendSim via `win32com.client.GetActiveObject`
- 80+ command handler functions mapped via dispatch table
- Input validation and parameter type coercion
- ModL command construction and execution
- Fire-and-forget threading for `simulation_run`
- JSON serialization with NaN/Infinity sanitization
- European decimal separator handling

**Design decisions:**
- `GetActiveObject("ExtendSim.Application")` connects to the running ExtendSim instance (no `CreateObject` — requires user to start ExtendSim manually)
- All variable access routes through `_set_var`/`_set_var_string` helpers enforcing the two-API pattern (VariableNumeric vs DialogVariable)
- Fire-and-forget simulation uses a background thread with `CoInitialize()` + separate `GetActiveObject()` for COM apartment safety
- `_escape_modl_string()` sanitizes all user input before ModL command construction to prevent injection

### 3.4 Dialog Watcher (dialog_watcher.py, ~280 lines)

**Responsibilities:**
- Windows UI Automation (UIA) to detect ExtendSim modal dialogs
- Read dialog text for error reporting
- Auto-dismiss by clicking OK/Close buttons
- Runs as a separate process to avoid blocking the main COM thread

**Design decisions:**
- Separate process avoids COM apartment threading conflicts
- Configurable poll interval and timeout
- Returns dialog text in JSON for inclusion in error messages

### 3.5 Advisor (advisor.ts, ~200 lines)

**Responsibilities:**
- Pure analysis functions operating on model state (no COM calls)
- Warning detection (unconnected blocks, Create without output, Activity without Queue)
- Suggestion generation (improvements, parameter tuning)
- Completion hints (what to add next)

### 3.6 Telemetry (telemetry.ts)

**Responsibilities:**
- Local-only JSONL event logging
- Session ID generation (random 6-hex-char)
- Event sequencing and counting
- File rotation at 10 MB
- Privacy-safe: no user data, file paths, or model content

---

## 4. Communication Protocols

### 4.1 AI Client ↔ MCP Server

**Protocol:** JSON-RPC 2.0 (MCP standard)

**stdio transport (default):**
- AI client spawns `node dist/index.js` as a child process
- Communication via stdin/stdout pipes
- No network stack involved
- Recommended for all clients except ChatGPT

**HTTP transport:**
- Express server on `localhost:<port>` (default 3001)
- Endpoint: `POST /mcp` (tool calls), `GET /mcp` (SSE), `DELETE /mcp` (session close)
- Session management via `mcp-session-id` header
- UUID session IDs generated server-side
- **No TLS** — localhost only. External exposure requires reverse proxy with HTTPS.

### 4.2 MCP Server ↔ Python Backend

**Protocol:** JSON over stdin/stdout (line-delimited)

**Request format:**
```json
{ "command": "model_open", "params": { "filePath": "C:/models/test.mox" } }
```

**Response format:**
```json
{ "success": true, "status": "success", "modelName": "test", "blockCount": 42 }
```

**Error response format:**
```json
{ "success": false, "status": "error", "errorCode": "COM_ERROR", "error": "Description", "suggestion": "Recovery hint" }
```

### 4.3 Python Backend ↔ ExtendSim

**Protocol:** COM/DCOM (Component Object Model)

- Uses `win32com.client.GetActiveObject("ExtendSim.Application")` to connect to the running ExtendSim instance
- All calls are synchronous on the COM thread (STA — Single-Threaded Apartment)
- Fire-and-forget operations use a background thread with `CoInitialize()` for a separate COM apartment
- `ExecuteModLCommand()` for ModL string execution
- Direct method calls for database, simulation setup, and block management

---

## 5. Security Architecture

### 5.1 Attack Surface Summary

| Surface | Exposure | Protocol | Authentication |
|---------|----------|----------|----------------|
| stdio transport | None (process-local) | stdin/stdout pipes | Process-level (parent process) |
| HTTP transport | localhost only | HTTP (no TLS) | Session ID (UUID) |
| Python subprocess | None (process-local) | stdin/stdout pipes | N/A |
| COM interface | Local machine | DCOM | Windows process-level |
| Telemetry | Local filesystem | File I/O | OS file permissions |

### 5.2 Network Exposure

**stdio mode (default):** Zero network exposure. The MCP server is a child process of the AI client, communicating exclusively via stdin/stdout pipes. No ports are opened. No listening sockets are created.

**HTTP mode:** Listens on `localhost:<port>` (default 3001). The server binds to the loopback interface only. There is no built-in TLS, authentication beyond session IDs, or rate limiting.

**Recommendation for HTTP mode:** Always deploy behind a reverse proxy (nginx, Cloudflare Tunnel, ngrok) with TLS termination when exposing to ChatGPT or any external client.

### 5.3 Input Validation

**MCP layer:** All tool parameters are validated using Zod schemas at the MCP SDK level. Invalid types, missing required fields, and unexpected parameters are rejected before reaching the backend.

**Python layer:**
- File paths are normalized (forward slashes for ExtendSim compatibility)
- ModL command strings are sanitized via `_escape_modl_string()` which escapes backslashes, double quotes, and parentheses before injection into ModL command templates
- Numeric inputs are type-coerced with explicit handling of NaN, Infinity, and locale-specific decimal separators
- Block IDs are validated against the active model
- Database indices are validated as numeric (not string names)

### 5.4 Command Injection Mitigation

The `execute_command` tool allows raw ModL command execution. This is the highest-risk tool:

- **ModL is sandboxed** — ModL runs inside ExtendSim's process space with no filesystem access, no network access, and no OS command execution capability
- **Input sanitization** — `_escape_modl_string()` prevents breakout from string contexts
- **Dangerous commands blocked** — `ExecuteMenuCommand(1)` through `ExecuteMenuCommand(4)` (which can kill ExtendSim) are explicitly blocked
- **AbortSilent()** outside simulation is blocked (kills ExtendSim)
- **ClearBlock(0)** is blocked (removes Executive block, corrupts model)

### 5.5 Data Privacy

| Data Type | Stored? | Where | Transmitted? |
|-----------|---------|-------|-------------|
| Model files | By user | User's filesystem | Never |
| Tool call names | Yes | Local telemetry JSONL | Never (unless user shares manually) |
| Tool parameters | Session log only (opt-in) | Local file | Never |
| Error messages | Yes | Telemetry + session log | Never |
| File paths | Never | — | — |
| Block labels | Never | — | — |
| Simulation data | Never | — | — |

**Telemetry is local-only.** The JSONL file is written to `temp/telemetry/telemetry.jsonl` and never transmitted. It records tool names, duration, error codes, and sequence numbers. No user-identifiable data, model content, or file paths are included.

### 5.6 Process Isolation

```
┌──────────────────────────────────────────────────────┐
│  User Session (Windows User)                         │
│                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐ │
│  │  AI Client   │  │  Node.js    │  │  Python      │ │
│  │  (parent)    │──│  MCP Server │──│  COM Backend │ │
│  └─────────────┘  └─────────────┘  └──────┬───────┘ │
│                                            │         │
│                                    ┌───────┴───────┐ │
│                                    │  ExtendSim    │ │
│                                    │  (COM server) │ │
│                                    └───────────────┘ │
└──────────────────────────────────────────────────────┘
```

All processes run under the same Windows user context. There is no privilege escalation. The MCP server inherits the permissions of the AI client that spawned it. ExtendSim runs as a regular user application.

### 5.7 Credential Management

The system stores and transmits **no credentials**. There are no API keys, tokens, passwords, or authentication secrets anywhere in the codebase or configuration.

- ExtendSim COM uses Windows process-level trust (same user, same machine)
- HTTP session IDs are random UUIDs, not authentication tokens
- Telemetry contains a random 6-hex-char session ID, not a user identifier

### 5.8 File System Access

The MCP server reads and writes files in these locations:

| Location | Access | Purpose |
|----------|--------|---------|
| `dist/` (install dir) | Read | Server code, reference JSON files |
| `temp/` | Read/Write | Telemetry, session logs, Python startup log |
| User-specified paths | Read/Write | Model files (via `model_open`, `model_save`, `db_import`, `db_export`) |

Model file paths are provided by the AI client (ultimately by the user). The server does not restrict which files can be opened — it relies on OS-level file permissions.

---

## 6. Data Flow Analysis

### 6.1 Typical Tool Call Flow

```
AI Client                MCP Server              Python Backend          ExtendSim
   │                         │                         │                      │
   │  tool_call(JSON-RPC)    │                         │                      │
   │────────────────────────►│                         │                      │
   │                         │  Zod validation         │                      │
   │                         │  safeToolCall()         │                      │
   │                         │                         │                      │
   │                         │  JSON command           │                      │
   │                         │────────────────────────►│                      │
   │                         │                         │  COM call            │
   │                         │                         │─────────────────────►│
   │                         │                         │                      │
   │                         │                         │  COM response        │
   │                         │                         │◄─────────────────────│
   │                         │  JSON response          │                      │
   │                         │◄────────────────────────│                      │
   │                         │                         │                      │
   │                         │  recordToolCall()       │                      │
   │                         │  sessionLog()           │                      │
   │                         │                         │                      │
   │  tool_result(JSON-RPC)  │                         │                      │
   │◄────────────────────────│                         │                      │
```

### 6.2 Fire-and-Forget Flow (simulation_run)

```
AI Client                MCP Server              Python Backend          ExtendSim
   │                         │                         │                      │
   │  simulation_run()       │                         │                      │
   │────────────────────────►│                         │                      │
   │                         │  JSON command           │                      │
   │                         │────────────────────────►│                      │
   │                         │                         │  Background thread   │
   │                         │                         │  CoInitialize()      │
   │                         │                         │  GetActiveObject()   │
   │                         │                         │──────────────────┐   │
   │                         │  { started: true }      │                  │   │
   │                         │◄────────────────────────│                  │   │
   │  { started: true }     │                         │  ExecuteMenu     │   │
   │◄────────────────────────│                         │  Command(6000)  │   │
   │                         │                         │─────────────────┼──►│
   │                         │                         │                  │   │
   │  simulation_status()   │                         │                  │   │
   │────────────────────────►│─────────────────────────►│                  │   │
   │  { running: true }     │◄─────────────────────────│ check phase     │   │
   │◄────────────────────────│                         │                  │   │
   │         ...             │         ...              │  ...running...   │   │
   │                         │                         │                  │   │
   │  simulation_status()   │                         │                  │   │
   │────────────────────────►│─────────────────────────►│                  │   │
   │  { running: false }    │◄─────────────────────────│ completed        │   │
   │◄────────────────────────│                         │◄─────────────────┘   │
   │                         │                         │                      │
   │  simulation_get_results│                         │                      │
   │────────────────────────►│─────────────────────────►│─────────────────────►│
   │  { results... }        │◄─────────────────────────│◄─────────────────────│
   │◄────────────────────────│                         │                      │
```

### 6.3 Timeout and Dialog Recovery Flow

```
AI Client                MCP Server              Python Backend          ExtendSim
   │                         │                         │                      │
   │  some_tool_call()      │                         │                      │
   │────────────────────────►│                         │                      │
   │                         │  JSON command           │                      │
   │                         │────────────────────────►│                      │
   │                         │                         │  COM call            │
   │                         │                         │─────────────────────►│
   │                         │                         │                      │
   │                         │  1s: early dialog check │               ┌──────┤
   │                         │  spawn dialog_watcher.py│               │Dialog│
   │                         │  → no dialog found      │               │shown │
   │                         │                         │               └──────┤
   │                         │                         │                      │
   │                         │  10s: TIMEOUT           │                      │
   │                         │  spawn dialog_watcher.py│                      │
   │                         │       ──────────────────┼──UIAutomation──────►│
   │                         │       dialog text found │               click  │
   │                         │       dialog dismissed  │               OK     │
   │                         │                         │                      │
   │  { error, dialogText } │                         │                      │
   │◄────────────────────────│                         │                      │
```

---

## 7. Error Handling and Resilience

### 7.1 Error Classification

| Error Code | Origin | Severity | Auto-Recovery |
|------------|--------|----------|---------------|
| `COM_ERROR` | Python/ExtendSim | Medium | Retry up to 2x |
| `BLOCK_NOT_FOUND` | Python | Low | None (user error) |
| `CONNECTION_FAILED` | Python | Medium | None |
| `NOT_CONNECTED` | Python | High | Auto-reconnect |
| `MISSING_PARAMETER` | TypeScript (Zod) | Low | None (client error) |
| `EXTENDSIM_NOT_RUNNING` | Python | High | User must start ExtendSim |
| `TIMEOUT` | TypeScript | Medium | Dialog dismissal + retry |
| `INVALID_JSON` | TypeScript | High | Discard + retry |
| `TOOL_ERROR` | TypeScript | Medium | None |

### 7.2 Recovery Mechanisms

**Python process death:**
1. Heartbeat detects exit (60-second interval)
2. Current request is failed
3. Python process is restarted
4. Queued requests are retried
5. Max 2 consecutive retries before permanent failure

**COM connection loss:**
1. Python catches `com_error` exception
2. Returns structured error with `COM_ERROR` code
3. Next call attempts `GetActiveObject()` reconnection

**Dialog blocking:**
1. Early dialog check at 1 second
2. If found: dismiss and return dialog text as error
3. If not found: wait for main timeout
4. On timeout: spawn dialog watcher again
5. Dialog text included in error response for debugging

**Stale responses:**
After a timeout, the Python process may still produce a response for the timed-out command. The stale response counter ensures these late responses are discarded and don't corrupt the response for the next command.

### 7.3 Graceful Shutdown

On `SIGINT` or process exit:
1. Close telemetry write stream
2. Terminate Python subprocess
3. Close all HTTP transport sessions (if HTTP mode)
4. Exit with code 0

---

## 8. Deployment Architecture

### 8.1 stdio Deployment (Recommended)

```
┌─────────────────────────────────────────┐
│  User's Machine                         │
│                                         │
│  ┌─────────────┐                        │
│  │  AI Client   │                        │
│  │  (e.g.      │                        │
│  │  Claude Code)│                        │
│  └──────┬──────┘                        │
│         │ spawn child process            │
│         ▼                                │
│  ┌─────────────┐    ┌─────────────────┐ │
│  │  Node.js    │───►│  Python         │ │
│  │  MCP Server │    │  COM Backend    │ │
│  └─────────────┘    └────────┬────────┘ │
│                              │ COM      │
│                       ┌──────┴──────┐   │
│                       │  ExtendSim  │   │
│                       └─────────────┘   │
└─────────────────────────────────────────┘
  No network. No ports. No firewall rules.
```

### 8.2 HTTP Deployment (ChatGPT)

```
┌──────────────────────────────────────────────────────┐
│  User's Machine                                      │
│                                                      │
│  ┌──────────────┐                                    │
│  │  Windows     │                                    │
│  │  Service     │                                    │
│  │  (nssm)      │                                    │
│  └──────┬───────┘                                    │
│         │                                            │
│  ┌──────┴───────┐    ┌─────────────────┐            │
│  │  Node.js     │───►│  Python         │            │
│  │  MCP Server  │    │  COM Backend    │            │
│  │  HTTP:3001   │    └────────┬────────┘            │
│  └──────┬───────┘             │ COM                 │
│         │              ┌──────┴──────┐              │
│         │              │  ExtendSim  │              │
│         │              └─────────────┘              │
│  ┌──────┴───────┐                                    │
│  │  Reverse     │ ◄── TLS termination                │
│  │  Proxy       │     (ngrok / Cloudflare / nginx)   │
│  │  HTTPS:443   │                                    │
│  └──────┬───────┘                                    │
└─────────┼────────────────────────────────────────────┘
          │ HTTPS
          ▼
  ┌───────────────┐
  │  ChatGPT      │
  │  (external)   │
  └───────────────┘
```

### 8.3 Windows Service

When installed as a Windows Service:
- Service name: `SimulationsMCP`
- Managed via `net start` / `net stop` or `services.msc`
- Environment: `MCP_TRANSPORT=http`, `MCP_PORT=3001`
- Logs: `[Install Dir]/logs/`

---

## 9. Performance Characteristics

### 9.1 Latency Profile

| Operation Type | Typical Latency | Max Timeout |
|----------------|-----------------|-------------|
| Reference lookups (TS-only) | < 5 ms | N/A |
| Simple COM calls (get/set value) | 50–200 ms | 10 s |
| File operations (open, save) | 500 ms – 5 s | 30 s |
| Block list (large model, 24k blocks) | 30–120 s | 120 s |
| Simulation run (fire-and-forget start) | 100–500 ms | 10 s |
| Simulation run (blocking, small model) | 1–30 s | 5 min |
| Scenario Manager (full run) | 1–60 min | 10 min (timeout) |

### 9.2 Memory Profile

| Component | Typical Memory |
|-----------|----------------|
| Node.js MCP Server | 50–150 MB (includes lazy-loaded JSON: pattern_library.json is 1.5 MB) |
| Python COM Backend | 30–80 MB |
| Dialog Watcher (on-demand) | 20–40 MB |

### 9.3 Scalability Constraints

- **Single COM thread** — All COM calls are serialized. No concurrent ExtendSim operations.
- **Single ExtendSim instance** — `GetActiveObject` connects to one running instance. Multiple MCP sessions share the same ExtendSim.
- **No horizontal scaling** — Architecture is inherently single-machine, single-user.
- **Large model limits** — Models with 24k+ blocks require extended timeouts for block enumeration.

---

## 10. Trust Model and Threat Analysis

### 10.1 Trust Boundaries

```
┌─ Trust Boundary 1: User's Machine ─────────────────────────┐
│                                                              │
│  ┌─ Trust Boundary 2: MCP Protocol ──────────────────────┐  │
│  │  AI Client ←→ MCP Server                              │  │
│  │  - Input validated via Zod schemas                     │  │
│  │  - Structured error responses                         │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Trust Boundary 3: Process Boundary ──────────────────┐  │
│  │  Node.js ←→ Python (stdin/stdout JSON)                │  │
│  │  - JSON parsing validation                            │  │
│  │  - Timeout enforcement                                │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Trust Boundary 4: COM Interface ─────────────────────┐  │
│  │  Python ←→ ExtendSim (COM/DCOM)                       │  │
│  │  - ModL input sanitization                            │  │
│  │  - Dangerous command blocking                         │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 10.2 Threat Analysis

| Threat | Risk | Mitigation |
|--------|------|------------|
| **Prompt injection via AI client** | Medium | Zod schema validation rejects unexpected parameters. ModL string sanitization prevents command injection into ExtendSim. |
| **ModL command injection** | Low | `_escape_modl_string()` escapes quotes, backslashes, and parentheses. ModL itself has no filesystem/network/OS access. |
| **Denial of service (ExtendSim crash)** | Medium | Dangerous `ExecuteMenuCommand` IDs (1–4) are blocked. `AbortSilent()` outside simulation is blocked. `ClearBlock(0)` is blocked. Timeouts prevent hangs. |
| **Local privilege escalation** | Very Low | All processes run as the same user. No setuid, no service accounts with elevated privileges. |
| **Data exfiltration** | Very Low | No outbound network calls. Telemetry is local-only. No cloud APIs. |
| **Session hijacking (HTTP mode)** | Low | Session IDs are random UUIDs. Localhost-only binding. Reverse proxy should add authentication. |
| **Supply chain attack** | Low | Minimal runtime dependencies (see Section 12). No auto-update mechanism. |
| **File system traversal** | Medium | Model file paths from AI client are passed to ExtendSim without path restriction. OS file permissions are the only guard. |
| **NaN/Infinity serialization** | Low | All numeric outputs sanitized before JSON serialization. Python `allow_nan=False` enforced. |

### 10.3 Residual Risks

1. **`execute_command` allows arbitrary ModL execution** — While ModL is sandboxed within ExtendSim (no OS access), it can modify any aspect of the open model. This is by design — it's the escape hatch for operations not covered by dedicated tools.

2. **HTTP mode has no built-in authentication** — Session IDs are not secrets. Any process on localhost can connect. For production HTTP deployments, always use a reverse proxy with authentication.

3. **COM interface reliability** — ExtendSim's COM interface can hang or crash on certain operations (documented in codebase as "quirks"). The dialog watcher and retry mechanisms mitigate but cannot eliminate this risk.

4. **Single-user design** — Multiple AI clients connecting simultaneously share the same ExtendSim instance and Python backend. Operations from different clients are serialized but not isolated at the model level.

---

## 11. Configuration Reference

### 11.1 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_TRANSPORT` | `stdio` | Transport mode: `stdio` or `http` |
| `MCP_PORT` | `3001` | HTTP port (only when `MCP_TRANSPORT=http`) |
| `MCP_SESSION_LOG` | `0` | Enable session logging: `1` to enable |
| `EXTENDSIM_DEBUG` | unset | Enable debug logging in Python backend |

### 11.2 Timeout Configuration

Timeouts are defined in `backend.ts` and cannot be changed without rebuilding. Current values:

| Category | Timeout | Commands |
|----------|---------|----------|
| Default | 10 s | Most commands |
| Medium | 30 s | File I/O, bulk operations, discoveries |
| Long | 60 s | `block_configure`, `simulation_get_results` |
| Extended | 120 s | `extendsim_start`, `model_extract`, `block_list` |
| Very long | 300 s (5 min) | `simulation_run` (blocking mode) |
| Maximum | 600 s (10 min) | Multi-run, SM, optimizer (blocking mode) |

### 11.3 File Locations

| File | Purpose |
|------|---------|
| `dist/index.js` | Server entry point |
| `dist/simulation_backend.py` | Python COM backend |
| `dist/dialog_watcher.py` | Dialog auto-dismisser |
| `dist/*.json` | Reference data (7 files) |
| `temp/telemetry/telemetry.jsonl` | Local telemetry |
| `temp/mcp_session.log` | Session log (opt-in) |
| `temp/python_startup.log` | Python startup diagnostics |

---

## 12. Dependencies and Supply Chain

### 12.1 Runtime Dependencies (Node.js)

| Package | Version | Purpose | Risk Assessment |
|---------|---------|---------|-----------------|
| `@modelcontextprotocol/sdk` | ^1.0.0 | MCP protocol implementation | Official Anthropic package |
| `express` | ^5.2.1 | HTTP transport (only when `MCP_TRANSPORT=http`) | Widely used, well-audited |
| `zod` | ^3.22.0 | Input schema validation | Widely used, no native code |
| `node-windows` | ^1.0.0-beta.8 | Windows Service management (installer only) | Windows-specific |

### 12.2 Runtime Dependencies (Python)

| Package | Version | Purpose | Risk Assessment |
|---------|---------|---------|-----------------|
| `pywin32` | Latest | COM interface to ExtendSim | Microsoft-maintained bridge |

### 12.3 Dev Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `typescript` | ^5.3.0 | TypeScript compiler |
| `vitest` | ^1.6.1 | Test framework |
| `ts-node` | ^10.9.0 | TypeScript execution |
| `rimraf` | ^5.0.0 | Cross-platform rm -rf |
| `@types/node` | ^20.0.0 | Node.js type definitions |
| `@types/express` | ^5.0.6 | Express type definitions |

### 12.4 No Auto-Update

The server has no auto-update mechanism. Updates are manual (new installer or `git pull` + `npm run build`). This eliminates supply chain risks from automatic dependency resolution at runtime.

---

---

**Trademark Notice:** ExtendSim is a registered trademark of Imagine That, Inc., a subsidiary of ANDRITZ Inc. This product is an independent third-party integration and is not affiliated with, endorsed by, or sponsored by Imagine That, Inc. or ANDRITZ. All other trademarks are the property of their respective owners.

*Copyright (c) 2025–2026 Duke Systems AB*
