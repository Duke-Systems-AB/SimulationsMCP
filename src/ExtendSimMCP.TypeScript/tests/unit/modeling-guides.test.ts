/**
 * Modeling guides validation tests — verify modeling_guides.json structure and content.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SRC_DIR = join(__dirname, "..", "..", "src");

let guides: Record<string, unknown>;
let scenarios: Record<string, Record<string, unknown>>;
let categories: Record<string, Record<string, unknown>>;

beforeAll(() => {
  const raw = readFileSync(join(SRC_DIR, "modeling_guides.json"), "utf-8");
  guides = JSON.parse(raw);
  scenarios = guides.scenarios as Record<string, Record<string, unknown>>;
  categories = guides.categories as Record<string, Record<string, unknown>>;
});

describe("Modeling Guides Structure", () => {
  it("should have version and description", () => {
    expect(guides.version).toBeDefined();
    expect(guides.description).toBeDefined();
    expect(typeof guides.description).toBe("string");
  });

  it("should have categories and scenarios objects", () => {
    expect(categories).toBeDefined();
    expect(scenarios).toBeDefined();
    expect(typeof categories).toBe("object");
    expect(typeof scenarios).toBe("object");
  });

  it("should have at least 10 scenarios", () => {
    expect(Object.keys(scenarios).length).toBeGreaterThanOrEqual(10);
  });

  it("should have at least 4 categories", () => {
    expect(Object.keys(categories).length).toBeGreaterThanOrEqual(4);
  });
});

describe("Scenario Structure", () => {
  const REQUIRED_FIELDS = [
    "name", "category", "description", "useWhen",
    "pattern", "keyParameters", "keyMetrics", "commonMistakes"
  ];

  it("every scenario should have all required fields", () => {
    const missing: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      for (const field of REQUIRED_FIELDS) {
        if (!(field in scenario)) {
          missing.push(`${key} missing '${field}'`);
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("every scenario should have a valid category reference", () => {
    const invalid: string[] = [];
    const catKeys = new Set(Object.keys(categories));
    for (const [key, scenario] of Object.entries(scenarios)) {
      if (!catKeys.has(scenario.category as string)) {
        invalid.push(`${key} has invalid category '${scenario.category}'`);
      }
    }
    expect(invalid).toEqual([]);
  });

  it("every category should have at least one scenario", () => {
    const scenarioCats = new Set(Object.values(scenarios).map(s => s.category));
    const emptyCats: string[] = [];
    for (const cat of Object.keys(categories)) {
      if (!scenarioCats.has(cat)) {
        emptyCats.push(cat);
      }
    }
    expect(emptyCats).toEqual([]);
  });

  it("useWhen should have 2-6 items per scenario", () => {
    const bad: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const uw = scenario.useWhen as unknown[];
      if (!Array.isArray(uw) || uw.length < 2 || uw.length > 6) {
        bad.push(`${key}: useWhen has ${Array.isArray(uw) ? uw.length : 0} items`);
      }
    }
    expect(bad).toEqual([]);
  });

  it("commonMistakes should have 2-5 items per scenario", () => {
    const bad: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const cm = scenario.commonMistakes as unknown[];
      if (!Array.isArray(cm) || cm.length < 2 || cm.length > 5) {
        bad.push(`${key}: commonMistakes has ${Array.isArray(cm) ? cm.length : 0} items`);
      }
    }
    expect(bad).toEqual([]);
  });
});

describe("Pattern Structure", () => {
  it("every pattern should have blocks and connections", () => {
    const missing: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const pattern = scenario.pattern as Record<string, unknown>;
      if (!pattern) continue;
      if (!Array.isArray(pattern.blocks)) missing.push(`${key} pattern missing blocks`);
      if (!Array.isArray(pattern.connections)) missing.push(`${key} pattern missing connections`);
    }
    expect(missing).toEqual([]);
  });

  it("every block in pattern should have name, library, label, purpose", () => {
    const missing: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const pattern = scenario.pattern as Record<string, unknown>;
      if (!pattern || !Array.isArray(pattern.blocks)) continue;
      for (const block of pattern.blocks as Record<string, unknown>[]) {
        for (const field of ["name", "library", "label", "purpose"]) {
          if (!block[field]) {
            missing.push(`${key} block '${block.name || "?"}' missing '${field}'`);
          }
        }
      }
    }
    expect(missing).toEqual([]);
  });

  it("all block libraries should be valid ExtendSim libraries", () => {
    const validLibs = new Set(["Item.lbr", "Value.lbr", "Rate.lbr"]);
    const invalid: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const pattern = scenario.pattern as Record<string, unknown>;
      if (!pattern || !Array.isArray(pattern.blocks)) continue;
      for (const block of pattern.blocks as Record<string, unknown>[]) {
        if (block.library && !validLibs.has(block.library as string)) {
          invalid.push(`${key} block '${block.name}' has invalid library '${block.library}'`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });
});

describe("Cross-references", () => {
  it("category.scenarios lists should reference existing scenario keys", () => {
    const scenarioKeys = new Set(Object.keys(scenarios));
    const invalid: string[] = [];
    for (const [catKey, cat] of Object.entries(categories)) {
      const refs = cat.scenarios as string[];
      if (!Array.isArray(refs)) continue;
      for (const ref of refs) {
        if (!scenarioKeys.has(ref)) {
          invalid.push(`category '${catKey}' references non-existent scenario '${ref}'`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });

  it("variation scenario cross-references should be valid or null", () => {
    const scenarioKeys = new Set(Object.keys(scenarios));
    const invalid: string[] = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const variations = scenario.variations as Record<string, unknown>[] | undefined;
      if (!Array.isArray(variations)) continue;
      for (const v of variations) {
        if (v.scenario && !scenarioKeys.has(v.scenario as string)) {
          invalid.push(`${key} variation references non-existent '${v.scenario}'`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });
});
