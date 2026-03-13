/**
 * Variable routing tests — verify _set_var/_get_var suffix routing logic.
 *
 * v1.16.2 switched from SetDialogVariable to SetVariableNumeric for numeric
 * variables. These tests verify the routing rules by static analysis of
 * simulation_backend.py source code.
 *
 * Pure static analysis — no ExtendSim needed.
 */

import { describe, it, expect, beforeAll } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";

const SRC_DIR = join(__dirname, "..", "..", "src");
let pythonSource: string;

beforeAll(() => {
  pythonSource = readFileSync(
    join(SRC_DIR, "simulation_backend.py"),
    "utf-8"
  );
});

describe("Variable API Routing — _set_var", () => {
  it("should define _TABLE_SUFFIXES with _dtbl and _ttbl", () => {
    expect(pythonSource).toContain(
      '_TABLE_SUFFIXES = ("_dtbl", "_ttbl")'
    );
  });

  it("should define _TEXT_SUFFIXES with _dtxt", () => {
    expect(pythonSource).toContain('_TEXT_SUFFIXES = ("_dtxt",)');
  });

  it("should combine into _DIALOG_VAR_SUFFIXES", () => {
    expect(pythonSource).toContain(
      "_DIALOG_VAR_SUFFIXES = _TABLE_SUFFIXES + _TEXT_SUFFIXES"
    );
  });

  it("should route _dtbl/_ttbl suffixes → SetDialogVariable", () => {
    // _set_var checks var_name.endswith(_TABLE_SUFFIXES) → SetDialogVariable
    const setVarMatch = pythonSource.match(
      /def _set_var\([\s\S]*?^def /m
    );
    expect(setVarMatch).not.toBeNull();
    const setVarBody = setVarMatch![0];
    expect(setVarBody).toContain("var_name.endswith(_TABLE_SUFFIXES)");
    expect(setVarBody).toContain("SetDialogVariable");
  });

  it("should route _dtxt suffix → SetDialogVariable", () => {
    const setVarMatch = pythonSource.match(
      /def _set_var\([\s\S]*?^def /m
    );
    const setVarBody = setVarMatch![0];
    expect(setVarBody).toContain("var_name.endswith(_TEXT_SUFFIXES)");
    expect(setVarBody).toContain("SetDialogVariable");
  });

  it("should route _prm/_pop/_chk/_rdo and no-suffix → SetVariableNumeric", () => {
    const setVarMatch = pythonSource.match(
      /def _set_var\([\s\S]*?^def /m
    );
    const setVarBody = setVarMatch![0];
    // The else branch uses SetVariableNumeric
    expect(setVarBody).toContain("SetVariableNumeric");
    // Comment documents which suffixes go here
    expect(setVarBody).toContain("_prm, _pop, _chk, _rdo, no suffix");
  });
});

describe("Variable API Routing — _get_var", () => {
  it("should route _DIALOG_VAR_SUFFIXES → GetDialogVariable", () => {
    const getVarMatch = pythonSource.match(
      /def _get_var\([\s\S]*?^def /m
    );
    expect(getVarMatch).not.toBeNull();
    const getVarBody = getVarMatch![0];
    expect(getVarBody).toContain("var_name.endswith(_DIALOG_VAR_SUFFIXES)");
    expect(getVarBody).toContain("GetDialogVariable");
  });

  it("should route non-dialog suffixes → GetVariableNumeric", () => {
    const getVarMatch = pythonSource.match(
      /def _get_var\([\s\S]*?^def /m
    );
    const getVarBody = getVarMatch![0];
    expect(getVarBody).toContain("GetVariableNumeric");
  });
});

describe("Variable API Routing — _set_var_string", () => {
  it("should always use SetDialogVariable for string values", () => {
    const match = pythonSource.match(
      /def _set_var_string\([\s\S]*?^(?=def |# ={3,})/m
    );
    expect(match).not.toBeNull();
    const body = match![0];
    expect(body).toContain("SetDialogVariable");
    // Code lines (not comments/docstrings) should NOT contain SetVariableNumeric
    const codeLines = body
      .split(/\r?\n/)
      .filter((l) => {
        const t = l.trim();
        return t && !t.startsWith("#") && !t.startsWith('"""') && !t.startsWith("Use this") && !t.startsWith("regardless");
      });
    const hasRawCall = codeLines.some((l) =>
      l.includes("SetVariableNumeric(")
    );
    expect(hasRawCall, "_set_var_string should not call SetVariableNumeric").toBe(false);
  });
});

describe("No raw SetDialogVariable/SetVariableNumeric outside helpers", () => {
  it("should not have direct SetDialogVariable calls in handler functions (except allowed exceptions)", () => {
    // Split source at ERROR CODES section (after helper definitions)
    const helperEnd = pythonSource.search(/# ={3,}\r?\n# ERROR CODES/);
    expect(helperEnd).toBeGreaterThan(0);

    // Get code after the helper definitions
    const afterHelpers = pythonSource.substring(helperEnd);

    // Find all SetDialogVariable calls in handler code
    const setDialogCalls = afterHelpers.match(
      /.*SetDialogVariable.*/g
    ) || [];

    // Filter out comments and allowed exceptions:
    // 1. block_set_value string fallback (line ~2387)
    // 2. block_discover_variables (line ~2162)
    // 3. attribute_set / attribute_get helpers using _set_var_string
    // 4. _persist_popup_change notes
    // 5. GetDialogVariableString (different function)
    const disallowed = setDialogCalls.filter((line) => {
      const trimmed = line.trim();
      // Skip comments
      if (trimmed.startsWith("#")) return false;
      if (trimmed.startsWith("//")) return false;
      // Skip docstrings/notes
      if (trimmed.startsWith("Note:")) return false;
      if (trimmed.startsWith("and SetDialogVariable")) return false;
      if (trimmed.startsWith("calls _persist_popup")) return false;
      if (trimmed.startsWith("setting popup menus")) return false;
      // Skip Get variants (different function)
      if (trimmed.includes("GetDialogVariable")) return false;
      if (trimmed.includes("GetDialogItemLabel")) return false;
      if (trimmed.includes("GetDialogItemInfo")) return false;
      // Allowed: block_set_value string fallback
      if (trimmed.includes("String value for a numeric variable")) return false;
      if (
        trimmed.includes("SetDialogVariable") &&
        trimmed.includes("_escape_modl_string(value)")
      )
        return false;
      // Allowed: _read_sm_config Scenarios_tbl checkbox access
      // Scenarios_tbl is a stringtable with _tbl suffix (not _dtbl/_ttbl),
      // so _set_var routes it to SetVariableNumeric which fails.
      // Must use direct SetDialogVariable/GetDialogVariable.
      if (trimmed.includes("Scenarios_tbl")) return false;
      // Allowed: block_discover_variables reading values
      // (it reads, doesn't write — and needs raw GetDialogVariable for discovery)
      return true;
    });

    expect(
      disallowed,
      `Found unexpected direct SetDialogVariable calls:\n${disallowed.join("\n")}`
    ).toEqual([]);
  });

  it("should have all handler functions use _set_var or _set_var_string (not raw APIs)", () => {
    // Extract all def handle_* and def configure_* functions
    // and verify they don't contain raw SetVariableNumeric (should use _set_var)
    const handlerRegex =
      /def (handle_\w+|configure_\w+)\([\s\S]*?(?=\ndef |\nCOMMANDS)/g;
    let match;
    const violations: string[] = [];

    while ((match = handlerRegex.exec(pythonSource)) !== null) {
      const funcName = match[1];
      const funcBody = match[0];

      // Check for raw SetVariableNumeric (should use _set_var)
      if (
        funcBody.includes("SetVariableNumeric(") &&
        !funcBody.includes("_set_var(") &&
        !funcBody.includes("_set_var_string(")
      ) {
        // Exclude comments
        const codeLines = funcBody
          .split("\n")
          .filter((l) => !l.trim().startsWith("#") && !l.trim().startsWith('"""'));
        const hasRawCall = codeLines.some((l) =>
          l.includes("SetVariableNumeric(")
        );
        if (hasRawCall) {
          violations.push(
            `${funcName} uses raw SetVariableNumeric instead of _set_var`
          );
        }
      }
    }

    expect(
      violations,
      `Handler functions should use _set_var/_set_var_string:\n${violations.join("\n")}`
    ).toEqual([]);
  });
});

describe("Variable routing constants", () => {
  it("should document the suffix-to-API mapping in the module header", () => {
    // Verify the documentation block exists
    expect(pythonSource).toContain("VARIABLE API ROUTING");
    expect(pythonSource).toContain(
      "_prm, _pop, _chk, _rdo, no suffix"
    );
    expect(pythonSource).toContain(
      "_dtbl, _ttbl"
    );
  });

  it("should have _set_var msg parameter defaulting to 1", () => {
    expect(pythonSource).toContain(
      "def _set_var(app, block_id: int, var_name: str, value, row: int = 0, col: int = 0, msg: int = 1)"
    );
  });
});
