/**
 * model_advisor analysis tests - verify warning/suggestion/completion logic.
 * Pure unit tests with mock topologies - no ExtendSim needed.
 */

import { describe, it, expect } from "vitest";
import { analyzeWarnings, analyzeSuggestions, analyzeCompletions } from "../../src/advisor.js";

function block(id: number, name: string, label?: string) {
  return { blockId: id, blockName: name, label: label || name };
}

function conn(fromId: number, fromName: string, toId: number, toName: string) {
  return {
    from: { blockId: fromId, blockName: fromName },
    to: { blockId: toId, blockName: toName },
  };
}

describe("analyzeWarnings", () => {
  it("should warn about unconnected blocks", () => {
    const blocks = [block(1, "Create"), block(2, "Exit"), block(3, "Activity")];
    const connections = [conn(1, "Create", 2, "Exit")];
    const result = analyzeWarnings(blocks, connections);
    const unconnected = result.find(w => w.message.includes("no connections"));
    expect(unconnected).toBeDefined();
    expect(unconnected!.blockId).toBe(3);
  });

  it("should error on Create without output", () => {
    const blocks = [block(1, "Create"), block(2, "Exit")];
    const connections: any[] = [];
    const result = analyzeWarnings(blocks, connections);
    const createErr = result.find(w => w.blockId === 1 && w.type === "error");
    expect(createErr).toBeDefined();
    expect(createErr!.message).toContain("no output");
  });

  it("should error on Exit without input", () => {
    const blocks = [block(1, "Create"), block(2, "Exit")];
    const connections: any[] = [];
    const result = analyzeWarnings(blocks, connections);
    const exitErr = result.find(w => w.blockId === 2 && w.type === "error");
    expect(exitErr).toBeDefined();
    expect(exitErr!.message).toContain("no input");
  });

  it("should warn about Activity without Queue predecessor", () => {
    const blocks = [block(1, "Create"), block(2, "Activity"), block(3, "Exit")];
    const connections = [conn(1, "Create", 2, "Activity"), conn(2, "Activity", 3, "Exit")];
    const result = analyzeWarnings(blocks, connections);
    const warn = result.find(w => w.blockId === 2 && w.message.includes("Queue"));
    expect(warn).toBeDefined();
  });

  it("should NOT warn about Activity with Queue predecessor", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity"), block(4, "Exit")];
    const connections = [
      conn(1, "Create", 2, "Queue"),
      conn(2, "Queue", 3, "Activity"),
      conn(3, "Activity", 4, "Exit"),
    ];
    const result = analyzeWarnings(blocks, connections);
    const warn = result.find(w => w.blockId === 3 && w.message.includes("Queue"));
    expect(warn).toBeUndefined();
  });

  it("should warn about Batch without downstream Unbatch", () => {
    const blocks = [block(1, "Create"), block(2, "Batch"), block(3, "Exit")];
    const connections = [conn(1, "Create", 2, "Batch"), conn(2, "Batch", 3, "Exit")];
    const result = analyzeWarnings(blocks, connections);
    const warn = result.find(w => w.blockId === 2 && w.message.includes("Unbatch"));
    expect(warn).toBeDefined();
  });

  it("should return empty array for valid simple model", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity"), block(4, "Exit")];
    const connections = [
      conn(1, "Create", 2, "Queue"),
      conn(2, "Queue", 3, "Activity"),
      conn(3, "Activity", 4, "Exit"),
    ];
    const result = analyzeWarnings(blocks, connections);
    expect(result).toEqual([]);
  });

  it("should return empty array for empty model", () => {
    expect(analyzeWarnings([], [])).toEqual([]);
  });
});

describe("analyzeSuggestions", () => {
  const mockPatterns = [
    {
      id: "test-simple-queue",
      name: "Simple Queue",
      description: "A basic queuing model",
      blockTypeSummary: { Create: 1, Queue: 1, Activity: 1, Exit: 1 },
    },
    {
      id: "test-tank-system",
      name: "Tank System",
      description: "A flow model",
      blockTypeSummary: { Tank: 2, Valve: 1, "Merge Flow": 1 },
    },
  ];

  const mockGuides = {
    scenarios: {
      simple_queue: {
        name: "Simple Queue (M/M/1)",
        pattern: { blocks: [{ name: "Create" }, { name: "Queue" }, { name: "Activity" }, { name: "Exit" }] },
        commonMistakes: ["Forgetting Queue before Activity"],
      },
      tank_system: {
        name: "Tank System",
        pattern: { blocks: [{ name: "Tank" }, { name: "Valve" }] },
        commonMistakes: ["Overflow without Sensor"],
      },
    },
  };

  it("should match model to pattern by block-type overlap", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity"), block(4, "Exit")];
    const result = analyzeSuggestions(blocks, [], mockPatterns, mockGuides);
    const match = result.find(s => s.source === "pattern_library");
    expect(match).toBeDefined();
    expect(match!.message).toContain("Simple Queue");
  });

  it("should match model to modeling guide scenario", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity"), block(4, "Exit")];
    const result = analyzeSuggestions(blocks, [], mockPatterns, mockGuides);
    const guide = result.find(s => s.source === "modeling_guide");
    expect(guide).toBeDefined();
    expect(guide!.scenario).toBe("simple_queue");
  });

  it("should not match unrelated patterns (below threshold)", () => {
    const blocks = [block(1, "Create"), block(2, "Exit")];
    const result = analyzeSuggestions(blocks, [], mockPatterns, mockGuides);
    const tankMatch = result.find(s => s.message.includes("Tank"));
    expect(tankMatch).toBeUndefined();
  });

  it("should return empty for empty model", () => {
    expect(analyzeSuggestions([], [], mockPatterns, mockGuides)).toEqual([]);
  });

  it("should include commonMistakes in guide suggestions", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity"), block(4, "Exit")];
    const result = analyzeSuggestions(blocks, [], mockPatterns, mockGuides);
    const guide = result.find(s => s.source === "modeling_guide");
    expect(guide!.message).toContain("Forgetting Queue");
  });
});

describe("analyzeCompletions", () => {
  it("should suggest Exit when Create exists but no Exit", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity")];
    const connections = [conn(1, "Create", 2, "Queue"), conn(2, "Queue", 3, "Activity")];
    const result = analyzeCompletions(blocks, connections);
    const exitSuggestion = result.find(c => c.message.includes("Exit"));
    expect(exitSuggestion).toBeDefined();
  });

  it("should suggest Activity when Queue has no downstream Activity/Workstation", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Exit")];
    const connections = [conn(1, "Create", 2, "Queue"), conn(2, "Queue", 3, "Exit")];
    const result = analyzeCompletions(blocks, connections);
    const actSuggestion = result.find(c => c.message.includes("Activity"));
    expect(actSuggestion).toBeDefined();
  });

  it("should report complete flow for Create->Queue->Activity->Exit", () => {
    const blocks = [block(1, "Create"), block(2, "Queue"), block(3, "Activity"), block(4, "Exit")];
    const connections = [
      conn(1, "Create", 2, "Queue"),
      conn(2, "Queue", 3, "Activity"),
      conn(3, "Activity", 4, "Exit"),
    ];
    const result = analyzeCompletions(blocks, connections);
    const complete = result.find(c => c.message.includes("complete"));
    expect(complete).toBeDefined();
  });

  it("should return empty for empty model", () => {
    expect(analyzeCompletions([], [])).toEqual([]);
  });

  it("should suggest Queue when Create connects directly to Activity", () => {
    const blocks = [block(1, "Create"), block(2, "Activity"), block(3, "Exit")];
    const connections = [conn(1, "Create", 2, "Activity"), conn(2, "Activity", 3, "Exit")];
    const result = analyzeCompletions(blocks, connections);
    const queueSuggestion = result.find(c => c.message.includes("Queue"));
    expect(queueSuggestion).toBeDefined();
  });
});
