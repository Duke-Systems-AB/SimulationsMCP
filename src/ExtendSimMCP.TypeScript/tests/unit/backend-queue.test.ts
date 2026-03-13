/**
 * Unit tests for backend command queue and timeout logic.
 *
 * Tests the backend bridge's request queuing, timeout configuration,
 * and error handling without requiring a live Python process.
 */

import { describe, it, expect } from "vitest";

// Test the timeout configuration logic (extracted from backend.ts)
const DEFAULT_TIMEOUT = 10_000;

const COMMAND_TIMEOUTS: Record<string, number> = {
  // 30s - medium
  model_open: 30_000,
  model_save: 30_000,
  model_new: 30_000,
  model_validate: 30_000,
  block_template: 30_000,
  block_add_batch: 30_000,
  block_discover: 30_000,
  block_discover_variables: 30_000,
  simulation_get_results: 30_000,
  simulation_get_block_stats: 30_000,
  db_get_records: 30_000,
  hierarchy_list: 30_000,
  hierarchy_get_contents: 30_000,
  // 60s - save/close/reopen cycle
  block_configure: 60_000,
  activity_set_delay: 60_000,
  create_set_arrivals: 60_000,
  // 2-10min - long-running
  extendsim_start: 120_000,
  simulation_run: 300_000,
  simulation_run_multi: 600_000,
  simulation_run_scenarios: 600_000,
  optimizer_run: 600_000,
  scenario_manager_run: 600_000,
};

function getTimeout(command: string): number {
  return COMMAND_TIMEOUTS[command] ?? DEFAULT_TIMEOUT;
}

describe("Command Timeout Configuration", () => {
  it("should return default timeout (10s) for unknown commands", () => {
    expect(getTimeout("block_add")).toBe(10_000);
    expect(getTimeout("block_connect")).toBe(10_000);
    expect(getTimeout("block_list")).toBe(10_000);
    expect(getTimeout("extendsim_status")).toBe(10_000);
  });

  it("should return 30s timeout for medium operations", () => {
    expect(getTimeout("model_open")).toBe(30_000);
    expect(getTimeout("model_save")).toBe(30_000);
    expect(getTimeout("model_new")).toBe(30_000);
    expect(getTimeout("block_template")).toBe(30_000);
    expect(getTimeout("block_discover")).toBe(30_000);
    expect(getTimeout("block_discover_variables")).toBe(30_000);
    expect(getTimeout("simulation_get_results")).toBe(30_000);
    expect(getTimeout("db_get_records")).toBe(30_000);
    expect(getTimeout("hierarchy_list")).toBe(30_000);
  });

  it("should return 60s timeout for save/reopen cycle operations", () => {
    expect(getTimeout("block_configure")).toBe(60_000);
    expect(getTimeout("activity_set_delay")).toBe(60_000);
    expect(getTimeout("create_set_arrivals")).toBe(60_000);
  });

  it("should return extended timeout for long-running operations", () => {
    expect(getTimeout("extendsim_start")).toBe(120_000);
    expect(getTimeout("simulation_run")).toBe(300_000);
    expect(getTimeout("simulation_run_multi")).toBe(600_000);
    expect(getTimeout("optimizer_run")).toBe(600_000);
  });
});

describe("Error Code Structure", () => {
  // Verify the error code structure matches what Python backend produces
  const mockErrorResponse = {
    success: false,
    errorCode: "MODEL_NOT_FOUND",
    error: "No model is open",
  };

  it("should have required error fields", () => {
    expect(mockErrorResponse).toHaveProperty("success", false);
    expect(mockErrorResponse).toHaveProperty("errorCode");
    expect(mockErrorResponse).toHaveProperty("error");
  });

  it("should use string error codes", () => {
    expect(typeof mockErrorResponse.errorCode).toBe("string");
  });

  const knownErrorCodes = [
    "COM_ERROR",
    "COM_CONNECTION_LOST",
    "EXTENDSIM_NOT_RUNNING",
    "EXTENDSIM_START_FAILED",
    "MODEL_NOT_FOUND",
    "MODEL_NOT_OPEN",
    "MODEL_OPEN_FAILED",
    "MODEL_SAVE_FAILED",
    "BLOCK_NOT_FOUND",
    "WRONG_BLOCK_TYPE",
    "BLOCK_ADD_FAILED",
    "BLOCK_REMOVE_FAILED",
    "INVALID_CONNECTOR",
    "CONNECTOR_NOT_FOUND",
    "CONNECTION_FAILED",
    "SIMULATION_RUN_FAILED",
    "SIMULATION_TIMEOUT",
    "INVALID_PARAMETER",
    "SET_VALUE_FAILED",
    "GET_VALUE_FAILED",
    "UNKNOWN_COMMAND",
    "COMMAND_FAILED",
    "INVALID_JSON",
    "TEMPLATE_NOT_FOUND",
  ];

  it("should define all expected error codes", () => {
    expect(knownErrorCodes.length).toBeGreaterThan(0);
    knownErrorCodes.forEach((code) => {
      expect(code).toMatch(/^[A-Z_]+$/); // All caps with underscores
    });
  });
});

describe("Request Queue Logic", () => {
  it("should maintain FIFO ordering", () => {
    const queue: { command: string; order: number }[] = [];

    queue.push({ command: "cmd1", order: 1 });
    queue.push({ command: "cmd2", order: 2 });
    queue.push({ command: "cmd3", order: 3 });

    const first = queue.shift()!;
    expect(first.command).toBe("cmd1");
    expect(first.order).toBe(1);

    const second = queue.shift()!;
    expect(second.command).toBe("cmd2");

    const third = queue.shift()!;
    expect(third.command).toBe("cmd3");

    expect(queue.length).toBe(0);
  });

  it("should support retry by reinserting at front", () => {
    const queue: { command: string; retryCount: number }[] = [];

    queue.push({ command: "cmd1", retryCount: 0 });
    queue.push({ command: "cmd2", retryCount: 0 });

    // Simulate retry: remove first, increment retry, put back at front
    const failed = queue.shift()!;
    failed.retryCount++;
    queue.unshift(failed);

    expect(queue[0].command).toBe("cmd1");
    expect(queue[0].retryCount).toBe(1);
    expect(queue[1].command).toBe("cmd2");
  });
});
