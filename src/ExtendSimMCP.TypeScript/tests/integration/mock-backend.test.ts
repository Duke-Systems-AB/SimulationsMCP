/**
 * Integration tests with a mock Python process.
 *
 * Spawns a simple mock Python script that simulates the backend protocol
 * (JSON in via stdin, JSON out via stdout) to test the full request/response cycle.
 */

import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { spawn, ChildProcess } from "child_process";
import * as readline from "readline";
import { writeFileSync, unlinkSync, existsSync } from "fs";
import { join } from "path";

const MOCK_SCRIPT = join(__dirname, "_mock_backend.py");

// Create a simple mock Python backend for testing
const MOCK_PYTHON = `
import sys
import json

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        request = json.loads(line)
        command = request.get("command")
        params = request.get("params", {})

        if command == "echo":
            result = {"success": True, "echo": params}
        elif command == "error_test":
            result = {"success": False, "errorCode": "TEST_ERROR", "error": "Test error message"}
        elif command == "slow_command":
            import time
            time.sleep(0.5)
            result = {"success": True, "message": "slow done"}
        else:
            result = {"success": False, "errorCode": "UNKNOWN_COMMAND", "error": f"Unknown: {command}"}

        print(json.dumps(result), flush=True)
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}), flush=True)
`;

let mockProcess: ChildProcess | null = null;
let mockRl: readline.Interface | null = null;

function sendMockCommand(
  command: string,
  params: object = {}
): Promise<any> {
  return new Promise((resolve, reject) => {
    if (!mockProcess || !mockProcess.stdin || !mockRl) {
      reject(new Error("Mock process not running"));
      return;
    }

    const timeout = setTimeout(() => {
      reject(new Error("Mock command timeout"));
    }, 5000);

    const handler = (line: string) => {
      clearTimeout(timeout);
      mockRl!.removeListener("line", handler);
      try {
        resolve(JSON.parse(line));
      } catch (e) {
        reject(new Error(`Invalid JSON: ${line}`));
      }
    };

    mockRl.on("line", handler);

    const request = JSON.stringify({ command, params });
    mockProcess.stdin.write(request + "\n");
  });
}

beforeAll(async () => {
  // Write mock script
  writeFileSync(MOCK_SCRIPT, MOCK_PYTHON, "utf-8");

  // Spawn mock process
  mockProcess = spawn("python", ["-u", MOCK_SCRIPT], {
    stdio: ["pipe", "pipe", "pipe"],
  });

  if (mockProcess.stdout) {
    mockRl = readline.createInterface({
      input: mockProcess.stdout,
      crlfDelay: Infinity,
    });
  }

  // Wait for process to start
  await new Promise((resolve) => setTimeout(resolve, 300));
});

afterAll(() => {
  if (mockProcess) {
    mockProcess.kill();
    mockProcess = null;
  }
  if (mockRl) {
    mockRl.close();
    mockRl = null;
  }
  // Cleanup mock script
  if (existsSync(MOCK_SCRIPT)) {
    unlinkSync(MOCK_SCRIPT);
  }
});

describe("Mock Backend Communication", () => {
  it("should send and receive JSON messages", async () => {
    const result = await sendMockCommand("echo", { test: "hello" });
    expect(result.success).toBe(true);
    expect(result.echo).toEqual({ test: "hello" });
  });

  it("should handle error responses with error codes", async () => {
    const result = await sendMockCommand("error_test");
    expect(result.success).toBe(false);
    expect(result.errorCode).toBe("TEST_ERROR");
    expect(result.error).toBe("Test error message");
  });

  it("should handle unknown commands", async () => {
    const result = await sendMockCommand("nonexistent_command");
    expect(result.success).toBe(false);
    expect(result.errorCode).toBe("UNKNOWN_COMMAND");
  });

  it("should handle slow commands", async () => {
    const result = await sendMockCommand("slow_command");
    expect(result.success).toBe(true);
    expect(result.message).toBe("slow done");
  });

  it("should handle multiple sequential commands", async () => {
    const r1 = await sendMockCommand("echo", { seq: 1 });
    const r2 = await sendMockCommand("echo", { seq: 2 });
    const r3 = await sendMockCommand("echo", { seq: 3 });

    expect(r1.echo.seq).toBe(1);
    expect(r2.echo.seq).toBe(2);
    expect(r3.echo.seq).toBe(3);
  });

  it("should handle complex parameter objects", async () => {
    const params = {
      modelId: "model_1",
      blockId: 42,
      nested: { a: 1, b: "two" },
      array: [1, 2, 3],
    };
    const result = await sendMockCommand("echo", params);
    expect(result.success).toBe(true);
    expect(result.echo).toEqual(params);
  });
});
