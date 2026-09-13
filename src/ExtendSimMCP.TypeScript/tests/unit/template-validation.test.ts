/**
 * Template validation tests — verify templates.json structure and integrity.
 *
 * Loads templates.json and validates each template's blocks, connections,
 * and parameters for structural correctness.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SRC_DIR = join(__dirname, "..", "..", "src");

interface TemplateBlock {
  name: string;
  library: string;
  block: string;
  label: string;
}

interface TemplateConnection {
  from: string;
  to: string;
  fromConnector?: string;
  toConnector?: string;
}

interface TemplateParameter {
  block: string;
  var: string;
  description: string;
}

interface Template {
  description: string;
  blocks: TemplateBlock[];
  connections: TemplateConnection[];
  parameters: Record<string, TemplateParameter>;
}

let templates: Record<string, Template>;
let templateNames: string[];

beforeAll(() => {
  templates = JSON.parse(
    readFileSync(join(SRC_DIR, "templates.json"), "utf-8")
  );
  templateNames = Object.keys(templates);
});

describe("Template Structure", () => {
  it("should have at least 26 templates", () => {
    expect(templateNames.length).toBeGreaterThanOrEqual(26);
  });

  it("should have required fields on every template", () => {
    const invalid: string[] = [];
    for (const [name, tpl] of Object.entries(templates)) {
      if (!tpl.description || !tpl.blocks || !tpl.connections || !tpl.parameters) {
        invalid.push(name);
      }
    }
    expect(invalid).toEqual([]);
  });

  it("should have non-empty description for every template", () => {
    for (const [name, tpl] of Object.entries(templates)) {
      expect(tpl.description.trim().length, `${name} has empty description`).toBeGreaterThan(0);
    }
  });
});

describe("Template Blocks", () => {
  it("should have required fields on every block entry", () => {
    const invalid: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      for (const block of tpl.blocks) {
        if (!block.name || !block.library || !block.block || !block.label) {
          invalid.push(`${tplName}/${block.name || "unnamed"}`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });

  it("should have unique block names within each template", () => {
    const duplicates: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      const seen = new Set<string>();
      for (const block of tpl.blocks) {
        if (seen.has(block.name)) {
          duplicates.push(`${tplName}/${block.name}`);
        }
        seen.add(block.name);
      }
    }
    expect(duplicates).toEqual([]);
  });

  it("should have library names ending with .lbr", () => {
    const invalid: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      for (const block of tpl.blocks) {
        if (!block.library.endsWith(".lbr")) {
          invalid.push(`${tplName}/${block.name}: ${block.library}`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });

  it("should have at least 2 blocks per template", () => {
    const tooSmall: string[] = [];
    for (const [name, tpl] of Object.entries(templates)) {
      if (tpl.blocks.length < 2) {
        tooSmall.push(name);
      }
    }
    expect(tooSmall).toEqual([]);
  });
});

describe("Template Connections", () => {
  it("should have at least 1 connection per template", () => {
    const noConnections: string[] = [];
    for (const [name, tpl] of Object.entries(templates)) {
      if (tpl.connections.length < 1) {
        noConnections.push(name);
      }
    }
    expect(noConnections).toEqual([]);
  });

  it("should reference valid block names in from/to", () => {
    const invalid: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      const blockNames = new Set(tpl.blocks.map((b) => b.name));
      for (const conn of tpl.connections) {
        if (!blockNames.has(conn.from)) {
          invalid.push(`${tplName}: from="${conn.from}" not in blocks`);
        }
        if (!blockNames.has(conn.to)) {
          invalid.push(`${tplName}: to="${conn.to}" not in blocks`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });
});

describe("Template Parameters", () => {
  it("should reference valid block names in parameters", () => {
    const invalid: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      const blockNames = new Set(tpl.blocks.map((b) => b.name));
      for (const [paramName, param] of Object.entries(tpl.parameters)) {
        if (!blockNames.has(param.block)) {
          invalid.push(`${tplName}.${paramName}: block="${param.block}" not in blocks`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });

  it("should have non-empty var names in all parameters", () => {
    const invalid: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      for (const [paramName, param] of Object.entries(tpl.parameters)) {
        if (!param.var || param.var.trim().length === 0) {
          invalid.push(`${tplName}.${paramName}`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });

  it("should have non-empty description in all parameters", () => {
    const invalid: string[] = [];
    for (const [tplName, tpl] of Object.entries(templates)) {
      for (const [paramName, param] of Object.entries(tpl.parameters)) {
        if (!param.description || param.description.trim().length === 0) {
          invalid.push(`${tplName}.${paramName}`);
        }
      }
    }
    expect(invalid).toEqual([]);
  });
});
