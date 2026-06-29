/**
 * Dispatch coverage tests — verify all 99 tools have complete wiring.
 *
 * Reads index.ts, backend.ts, and simulation_backend.py as text and uses
 * regex to extract tool names, export names, and COMMANDS keys.
 * Pure static analysis — no ExtendSim needed.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SRC_DIR = join(__dirname, "..", "..", "src");

let indexSource: string;
let backendSource: string;
let pythonSource: string;

let toolNames: string[];
let toolDescriptions: Map<string, string>;
let backendExports: string[];
let commandsKeys: string[];

beforeAll(() => {
  indexSource = readFileSync(join(SRC_DIR, "index.ts"), "utf-8");
  backendSource = readFileSync(join(SRC_DIR, "backend.ts"), "utf-8");
  pythonSource = readFileSync(join(SRC_DIR, "simulation_backend.py"), "utf-8");

  // Extract server.tool() names — pattern: server.tool(\n  "tool_name",
  const toolRegex = /server\.tool\(\s*"([^"]+)"/g;
  toolNames = [];
  let match;
  while ((match = toolRegex.exec(indexSource)) !== null) {
    toolNames.push(match[1]);
  }

  // Extract tool descriptions — pattern: server.tool(\n  "name",\n  "description" or `description`
  const descRegex = /server\.tool\(\s*"([^"]+)",\s*(?:"([^"]+)"|`([^`]+)`)/g;
  toolDescriptions = new Map();
  while ((match = descRegex.exec(indexSource)) !== null) {
    toolDescriptions.set(match[1], match[2] || match[3]);
  }

  // Extract backend.ts exported async functions — pattern: export async function name(
  const exportRegex = /export\s+(?:async\s+)?function\s+(\w+)\s*\(/g;
  backendExports = [];
  while ((match = exportRegex.exec(backendSource)) !== null) {
    backendExports.push(match[1]);
  }

  // Extract COMMANDS keys from Python — pattern: "command_name": lambda
  const cmdRegex = /"(\w+)":\s*lambda/g;
  commandsKeys = [];
  while ((match = cmdRegex.exec(pythonSource)) !== null) {
    commandsKeys.push(match[1]);
  }
});

describe("Tool Registration Coverage", () => {
  it("should have exactly 99 registered tools", () => {
    expect(toolNames.length).toBe(99);
  });

  it("should have no duplicate tool names in index.ts", () => {
    const seen = new Set<string>();
    const duplicates: string[] = [];
    for (const name of toolNames) {
      if (seen.has(name)) duplicates.push(name);
      seen.add(name);
    }
    expect(duplicates).toEqual([]);
  });

  it("should have a description string for every tool (4-arg form)", () => {
    const missingDesc: string[] = [];
    for (const name of toolNames) {
      if (!toolDescriptions.has(name)) {
        missingDesc.push(name);
      }
    }
    expect(missingDesc).toEqual([]);
  });

  it("should have non-empty descriptions for all tools", () => {
    const emptyDesc: string[] = [];
    for (const [name, desc] of toolDescriptions) {
      if (!desc || desc.trim().length === 0) {
        emptyDesc.push(name);
      }
    }
    expect(emptyDesc).toEqual([]);
  });
});

describe("Backend Export Coverage", () => {
  it("should have no duplicate export names in backend.ts", () => {
    const seen = new Set<string>();
    const duplicates: string[] = [];
    for (const name of backendExports) {
      if (seen.has(name)) duplicates.push(name);
      seen.add(name);
    }
    expect(duplicates).toEqual([]);
  });

  it("should have backend exports for infrastructure functions", () => {
    expect(backendExports).toContain("initBackend");
    expect(backendExports).toContain("shutdownBackend");
  });
});

describe("Python COMMANDS Coverage", () => {
  it("should have no duplicate COMMANDS keys", () => {
    const seen = new Set<string>();
    const duplicates: string[] = [];
    for (const key of commandsKeys) {
      if (seen.has(key)) duplicates.push(key);
      seen.add(key);
    }
    expect(duplicates).toEqual([]);
  });

  it("should have COMMANDS entries for all critical tool categories", () => {
    const criticalCommands = [
      "extendsim_status",
      "model_open",
      "model_save",
      "block_add",
      "block_connect",
      "block_set_value",
      "block_get_value",
      "simulation_run",
      "simulation_stop",
      "block_configure",
      "db_list",
      "db_get_records",
    ];
    for (const cmd of criticalCommands) {
      expect(commandsKeys).toContain(cmd);
    }
  });

  it("should have more COMMANDS entries than MCP tools (includes legacy consolidated tools)", () => {
    // Old tools like activity_set_delay still exist in COMMANDS but have no MCP registration
    expect(commandsKeys.length).toBeGreaterThanOrEqual(toolNames.length);
  });
});

describe("Cross-layer Consistency", () => {
  // Tools handled purely in TypeScript (read local JSON, no Python call)
  const TS_ONLY_TOOLS = new Set([
    "MCP_init",
    "modl_search",
    "block_search",
    "dialog_search",
    "simulation_type_guide",
    "modeling_guide",
    "pattern_search",
    "model_advisor",
    "telemetry_control",
  ]);

  // Map of tool name -> expected sendCommand string (snake_case)
  const TOOL_TO_COMMAND_OVERRIDES: Record<string, string> = {
    extendsim_get_license: "detect_license",
  };

  it("should have a sendCommand call in backend.ts for each Python-backed tool", () => {
    const missing: string[] = [];
    for (const tool of toolNames) {
      if (TS_ONLY_TOOLS.has(tool)) continue;
      const expectedCmd = TOOL_TO_COMMAND_OVERRIDES[tool] || tool;
      const pattern = `sendCommand("${expectedCmd}"`;
      if (!backendSource.includes(pattern)) {
        missing.push(`${tool} -> sendCommand("${expectedCmd}")`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("should have a COMMANDS entry for each Python-backed tool", () => {
    const missing: string[] = [];
    const commandsSet = new Set(commandsKeys);
    for (const tool of toolNames) {
      if (TS_ONLY_TOOLS.has(tool)) continue;
      const expectedCmd = TOOL_TO_COMMAND_OVERRIDES[tool] || tool;
      if (!commandsSet.has(expectedCmd)) {
        missing.push(`${tool} -> COMMANDS["${expectedCmd}"]`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("TS-only tools should NOT have sendCommand calls", () => {
    for (const tool of TS_ONLY_TOOLS) {
      const pattern = `sendCommand("${tool}"`;
      expect(backendSource.includes(pattern), `${tool} should be TS-only but has sendCommand`).toBe(false);
    }
  });

  it("every safeToolCall invocation should have a tool name string as first arg", () => {
    // Match safeToolCall( followed by anything that is NOT a double-quote (wrong pattern)
    const badCalls = indexSource.match(/safeToolCall\(\s*\(\)/g) || [];
    expect(badCalls.length, "Found safeToolCall(() => ...) without tool name").toBe(0);
  });
});
