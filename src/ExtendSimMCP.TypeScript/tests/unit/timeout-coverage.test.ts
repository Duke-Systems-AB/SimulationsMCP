/**
 * Timeout coverage tests — verify timeout configuration consistency.
 *
 * Parses COMMAND_TIMEOUTS from backend.ts to validate that all long-running
 * commands have appropriate timeouts.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SRC_DIR = join(__dirname, "..", "..", "src");

let backendSource: string;
let defaultTimeout: number;
let commandTimeouts: Record<string, number>;

beforeAll(() => {
  backendSource = readFileSync(join(SRC_DIR, "backend.ts"), "utf-8");

  // Extract DEFAULT_TIMEOUT
  const defaultMatch = backendSource.match(
    /const\s+DEFAULT_TIMEOUT\s*=\s*([\d_]+)/
  );
  defaultTimeout = defaultMatch
    ? parseInt(defaultMatch[1].replace(/_/g, ""), 10)
    : 0;

  // Extract COMMAND_TIMEOUTS entries — pattern: command_name: 30_000,
  commandTimeouts = {};
  const timeoutsBlockMatch = backendSource.match(
    /const\s+COMMAND_TIMEOUTS[^{]*\{([^}]+)\}/s
  );
  if (timeoutsBlockMatch) {
    const block = timeoutsBlockMatch[1];
    const entryRegex = /(\w+):\s*([\d_]+)/g;
    let match;
    while ((match = entryRegex.exec(block)) !== null) {
      commandTimeouts[match[1]] = parseInt(match[2].replace(/_/g, ""), 10);
    }
  }
});

describe("Default Timeout", () => {
  it("should have a reasonable default timeout (5-60 seconds)", () => {
    expect(defaultTimeout).toBeGreaterThanOrEqual(5_000);
    expect(defaultTimeout).toBeLessThanOrEqual(60_000);
  });
});

describe("Long-Running Command Timeouts", () => {
  it("simulation_run should have timeout >= 300s (5 min)", () => {
    expect(commandTimeouts["simulation_run"]).toBeGreaterThanOrEqual(300_000);
  });

  it("optimizer_run should have timeout >= 600s (10 min)", () => {
    expect(commandTimeouts["optimizer_run"]).toBeGreaterThanOrEqual(600_000);
  });

  it("scenario_manager_run should have timeout >= 600s (10 min)", () => {
    expect(commandTimeouts["scenario_manager_run"]).toBeGreaterThanOrEqual(
      600_000
    );
  });

  it("long-running commands should exceed default timeout", () => {
    const longRunning = [
      "simulation_run",
      "optimizer_run",
      "scenario_manager_run",
      "simulation_run_multi",
      "simulation_run_scenarios",
    ];
    for (const cmd of longRunning) {
      expect(
        commandTimeouts[cmd],
        `${cmd} should exceed default timeout`
      ).toBeGreaterThan(defaultTimeout);
    }
  });
});

describe("Medium Command Timeouts", () => {
  it("db_import and db_export should have explicit timeouts", () => {
    expect(commandTimeouts["db_import"]).toBeDefined();
    expect(commandTimeouts["db_export"]).toBeDefined();
  });

  it("model operations should have explicit timeouts", () => {
    expect(commandTimeouts["model_open"]).toBeDefined();
    expect(commandTimeouts["model_save"]).toBeDefined();
  });
});

describe("Timeout Sanity Checks", () => {
  it("all timeout values should be positive numbers", () => {
    const invalid: string[] = [];
    for (const [cmd, timeout] of Object.entries(commandTimeouts)) {
      if (typeof timeout !== "number" || timeout <= 0 || isNaN(timeout)) {
        invalid.push(`${cmd}: ${timeout}`);
      }
    }
    expect(invalid).toEqual([]);
  });

  it("no timeout should exceed 15 minutes", () => {
    const maxTimeout = 15 * 60 * 1000; // 900_000
    const excessive: string[] = [];
    for (const [cmd, timeout] of Object.entries(commandTimeouts)) {
      if (timeout > maxTimeout) {
        excessive.push(`${cmd}: ${timeout}ms`);
      }
    }
    expect(excessive).toEqual([]);
  });

  it("should have at least 15 explicit timeout entries", () => {
    expect(Object.keys(commandTimeouts).length).toBeGreaterThanOrEqual(15);
  });
});
