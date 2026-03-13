/**
 * Simulations MCP Server - TypeScript Implementation
 *
 * MCP Server that exposes ExtendSim functionality for AI assistants.
 * Uses Python backend via subprocess for COM integration.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { isInitializeRequest } from "@modelcontextprotocol/sdk/types.js";
import { randomUUID } from "node:crypto";
import { z } from "zod";
import * as backend from "./backend.js";
import { readFileSync } from "fs";
import { join } from "path";
import { analyzeWarnings, analyzeSuggestions, analyzeCompletions } from "./advisor.js";
import { initTelemetry, recordToolCall, getStatus as getTelemetryStatus, closeTelemetry, updateEnvInfo } from "./telemetry.js";
import { appendFileSync, mkdirSync, existsSync } from "fs";

// Session logging — opt-in via MCP_SESSION_LOG=1 env var OR temp/mcp_session_enable marker file.
// Writes all tool calls to temp/mcp_session.log for multi-AI client testing.
const SESSION_LOG_DIR = join(__dirname, "..", "..", "..", "temp");
const SESSION_LOG_PATH = join(SESSION_LOG_DIR, "mcp_session.log");
const SESSION_LOG_ENABLED = process.env.MCP_SESSION_LOG === "1"
  || existsSync(join(SESSION_LOG_DIR, "mcp_session_enable"));

function sessionLog(toolName: string, durationMs: number, params: Record<string, unknown> | undefined, result: any) {
  if (!SESSION_LOG_ENABLED) return;
  try {
    if (!existsSync(SESSION_LOG_DIR)) mkdirSync(SESSION_LOG_DIR, { recursive: true });
    const ts = new Date().toISOString();
    const status = result?.status || (result?.success ? "success" : "unknown");
    const resultStr = JSON.stringify(result, null, 0);
    const truncated = resultStr.length > 2000 ? resultStr.slice(0, 2000) + "...[truncated]" : resultStr;
    const line = `[${ts}] ${toolName} (${Math.round(durationMs)}ms) status=${status}\n  params: ${JSON.stringify(params || {})}\n  result: ${truncated}\n\n`;
    appendFileSync(SESSION_LOG_PATH, line);
  } catch { /* never fail on logging */ }
}

interface ModlFunction {
  description: string;
  signature: string;
  arguments?: { name: string; type: string; description: string }[];
  returns: string;
  obsolete?: boolean;
}

interface ModlCategory {
  description: string;
  functions: Record<string, ModlFunction>;
}

interface ModlReference {
  version: string;
  description: string;
  returnTypes: Record<string, string>;
  dialogItemTypes: Record<string, string>;
  categories: Record<string, ModlCategory>;
}

interface BlockConnector {
  direction: string;
  usage: string;
  isArray?: boolean;
  connectTo?: string;
}

interface BlockInfo {
  description: string;
  connectors?: Record<string, BlockConnector>;
  dialogVariables?: Record<string, string>;
  patterns?: string[];
}

interface BlockCategory {
  blocks: Record<string, BlockInfo | string>;
}

interface BlockLibrary {
  name: string;
  description: string;
  categories: Record<string, BlockCategory>;
}

interface BlockReference {
  version: string;
  description: string;
  libraries: Record<string, BlockLibrary>;
}

interface DialogVariable {
  dialogId: number;
  name: string;
  type: string;
  description: string;
}

interface DialogBlockInfo {
  description: string;
  help?: {
    summary: string;
    tabs: string[];
  };
  connectors?: Record<string, Record<string, string>>;
  variables?: {
    inputs?: DialogVariable[];
    outputs?: DialogVariable[];
  };
}

interface DialogLibrary {
  name: string;
  blocks: Record<string, DialogBlockInfo>;
}

interface DialogReference {
  version: string;
  generated: string;
  description: string;
  typeCodeReference: Record<string, string>;
  libraries: Record<string, DialogLibrary>;
}

// Load references at startup - lazy loading to avoid issues
let modlReference: ModlReference | null = null;
let blockReference: BlockReference | null = null;
let dialogReference: DialogReference | null = null;

function loadReferences(): void {
  if (modlReference !== null) return; // Already loaded

  try {
    modlReference = JSON.parse(readFileSync(join(__dirname, "modl_reference.json"), "utf-8"));
    blockReference = JSON.parse(readFileSync(join(__dirname, "block_reference.json"), "utf-8"));
    dialogReference = JSON.parse(readFileSync(join(__dirname, "dialog_reference.json"), "utf-8"));
  } catch (e) {
    console.error("Warning: Could not load reference files:", e);
    modlReference = { version: "0", description: "", returnTypes: {}, dialogItemTypes: {}, categories: {} };
    blockReference = { version: "0", description: "", libraries: {} };
    dialogReference = { version: "0", generated: "", description: "", typeCodeReference: {}, libraries: {} };
  }
}

function getModlReference(): ModlReference {
  loadReferences();
  return modlReference!;
}

function getBlockReference(): BlockReference {
  loadReferences();
  return blockReference!;
}

function getDialogReference(): DialogReference {
  loadReferences();
  return dialogReference!;
}

// Lazy-loaded simulation type guide
let simulationTypeGuide: Record<string, unknown> | null = null;

function getSimulationTypeGuide(): Record<string, unknown> {
  if (simulationTypeGuide === null) {
    try {
      simulationTypeGuide = JSON.parse(readFileSync(join(__dirname, "simulation_type_guide.json"), "utf-8"));
    } catch (e) {
      console.error("Warning: Could not load simulation_type_guide.json:", e);
      simulationTypeGuide = { error: "Could not load simulation type guide" };
    }
  }
  return simulationTypeGuide!;
}

// Lazy-loaded modeling guides
let modelingGuides: Record<string, unknown> | null = null;

function getModelingGuides(): Record<string, unknown> {
  if (modelingGuides === null) {
    try {
      modelingGuides = JSON.parse(readFileSync(join(__dirname, "modeling_guides.json"), "utf-8"));
    } catch (e) {
      console.error("Warning: Could not load modeling_guides.json:", e);
      modelingGuides = { error: "Could not load modeling guides" };
    }
  }
  return modelingGuides!;
}

// Lazy-loaded pattern library
let patternLibrary: Record<string, unknown> | null = null;

function getPatternLibrary(): Record<string, unknown> {
  if (patternLibrary === null) {
    try {
      patternLibrary = JSON.parse(readFileSync(join(__dirname, "pattern_library.json"), "utf-8"));
    } catch (e) {
      console.error("Warning: Could not load pattern_library.json:", e);
      patternLibrary = { error: "Could not load pattern library" };
    }
  }
  return patternLibrary!;
}

// ============================================================================
// TOOL RESPONSE HELPER
// ============================================================================

/**
 * Wraps a backend result into an MCP tool response.
 * Sets isError: true when the result has status "error", so the AI client
 * knows the call failed and can read the structured error (including dialog text).
 */
function toolResponse(result: any) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(result) }],
    ...(result?.status === "error" ? { isError: true } : {}),
  };
}

/** Wraps a backend call with try/catch, telemetry recording, and returns toolResponse.
 * Usage: return safeToolCall("model_open", () => backend.modelOpen({filePath}), {filePath})
 */
async function safeToolCall(toolName: string, fn: () => Promise<any>, params?: Record<string, unknown>) {
  const startTime = performance.now();
  try {
    const result = await fn();
    const durationMs = performance.now() - startTime;
    recordToolCall(toolName, startTime, result, params);
    sessionLog(toolName, durationMs, params, result);
    return toolResponse(result);
  } catch (e: any) {
    const errorResult = {
      status: "error",
      errorCode: "TOOL_ERROR",
      error: e?.message || String(e),
    };
    const durationMs = performance.now() - startTime;
    recordToolCall(toolName, startTime, errorResult, params);
    sessionLog(toolName, durationMs, params, errorResult);
    return toolResponse(errorResult);
  }
}

// ============================================================================
// SEARCH HELPERS
// ============================================================================

interface ModlSearchResult {
  name: string;
  category: string;
  signature: string;
  description: string;
  arguments?: { name: string; type: string; description: string }[];
  returns: string;
  returnType: string;
  obsolete?: boolean;
}

function searchModl(query: string, maxResults: number = 10): { results: ModlSearchResult[]; totalMatches: number; truncated: boolean } {
  const ref = getModlReference();
  const results: ModlSearchResult[] = [];
  const queryLower = query.toLowerCase();
  let totalMatches = 0;

  for (const [categoryName, category] of Object.entries(ref.categories)) {
    for (const [funcName, func] of Object.entries(category.functions)) {
      // Search in function name, description, and arguments
      const nameMatch = funcName.toLowerCase().includes(queryLower);
      const descMatch = func.description.toLowerCase().includes(queryLower);
      const argMatch = func.arguments?.some(
        arg => arg.name.toLowerCase().includes(queryLower) ||
               arg.description.toLowerCase().includes(queryLower)
      );

      if (nameMatch || descMatch || argMatch) {
        totalMatches++;
        if (results.length < maxResults) {
          results.push({
            name: funcName,
            category: categoryName,
            signature: func.signature,
            description: func.description,
            arguments: func.arguments,
            returns: func.returns,
            returnType: ref.returnTypes[func.returns] || func.returns,
            obsolete: func.obsolete
          });
        }
      }
    }
  }

  return { results, totalMatches, truncated: totalMatches > results.length };
}

interface BlockSearchResult {
  library: string;
  category: string;
  name: string;
  description: string;
  connectors?: Record<string, BlockConnector>;
  patterns?: string[];
}

function searchBlocks(query: string, library?: string, maxResults: number = 10): { results: BlockSearchResult[]; totalMatches: number; truncated: boolean } {
  const ref = getBlockReference();
  const results: BlockSearchResult[] = [];
  const queryLower = query.toLowerCase();
  let totalMatches = 0;

  for (const [libName, lib] of Object.entries(ref.libraries)) {
    // Skip if library filter is specified and doesn't match
    if (library && !libName.toLowerCase().includes(library.toLowerCase())) {
      continue;
    }

    for (const [catName, category] of Object.entries(lib.categories)) {
      for (const [blockName, block] of Object.entries(category.blocks)) {
        // Handle both string descriptions and full block objects
        const blockInfo = typeof block === "string"
          ? { description: block } as BlockInfo
          : block;

        // Search in block name, description, connectors, and patterns
        const nameMatch = blockName.toLowerCase().includes(queryLower);
        const descMatch = blockInfo.description.toLowerCase().includes(queryLower);
        const connectorMatch = blockInfo.connectors && Object.keys(blockInfo.connectors).some(
          conn => conn.toLowerCase().includes(queryLower)
        );
        const patternMatch = blockInfo.patterns?.some(
          p => p.toLowerCase().includes(queryLower)
        );

        if (nameMatch || descMatch || connectorMatch || patternMatch) {
          totalMatches++;
          if (results.length < maxResults) {
            results.push({
              library: libName,
              category: catName,
              name: blockName,
              description: blockInfo.description,
              connectors: blockInfo.connectors,
              patterns: blockInfo.patterns
            });
          }
        }
      }
    }
  }

  return { results, totalMatches, truncated: totalMatches > results.length };
}

interface DialogSearchResult {
  library: string;
  block: string;
  dialogId: number;
  name: string;
  type: string;
  description: string;
  category: "input" | "output";
}

function searchDialogs(query: string, block?: string, maxResults: number = 10): { results: DialogSearchResult[]; totalMatches: number; truncated: boolean } {
  const ref = getDialogReference();
  const results: DialogSearchResult[] = [];
  const queryLower = query.toLowerCase();
  let totalMatches = 0;

  for (const [libName, lib] of Object.entries(ref.libraries)) {
    for (const [blockName, blockInfo] of Object.entries(lib.blocks)) {
      // Skip if block filter is specified and doesn't match
      if (block && !blockName.toLowerCase().includes(block.toLowerCase())) {
        continue;
      }

      // Search in inputs
      if (blockInfo.variables?.inputs) {
        for (const variable of blockInfo.variables.inputs) {
          const nameMatch = variable.name.toLowerCase().includes(queryLower);
          const descMatch = variable.description.toLowerCase().includes(queryLower);
          const typeMatch = variable.type.toLowerCase().includes(queryLower);

          if (nameMatch || descMatch || typeMatch) {
            totalMatches++;
            if (results.length < maxResults) {
              results.push({
                library: libName,
                block: blockName,
                dialogId: variable.dialogId,
                name: variable.name,
                type: variable.type,
                description: variable.description,
                category: "input"
              });
            }
          }
        }
      }

      // Search in outputs
      if (blockInfo.variables?.outputs) {
        for (const variable of blockInfo.variables.outputs) {
          const nameMatch = variable.name.toLowerCase().includes(queryLower);
          const descMatch = variable.description.toLowerCase().includes(queryLower);
          const typeMatch = variable.type.toLowerCase().includes(queryLower);

          if (nameMatch || descMatch || typeMatch) {
            totalMatches++;
            if (results.length < maxResults) {
              results.push({
                library: libName,
                block: blockName,
                dialogId: variable.dialogId,
                name: variable.name,
                type: variable.type,
                description: variable.description,
                category: "output"
              });
            }
          }
        }
      }
    }
  }

  return { results, totalMatches, truncated: totalMatches > results.length };
}

// Read version from package.json dynamically (C5 fix)
const packageJsonPath = join(__dirname, "..", "package.json");
let serverVersion = "1.9.3";
try {
  const pkg = JSON.parse(readFileSync(packageJsonPath, "utf-8"));
  serverVersion = pkg.version || serverVersion;
} catch {
  // Fallback to hardcoded version
}

const server = new McpServer({
  name: "simulations-mcp-server",
  version: serverVersion
});

// ============================================================================
// GUIDE AND REFERENCE TOOLS
// ============================================================================

server.tool(
  "MCP_init",
  "Initialize the ExtendSim MCP session. Returns critical usage rules, workflow tips, and license info. Run this FIRST in every session.",
  {},
  async () => {
    const startTime = performance.now();
    const guideContent = {
      welcome: "ExtendSim MCP - RUN THIS FIRST!",
      critical_rules: [
        {
          topic: "1. NEVER invent ModL functions — ALWAYS search first!",
          rule: "ALWAYS use modl_search to verify a ModL function exists before using it in execute_command. ExtendSim ModL has non-obvious function names and many functions you might guess do NOT exist. Using a non-existent function can crash ExtendSim.",
          wrong: "NEVER guess or assume a ModL function name. Functions like GetMaxBlockNumber(), GetBlockPosition(), GetConnectedBlock(), MakeStringFromValue(), DBGetIndex() do NOT exist.",
          solution: "Run modl_search('keyword') first. Similarly, use block_search before block_add and dialog_search before block_set_value.",
          example: "modl_search('block position') → finds GetBlockTypePosition(). modl_search('array size') → finds GAGetRows(), GAGetCols()."
        },
        {
          topic: "2. Connection direction",
          rule: "Connections ALWAYS go from OUT-connector to IN-connector",
          example: "block_connect(sourceBlock, 'ItemOut', targetBlock, 'ItemIn')"
        },
        {
          topic: "3. Activity delay — use block_configure",
          rule: "ALWAYS use block_configure to set Activity delay. Use delayType and value/distribution params.",
          wrong: "Do NOT try to set delay directly via block_set_value on Activity dialog variables",
          pattern: "block_configure(blockId=activityId, config={delayType:'fixed', value:10}) or config={delayType:'distribution', distribution:'exponential', arg1:5}"
        },
        {
          topic: "4. Queue block required",
          rule: "There MUST be at least one Queue block between Create→Activity and between Activity→Activity",
          wrong: "NEVER connect Create directly to Activity or Activity directly to Activity",
          pattern: "Create → Queue → Activity → Queue → Activity → Exit"
        }
      ],
      other_tips: [
        {
          topic: "Simulation time",
          problem: "AI often guesses wrong syntax for SetRunParameter",
          solution: "Use simulation_setup_set(endTime=1200) instead of execute_command",
          correct: "simulation_setup_set is the safest way to set simulation parameters"
        },
        {
          topic: "Array connectors (Select Item In/Out)",
          problem: "Connector 1 is an array - must be expanded for multiple connections",
          solution: "block_connect handles this automatically - just connect multiple times to the same connector name",
          note: "Use connector names (e.g. 'ItemsOut') instead of indices for array connectors"
        },
        {
          topic: "Removing connections",
          problem: "Need to remove incorrect connections",
          solution: "Use block_disconnect(sourceBlockId, sourceConnector, targetBlockId, targetConnector)"
        },
        {
          topic: "Sequential calls",
          problem: "ExtendSim crashes if multiple MCP calls run in parallel",
          solution: "ALWAYS run one MCP call at a time against ExtendSim - wait for response before next call",
          note: "The COM interface cannot handle concurrent calls"
        }
      ],
      workflow: [
        "1. Run MCP_init (this command) FIRST in every session",
        "2. Use modeling_guide or pattern_search to find the right approach for your scenario",
        "3. Use block_search to find the right blocks and connectors",
        "4. Use block_configure to set block parameters (delay, distribution, queue rules, etc.)",
        "5. Use model_advisor to check your model for warnings and get suggestions",
        "6. Save the model often - ExtendSim may crash on invalid commands",
        "7. Use context_set after building/modifying a model to save purpose, assumptions, and key block roles"
      ],
      knowledge_tools: {
        modeling_guide: "Get step-by-step guidance for common scenarios (queuing, manufacturing, logistics, resources, flow, continuous). Returns recommended blocks, connections, parameters, and common mistakes.",
        pattern_search: "Search 268 verified example models by keyword or domain. Returns block topologies, connections, and metadata from real ExtendSim models.",
        model_advisor: "Analyze your current model and get warnings (missing connections, bad patterns), suggestions (improvements), and completions (what to add next).",
        simulation_type_guide: "Choose the right simulation type (discrete event, continuous, flow, RBD) based on your system."
      },
      reference_tools: {
        modl_search: "Search ModL functions (syntax, arguments, return type). Use before execute_command.",
        block_search: "Search blocks (connectors, patterns, libraries)",
        dialog_search: "Search dialog variables (name, type, dialogId)"
      },
      fire_and_forget: {
        description: "Long-running operations (simulation, scenario manager, optimizer) default to non-blocking mode. They start and return immediately.",
        simulation_run: "Set waitForCompletion=false (or omit) to start in background. Poll with simulation_status, collect results with simulation_get_results.",
        scenario_manager_run: "Returns immediately by default. Auto-selects all scenarios. Poll with scenario_manager_status, collect with scenario_manager_get_results.",
        optimizer_run: "Returns immediately by default. Poll with simulation_status, collect with optimizer_get_results.",
        blocking_mode: "Set waitForCompletion=true on any of these to wait for completion (legacy behavior)."
      },
      license: null as unknown
    };

    // Try to detect license (non-blocking - don't fail MCP_init if this fails)
    try {
      const licenseResult = await backend.detectLicense({});
      if (licenseResult && licenseResult.success) {
        guideContent.license = {
          type: licenseResult.license,
          libraries: licenseResult.libraries,
          simulationTypes: licenseResult.simulationTypes
        };
      }
    } catch {
      // License detection failed - leave as null, not critical
    }

    recordToolCall("MCP_init", startTime, { status: "ok" });
    return { content: [{ type: "text" as const, text: JSON.stringify(guideContent, null, 2) }] };
  }
);

server.tool(
  "modl_search",
  "Search the ExtendSim ModL function reference by name or keyword. Returns function signatures, arguments, and descriptions. Use before execute_command.",
  {
    query: z.string().describe("Search term (function name or keyword)"),
    maxResults: z.number().optional().describe("Maximum results to return (default 10)")
  },
  async ({ query, maxResults }) => {
    const startTime = performance.now();
    const { results, totalMatches, truncated } = searchModl(query, maxResults);
    if (results.length === 0) {
      recordToolCall("modl_search", startTime, { status: "ok" }, { query });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ message: `No ModL functions found matching '${query}'`, suggestions: ["Try a different keyword", "Use broader search terms"] })
        }]
      };
    }
    const response: Record<string, unknown> = { results };
    if (truncated) {
      response.totalMatches = totalMatches;
      response.truncated = true;
      response.hint = `Showing ${results.length} of ${totalMatches} matches. Use maxResults to see more.`;
    }
    recordToolCall("modl_search", startTime, { status: "ok" }, { query });
    return { content: [{ type: "text" as const, text: JSON.stringify(response, null, 2) }] };
  }
);

server.tool(
  "block_search",
  "Search the block reference for block types, connectors, and patterns. Use to find the right block name and library before block_add.",
  {
    query: z.string().describe("Search term (block name, connector, or keyword)"),
    library: z.string().optional().describe("Filter by library (e.g., 'Item.lbr')"),
    maxResults: z.number().optional().describe("Maximum results to return (default 10)")
  },
  async ({ query, library, maxResults }) => {
    const startTime = performance.now();
    const { results, totalMatches, truncated } = searchBlocks(query, library, maxResults);
    if (results.length === 0) {
      recordToolCall("block_search", startTime, { status: "ok" }, { query });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ message: `No blocks found matching '${query}'${library ? ` in ${library}` : ""}`, suggestions: ["Try a different keyword", "Check library name spelling"] })
        }]
      };
    }
    const response: Record<string, unknown> = { results };
    if (truncated) {
      response.totalMatches = totalMatches;
      response.truncated = true;
      response.hint = `Showing ${results.length} of ${totalMatches} matches. Use maxResults to see more.`;
    }
    recordToolCall("block_search", startTime, { status: "ok" }, { query });
    return { content: [{ type: "text" as const, text: JSON.stringify(response, null, 2) }] };
  }
);

server.tool(
  "dialog_search",
  "Search dialog variable names for blocks. Use to find the correct variable name before block_set_value or block_get_value.",
  {
    query: z.string().describe("Search term (variable name or keyword)"),
    block: z.string().optional().describe("Filter by block type (e.g., 'Activity')"),
    maxResults: z.number().optional().describe("Maximum results to return (default 10)")
  },
  async ({ query, block, maxResults }) => {
    const startTime = performance.now();
    const { results, totalMatches, truncated } = searchDialogs(query, block, maxResults);
    if (results.length === 0) {
      recordToolCall("dialog_search", startTime, { status: "ok" }, { query });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ message: `No dialog variables found matching '${query}'${block ? ` in ${block}` : ""}`, suggestions: ["Try a different keyword", "Check block name spelling"] })
        }]
      };
    }
    const response: Record<string, unknown> = { results };
    if (truncated) {
      response.totalMatches = totalMatches;
      response.truncated = true;
      response.hint = `Showing ${results.length} of ${totalMatches} matches. Use maxResults to see more.`;
    }
    recordToolCall("dialog_search", startTime, { status: "ok" }, { query });
    return { content: [{ type: "text" as const, text: JSON.stringify(response, null, 2) }] };
  }
);

// ============================================================================
// STATUS TOOLS
// ============================================================================

server.tool(
  "extendsim_status",
  "Check if ExtendSim is running and connected via COM.",
  {},
  async () => {
    return safeToolCall("extendsim_status", () => backend.extendsimStatus());
  }
);

server.tool(
  "extendsim_start",
  "Launch ExtendSim application if not already running.",
  {},
  async () => {
    return safeToolCall("extendsim_start", () => backend.extendsimStart());
  }
);

server.tool(
  "extendsim_get_license",
  "Detect the ExtendSim license type (CP/DE/Pro) and available libraries.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("extendsim_get_license", () => backend.detectLicense({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_type_guide",
  "Get guidance on choosing the right simulation type (discrete event, continuous, flow, RBD). Helps decide which blocks and libraries to use.",
  {
    query: z.string().optional().describe("Filter by simulation type (e.g. 'discrete', 'flow', 'continuous', 'rbd')")
  },
  async ({ query }) => {
    const startTime = performance.now();
    const guide = getSimulationTypeGuide();
    if (!query) {
      recordToolCall("simulation_type_guide", startTime, { status: "ok" });
      return { content: [{ type: "text" as const, text: JSON.stringify(guide, null, 2) }] };
    }

    const lower = query.toLowerCase();
    const types = guide.simulationTypes as Record<string, unknown> | undefined;
    if (!types) {
      recordToolCall("simulation_type_guide", startTime, { status: "ok" }, { query });
      return { content: [{ type: "text" as const, text: JSON.stringify(guide, null, 2) }] };
    }

    // Filter to matching type(s)
    const matches: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(types)) {
      if (key.toLowerCase().includes(lower)) {
        matches[key] = value;
      }
    }

    if (Object.keys(matches).length === 0) {
      recordToolCall("simulation_type_guide", startTime, { status: "ok" }, { query });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            message: `No simulation type matching '${query}'`,
            availableTypes: Object.keys(types),
            decisionGuide: guide.decisionGuide
          }, null, 2)
        }]
      };
    }

    recordToolCall("simulation_type_guide", startTime, { status: "ok" }, { query });
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({
          simulationTypes: matches,
          decisionGuide: guide.decisionGuide
        }, null, 2)
      }]
    };
  }
);

server.tool(
  "modeling_guide",
  "Get modeling guidance for common simulation scenarios. Returns recommended block patterns, key parameters, common mistakes, and example references. Use this when deciding how to model a system.",
  {
    query: z.string().optional().describe("Search term or scenario description (e.g. 'queue', 'manufacturing', 'supply chain')"),
    category: z.string().optional().describe("Filter by category: queuing, manufacturing, logistics, resources, flow, continuous"),
    scenario: z.string().optional().describe("Get a specific scenario by key (e.g. 'simple_queue', 'serial_line')")
  },
  async ({ query, category, scenario }) => {
    const startTime = performance.now();
    const guides = getModelingGuides();
    const scenarios = guides.scenarios as Record<string, unknown> | undefined;
    const categories = guides.categories as Record<string, unknown> | undefined;
    const params = { category, scenario };

    if (!scenarios) {
      recordToolCall("modeling_guide", startTime, { status: "ok" }, params);
      return { content: [{ type: "text" as const, text: JSON.stringify(guides, null, 2) }] };
    }

    // Direct scenario lookup
    if (scenario) {
      const s = scenarios[scenario];
      recordToolCall("modeling_guide", startTime, { status: "ok" }, params);
      if (s) {
        return { content: [{ type: "text" as const, text: JSON.stringify(s, null, 2) }] };
      }
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            message: `No scenario '${scenario}'`,
            availableScenarios: Object.keys(scenarios)
          }, null, 2)
        }]
      };
    }

    // Category filter
    if (category && !query) {
      const lower = category.toLowerCase();
      const matches: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(scenarios)) {
        const val = value as Record<string, unknown>;
        if (typeof val.category === "string" && val.category.toLowerCase() === lower) {
          matches[key] = value;
        }
      }
      recordToolCall("modeling_guide", startTime, { status: "ok" }, params);
      if (Object.keys(matches).length === 0) {
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({
              message: `No scenarios in category '${category}'`,
              availableCategories: categories ? Object.keys(categories) : []
            }, null, 2)
          }]
        };
      }
      return { content: [{ type: "text" as const, text: JSON.stringify({ scenarios: matches }, null, 2) }] };
    }

    // Text search across name, description, useWhen, category
    if (query) {
      const lower = query.toLowerCase();
      const matches: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(scenarios)) {
        const val = value as Record<string, unknown>;
        const searchable = JSON.stringify(val).toLowerCase();
        if (searchable.includes(lower)) {
          matches[key] = value;
        }
      }
      recordToolCall("modeling_guide", startTime, { status: "ok" }, params);
      if (Object.keys(matches).length === 0) {
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({
              message: `No scenarios matching '${query}'`,
              availableScenarios: Object.keys(scenarios),
              availableCategories: categories ? Object.keys(categories) : []
            }, null, 2)
          }]
        };
      }
      return { content: [{ type: "text" as const, text: JSON.stringify({ scenarios: matches }, null, 2) }] };
    }

    // No filters — return overview (categories + scenario keys, not full content)
    const overview: Record<string, unknown> = {
      description: guides.description,
      categories,
      scenarioCount: Object.keys(scenarios).length,
      scenarios: Object.fromEntries(
        Object.entries(scenarios).map(([key, val]) => {
          const v = val as Record<string, unknown>;
          return [key, { name: v.name, category: v.category }];
        })
      )
    };
    recordToolCall("modeling_guide", startTime, { status: "ok" }, params);
    return { content: [{ type: "text" as const, text: JSON.stringify(overview, null, 2) }] };
  }
);

server.tool(
  "pattern_search",
  "Search the pattern library of 268 verified ExtendSim example models. Returns block topologies, connections, and metadata. Use this to find relevant modeling patterns for a given scenario.",
  {
    query: z.string().optional().describe("Free-text search in name, description, tags, block types (e.g. 'manufacturing', 'queue', 'tank')"),
    category: z.string().optional().describe("Filter by category: discrete_event, discrete_rate, continuous, reliability, textbook, how_to, tutorial"),
    domain: z.string().optional().describe("Filter by domain: queuing, manufacturing, batching, routing, resources, flow, continuous, reliability, logistics"),
    complexity: z.string().optional().describe("Filter by complexity: simple, medium, complex"),
    maxResults: z.number().optional().describe("Maximum results to return (default 10)")
  },
  async ({ query, category, domain, complexity, maxResults }) => {
    const startTime = performance.now();
    const ctxParams = { category, domain, complexity };
    const lib = getPatternLibrary();
    const patterns = lib.patterns as Array<Record<string, unknown>> | undefined;

    if (!patterns) {
      recordToolCall("pattern_search", startTime, { status: "ok" }, ctxParams);
      return { content: [{ type: "text" as const, text: JSON.stringify(lib, null, 2) }] };
    }

    const max = maxResults ?? 10;

    // Filter phase
    let filtered = patterns;
    if (category) {
      const lower = category.toLowerCase();
      filtered = filtered.filter(p => (p.category as string || "").toLowerCase() === lower);
    }
    if (domain) {
      const lower = domain.toLowerCase();
      filtered = filtered.filter(p => (p.domain as string || "").toLowerCase() === lower);
    }
    if (complexity) {
      const lower = complexity.toLowerCase();
      filtered = filtered.filter(p => (p.complexity as string || "").toLowerCase() === lower);
    }

    // Search/scoring phase
    if (query) {
      const terms = query.toLowerCase().split(/\s+/);
      const scored = filtered.map(p => {
        let score = 0;
        const name = ((p.name as string) || "").toLowerCase();
        const desc = ((p.description as string) || "").toLowerCase();
        const tags = ((p.tags as string[]) || []).map(t => t.toLowerCase());
        const blockTypes = Object.keys((p.blockTypeSummary as Record<string, number>) || {}).map(t => t.toLowerCase());

        for (const term of terms) {
          if (name.includes(term)) score += 10;
          if (tags.some(t => t.includes(term))) score += 5;
          if (desc.includes(term)) score += 3;
          if (blockTypes.some(t => t.includes(term))) score += 2;
        }
        return { pattern: p, score };
      });

      scored.sort((a, b) => b.score - a.score);
      const results = scored.filter(s => s.score > 0).slice(0, max).map(s => s.pattern);

      recordToolCall("pattern_search", startTime, { status: "ok" }, ctxParams);
      if (results.length === 0) {
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({
              message: `No patterns matching '${query}'`,
              totalPatterns: patterns.length,
              categories: [...new Set(patterns.map(p => p.category as string))],
              domains: [...new Set(patterns.map(p => p.domain as string))]
            }, null, 2)
          }]
        };
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ matchCount: results.length, totalPatterns: patterns.length, patterns: results }, null, 2)
        }]
      };
    }

    // No query — return filtered results (or overview if no filters)
    if (!category && !domain && !complexity) {
      const overview = {
        version: lib.version,
        modelCount: lib.modelCount,
        categories: {} as Record<string, number>,
        domains: {} as Record<string, number>,
      };
      for (const p of patterns) {
        const cat = p.category as string;
        const dom = p.domain as string;
        overview.categories[cat] = (overview.categories[cat] || 0) + 1;
        overview.domains[dom] = (overview.domains[dom] || 0) + 1;
      }
      recordToolCall("pattern_search", startTime, { status: "ok" }, ctxParams);
      return { content: [{ type: "text" as const, text: JSON.stringify(overview, null, 2) }] };
    }

    const results = filtered.slice(0, max);
    recordToolCall("pattern_search", startTime, { status: "ok" }, ctxParams);
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify({ matchCount: filtered.length, showing: results.length, patterns: results }, null, 2)
      }]
    };
  }
);

server.tool(
  "model_advisor",
  "Analyze current model topology and return warnings, suggestions, and completions. Uses pattern library, modeling guides, and structural rules to advise on model quality and next steps.",
  {
    focus: z.enum(["all", "warnings", "suggestions", "completions"]).optional()
      .describe("Focus on specific analysis type (default: all)")
  },
  async ({ focus }) => {
    const startTime = performance.now();
    const snapshot = await backend.modelSnapshot({});
    if (!snapshot.success) {
      recordToolCall("model_advisor", startTime, snapshot, { focus });
      return toolResponse(snapshot);
    }

    const blocks = snapshot.blocks || [];
    const connections = snapshot.connections || [];
    const f = focus || "all";

    const result: Record<string, unknown> = {
      modelName: snapshot.modelName,
      blockCount: snapshot.blockCount,
      connectionCount: snapshot.connectionCount,
    };

    if (f === "all" || f === "warnings") {
      result.warnings = analyzeWarnings(blocks, connections);
    }

    if (f === "all" || f === "suggestions") {
      const lib = getPatternLibrary();
      const patterns = (lib.patterns as Array<Record<string, unknown>>) || [];
      const guides = getModelingGuides();
      result.suggestions = analyzeSuggestions(blocks, connections, patterns, guides);
    }

    if (f === "all" || f === "completions") {
      result.completions = analyzeCompletions(blocks, connections);
    }

    recordToolCall("model_advisor", startTime, { status: "ok" }, { focus });
    return toolResponse(result);
  }
);

// ============================================================================
// MODEL TOOLS
// ============================================================================

server.tool(
  "model_open",
  "Open an ExtendSim model file (.mox). Required before any block or simulation operations.",
  {
    filePath: z.string().describe("Path to the .mox file"),
    readOnly: z.boolean().optional().describe("Open in read-only mode")
  },
  async ({ filePath, readOnly }) => {
    return safeToolCall("model_open", () => backend.modelOpen({ filePath, readOnly }), { filePath, readOnly });
  }
);

server.tool(
  "model_save",
  "Save the current model. Optionally provide a filePath for Save As.",
  {
    modelId: z.string().optional().describe("Model ID"),
    filePath: z.string().optional().describe("New path for Save As")
  },
  async ({ modelId, filePath }) => {
    return safeToolCall("model_save", () => backend.modelSave({ modelId, filePath }), { modelId, filePath });
  }
);

server.tool(
  "model_list",
  "List all currently open models.",
  {},
  async () => {
    return safeToolCall("model_list", () => backend.modelList());
  }
);

server.tool(
  "model_info",
  "Get model metadata including name, block count, and optionally simulation run statistics.",
  {
    modelId: z.string().optional().describe("Model ID"),
    includeStatistics: z.boolean().optional().describe("Include run statistics")
  },
  async ({ modelId, includeStatistics }) => {
    return safeToolCall("model_info", () => backend.modelInfo({ modelId, includeStatistics }), { modelId, includeStatistics });
  }
);

server.tool(
  "model_close",
  "Close the current model. Optionally save before closing.",
  {
    modelId: z.string().optional().describe("Model ID"),
    saveFirst: z.boolean().optional().describe("Save before closing")
  },
  async ({ modelId, saveFirst }) => {
    return safeToolCall("model_close", () => backend.modelClose({ modelId, saveFirst }), { modelId, saveFirst });
  }
);

server.tool(
  "model_new",
  "Create a new empty ExtendSim model. Optionally specify a save path.",
  {
    savePath: z.string().optional().describe("Path to save the new model")
  },
  async ({ savePath }) => {
    return safeToolCall("model_new", () => backend.modelNew({ savePath }), { savePath });
  }
);

// ============================================================================
// BLOCK TOOLS
// ============================================================================

server.tool(
  "block_add",
  "Add a new block to the model. Use block_search to find the correct libraryName and blockName. Position with x/y or relative to a neighbor block.",
  {
    modelId: z.string().optional().describe("Model ID"),
    libraryName: z.string().describe("Library name (e.g. 'Item.lbr')"),
    blockName: z.string().describe("Block type (e.g. 'Create')"),
    x: z.number().optional().describe("X position in pixels"),
    y: z.number().optional().describe("Y position in pixels"),
    neighbor: z.number().optional().describe("Block ID to place relative to (-1 for absolute position)"),
    side: z.number().optional().describe("Side relative to neighbor: 0=left, 1=top, 2=right, 3=bottom"),
    label: z.string().optional().describe("Block label")
  },
  async ({ modelId, libraryName, blockName, x, y, neighbor, side, label }) => {
    return safeToolCall("block_add", () => backend.blockAdd({ modelId, libraryName, blockName, x, y, neighbor, side, label }), { modelId, libraryName, blockName, x, y, neighbor, side, label });
  }
);

server.tool(
  "block_add_batch",
  "Add multiple blocks to the model in one call. More efficient than calling block_add repeatedly. Each block can specify position via x/y or relative to a neighbor.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blocks: z.array(z.object({
      libraryName: z.string().describe("Library name (e.g. 'Item.lbr')"),
      blockName: z.string().describe("Block type (e.g. 'Create')"),
      x: z.number().optional().describe("X position in pixels"),
      y: z.number().optional().describe("Y position in pixels"),
      neighbor: z.number().optional().describe("Block ID to place relative to"),
      side: z.number().optional().describe("Side relative to neighbor: 0=left, 1=top, 2=right, 3=bottom"),
      label: z.string().optional().describe("Block label")
    })).describe("Array of blocks to add")
  },
  async ({ modelId, blocks }) => {
    return safeToolCall("block_add_batch", () => backend.blockAddBatch({ modelId, blocks }), { modelId, blocks });
  }
);

server.tool(
  "block_connect",
  "Connect two blocks. Source connector (output) → target connector (input). Supports connector names (e.g. 'ItemOut') or numeric indices. Array connectors are expanded automatically.",
  {
    modelId: z.string().optional().describe("Model ID"),
    sourceBlockId: z.number().describe("Source block ID"),
    sourceConnector: z.union([z.number(), z.string()]).describe("Output port: index (0-based) or name (e.g. 'ItemOut')"),
    targetBlockId: z.number().describe("Target block ID"),
    targetConnector: z.union([z.number(), z.string()]).describe("Input port: index (0-based) or name (e.g. 'ItemIn')")
  },
  async ({ modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector }) => {
    return safeToolCall("block_connect", () => backend.blockConnect({
      modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector
    }), { modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector });
  }
);

server.tool(
  "block_disconnect",
  "Remove a connection between two blocks. Specify the same source/target connectors used in block_connect.",
  {
    modelId: z.string().optional().describe("Model ID"),
    sourceBlockId: z.number().describe("Source block ID"),
    sourceConnector: z.union([z.number(), z.string()]).describe("Output port: index (0-based) or name"),
    targetBlockId: z.number().describe("Target block ID"),
    targetConnector: z.union([z.number(), z.string()]).describe("Input port: index (0-based) or name")
  },
  async ({ modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector }) => {
    return safeToolCall("block_disconnect", () => backend.blockDisconnect({
      modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector
    }), { modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector });
  }
);

server.tool(
  "connect_chain",
  "Connect multiple blocks in sequence with one call. Connects block[0]→block[1]→block[2]→... using the specified connectors.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockIds: z.array(z.number()).describe("Block IDs to connect in sequence"),
    sourceConnector: z.union([z.number(), z.string()]).optional().default("ItemOut").describe("Output connector name/index"),
    targetConnector: z.union([z.number(), z.string()]).optional().default("ItemIn").describe("Input connector name/index")
  },
  async ({ modelId, blockIds, sourceConnector, targetConnector }) => {
    return safeToolCall("connect_chain", () => backend.connectChain({
      modelId, blockIds, sourceConnector, targetConnector
    }), { modelId, blockIds, sourceConnector, targetConnector });
  }
);

server.tool(
  "connect_graph",
  "Connect multiple arbitrary block pairs in one call. Each connection specifies source/target block IDs and optional connector names. More flexible than connect_chain for non-linear topologies (splits, merges, parallel paths).",
  {
    modelId: z.string().optional().describe("Model ID"),
    connections: z.array(z.object({
      sourceBlockId: z.number().describe("Source block ID"),
      targetBlockId: z.number().describe("Target block ID"),
      sourceConnector: z.union([z.number(), z.string()]).optional().describe("Output connector name/index (default 'ItemOut')"),
      targetConnector: z.union([z.number(), z.string()]).optional().describe("Input connector name/index (default 'ItemIn')")
    })).describe("Array of connections to make")
  },
  async ({ modelId, connections }) => {
    return safeToolCall("connect_graph", () => backend.connectGraph({ modelId, connections }), { modelId, connections });
  }
);

server.tool(
  "block_remove",
  "Remove a block from the model. Also removes all its connections.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID"),
    allowUndo: z.boolean().optional().describe("Allow undo (ClearBlockUndo). Default: false (permanent)")
  },
  async ({ modelId, blockId, allowUndo }) => {
    return safeToolCall("block_remove", () => backend.blockRemove({ modelId, blockId, allowUndo }), { modelId, blockId, allowUndo });
  }
);

server.tool(
  "block_list",
  "List all blocks in the model with their IDs, types, labels, and positions. Use detail='full' to include connector info.",
  {
    modelId: z.string().optional().describe("Model ID"),
    detail: z.enum(["summary", "full"]).optional().describe("'summary' (default) for overview, 'full' to include connectors")
  },
  async ({ modelId, detail }) => {
    return safeToolCall("block_list", () => backend.blockList({ modelId, detail }), { modelId, detail });
  }
);

server.tool(
  "connection_list",
  "List all connections in the model, showing which block connectors are linked.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("connection_list", () => backend.connectionList({ modelId }), { modelId });
  }
);

server.tool(
  "block_info",
  "Get info about a block. Use blockId for live info from model (connectors, current values). Use query for reference info about a block type.",
  {
    modelId: z.string().optional().describe("Model ID"),
    query: z.string().optional().describe("'all' for all available blocks, or block name (e.g. 'Activity') for description"),
    blockId: z.number().optional().describe("Block ID for live info from model")
  },
  async ({ modelId, query, blockId }) => {
    return safeToolCall("block_info", () => backend.blockInfo({ modelId, query, blockId }), { modelId, query, blockId });
  }
);

server.tool(
  "block_discover",
  "Discover a block type's connectors by temporarily placing and inspecting it. Returns connector names, directions, types (Value/Item/Flow/Universal/Reliability), and array info.",
  {
    modelId: z.string().optional().describe("Model ID"),
    libraryName: z.string().describe("Library name (e.g. 'Item.lbr')"),
    blockName: z.string().describe("Block type to examine (e.g. 'Gate')")
  },
  async ({ modelId, libraryName, blockName }) => {
    return safeToolCall("block_discover", () => backend.blockDiscover({ modelId, libraryName, blockName }), { modelId, libraryName, blockName });
  }
);

server.tool(
  "block_discover_variables",
  "Scan a block's dialog variables by ID range. Returns variable names, types, and current values. Use to find correct variable names for block_set_value.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().optional().describe("Existing block ID in model (use this OR libraryName+blockName)"),
    libraryName: z.string().optional().describe("Library name (e.g. 'Item.lbr') - places temporary block"),
    blockName: z.string().optional().describe("Block type (e.g. 'Activity') - places temporary block"),
    maxDialogId: z.number().optional().describe("Maximum dialogID to scan (default 200)")
  },
  async ({ modelId, blockId, libraryName, blockName, maxDialogId }) => {
    return safeToolCall("block_discover_variables", () => backend.blockDiscoverVariables({ modelId, blockId, libraryName, blockName, maxDialogId }), { modelId, blockId, libraryName, blockName, maxDialogId });
  }
);

server.tool(
  "block_set_value",
  "Set a dialog variable value on a block. Use variable name (e.g. 'WaitDelta_prm') or numeric dialog ID. Use dialog_search to find variable names.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID"),
    dialogNumber: z.union([z.number(), z.string()]).describe("Variable name (e.g. 'WaitDelta_prm') or dialog number"),
    value: z.union([z.number(), z.string()]).describe("Value to set"),
    row: z.number().optional().describe("Row index for table/array values (0-based)"),
    col: z.number().optional().describe("Column index for table values (0-based)")
  },
  async ({ modelId, blockId, dialogNumber, value, row, col }) => {
    return safeToolCall("block_set_value", () => backend.blockSetValue({ modelId, blockId, dialogNumber, value, row, col }), { modelId, blockId, dialogNumber, value, row, col });
  }
);

server.tool(
  "block_get_value",
  "Read a dialog variable value from a block. Use variable name or numeric dialog ID. Use asString=true for text values.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID"),
    dialogNumber: z.union([z.number(), z.string()]).describe("Variable name (e.g. 'WaitDelta_prm') or dialog number"),
    row: z.number().optional().describe("Row index for table/array values (0-based)"),
    col: z.number().optional().describe("Column index for table values (0-based)"),
    asString: z.boolean().optional().describe("Return value as string instead of number")
  },
  async ({ modelId, blockId, dialogNumber, row, col, asString }) => {
    return safeToolCall("block_get_value", () => backend.blockGetValue({ modelId, blockId, dialogNumber, row, col, asString }), { modelId, blockId, dialogNumber, row, col, asString });
  }
);

server.tool(
  "execute_command",
  "Execute a raw ModL command in ExtendSim. Use modl_search first to verify syntax. Use getResult=true to capture the return value.",
  {
    command: z.string().describe("ExtendSim ModL command to execute"),
    getResult: z.boolean().optional().describe("Whether to return the result of the command"),
    resultType: z.enum(["number", "string"]).optional().describe("Type of result to return")
  },
  async ({ command, getResult, resultType }) => {
    return safeToolCall("execute_command", () => backend.executeCommand({ command, getResult, resultType }), { command, getResult, resultType });
  }
);

// ============================================================================
// BLOCK CONFIGURATION (v1.7 - consolidated from 33 individual tools)
// ============================================================================

server.tool(
  "block_configure",
  "Configure any supported block by auto-detecting its type. Pass blockId with config params, or just blockId (omit config) to see available parameters for that block type.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID to configure"),
    config: z.record(z.string(), z.any()).optional().describe(
      "Configuration parameters. Omit to get help for the block type. " +
      "Supported block types and their params:\n" +
      "Activity: delayType, value, distribution, arg1, arg2, arg3, maxItems, preemptEnabled, shutdownEnabled, costPerTime, costPerItem, costTimeUnit, shift\n" +
      "Queue: rankType, sortAttribute, ascending, resourcePoolBlockId, resourcesNeeded, maxLength, renegeEnabled, renegeTime, calcWaitCosts, shift, calcDelay\n" +
      "Create: arrivalType, distribution, arg1, arg2, arg3, maxArrivals, costPerTime, costPerItem, costTimeUnit\n" +
      "Gate: demandType, initialState, openValue, closeValue\n" +
      "Select Item Out: mode, attributeName, probabilities, ifBlocked, predictPath\n" +
      "Select Item In: mode\n" +
      "Batch: batchType, batchSize, preserveUniqueness, matchAttribute, showDemandConnector, demandConnectorValue, allowZeroBatchSize, batchSizeWhen\n" +
      "Unbatch: preserveUniqueness, quantityPerOutput, costType, usePreservedQuantity, duplicatePreserved, quantityOut\n" +
      "Resource Pool: poolName, initialResources, allocationRule\n" +
      "Resource Pool Release: releaseQuantity\n" +
      "Workstation: maxServers, maxQueueLength, delayType, distribution, arg1-3, value, costPerTime, costPerItem\n" +
      "Equation: equation\n" +
      "Equation(I): equation, showInputNames, showInputValues, showOutputNames, showOutputValues, outputInitValue, includeEnabled, expandRecords\n" +
      "Queue Equation: equation, releaseRule\n" +
      "Shift: schedule [{startTime, endTime, capacity}], statusType, shiftName, repeat, repeatTime, repeatUnit, timeUnit, timeFormat\n" +
      "Transport: defaultDistance, defaultSpeed\n" +
      "Convey Item: conveyorLength, defaultSpeed, accumulating\n" +
      "Shutdown: tbfDistribution, tbfArg1, tbfArg2, ttrDistribution, ttrArg1, ttrArg2\n" +
      "Tank: capacity, initialLevel, maxInputRate, maxOutputRate, flowControlEnabled, flowControlPolicy, flowControlValue\n" +
      "Valve: maxRate, goal, goalType, goalOffStatus, controlType, startCondition, stopCondition, shutdownCondition, pullConstraintDelay\n" +
      "Merge: mode, initialValueSelected, initializeSelected, paramFromConnectors\n" +
      "Diverge: mode, initialValueSelected, initializeSelected, paramFromConnectors\n" +
      "Interchange: capacity, initialLevel, maxInputRate, maxOutputRate, mode, releaseCondition, releaseTarget, releaseInterrupt\n" +
      "Convey Flow: speed, length, capacityMax, accumulating, conveyorType, maxDensity, delay, shift, emptyWhenOffShift, attributeTransform\n" +
      "Change Units: factor | Bias: biasOrder | Catch Flow: position | Throw Flow: position, connectorNum\n" +
      "Throw Item: catchType, catchGroup, attributeName, useBlockNum\n" +
      "Catch Item: catchGroup, countByThrow\n" +
      "Resource Item: initialCount, stripAttributes, itemType, costEnabled, costPerTime, costPerItem, costTimeUnit, shift\n" +
      "Clear Statistics: clearTime, timeUnits, clearActivity, clearResource, clearQueue, clearExit, clearMeanVariance, clearInformation, clearRate, clearMaxMin\n" +
      "Information: cycleAttribute, addCount, countByOne, noReset, resetWhen, detailedStats, resetEvery, resetEveryInterval\n" +
      "Mean & Variance: multiSim, weight, clearTime, confidence, initValue, movingAverage, movingAverageInterval, recordHistory, relativeError, relativeErrorThreshold\n" +
      "Line Chart: startTime, endTime, disableRecording, fixedPoints\n" +
      "Histogram: numBins, binSize, xMin, xMax\n" +
      "History(R): maxRows, enableDatabaseLog | Get(R): locationBlockId, infoType, flowAttribute | Set(R): (no params)\n" +
      "Optimizer: populationSize, maxGenerations, convergencePercent, minGenerations, maxSampleSize, truncate, truncatePercent, antithetic, showPlotter\n" +
      "Scenario Manager: runsPerScenario, confidenceInterval, simStart, simEnd, reportDetails, saveScenarios\n" +
      "Analysis Manager: enableDbResponses, enableBlockResponses, enableReliabilityResponses, enableDbFactors, enableBlockFactors, enableReliabilityFactors, enableResultsTable, autoExport"
    )
  },
  async ({ modelId, blockId, config }) => {
    return safeToolCall("block_configure", () => backend.blockConfigure({ modelId, blockId, config }), { modelId, blockId, config });
  }
);

server.tool(
  "template_list",
  "List available model templates (pre-built block patterns like Create→Queue→Activity→Exit).",
  {},
  async () => {
    return safeToolCall("template_list", () => backend.templateList());
  }
);

server.tool(
  "block_template",
  "Place a pre-built template of connected blocks into the model. Use template_list to see available templates. Parameters can be set during placement.",
  {
    modelId: z.string().optional().describe("Model ID"),
    templateName: z.string().describe("Name of the template to use"),
    startX: z.number().optional().default(100).describe("Starting X position (pixels)"),
    startY: z.number().optional().default(100).describe("Starting Y position (pixels)"),
    spacing: z.number().optional().default(120).describe("Horizontal spacing between blocks (pixels)"),
    parameters: z.record(z.union([z.number(), z.string()])).optional().describe("Parameter values to set (e.g., {arrivalRate: 5, processTime: 10})")
  },
  async ({ modelId, templateName, startX, startY, spacing, parameters }) => {
    return safeToolCall("block_template", () => backend.blockTemplate({
      modelId, templateName, startX, startY, spacing, parameters
    }), { modelId, templateName, startX, startY, spacing, parameters });
  }
);

// ============================================================================
// SIMULATION TOOLS
// ============================================================================

server.tool(
  "simulation_run",
  "Run the simulation. Set waitForCompletion=false to start and return immediately (fire-and-forget) — then poll simulation_status and collect simulation_get_results. Default waitForCompletion=true waits for completion. Set includeStats=true to collect Exit/Queue/Activity/Create statistics inline.",
  {
    modelId: z.string().optional().describe("Model ID"),
    endTime: z.number().optional().describe("Simulation time to run to"),
    runMode: z.enum(["normal", "fast", "step"]).optional().describe("Run mode"),
    resetFirst: z.boolean().optional().describe("Reset model before running"),
    waitForCompletion: z.boolean().optional().describe("Wait until simulation completes (default true). Set false for fire-and-forget."),
    includeStats: z.boolean().optional().describe("Include Exit/Queue/Activity/Create statistics in the response (default false)"),
    statsBlockIds: z.array(z.number()).optional().describe("Block IDs to collect statistics for (with includeStats=true). If omitted, collects all.")
  },
  async ({ modelId, endTime, runMode, resetFirst, waitForCompletion, includeStats, statsBlockIds }) => {
    return safeToolCall("simulation_run", () => backend.simulationRun({ modelId, endTime, runMode, resetFirst, waitForCompletion, includeStats, statsBlockIds }), { modelId, endTime, runMode, resetFirst, waitForCompletion, includeStats, statsBlockIds });
  }
);

server.tool(
  "simulation_stop",
  "Stop a running simulation.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_stop", () => backend.simulationStop({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_pause",
  "Pause a running simulation. Use simulation_resume to continue.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_pause", () => backend.simulationPause({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_resume",
  "Resume a paused simulation.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_resume", () => backend.simulationResume({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_status",
  "Get the current simulation state (running, paused, stopped), current time, and phase name.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_status", () => backend.simulationStatus({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_get_results",
  "Get simulation results after a run completes. Returns Exit block throughput and timing statistics.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_get_results", () => backend.simulationGetResults({ modelId }), { modelId });
  }
);

// ============================================================================
// ATTRIBUTE TOOLS
// ============================================================================

server.tool(
  "attribute_set",
  "Set an item attribute value on a Set block. Supports constant values, connector input, or distribution-based values.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Set block ID"),
    attributeName: z.string().describe("Name of the attribute to set (e.g., 'priority', 'customer_type')"),
    valueType: z.enum(["constant", "connector", "distribution"])
      .optional().default("constant").describe("How to determine the value"),
    value: z.number().optional().describe("Constant value to set (for valueType='constant')"),
    distribution: z.enum([
      "constant", "uniform", "triangular", "normal",
      "exponential", "erlang", "gamma", "weibull",
      "lognormal", "beta", "pearson5", "pearson6"
    ]).optional().describe("Distribution name (for valueType='distribution')"),
    arg1: z.number().optional().describe("Distribution arg 1"),
    arg2: z.number().optional().describe("Distribution arg 2"),
    arg3: z.number().optional().describe("Distribution arg 3")
  },
  async ({ modelId, blockId, attributeName, valueType, value, distribution, arg1, arg2, arg3 }) => {
    return safeToolCall("attribute_set", () => backend.attributeSet({
      modelId, blockId, attributeName, valueType, value, distribution, arg1, arg2, arg3
    }), { modelId, blockId, attributeName, valueType, value, distribution, arg1, arg2, arg3 });
  }
);

server.tool(
  "attribute_get",
  "Read an item attribute configuration from a Get block.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Get block ID"),
    attributeName: z.string().describe("Name of the attribute to read (e.g., 'priority', 'customer_type')")
  },
  async ({ modelId, blockId, attributeName }) => {
    return safeToolCall("attribute_get", () => backend.attributeGet({ modelId, blockId, attributeName }), { modelId, blockId, attributeName });
  }
);

// ============================================================================
// VALIDATION TOOLS
// ============================================================================

server.tool(
  "model_validate",
  "Validate model integrity. Checks for unconnected blocks and missing required connections.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("model_validate", () => backend.modelValidate({ modelId }), { modelId });
  }
);

server.tool(
  "model_overview",
  "Get a comprehensive model summary in one call: hierarchy structure (with duplicate detection), databases, simulation setup, and AI context. Efficient for large models — no block enumeration.",
  {
    modelId: z.string().optional().describe("Model ID"),
  },
  async ({ modelId }) => {
    return safeToolCall("model_overview", () => backend.modelOverview({ modelId }), { modelId });
  }
);

server.tool(
  "model_snapshot",
  "Get a complete snapshot of the model: all blocks and all connections in one call. More efficient than calling block_list + connection_list separately.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("model_snapshot", () => backend.modelSnapshot({ modelId }), { modelId });
  }
);

server.tool(
  "model_extract",
  "Extract a complete or partial model snapshot: blocks, connections, parameters, simulation setup, databases, hierarchies, and global arrays. Returns structured JSON for analysis. Use savePath to write to file instead.",
  {
    savePath: z.string().optional().describe("If set, write JSON to file and return path instead of inline data"),
    sections: z.array(z.string()).optional().describe("Sections to extract (default: all). Options: blocks, connections, parameters, simulation, databases, hierarchies, global_arrays"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ savePath, sections, modelId }) => {
    return safeToolCall("model_extract", () => backend.modelExtract({ savePath, sections, modelId }), { savePath, sections, modelId });
  }
);

// ============================================================================
// DATABASE TOOLS
// ============================================================================

server.tool(
  "db_list",
  "List all databases in the model with their tables.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("db_list", () => backend.dbList({ modelId }), { modelId });
  }
);

server.tool(
  "db_table_info",
  "Get table structure: field names, types, and record count.",
  {
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ databaseName, tableName, modelId }) => {
    return safeToolCall("db_table_info", () => backend.dbTableInfo({ databaseName, tableName, modelId }), { databaseName, tableName, modelId });
  }
);

server.tool(
  "db_get_value",
  "Read a single value from a database table cell by field name and record index.",
  {
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    fieldName: z.string().describe("Field (column) name"),
    record: z.number().describe("Record index (0-based)"),
    asString: z.boolean().optional().describe("Return value as string instead of number"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ databaseName, tableName, fieldName, record, asString, modelId }) => {
    return safeToolCall("db_get_value", () => backend.dbGetValue({ databaseName, tableName, fieldName, record, asString, modelId }), { databaseName, tableName, fieldName, record, asString, modelId });
  }
);

server.tool(
  "db_set_value",
  "Write a single value to a database table cell by field name and record index.",
  {
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    fieldName: z.string().describe("Field (column) name"),
    record: z.number().describe("Record index (0-based)"),
    value: z.union([z.number(), z.string()]).describe("Value to set"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ databaseName, tableName, fieldName, record, value, modelId }) => {
    return safeToolCall("db_set_value", () => backend.dbSetValue({ databaseName, tableName, fieldName, record, value, modelId }), { databaseName, tableName, fieldName, record, value, modelId });
  }
);

server.tool(
  "db_get_records",
  "Read multiple records from a database table. Returns rows as objects. endRecord is exclusive (reads up to but not including it).",
  {
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    startRecord: z.number().optional().describe("Start record index (0-based, default 0)"),
    endRecord: z.number().optional().describe("End record index (exclusive)"),
    fields: z.array(z.string()).optional().describe("Field names to include (default: all)"),
    maxRecords: z.number().optional().describe("Maximum records to return (default 1000)"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ databaseName, tableName, startRecord, endRecord, fields, maxRecords, modelId }) => {
    return safeToolCall("db_get_records", () => backend.dbGetRecords({ databaseName, tableName, startRecord, endRecord, fields, maxRecords, modelId }), { databaseName, tableName, startRecord, endRecord, fields, maxRecords, modelId });
  }
);

server.tool(
  "db_add_records",
  "Add empty records to a database table. Use db_set_value to populate them afterwards.",
  {
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    count: z.number().optional().describe("Number of records to add (default 1)"),
    position: z.number().optional().describe("Insert position (default: end)"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ databaseName, tableName, count, position, modelId }) => {
    return safeToolCall("db_add_records", () => backend.dbAddRecords({ databaseName, tableName, count, position, modelId }), { databaseName, tableName, count, position, modelId });
  }
);

server.tool(
  "db_delete_records",
  "Delete records from a database table. endRecord is inclusive (deletes through and including that index).",
  {
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    startRecord: z.number().describe("Start record index (0-based)"),
    endRecord: z.number().describe("End record index (inclusive)"),
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ databaseName, tableName, startRecord, endRecord, modelId }) => {
    return safeToolCall("db_delete_records", () => backend.dbDeleteRecords({ databaseName, tableName, startRecord, endRecord, modelId }), { databaseName, tableName, startRecord, endRecord, modelId });
  }
);

// ============================================================================
// RESOURCE POOL STATS
// ============================================================================

server.tool(
  "resource_pool_get_stats",
  "Get utilization statistics for a Resource Pool block (allocated, available, queue length).",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Resource Pool block ID")
  },
  async ({ modelId, blockId }) => {
    return safeToolCall("resource_pool_get_stats", () => backend.resourcePoolGetStats({ modelId, blockId }), { modelId, blockId });
  }
);

// ============================================================================
// SIMULATION SETUP TOOLS
// ============================================================================

server.tool(
  "simulation_setup_get",
  "Get current simulation setup: start/end time, number of runs, random seed.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_setup_get", () => backend.simulationSetupGet({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_setup_set",
  "Set simulation parameters: end time, start time, number of runs, random seed, time units, delta time, num steps. Preferred over execute_command for simulation settings.",
  {
    modelId: z.string().optional().describe("Model ID"),
    endTime: z.number().optional().describe("Simulation end time"),
    startTime: z.number().optional().describe("Simulation start time"),
    numberOfRuns: z.number().optional().describe("Number of simulation runs"),
    randomSeed: z.number().optional().describe("Random number seed"),
    seedControl: z.union([z.literal(0), z.literal(1)]).optional().describe("Seed control (0=use seed, 1=random each run)"),
    timeUnits: z.number().optional().describe("Time units (via SetTimeUnits)"),
    deltaTime: z.number().optional().describe("Delta time for continuous models"),
    numSteps: z.number().optional().describe("Number of steps for continuous models"),
    simulationOrder: z.number().optional().describe("Simulation order (0=left-to-right, 2=flow, 3=custom)")
  },
  async ({ modelId, endTime, startTime, numberOfRuns, randomSeed, seedControl, timeUnits, deltaTime, numSteps, simulationOrder }) => {
    return safeToolCall("simulation_setup_set", () => backend.simulationSetupSet({
      modelId, endTime, startTime, numberOfRuns, randomSeed, seedControl, timeUnits, deltaTime, numSteps, simulationOrder
    }), { modelId, endTime, startTime, numberOfRuns, randomSeed, seedControl, timeUnits, deltaTime, numSteps, simulationOrder });
  }
);

// ============================================================================
// BLOCK STATISTICS TOOLS
// ============================================================================

server.tool(
  "block_get_stats",
  "Get statistics for a single block after simulation (throughput, utilization, wait times).",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID to get statistics for")
  },
  async ({ modelId, blockId }) => {
    return safeToolCall("block_get_stats", () => backend.blockGetStats({ modelId, blockId }), { modelId, blockId });
  }
);

server.tool(
  "simulation_get_block_stats",
  "Get statistics for multiple blocks at once after simulation. More efficient than calling block_get_stats repeatedly.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockIds: z.array(z.number()).describe("Array of block IDs to get statistics for")
  },
  async ({ modelId, blockIds }) => {
    return safeToolCall("simulation_get_block_stats", () => backend.simulationGetBlockStats({ modelId, blockIds }), { modelId, blockIds });
  }
);

// ============================================================================
// MULTI-RUN AND SCENARIO TOOLS
// ============================================================================

server.tool(
  "simulation_run_multi",
  "Run multiple simulation replications and collect aggregated statistics. Returns per-run and summary results for specified blocks.",
  {
    modelId: z.string().optional().describe("Model ID"),
    numberOfRuns: z.number().min(1).max(100).describe("Number of simulation replications (1-100)"),
    endTime: z.number().optional().describe("Simulation end time"),
    randomSeed: z.number().optional().describe("Initial random seed"),
    runMode: z.enum(["normal", "fast"]).optional().describe("Run mode"),
    collectPerRun: z.boolean().optional().describe("Collect detailed results per run (default true)"),
    blockIds: z.array(z.number()).optional().describe("Block IDs to collect detailed statistics for")
  },
  async ({ modelId, numberOfRuns, endTime, randomSeed, runMode, collectPerRun, blockIds }) => {
    return safeToolCall("simulation_run_multi", () => backend.simulationRunMulti({
      modelId, numberOfRuns, endTime, randomSeed, runMode, collectPerRun, blockIds
    }), { modelId, numberOfRuns, endTime, randomSeed, runMode, collectPerRun, blockIds });
  }
);

server.tool(
  "simulation_run_scenarios",
  "Run what-if scenarios by varying a single parameter across multiple values. Runs simulation for each value and collects results.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID containing the parameter to vary"),
    dialogVariable: z.string().describe("Dialog variable name to change between scenarios"),
    values: z.array(z.union([z.number(), z.string()])).max(20).describe("Values to test (max 20)"),
    endTime: z.number().optional().describe("Simulation end time"),
    runMode: z.enum(["normal", "fast"]).optional().describe("Run mode")
  },
  async ({ modelId, blockId, dialogVariable, values, endTime, runMode }) => {
    return safeToolCall("simulation_run_scenarios", () => backend.simulationRunScenarios({
      modelId, blockId, dialogVariable, values, endTime, runMode
    }), { modelId, blockId, dialogVariable, values, endTime, runMode });
  }
);

// ============================================================================
// v1.5 TOOLS - Hierarchies, Optimizer, Scenario Manager, Analysis Manager
// ============================================================================

server.tool(
  "hierarchy_list",
  "List all hierarchy blocks (H-blocks) in the model with their nesting structure.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("hierarchy_list", () => backend.hierarchyList({ modelId }), { modelId });
  }
);

server.tool(
  "hierarchy_get_contents",
  "Get the blocks and connections inside a hierarchy block (H-block).",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("H-block ID to inspect")
  },
  async ({ modelId, blockId }) => {
    return safeToolCall("hierarchy_get_contents", () => backend.hierarchyGetContents({ modelId, blockId }), { modelId, blockId });
  }
);

server.tool(
  "optimizer_run",
  "Run the ExtendSim optimizer. Returns immediately by default (fire-and-forget) — poll simulation_status and use optimizer_get_results. Set waitForCompletion=true to block until done.",
  {
    modelId: z.string().optional().describe("Model ID"),
    timeout: z.number().optional().describe("Max seconds to wait when waitForCompletion=true (default 600)"),
    waitForCompletion: z.boolean().optional().describe("Wait until optimizer completes (default false). Set true for blocking mode.")
  },
  async ({ modelId, timeout, waitForCompletion }) => {
    return safeToolCall("optimizer_run", () => backend.optimizerRun({ modelId, timeout, waitForCompletion }), { modelId, timeout, waitForCompletion });
  }
);

server.tool(
  "optimizer_get_results",
  "Read optimization results: best cost, generation, convergence, and elapsed time from an Optimizer block.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Optimizer block ID (Analysis.lbr)")
  },
  async ({ modelId, blockId }) => {
    return safeToolCall("optimizer_get_results", () => backend.optimizerGetResults({ modelId, blockId }), { modelId, blockId });
  }
);

server.tool(
  "scenario_manager_run",
  "Run the Scenario Manager. Returns immediately by default (fire-and-forget) — poll scenario_manager_status and use scenario_manager_get_results. Validates config and auto-selects all scenarios if none are selected. Set waitForCompletion=true to block until done.",
  {
    modelId: z.string().optional().describe("Model ID"),
    timeout: z.number().optional().describe("Max seconds to wait when waitForCompletion=true (default 600)"),
    waitForCompletion: z.boolean().optional().describe("Wait until SM completes (default false). Set true for blocking mode.")
  },
  async ({ modelId, timeout, waitForCompletion }) => {
    return safeToolCall("scenario_manager_run", () => backend.scenarioManagerRun({ modelId, timeout, waitForCompletion }), { modelId, timeout, waitForCompletion });
  }
);

server.tool(
  "scenario_manager_status",
  "Check progress of a running Scenario Manager. Returns currentScenario (e.g. '5/20'), currentRun, phase, and running boolean. Use after scenario_manager_run to poll progress.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("scenario_manager_status", () => backend.scenarioManagerStatus({ modelId }), { modelId });
  }
);

server.tool(
  "scenario_manager_get_results",
  "Collect results from a completed Scenario Manager run. Returns all scenario data from Scenarios_tbl including factor values and response values. Use after scenario_manager_status shows running=false.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("scenario_manager_get_results", () => backend.scenarioManagerGetResults({ modelId }), { modelId });
  }
);

// ============================================================================
// AI CONTEXT PERSISTENCE (v1.9.5)
// ============================================================================

server.tool(
  "context_get",
  "Read stored AI context from the model's internal database. Returns purpose, assumptions, key blocks, notes, tags, and change history. Returns {exists: false} if no context has been saved yet.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("context_get", () => backend.contextGet({ modelId }), { modelId });
  }
);

server.tool(
  "context_set",
  "Save or update AI context in the model's internal database. Only provided fields are updated (merge semantics). Context persists inside the .mox file and auto-loads on model_open. Use after building or modifying a model.",
  {
    modelId: z.string().optional().describe("Model ID"),
    purpose: z.string().optional().describe("What this model simulates (e.g. 'Three-line manufacturing plant with CNC bottleneck')"),
    keyBlocks: z.array(z.object({
      blockId: z.number().describe("Block ID"),
      label: z.string().describe("Block label"),
      role: z.string().describe("Role in the model (e.g. 'bottleneck', 'arrival source')")
    })).optional().describe("Important blocks and their roles"),
    assumptions: z.array(z.string()).optional().describe("Model assumptions (e.g. ['Arrivals exponential mean 5 min', 'Shift 8h/16h'])"),
    notes: z.string().optional().describe("Free-form notes (e.g. 'Customer wants 95th percentile < 60 min')"),
    tags: z.array(z.string()).optional().describe("Tags for categorization (e.g. ['manufacturing', 'bottleneck-analysis'])"),
    custom: z.record(z.any()).optional().describe("Custom key-value pairs for additional context"),
    changeEntry: z.object({
      summary: z.string().describe("Short summary of what changed"),
      details: z.string().optional().describe("Detailed description of the change")
    }).optional().describe("Append an entry to the change history log")
  },
  async ({ modelId, purpose, keyBlocks, assumptions, notes, tags, custom, changeEntry }) => {
    return safeToolCall("context_set", () => backend.contextSet({ modelId, purpose, keyBlocks, assumptions, notes, tags, custom, changeEntry }), { modelId, purpose, keyBlocks, assumptions, notes, tags, custom, changeEntry });
  }
);

server.tool(
  "context_clear",
  "Delete the AI_Context database from the model. Requires confirm=true as a safety guard. This permanently removes all stored context and change history.",
  {
    modelId: z.string().optional().describe("Model ID"),
    confirm: z.boolean().describe("Must be true to confirm deletion")
  },
  async ({ modelId, confirm }) => {
    return safeToolCall("context_clear", () => backend.contextClear({ modelId, confirm }), { modelId, confirm });
  }
);

// ============================================================================
// v1.10.0 — BLOCK TOOLS
// ============================================================================

server.tool(
  "block_move",
  "Reposition an existing block to an absolute pixel position on the worksheet.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID to move"),
    x: z.number().describe("New X position in pixels"),
    y: z.number().describe("New Y position in pixels")
  },
  async ({ modelId, blockId, x, y }) => {
    return safeToolCall("block_move", () => backend.blockMove({ modelId, blockId, x, y }), { modelId, blockId, x, y });
  }
);

server.tool(
  "block_get_position",
  "Read a block's current position and size on the worksheet. Returns x, y, width, height, and bounds.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID")
  },
  async ({ modelId, blockId }) => {
    return safeToolCall("block_get_position", () => backend.blockGetPosition({ modelId, blockId }), { modelId, blockId });
  }
);

server.tool(
  "block_align",
  "Auto-straighten a connection line by moving the target block so the connection is straight (horizontal or vertical).",
  {
    modelId: z.string().optional().describe("Model ID"),
    sourceBlockId: z.number().describe("Source block ID"),
    sourceConnector: z.union([z.number(), z.string()]).describe("Source output connector name or index"),
    targetBlockId: z.number().describe("Target block ID"),
    targetConnector: z.union([z.number(), z.string()]).describe("Target input connector name or index"),
    vertical: z.boolean().optional().default(true).describe("True for vertical alignment, false for horizontal")
  },
  async ({ modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector, vertical }) => {
    return safeToolCall("block_align", () => backend.blockAlign({ modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector, vertical }), { modelId, sourceBlockId, sourceConnector, targetBlockId, targetConnector, vertical });
  }
);

server.tool(
  "block_duplicate",
  "Copy a block with all its settings. Returns the new block ID. Optionally set a label on the copy.",
  {
    modelId: z.string().optional().describe("Model ID"),
    blockId: z.number().describe("Block ID to duplicate"),
    label: z.string().optional().describe("Label for the new copy")
  },
  async ({ modelId, blockId, label }) => {
    return safeToolCall("block_duplicate", () => backend.blockDuplicate({ modelId, blockId, label }), { modelId, blockId, label });
  }
);

server.tool(
  "block_find",
  "Find a block by its label or block type name. Returns blockId, blockName, and label.",
  {
    modelId: z.string().optional().describe("Model ID"),
    searchStr: z.string().describe("Text to search for"),
    which: z.number().optional().default(1).describe("Search type: 1=label (default), 2=block type name")
  },
  async ({ modelId, searchStr, which }) => {
    return safeToolCall("block_find", () => backend.blockFind({ modelId, searchStr, which }), { modelId, searchStr, which });
  }
);

// ============================================================================
// v1.10.0 — DATABASE TOOLS
// ============================================================================

server.tool(
  "db_create",
  "Create a database with optional tables and fields. Idempotent — skips existing DB/table/field. Types: 'integer', 'real', 'string', 'boolean'.",
  {
    modelId: z.string().optional().describe("Model ID"),
    databaseName: z.string().describe("Database name to create"),
    tables: z.array(z.object({
      name: z.string().describe("Table name"),
      fields: z.array(z.object({
        name: z.string().describe("Field name"),
        type: z.enum(["integer", "real", "string", "boolean"]).optional().default("real").describe("Field type")
      })).optional().describe("Fields to create in the table")
    })).optional().describe("Tables to create in the database")
  },
  async ({ modelId, databaseName, tables }) => {
    return safeToolCall("db_create", () => backend.dbCreate({ modelId, databaseName, tables }), { modelId, databaseName, tables });
  }
);

server.tool(
  "db_import",
  "Import data from a CSV/delimited file into a database table. Negative dbIdx means first line has field names.",
  {
    modelId: z.string().optional().describe("Model ID"),
    filePath: z.string().describe("Path to the data file"),
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    delimiter: z.string().optional().default(",").describe("Field delimiter (default ',')"),
    hasHeader: z.boolean().optional().default(true).describe("First line contains field names (default true)")
  },
  async ({ modelId, filePath, databaseName, tableName, delimiter, hasHeader }) => {
    return safeToolCall("db_import", () => backend.dbImport({ modelId, filePath, databaseName, tableName, delimiter, hasHeader }), { modelId, filePath, databaseName, tableName, delimiter, hasHeader });
  }
);

server.tool(
  "db_export",
  "Export a database table to a CSV/delimited file.",
  {
    modelId: z.string().optional().describe("Model ID"),
    filePath: z.string().describe("Path to write the data file"),
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    delimiter: z.string().optional().default(",").describe("Field delimiter (default ',')"),
    includeHeader: z.boolean().optional().default(true).describe("Include field names as first line (default true)")
  },
  async ({ modelId, filePath, databaseName, tableName, delimiter, includeHeader }) => {
    return safeToolCall("db_export", () => backend.dbExport({ modelId, filePath, databaseName, tableName, delimiter, includeHeader }), { modelId, filePath, databaseName, tableName, delimiter, includeHeader });
  }
);

server.tool(
  "db_find_record",
  "Search for a record in a database table by field value. Returns the record index or not-found.",
  {
    modelId: z.string().optional().describe("Model ID"),
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    fieldName: z.string().describe("Field name to search in"),
    findValue: z.union([z.number(), z.string()]).describe("Value to search for"),
    exactMatch: z.boolean().optional().default(true).describe("Exact match (default true)"),
    startRecord: z.number().optional().default(0).describe("Record index to start searching from (default 0)")
  },
  async ({ modelId, databaseName, tableName, fieldName, findValue, exactMatch, startRecord }) => {
    return safeToolCall("db_find_record", () => backend.dbFindRecord({ modelId, databaseName, tableName, fieldName, findValue, exactMatch, startRecord }), { modelId, databaseName, tableName, fieldName, findValue, exactMatch, startRecord });
  }
);

server.tool(
  "db_sort",
  "Sort a database table by up to 3 fields. Direction: 0=ascending, 1=descending.",
  {
    modelId: z.string().optional().describe("Model ID"),
    databaseName: z.string().describe("Database name"),
    tableName: z.string().describe("Table name"),
    field1: z.string().describe("Primary sort field name"),
    direction1: z.number().optional().default(0).describe("Primary sort direction: 0=ascending, 1=descending"),
    field2: z.string().optional().describe("Secondary sort field name"),
    direction2: z.number().optional().default(0).describe("Secondary sort direction"),
    field3: z.string().optional().describe("Tertiary sort field name"),
    direction3: z.number().optional().default(0).describe("Tertiary sort direction")
  },
  async ({ modelId, databaseName, tableName, field1, direction1, field2, direction2, field3, direction3 }) => {
    return safeToolCall("db_sort", () => backend.dbSort({ modelId, databaseName, tableName, field1, direction1, field2, direction2, field3, direction3 }), { modelId, databaseName, tableName, field1, direction1, field2, direction2, field3, direction3 });
  }
);

server.tool(
  "db_relations_list",
  "List all relations in a database.",
  {
    modelId: z.string().optional().describe("Model ID"),
    databaseName: z.string().describe("Database name")
  },
  async ({ modelId, databaseName }) => {
    return safeToolCall("db_relations_list", () => backend.dbRelationsList({ modelId, databaseName }), { modelId, databaseName });
  }
);

server.tool(
  "db_relation_create",
  "Create a relation between two tables in a database (child field -> parent field).",
  {
    modelId: z.string().optional().describe("Model ID"),
    databaseName: z.string().describe("Database name"),
    childTable: z.string().describe("Child table name"),
    childField: z.string().describe("Child field name"),
    parentTable: z.string().describe("Parent table name"),
    parentField: z.string().describe("Parent field name")
  },
  async ({ modelId, databaseName, childTable, childField, parentTable, parentField }) => {
    return safeToolCall("db_relation_create", () => backend.dbRelationCreate({ modelId, databaseName, childTable, childField, parentTable, parentField }), { modelId, databaseName, childTable, childField, parentTable, parentField });
  }
);

// ============================================================================
// v1.10.0 — SIMULATION TOOLS
// ============================================================================

server.tool(
  "simulation_step",
  "Single-step the simulation by one event. Returns current time and phase after step. Useful for debug-stepping through a simulation.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_step", () => backend.simulationStep({ modelId }), { modelId });
  }
);

server.tool(
  "simulation_get_state",
  "Read live simulation system variables: CurrentTime, CurrentStep, CurrentSim, NumSteps, NumSims, EndTime, RandomSeed, and phase.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("simulation_get_state", () => backend.simulationGetState({ modelId }), { modelId });
  }
);

// ============================================================================
// v1.10.0 — GLOBAL ARRAY TOOLS
// ============================================================================

server.tool(
  "ga_list",
  "List all global arrays in the model with their names, sizes, and types.",
  {
    modelId: z.string().optional().describe("Model ID")
  },
  async ({ modelId }) => {
    return safeToolCall("ga_list", () => backend.gaList({ modelId }), { modelId });
  }
);

server.tool(
  "ga_create",
  "Create a global array. Types: 'real' (default), 'integer', 'string'.",
  {
    modelId: z.string().optional().describe("Model ID"),
    name: z.string().describe("Array name"),
    type: z.enum(["real", "integer", "string"]).optional().default("real").describe("Data type"),
    cols: z.number().optional().default(1).describe("Number of columns"),
    rows: z.number().optional().default(0).describe("Number of rows (0 = empty)")
  },
  async ({ modelId, name, type, cols, rows }) => {
    return safeToolCall("ga_create", () => backend.gaCreate({ modelId, name, type, cols, rows }), { modelId, name, type, cols, rows });
  }
);

server.tool(
  "ga_read",
  "Read from a global array. Single cell (row, col) or range (row..endRow, col..endCol).",
  {
    modelId: z.string().optional().describe("Model ID"),
    name: z.string().describe("Array name"),
    row: z.number().optional().default(0).describe("Row index (0-based)"),
    col: z.number().optional().default(0).describe("Column index (0-based)"),
    endRow: z.number().optional().describe("End row for range read (inclusive)"),
    endCol: z.number().optional().describe("End column for range read (inclusive)")
  },
  async ({ modelId, name, row, col, endRow, endCol }) => {
    return safeToolCall("ga_read", () => backend.gaRead({ modelId, name, row, col, endRow, endCol }), { modelId, name, row, col, endRow, endCol });
  }
);

server.tool(
  "ga_write",
  "Write a value to a global array cell.",
  {
    modelId: z.string().optional().describe("Model ID"),
    name: z.string().describe("Array name"),
    row: z.number().describe("Row index (0-based)"),
    col: z.number().describe("Column index (0-based)"),
    value: z.union([z.number(), z.string()]).describe("Value to write")
  },
  async ({ modelId, name, row, col, value }) => {
    return safeToolCall("ga_write", () => backend.gaWrite({ modelId, name, row, col, value }), { modelId, name, row, col, value });
  }
);

// ============================================================================
// v1.10.0 — TEXT BLOCK & TIME CONVERT
// ============================================================================

server.tool(
  "text_block_add",
  "Place a text annotation block on the worksheet. Returns block ID.",
  {
    modelId: z.string().optional().describe("Model ID"),
    text: z.string().describe("Text content for the annotation"),
    x: z.number().optional().default(100).describe("X position in pixels"),
    y: z.number().optional().default(100).describe("Y position in pixels"),
    neighbor: z.number().optional().default(-1).describe("Block ID to place relative to (-1 for absolute)"),
    side: z.number().optional().default(2).describe("Side relative to neighbor: 0=left, 1=top, 2=right, 3=bottom"),
    width: z.number().optional().default(200).describe("Text block width in pixels")
  },
  async ({ modelId, text, x, y, neighbor, side, width }) => {
    return safeToolCall("text_block_add", () => backend.textBlockAdd({ modelId, text, x, y, neighbor, side, width }), { modelId, text, x, y, neighbor, side, width });
  }
);

server.tool(
  "time_convert",
  "Time/date conversion utility. Operations: 'convert_units' (value between time units), 'sim_to_date' (simulation time to date string), 'date_to_sim' (date string to simulation time).",
  {
    modelId: z.string().optional().describe("Model ID"),
    operation: z.enum(["convert_units", "sim_to_date", "date_to_sim"]).describe("Conversion operation"),
    value: z.number().optional().describe("Value to convert (for convert_units)"),
    fromType: z.number().optional().describe("Source time unit type (for convert_units)"),
    toType: z.number().optional().describe("Target time unit type (for convert_units)"),
    simTime: z.number().optional().describe("Simulation time (for sim_to_date)"),
    timeUnits: z.number().optional().describe("Time units (for sim_to_date and date_to_sim)"),
    date: z.string().optional().describe("Date string (for date_to_sim)")
  },
  async ({ modelId, operation, value, fromType, toType, simTime, timeUnits, date }) => {
    return safeToolCall("time_convert", () => backend.timeConvert({ modelId, operation, value, fromType, toType, simTime, timeUnits, date }), { modelId, operation, value, fromType, toType, simTime, timeUnits, date });
  }
);

// ============================================================================
// TELEMETRY CONTROL
// ============================================================================

server.tool(
  "telemetry_control",
  `Check telemetry status. Call this when you notice repeated failures or are stuck in a loop, when the user reports frustration, or when you want to ask the user to share telemetry data with the developers. When asking the user to share, ALWAYS explain: "The telemetry log records which tools were used and in what order, plus error codes. It NEVER contains your model data, file names, block labels, or parameter values. You can inspect the file yourself before sharing." Returns: event count, error count, file path, file size, session ID.`,
  {
    action: z.enum(["get_status"]).describe("Action to perform")
  },
  async ({ action }) => {
    const startTime = performance.now();
    const status = getTelemetryStatus();
    recordToolCall("telemetry_control", startTime, { status: "ok" });
    return toolResponse(status);
  }
);

// ============================================================================
// START SERVER
// ============================================================================

async function main() {
  // Initialize telemetry (local-only, fire-and-forget)
  initTelemetry(serverVersion);

  // Initialize Python backend
  await backend.initBackend();

  // Determine transport mode from env or CLI args
  const useHttp = process.env.MCP_TRANSPORT === 'http' || process.argv.includes('--http');

  if (useHttp) {
    const port = parseInt(process.env.MCP_PORT || '3001', 10);
    const app = createMcpExpressApp();

    // Map to store transports by session ID
    const transports: Record<string, StreamableHTTPServerTransport> = {};

    app.post('/mcp', async (req, res) => {
      const sessionId = req.headers['mcp-session-id'] as string | undefined;
      try {
        let transport: StreamableHTTPServerTransport;
        if (sessionId && transports[sessionId]) {
          transport = transports[sessionId];
        } else if (!sessionId && isInitializeRequest(req.body)) {
          transport = new StreamableHTTPServerTransport({
            sessionIdGenerator: () => randomUUID(),
            onsessioninitialized: (sid) => {
              transports[sid] = transport;
            }
          });
          transport.onclose = () => {
            const sid = transport.sessionId;
            if (sid && transports[sid]) {
              delete transports[sid];
            }
          };
          await server.connect(transport);
          await transport.handleRequest(req, res, req.body);
          return;
        } else {
          res.status(400).json({
            jsonrpc: '2.0',
            error: { code: -32000, message: 'Bad Request: No valid session ID provided' },
            id: null
          });
          return;
        }
        await transport.handleRequest(req, res, req.body);
      } catch (error) {
        console.error('Error handling MCP request:', error);
        if (!res.headersSent) {
          res.status(500).json({
            jsonrpc: '2.0',
            error: { code: -32603, message: 'Internal server error' },
            id: null
          });
        }
      }
    });

    app.get('/mcp', async (req, res) => {
      const sessionId = req.headers['mcp-session-id'] as string | undefined;
      if (!sessionId || !transports[sessionId]) {
        res.status(400).send('Invalid or missing session ID');
        return;
      }
      await transports[sessionId].handleRequest(req, res);
    });

    app.delete('/mcp', async (req, res) => {
      const sessionId = req.headers['mcp-session-id'] as string | undefined;
      if (!sessionId || !transports[sessionId]) {
        res.status(400).send('Invalid or missing session ID');
        return;
      }
      await transports[sessionId].handleRequest(req, res);
    });

    app.listen(port, () => {
      console.error(`Simulations MCP Server running on http://localhost:${port}/mcp`);
    });

    process.on('SIGINT', async () => {
      console.error('Shutting down...');
      closeTelemetry();
      backend.shutdownBackend();
      for (const sid in transports) {
        try { await transports[sid].close(); } catch { /* ignore */ }
        delete transports[sid];
      }
      process.exit(0);
    });
  } else {
    // Default: stdio transport (for Claude Code CLI)
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("Simulations MCP Server running on stdio");
    if (SESSION_LOG_ENABLED) {
      if (!existsSync(SESSION_LOG_DIR)) mkdirSync(SESSION_LOG_DIR, { recursive: true });
      appendFileSync(SESSION_LOG_PATH, `\n${"=".repeat(80)}\n[${new Date().toISOString()}] SESSION START\n${"=".repeat(80)}\n\n`);
      console.error(`Session logging enabled: ${SESSION_LOG_PATH}`);
    }
  }

  // Safety net: clean up Python process and telemetry on exit (C3 fix)
  process.on('exit', () => {
    closeTelemetry();
    backend.shutdownBackend();
  });
}

main().catch(console.error);
