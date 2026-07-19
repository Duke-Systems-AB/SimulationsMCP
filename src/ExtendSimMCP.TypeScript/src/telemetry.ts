/**
 * Telemetry module — local-only usage pattern logging.
 *
 * Always-on, fire-and-forget. Writes JSONL to temp/telemetry/telemetry.jsonl.
 * Nothing leaves the machine automatically. Privacy-safe: no file paths,
 * labels, parameter values, or user-defined names are logged.
 */

import { randomBytes } from "node:crypto";
import { createWriteStream, existsSync, mkdirSync, renameSync, statSync, WriteStream } from "node:fs";
import { join } from "node:path";
import { platform, version as nodeVersion, release as osRelease } from "node:os";

const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10MB rotation threshold

let sessionId = "";
let sequence = 0;
let eventCount = 0;
let errorCount = 0;
let writeStream: WriteStream | null = null;
let filePath = "";
let mcpVersion = "";
let envInfo: Record<string, string> = {};

// ── Public API ──────────────────────────────────────────────────────────

export function initTelemetry(version: string): void {
  try {
    mcpVersion = version;
    sessionId = randomBytes(3).toString("hex");
    sequence = 0;
    eventCount = 0;
    errorCount = 0;

    // Resolve temp/telemetry/ relative to project root (dist/../..)
    const telemetryDir = join(__dirname, "..", "..", "..", "temp", "telemetry");
    if (!existsSync(telemetryDir)) {
      mkdirSync(telemetryDir, { recursive: true });
    }

    filePath = join(telemetryDir, "telemetry.jsonl");

    // Rotate if >10MB
    if (existsSync(filePath)) {
      try {
        const stat = statSync(filePath);
        if (stat.size > MAX_FILE_SIZE) {
          const ts = new Date().toISOString().replace(/[:.]/g, "-");
          renameSync(filePath, join(telemetryDir, `telemetry-${ts}.jsonl`));
        }
      } catch { /* ignore rotation errors */ }
    }

    writeStream = createWriteStream(filePath, { flags: "a" });

    // Write session_start event
    writeEvent({
      ts: new Date().toISOString(),
      sid: sessionId,
      seq: sequence++,
      type: "session_start",
      env: {
        mcpVersion,
        nodeVersion,
        platform: platform(),
        os: `${platform()}/${osRelease()}`,
      },
    });
  } catch {
    // Telemetry init failure is non-fatal
    writeStream = null;
  }
}

export function recordToolCall(
  toolName: string,
  startTime: number,
  result: any,
  params?: Record<string, unknown>,
): void {
  try {
    const durMs = Math.round(performance.now() - startTime);
    const ok = result?.status !== "error" && result?.success !== false;

    const event: Record<string, unknown> = {
      ts: new Date().toISOString(),
      sid: sessionId,
      seq: sequence++,
      tool: toolName,
      ok,
      dur_ms: durMs,
    };

    if (!ok) {
      errorCount++;
      if (result?.errorCode) event.err = result.errorCode;
    }

    if (result?.dialog?.found) {
      event.dialog = true;
    }

    const ctx = extractContext(toolName, params);
    if (ctx && Object.keys(ctx).length > 0) {
      event.ctx = ctx;
    }

    eventCount++;
    writeEvent(event);
  } catch {
    // Fire-and-forget — never throw
  }
}

export function getStatus(): Record<string, unknown> {
  let fileSize = 0;
  try {
    if (filePath && existsSync(filePath)) {
      fileSize = statSync(filePath).size;
    }
  } catch { /* ignore */ }

  return {
    sessionId,
    eventCount,
    errorCount,
    filePath: filePath || "(not initialized)",
    fileSize,
    mcpVersion,
    submitTo: "mcp-bug-report@duke.se",
  };
}

export function updateEnvInfo(key: string, value: string): void {
  try {
    envInfo[key] = value;
  } catch { /* ignore */ }
}

export function closeTelemetry(): void {
  try {
    if (writeStream) {
      writeEvent({
        ts: new Date().toISOString(),
        sid: sessionId,
        seq: sequence++,
        type: "session_end",
        totalCalls: eventCount,
        totalErrors: errorCount,
      });
      writeStream.end();
      writeStream = null;
    }
  } catch {
    writeStream = null;
  }
}

// ── Internal helpers ────────────────────────────────────────────────────

function writeEvent(event: Record<string, unknown>): void {
  if (!writeStream) return;
  try {
    writeStream.write(JSON.stringify(event) + "\n");
  } catch { /* ignore write errors */ }
}

/**
 * Extract privacy-safe context from tool parameters.
 * Only block types, library names, connector names, template names,
 * run modes, and numeric counts are logged. Never file paths, labels,
 * parameter values, equation text, or user-defined names.
 */
export function extractContext(
  toolName: string,
  params?: Record<string, unknown>,
): Record<string, unknown> | null {
  if (!params) return null;

  switch (toolName) {
    case "block_add":
    case "block_discover":
    case "block_introspect":
      return pick(params, "libraryName", "blockName");

    case "block_add_batch":
      return { count: Array.isArray(params.blocks) ? params.blocks.length : 0 };

    case "block_connect":
    case "block_disconnect":
      return pick(params, "sourceConnector", "targetConnector");

    case "connect_chain":
      return { chainLength: Array.isArray(params.blockIds) ? params.blockIds.length : 0 };

    case "connect_graph":
      return { connectionCount: Array.isArray(params.connections) ? params.connections.length : 0 };

    case "block_template":
      return pick(params, "templateName");

    case "block_configure":
      return null; // Config may contain sensitive values

    case "simulation_run":
      return pick(params, "runMode", "includeStats");

    case "simulation_run_multi":
      return pick(params, "numberOfRuns", "runMode");

    case "simulation_run_scenarios":
      return { scenarioCount: Array.isArray(params.values) ? params.values.length : 0 };

    case "simulation_setup_set":
      return pick(params, "endTime", "numberOfRuns");

    case "pattern_search":
      return pick(params, "category", "domain", "complexity");

    case "modeling_guide":
      return pick(params, "category", "scenario");

    default:
      return null;
  }
}

function pick(
  obj: Record<string, unknown>,
  ...keys: string[]
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const key of keys) {
    if (obj[key] !== undefined) {
      result[key] = obj[key];
    }
  }
  return result;
}
