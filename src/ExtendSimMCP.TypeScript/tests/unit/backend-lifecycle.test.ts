/**
 * Regression tests for the real backend.ts timer/grace-window lifecycle
 * (W2-1 clearRequestTimers/armRequestTimers, W2-2 dialog-dismiss grace
 * window). Unlike backend-queue.test.ts (which tests a hand-copied toy),
 * this file imports and drives the actual production functions.
 *
 * child_process is mocked so no real Python subprocess is ever spawned:
 * dismissExtendSimDialog's execFile call resolves synchronously with
 * "no dialog found", which is all the timer-driven paths below need. Queue
 * membership is controlled directly via the exported test-only seam
 * (__getRequestQueueForTests / __resetBackendStateForTests) instead of going
 * through initBackend()/sendCommand().
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

vi.mock("child_process", () => ({
  execFile: vi.fn(
    (
      _file: string,
      _args: string[],
      _opts: unknown,
      cb: (err: unknown, stdout: string, stderr: string) => void,
    ) => cb(null, JSON.stringify({ found: false }), ""),
  ),
  spawn: vi.fn(),
}));

import {
  clearRequestTimers,
  armRequestTimers,
  handleDialogResult,
  resolveWithDialogError,
  shouldDeferDialogError,
  wrapResolveReject,
  processResponse,
  __getRequestQueueForTests,
  __resetBackendStateForTests,
  DIALOG_DISMISS_GRACE_MS,
  type PendingRequest,
} from "../../src/backend.js";

/**
 * Builds a PendingRequest wired exactly like sendCommand does: req.resolve /
 * req.reject are REPLACED by wrapResolveReject with wrapper functions that
 * clear timers before delegating to the original resolve/reject. So
 * assertions must watch the original spies (resolveSpy/rejectSpy), not
 * req.resolve/req.reject (which are the wrapper functions, not the spies).
 */
function makeRequest(command = "block_add") {
  const resolveSpy = vi.fn();
  const rejectSpy = vi.fn();
  const req = {
    resolve: resolveSpy,
    reject: rejectSpy,
    command,
    params: {},
    retryCount: 0,
  } as unknown as PendingRequest;
  wrapResolveReject(req, resolveSpy, rejectSpy);
  return { req, resolveSpy, rejectSpy };
}

beforeEach(() => {
  vi.useFakeTimers();
  __resetBackendStateForTests();
});

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  __resetBackendStateForTests();
});

describe("shouldDeferDialogError (pure grace-window decision)", () => {
  it("defers only for a confirmed, successfully-dismissed dialog", () => {
    expect(shouldDeferDialogError({ found: true, dismissed: true })).toBe(true);
  });

  it("does not defer when no dialog was found", () => {
    expect(shouldDeferDialogError({ found: false })).toBe(false);
  });

  it("does not defer when a dialog was found but could not be dismissed", () => {
    expect(shouldDeferDialogError({ found: true, dismissed: false })).toBe(false);
  });

  it("does not defer when 'dismissed' is left undefined", () => {
    expect(shouldDeferDialogError({ found: true })).toBe(false);
  });
});

describe("clearRequestTimers / armRequestTimers (W2-1)", () => {
  it("(a) arm then clear leaves no pending timers", () => {
    const { req } = makeRequest();
    armRequestTimers(req);
    expect(req.timeoutId).toBeDefined();
    expect(req.earlyDialogTimerId).toBeDefined();
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    clearRequestTimers(req);

    expect(req.timeoutId).toBeUndefined();
    expect(req.earlyDialogTimerId).toBeUndefined();
    expect(req.graceTimerId).toBeUndefined();
    expect(req.earlyResolved).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("(b) re-arming after a clear yields a fresh, full timeout window (old countdown is truly cancelled)", async () => {
    const { req, resolveSpy } = makeRequest("model_open"); // 30s configured timeout
    const queue = __getRequestQueueForTests();
    queue.push(req);

    armRequestTimers(req); // main timer fires at t=30s if never cleared

    // Consume the 1s early-dialog check (mocked: no dialog found -> no resolve).
    await vi.advanceTimersByTimeAsync(1_000);
    expect(resolveSpy).not.toHaveBeenCalled();

    // Burn most of the window (24s more -> t=25s), then clear+re-arm,
    // simulating a retry after a process death (W2-1).
    await vi.advanceTimersByTimeAsync(24_000);
    clearRequestTimers(req);
    armRequestTimers(req); // fresh main timer now fires at t=25+30=55s

    // If the OLD main timer had survived the clear, it would fire at its
    // original t=30s mark, which falls inside this next 6s window
    // (t=25s -> t=31s). It must NOT fire.
    await vi.advanceTimersByTimeAsync(6_000);
    expect(resolveSpy).not.toHaveBeenCalled();

    // The fresh timer needs its own full 30s from the re-arm point (t=25s),
    // so it fires at t=55s. Advance the remaining 24s (t=31s -> t=55s).
    await vi.advanceTimersByTimeAsync(24_000);
    expect(resolveSpy).toHaveBeenCalledTimes(1);
    const result = resolveSpy.mock.calls[0][0];
    expect(result.errorCode).toBe("COM_TIMEOUT");
  });
});

describe("dialog-dismiss grace window (W2-2)", () => {
  it("(c) a successful dismiss defers resolution, and a delivered response cancels the grace timer", () => {
    const { req, resolveSpy } = makeRequest();
    const queue = __getRequestQueueForTests();
    queue.push(req);

    handleDialogResult(req, { found: true, dismissed: true }, "timeout");

    // Deferred: not resolved yet, but a grace timer is now running.
    expect(resolveSpy).not.toHaveBeenCalled();
    expect(req.graceTimerId).toBeDefined();
    expect(vi.getTimerCount()).toBeGreaterThan(0);

    // Simulate the real response arriving from Python before the grace
    // window lapses (the same processNextRequest -> processResponse path
    // production takes).
    processResponse({ success: true, blockId: 42 });

    expect(resolveSpy).toHaveBeenCalledTimes(1);
    expect(resolveSpy).toHaveBeenCalledWith({ success: true, blockId: 42 });
    // The grace timer must have been cancelled by the real resolve.
    expect(req.graceTimerId).toBeUndefined();
    expect(vi.getTimerCount()).toBe(0);
    expect(queue).not.toContain(req);
  });

  it("(d) grace lapse without a real response produces the synthetic COM_TIMEOUT error exactly once", () => {
    const { req, resolveSpy } = makeRequest();
    const queue = __getRequestQueueForTests();
    queue.push(req);

    const dialogInfo = { found: true, dismissed: true, text: "benign popup" };
    handleDialogResult(req, dialogInfo, "timeout");
    expect(resolveSpy).not.toHaveBeenCalled();

    vi.advanceTimersByTime(DIALOG_DISMISS_GRACE_MS);

    expect(resolveSpy).toHaveBeenCalledTimes(1);
    const result = resolveSpy.mock.calls[0][0];
    expect(result.errorCode).toBe("COM_TIMEOUT");
    expect(result.dialog).toEqual(dialogInfo);
    expect(queue).not.toContain(req);

    // Advancing further, or calling the fallback again directly, must not
    // resolve a second time (already removed from the queue -> no-op).
    vi.advanceTimersByTime(DIALOG_DISMISS_GRACE_MS * 2);
    resolveWithDialogError(req, dialogInfo, "timeout");
    expect(resolveSpy).toHaveBeenCalledTimes(1);
  });

  it("a failed dismiss resolves immediately (no grace window)", () => {
    const { req, resolveSpy } = makeRequest();
    const queue = __getRequestQueueForTests();
    queue.push(req);

    handleDialogResult(req, { found: true, dismissed: false }, "timeout");

    expect(resolveSpy).toHaveBeenCalledTimes(1);
    expect(req.graceTimerId).toBeUndefined();
    const result = resolveSpy.mock.calls[0][0];
    expect(result.errorCode).toBe("COM_TIMEOUT");
  });
});
