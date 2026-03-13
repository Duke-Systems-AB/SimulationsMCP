/**
 * Error code coverage tests — verify error code enum and safety protections.
 *
 * Parses ErrorCode class, _error() helper, BLOCKED_MENU_COMMANDS,
 * and safety protections from simulation_backend.py.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SRC_DIR = join(__dirname, "..", "..", "src");

let pythonSource: string;
let errorCodes: string[];

beforeAll(() => {
  pythonSource = readFileSync(
    join(SRC_DIR, "simulation_backend.py"),
    "utf-8"
  );

  // Extract ErrorCode class members — pattern: CODE_NAME = "CODE_NAME"
  const codeRegex = /^\s+(\w+)\s*=\s*"(\w+)"/gm;
  errorCodes = [];
  let match;
  // Only extract from within the ErrorCode class
  const classMatch = pythonSource.match(
    /class\s+ErrorCode[\s\S]*?(?=\nclass\s|\ndef\s|\n#\s*={3,})/
  );
  if (classMatch) {
    while ((match = codeRegex.exec(classMatch[0])) !== null) {
      errorCodes.push(match[2]);
    }
  }
});

describe("ErrorCode Enum", () => {
  it("should have error code entries", () => {
    expect(errorCodes.length).toBeGreaterThan(0);
  });

  it("should have all unique error codes", () => {
    const seen = new Set<string>();
    const duplicates: string[] = [];
    for (const code of errorCodes) {
      if (seen.has(code)) duplicates.push(code);
      seen.add(code);
    }
    expect(duplicates).toEqual([]);
  });

  it("should use clean identifiers (uppercase + underscores only)", () => {
    const invalid: string[] = [];
    for (const code of errorCodes) {
      if (!/^[A-Z][A-Z0-9_]*$/.test(code)) {
        invalid.push(code);
      }
    }
    expect(invalid).toEqual([]);
  });

  it("should include critical error codes", () => {
    const critical = [
      "COM_ERROR",
      "BLOCK_NOT_FOUND",
      "CONNECTION_FAILED",
      "NOT_CONNECTED",
      "MISSING_PARAMETER",
      "MODEL_NOT_OPEN",
    ];
    for (const code of critical) {
      expect(errorCodes, `missing critical code: ${code}`).toContain(code);
    }
  });

  it("should have at least 25 error codes", () => {
    expect(errorCodes.length).toBeGreaterThanOrEqual(25);
  });
});

describe("Error Helper Function", () => {
  it("should define _error() helper function", () => {
    expect(pythonSource).toMatch(/def\s+_error\s*\(/);
  });

  it("_error() should accept error code and message", () => {
    // Pattern: def _error(code, message, **extra) or similar
    const errorFuncMatch = pythonSource.match(
      /def\s+_error\s*\(([^)]+)\)/
    );
    expect(errorFuncMatch).not.toBeNull();
    // Should have at least 2 parameters
    const params = errorFuncMatch![1].split(",").map((p) => p.trim());
    expect(params.length).toBeGreaterThanOrEqual(2);
  });
});

describe("Safety Protections", () => {
  it("should define BLOCKED_MENU_COMMANDS dict", () => {
    expect(pythonSource).toMatch(/BLOCKED_MENU_COMMANDS\s*=\s*\{/);
  });

  it("should block ExecuteMenuCommand(1) (Quit)", () => {
    // BLOCKED_MENU_COMMANDS should contain key 1
    const blockMatch = pythonSource.match(
      /BLOCKED_MENU_COMMANDS\s*=\s*\{[^}]+\}/s
    );
    expect(blockMatch).not.toBeNull();
    expect(blockMatch![0]).toMatch(/\b1\b/);
  });

  it("should block at least 4 dangerous menu commands", () => {
    const blockMatch = pythonSource.match(
      /BLOCKED_MENU_COMMANDS\s*=\s*\{([^}]+)\}/s
    );
    expect(blockMatch).not.toBeNull();
    // Count numeric keys (lines like "1:", "2:", etc.)
    const keys = blockMatch![1].match(/^\s*\d+\s*:/gm);
    expect(keys).not.toBeNull();
    expect(keys!.length).toBeGreaterThanOrEqual(4);
  });

  it("should protect Executive block (block 0) from deletion", () => {
    // block_remove handler should check for block 0
    expect(pythonSource).toMatch(/block.?id\s*==\s*0|block_id\s*==\s*0/i);
  });

  it("should handle Infinity/NaN in JSON serialization", () => {
    // Should contain allow_nan=False or equivalent sanitization
    expect(pythonSource).toMatch(/allow_nan\s*=\s*False/);
  });
});
