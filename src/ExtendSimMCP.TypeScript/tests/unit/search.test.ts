/**
 * Unit tests for search helper functions.
 *
 * These tests use the actual reference JSON files to verify search logic.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

// Load the same reference files the server uses
const SRC_DIR = join(__dirname, "..", "..", "src");

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

let modlRef: ModlReference;
let blockRef: any;
let dialogRef: any;

beforeAll(() => {
  modlRef = JSON.parse(readFileSync(join(SRC_DIR, "modl_reference.json"), "utf-8"));
  blockRef = JSON.parse(readFileSync(join(SRC_DIR, "block_reference.json"), "utf-8"));
  dialogRef = JSON.parse(readFileSync(join(SRC_DIR, "dialog_reference.json"), "utf-8"));
});

// Replicate the search functions from index.ts for testing
function searchModl(query: string): any[] {
  const results: any[] = [];
  const queryLower = query.toLowerCase();
  const maxResults = 10;

  for (const [categoryName, category] of Object.entries(modlRef.categories)) {
    for (const [funcName, func] of Object.entries(category.functions)) {
      const nameMatch = funcName.toLowerCase().includes(queryLower);
      const descMatch = func.description.toLowerCase().includes(queryLower);
      const argMatch = func.arguments?.some(
        (arg) =>
          arg.name.toLowerCase().includes(queryLower) ||
          arg.description.toLowerCase().includes(queryLower)
      );

      if (nameMatch || descMatch || argMatch) {
        results.push({
          name: funcName,
          category: categoryName,
          signature: func.signature,
          description: func.description,
          arguments: func.arguments,
          returns: func.returns,
          returnType: modlRef.returnTypes[func.returns] || func.returns,
          obsolete: func.obsolete,
        });

        if (results.length >= maxResults) {
          return results;
        }
      }
    }
  }

  return results;
}

function searchBlocks(query: string, library?: string): any[] {
  const results: any[] = [];
  const queryLower = query.toLowerCase();
  const maxResults = 10;

  for (const [libName, lib] of Object.entries(blockRef.libraries) as any[]) {
    if (library && !libName.toLowerCase().includes(library.toLowerCase())) {
      continue;
    }

    for (const [catName, category] of Object.entries(lib.categories) as any[]) {
      for (const [blockName, block] of Object.entries(category.blocks) as any[]) {
        const blockInfo =
          typeof block === "string" ? { description: block } : block;

        const nameMatch = blockName.toLowerCase().includes(queryLower);
        const descMatch = blockInfo.description.toLowerCase().includes(queryLower);

        if (nameMatch || descMatch) {
          results.push({
            library: libName,
            category: catName,
            name: blockName,
            description: blockInfo.description,
          });

          if (results.length >= maxResults) {
            return results;
          }
        }
      }
    }
  }

  return results;
}

describe("ModL Search", () => {
  it("should find functions by exact name", () => {
    const results = searchModl("GetModelName");
    expect(results.length).toBeGreaterThan(0);
    expect(results[0].name).toBe("GetModelName");
  });

  it("should find functions by partial name", () => {
    const results = searchModl("Block");
    expect(results.length).toBeGreaterThan(0);
    results.forEach((r) => {
      const matchesName = r.name.toLowerCase().includes("block");
      const matchesDesc = r.description.toLowerCase().includes("block");
      const matchesArgs = r.arguments?.some(
        (a: any) =>
          a.name.toLowerCase().includes("block") ||
          a.description.toLowerCase().includes("block")
      );
      expect(matchesName || matchesDesc || matchesArgs).toBe(true);
    });
  });

  it("should return empty array for nonexistent function", () => {
    const results = searchModl("xyzNonExistentFunction123");
    expect(results).toEqual([]);
  });

  it("should limit results to 10", () => {
    // A very common search term should hit the cap
    const results = searchModl("get");
    expect(results.length).toBeLessThanOrEqual(10);
  });

  it("should include signature and return type", () => {
    const results = searchModl("GetModelName");
    if (results.length > 0) {
      expect(results[0]).toHaveProperty("signature");
      expect(results[0]).toHaveProperty("returns");
      expect(results[0]).toHaveProperty("returnType");
    }
  });
});

describe("Block Search", () => {
  it("should find blocks by name", () => {
    const results = searchBlocks("Create");
    expect(results.length).toBeGreaterThan(0);
    expect(results.some((r) => r.name === "Create")).toBe(true);
  });

  it("should find blocks by description keyword", () => {
    const results = searchBlocks("queue");
    expect(results.length).toBeGreaterThan(0);
  });

  it("should filter by library", () => {
    const results = searchBlocks("Create", "Item.lbr");
    expect(results.length).toBeGreaterThan(0);
    results.forEach((r) => {
      expect(r.library.toLowerCase()).toContain("item");
    });
  });

  it("should return empty for nonexistent blocks", () => {
    const results = searchBlocks("xyzNonExistentBlock123");
    expect(results).toEqual([]);
  });

  it("should limit results to 10", () => {
    const results = searchBlocks("a");
    expect(results.length).toBeLessThanOrEqual(10);
  });
});

describe("Reference Files", () => {
  it("should load modl_reference.json with categories", () => {
    expect(modlRef).toBeDefined();
    expect(modlRef.version).toBeDefined();
    expect(Object.keys(modlRef.categories).length).toBeGreaterThan(0);
  });

  it("should load block_reference.json with libraries", () => {
    expect(blockRef).toBeDefined();
    expect(blockRef.libraries).toBeDefined();
    expect(Object.keys(blockRef.libraries).length).toBeGreaterThan(0);
  });

  it("should load dialog_reference.json with libraries", () => {
    expect(dialogRef).toBeDefined();
    expect(dialogRef.libraries).toBeDefined();
  });
});
