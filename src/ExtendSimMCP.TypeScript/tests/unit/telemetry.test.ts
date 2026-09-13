/**
 * Telemetry module tests — verify event format, privacy filtering,
 * context extraction, and status reporting.
 *
 * Pure unit tests — no file I/O, no ExtendSim needed.
 */

import { describe, it, expect } from "vitest";
import { extractContext } from "../../src/telemetry.js";

describe("Context Extraction", () => {
  it("should extract libraryName and blockName for block_add", () => {
    const ctx = extractContext("block_add", {
      libraryName: "Item.lbr",
      blockName: "Create",
      x: 100,
      y: 200,
      label: "My Source",
    });
    expect(ctx).toEqual({ libraryName: "Item.lbr", blockName: "Create" });
  });

  it("should extract libraryName and blockName for block_discover", () => {
    const ctx = extractContext("block_discover", {
      libraryName: "Item.lbr",
      blockName: "Gate",
      modelId: "m1",
    });
    expect(ctx).toEqual({ libraryName: "Item.lbr", blockName: "Gate" });
  });

  it("should extract batch count for block_add_batch", () => {
    const ctx = extractContext("block_add_batch", {
      blocks: [
        { libraryName: "Item.lbr", blockName: "Create" },
        { libraryName: "Item.lbr", blockName: "Queue" },
        { libraryName: "Item.lbr", blockName: "Activity" },
      ],
    });
    expect(ctx).toEqual({ count: 3 });
  });

  it("should extract connector names for block_connect", () => {
    const ctx = extractContext("block_connect", {
      sourceBlockId: 1,
      sourceConnector: "ItemOut",
      targetBlockId: 2,
      targetConnector: "ItemIn",
    });
    expect(ctx).toEqual({ sourceConnector: "ItemOut", targetConnector: "ItemIn" });
  });

  it("should extract chain length for connect_chain", () => {
    const ctx = extractContext("connect_chain", {
      blockIds: [1, 2, 3, 4],
      sourceConnector: "ItemOut",
      targetConnector: "ItemIn",
    });
    expect(ctx).toEqual({ chainLength: 4 });
  });

  it("should extract connection count for connect_graph", () => {
    const ctx = extractContext("connect_graph", {
      connections: [
        { sourceBlockId: 1, targetBlockId: 2 },
        { sourceBlockId: 2, targetBlockId: 3 },
      ],
    });
    expect(ctx).toEqual({ connectionCount: 2 });
  });

  it("should extract templateName for block_template", () => {
    const ctx = extractContext("block_template", {
      templateName: "basic_queue",
      startX: 100,
      startY: 100,
    });
    expect(ctx).toEqual({ templateName: "basic_queue" });
  });

  it("should return null for block_configure (may contain sensitive values)", () => {
    const ctx = extractContext("block_configure", {
      blockId: 5,
      config: { delayType: "fixed", value: 10 },
    });
    expect(ctx).toBeNull();
  });

  it("should extract runMode for simulation_run", () => {
    const ctx = extractContext("simulation_run", {
      runMode: "fast",
      endTime: 1000,
      includeStats: true,
    });
    expect(ctx).toEqual({ runMode: "fast", includeStats: true });
  });

  it("should extract numberOfRuns for simulation_run_multi", () => {
    const ctx = extractContext("simulation_run_multi", {
      numberOfRuns: 10,
      runMode: "fast",
      endTime: 500,
    });
    expect(ctx).toEqual({ numberOfRuns: 10, runMode: "fast" });
  });

  it("should extract scenario count for simulation_run_scenarios", () => {
    const ctx = extractContext("simulation_run_scenarios", {
      blockId: 5,
      dialogVariable: "WaitDelta_prm",
      values: [1, 2, 3, 4, 5],
    });
    expect(ctx).toEqual({ scenarioCount: 5 });
  });

  it("should extract pattern_search filters", () => {
    const ctx = extractContext("pattern_search", {
      category: "discrete_event",
      domain: "queuing",
      complexity: "simple",
      query: "manufacturing",
    });
    expect(ctx).toEqual({
      category: "discrete_event",
      domain: "queuing",
      complexity: "simple",
    });
  });

  it("should extract modeling_guide params", () => {
    const ctx = extractContext("modeling_guide", {
      category: "queuing",
      scenario: "simple_queue",
      query: "queue",
    });
    expect(ctx).toEqual({ category: "queuing", scenario: "simple_queue" });
  });

  it("should return null for unknown tools", () => {
    const ctx = extractContext("model_open", { filePath: "C:\\secret\\model.mox" });
    expect(ctx).toBeNull();
  });

  it("should return null when no params provided", () => {
    const ctx = extractContext("block_add", undefined);
    expect(ctx).toBeNull();
  });

  it("should extract simulation_setup_set params", () => {
    const ctx = extractContext("simulation_setup_set", {
      endTime: 1000,
      numberOfRuns: 5,
      randomSeed: 42,
    });
    expect(ctx).toEqual({ endTime: 1000, numberOfRuns: 5 });
  });
});

describe("Privacy Safety", () => {
  it("should NOT include file paths in block_add context", () => {
    const ctx = extractContext("block_add", {
      libraryName: "Item.lbr",
      blockName: "Create",
      label: "Customer Arrivals",
      modelId: "secret-model-id",
    });
    expect(ctx).not.toHaveProperty("label");
    expect(ctx).not.toHaveProperty("modelId");
  });

  it("should NOT include block labels in block_connect context", () => {
    const ctx = extractContext("block_connect", {
      sourceBlockId: 1,
      sourceConnector: "ItemOut",
      targetBlockId: 2,
      targetConnector: "ItemIn",
      modelId: "m1",
    });
    expect(ctx).not.toHaveProperty("sourceBlockId");
    expect(ctx).not.toHaveProperty("targetBlockId");
    expect(ctx).not.toHaveProperty("modelId");
  });

  it("should NOT leak parameter values from simulation_run_scenarios", () => {
    const ctx = extractContext("simulation_run_scenarios", {
      blockId: 5,
      dialogVariable: "WaitDelta_prm",
      values: [10, 20, 30],
    });
    expect(ctx).not.toHaveProperty("values");
    expect(ctx).not.toHaveProperty("dialogVariable");
    expect(ctx).not.toHaveProperty("blockId");
    expect(ctx).toEqual({ scenarioCount: 3 });
  });

  it("should NOT leak search queries from pattern_search", () => {
    const ctx = extractContext("pattern_search", {
      query: "my secret project",
      category: "discrete_event",
    });
    expect(ctx).not.toHaveProperty("query");
  });
});

describe("Telemetry Source Coverage", () => {
  it("should have extractContext coverage for high-value tools", () => {
    const highValueTools = [
      "block_add",
      "block_add_batch",
      "block_connect",
      "block_disconnect",
      "connect_chain",
      "connect_graph",
      "block_template",
      "simulation_run",
      "simulation_run_multi",
      "simulation_run_scenarios",
      "pattern_search",
      "modeling_guide",
      "simulation_setup_set",
    ];

    for (const tool of highValueTools) {
      // Each should return a non-null context when given relevant params
      const ctx = extractContext(tool, { libraryName: "Item.lbr", blockName: "X", blocks: [{}], blockIds: [1], connections: [{}], values: [1], runMode: "fast", numberOfRuns: 1, category: "test", scenario: "test", endTime: 100 });
      expect(ctx, `${tool} should have context extraction`).not.toBeNull();
    }
  });
});
