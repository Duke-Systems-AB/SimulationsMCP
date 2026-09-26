/**
 * Backend Bridge - Connects to Python COM backend
 *
 * Uses a long-lived Python subprocess for COM communication with ExtendSim.
 * The Python process is kept alive for the entire MCP session.
 *
 * Features:
 * - Configurable per-command timeouts
 * - Heartbeat/ping to detect hung Python process
 * - Auto-retry on COM connection loss
 */

import { spawn, execFile, ChildProcess } from "child_process";
import * as path from "path";
import * as readline from "readline";
import { busyResult, stuckStatus, type StuckInfo } from "./stuck-state.js";
import { WATCHDOG_INTERVAL_MS, nextInterval, shouldDismiss, dialogLine, type ProbeCheck } from "./watchdog-core.js";
import { ProbeClient, type ProbeLike, type ProbeDismissed } from "./probe-client.js";

// Path to Python scripts
const PYTHON_SCRIPT = path.join(__dirname, "simulation_backend.py");
const DIALOG_WATCHER_SCRIPT = path.join(__dirname, "dialog_watcher.py");
const PROBE_SCRIPT = path.join(__dirname, "extendsim_probe.py");

// ============================================================================
// TIMEOUT CONFIGURATION
// ============================================================================

/** Default timeout in ms for most commands (10s gives 5x margin over typical <2s) */
const DEFAULT_TIMEOUT = 10_000;

/** Early dialog check delay in ms - fires before main timeout while ExtendSim is still responsive.
 * A dialog appearing this quickly always indicates a config error, never a long-running operation. */
const EARLY_DIALOG_CHECK_MS = 1_000;

/** Per-command timeout overrides (ms) */
const COMMAND_TIMEOUTS: Record<string, number> = {
  // 30s - medium: file I/O, multi-block ops, bulk reads
  model_open: 30_000,
  model_save: 30_000,
  model_close: 30_000,  // saveFirst runs SaveModel, same op model_save gets 30s for
  detect_license: 30_000,  // opens+closes a temp model; called by MCP_init
  model_new: 30_000,
  model_validate: 30_000,
  block_template: 30_000,
  block_add_batch: 30_000,
  block_discover: 30_000,
  block_discover_variables: 30_000,
  block_introspect: 30_000,
  simulation_get_results: 60_000,  // Large models: lightweight scan of 24k+ blocks
  simulation_get_block_stats: 30_000,
  block_list: 120_000,  // Large models: 5 COM calls per block, 24k+ blocks
  db_get_records: 30_000,
  db_import: 30_000,
  db_export: 30_000,
  db_create: 30_000,
  hierarchy_list: 30_000,
  hierarchy_get_contents: 30_000,
  // 60s - save/close/reopen cycle
  block_configure: 60_000,
  // 2-10min - long-running operations
  extendsim_start: 120_000,
  simulation_run: 300_000,
  simulation_run_multi: 600_000,
  simulation_run_scenarios: 600_000,
  optimizer_run: 600_000,       // Only used with waitForCompletion=true
  scenario_manager_run: 600_000, // Only used with waitForCompletion=true
  scenario_manager_status: 30_000,      // May need COM calls during active SM run
  scenario_manager_get_results: 30_000,
  model_extract: 120_000,
};

// Commands that intentionally trigger dialogs as part of their workflow.
// Skip the early dialog check for these — they manage their own timeout.
const SKIP_EARLY_DIALOG_CHECK = new Set([
  'scenario_manager_run',
  'scenario_manager_status',
  'optimizer_run',
]);

/** Heartbeat interval in ms (checks if Python process is alive) */
const HEARTBEAT_INTERVAL = 60_000; // 1 minute

/** Max consecutive retries when Python process dies */
const MAX_RETRIES = 2;

/** Grace window in ms given to an in-flight command after a blocking dialog has
 * been successfully auto-dismissed, for the real response to still arrive before
 * we fall back to a synthetic EXTENDSIM_ERROR_DIALOG error (W2-2). A benign popup (e.g. an
 * informational message) shouldn't fail an otherwise-successful command. */
export const DIALOG_DISMISS_GRACE_MS = 5_000;

// ============================================================================
// PROCESS STATE
// ============================================================================

// Python process (singleton)
let pythonProcess: ChildProcess | null = null;
let rl: readline.Interface | null = null;
let isInitialized = false;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

// Request queue for handling sequential calls
export interface PendingRequest {
  resolve: (value: any) => void;
  reject: (error: Error) => void;
  command: string;
  params: object;
  retryCount: number;
  // Timers armed per send attempt (W2-1: re-armed fresh on every actual send,
  // including retries, so a retry never inherits a leftover countdown).
  timeoutId?: ReturnType<typeof setTimeout>;
  earlyDialogTimerId?: ReturnType<typeof setTimeout>;
  earlyResolved?: boolean;
  // Grace-window timer after a successful dialog dismiss (W2-2).
  graceTimerId?: ReturnType<typeof setTimeout>;
  // The watchdog (spec §5.3): a probe check every ~10s while this request runs.
  watchdogTimerId?: ReturnType<typeof setTimeout>;
  // The most recent probe result seen for this request, if any.
  lastCheck?: ProbeCheck | null;
  // Bumped by clearRequestTimers - so on every (re-)arm, every resolve/reject, AND the
  // instant a dying Python process's timers are cleared for retry (bumping only on re-arm
  // would leave a gap between the death and the retry being re-armed, during which a
  // callback from the dead attempt would still see the request queued, the same attempt,
  // and freshly-reset flags). Scheduled async callbacks capture the value at schedule time
  // and bail if it no longer matches after an await.
  attempt?: number;
  // True from the moment the main timeout's own dialog check starts until it either settles
  // the request or defers it into the grace window. While true, the watchdog does not run a
  // check of its own - the two would otherwise race the same dialog.
  timeoutChecking?: boolean;
  // True from just before the watchdog calls p.dismiss() until it returns. Tells the main
  // timeout, if it fires during that window, to defer to the watchdog's outcome instead of
  // running its own, separate dialog_watcher check against a dialog the probe may already
  // have clicked away.
  watchdogActing?: boolean;
  // Set by the main timeout when it found `watchdogActing` set and stood aside. If the
  // watchdog then finds the dialog gone, it owes the timeout an answer - it runs the
  // timeout's own check itself rather than silently losing it.
  timeoutDeferred?: boolean;
}
const requestQueue: PendingRequest[] = [];
let isProcessingRequest = false;
// The stuck state (spec 2026-09-24 §5.1): a command's outcome was settled (timeout, or a
// dialog and the grace window lapsed) while Python is still inside its COM call. Nothing is
// written to Python until it answers that call - this replaces the old stale-response
// counter, which let the queue write the next command behind the blocked one, where it
// timed out in turn (one stuck call ruined the whole session).
let stuck: StuckInfo | null = null;
// Re-entrancy guard for handleProcessDeath (C4 fix)
let isHandlingProcessDeath = false;

// The watchdog's eyes (spec §5.2): started on first use; null when disabled for the session.
let probe: ProbeLike | null = null;
let probeCreated = false;
let stuckPollTimer: ReturnType<typeof setInterval> | null = null;

function getProbe(): ProbeLike | null {
  if (!probe && !probeCreated) { probe = new ProbeClient(PROBE_SCRIPT); probeCreated = true; }
  return probe && !probe.disabled ? probe : null;
}

/** Same shape the early and timeout checks build from dialog_watcher.py output. */
function dialogInfoFrom(dismissed: ProbeDismissed[]): DialogInfo {
  return {
    found: true,
    text: dismissed.flatMap((d) => d.texts ?? []).join("; "),
    dismissed: dismissed.every((d) => d.dismissed === true),
    details: dismissed,
  };
}

/** A probe call that throws is treated exactly like one that returns null: no answer. */
async function probeCall<T>(call: () => Promise<T | null>): Promise<T | null> {
  try {
    return await call();
  } catch (e) {
    console.error(`Probe call failed: ${e}`);
    return null;
  }
}

/**
 * Re-arms the watchdog after `delay` ms: asks the probe for a check, dismisses
 * a dialog for ordinary commands (S6/S7), and widens the interval when checks
 * are slow. Silently does nothing when the request has already settled, when
 * there is no usable probe (fallback: today's 1s + timeout checks only), when
 * a later attempt has superseded this one, or while the main timeout's own
 * dialog check is running (the two would otherwise race the same dialog).
 *
 * A dialog is acted on only when the probe also recognised ExtendSim's main window
 * (`windowFound`). The probe reports any visible window titled "ExtendSim" as a dialog,
 * but dismiss() refuses to click one it cannot tie to ExtendSim - acting on it anyway
 * would settle the command as "could not be dismissed" for a window we were never allowed
 * to touch. Without the main window the check is only recorded and the watch goes on.
 */
function scheduleWatchdog(req: PendingRequest, delay: number): void {
  const attempt = req.attempt;
  const bail = () =>
    !requestQueue.includes(req) || req.earlyResolved || !!req.graceTimerId || !!req.timeoutChecking || req.attempt !== attempt;

  req.watchdogTimerId = setTimeout(async () => {
    req.watchdogTimerId = undefined;
    if (bail()) return;
    const p = getProbe();
    if (!p) return;                                   // fallback: today's 1 s + timeout checks only
    const check = await probeCall(() => p.check());
    if (bail()) return;
    if (check) req.lastCheck = check;
    if (check && check.windowFound && shouldDismiss(req.command, check)) {
      // Tells the main timeout, if it fires while this is in flight, to defer to us instead
      // of running its own separate check against a dialog we may be about to click away.
      // Cleared in `finally` so the timeout can never be left deferring forever.
      req.watchdogActing = true;
      let dismissed: ProbeDismissed[] | null;
      try {
        dismissed = await probeCall(() => p.dismiss());
      } finally {
        req.watchdogActing = false;
      }
      if (bail()) return;
      if (!dismissed || dismissed.length === 0) {
        // An empty dismiss means "nothing there to click" (the dialog may have closed on
        // its own), not "found but could not click". Re-check once before deciding: still
        // there -> genuinely stuck; gone (or the re-check itself fails, same as
        // `dismissed === null`) -> nothing to report, keep watching.
        const recheck = await probeCall(() => p.check());
        if (bail()) return;
        if (recheck && recheck.windowFound && recheck.dialogs.length > 0) {
          req.earlyResolved = true;
          clearTimeout(req.timeoutId);
          const info: DialogInfo = {
            found: true, text: recheck.dialogs.map(dialogLine).join("; "), dismissed: false, details: recheck.dialogs,
          };
          console.error(`Watchdog found a dialog during '${req.command}': ${info.text} (dismissed: ${info.dismissed})`);
          handleDialogResult(req, info, "watchdog");
          return;
        }
        if (req.timeoutDeferred) {
          // The main timeout fired while we were mid-dismiss and stood aside. Now that we
          // know the dialog is gone, its check is ours to run - otherwise its answer (and,
          // if nothing is found, the request's genuine COM_TIMEOUT) is simply lost.
          req.timeoutDeferred = false;
          await runTimeoutCheck(req, attempt);
          return;
        }
        scheduleWatchdog(req, nextInterval(delay, recheck ? recheck.durationMs : null));
        return;
      }
      req.earlyResolved = true;
      clearTimeout(req.timeoutId);
      const info = dialogInfoFrom(dismissed);
      console.error(`Watchdog found a dialog during '${req.command}': ${info.text} (dismissed: ${info.dismissed})`);
      handleDialogResult(req, info, "watchdog");
      return;
    }
    scheduleWatchdog(req, nextInterval(delay, check ? check.durationMs : null));
  }, delay);
}

/** While stuck, keep polling the probe so extendsim_status shows a fresh last check (spec
 * §5.1). Captures the stuck episode before the await: a process death (or the command
 * finally answering) between the check starting and finishing must not write a check from
 * one episode into the next. */
function startStuckPolling(): void {
  stopStuckPolling();
  const poll = async () => {
    const p = getProbe();
    const episode = stuck;
    if (!p || !episode) return;
    const check = await probeCall(() => p.check());
    if (check && stuck === episode) episode.lastCheck = check;
  };
  void poll();
  stuckPollTimer = setInterval(() => { void poll(); }, WATCHDOG_INTERVAL_MS);
}

function stopStuckPolling(): void {
  if (stuckPollTimer) clearInterval(stuckPollTimer);
  stuckPollTimer = null;
}

// ============================================================================
// DIALOG WATCHER
// ============================================================================

/** Shape reported by dialog_watcher.py on stdout (and passed back up from here). */
interface DialogWatcherResult {
  found: boolean;
  dialogText?: string;
  dialogs?: any[];
}

/**
 * Parses dialog_watcher.py's stdout. Returns null when stdout is not the expected JSON
 * (e.g. the process was killed before printing anything), which is the only case that
 * should be logged as an error - a normal "no dialog" run exits 1 with valid JSON.
 */
function parseDialogWatcherOutput(stdout: string): DialogWatcherResult | null {
  try {
    const result = JSON.parse(stdout.trim());
    if (result && typeof result === "object" && "found" in result) {
      return result;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Spawns a separate Python process to detect and dismiss blocking ExtendSim dialogs.
 * Uses Windows UI Automation to find QMessageBox popups, read their text, and click OK.
 *
 * Called when a COM command times out - the dialog may be blocking ExtendSim.
 * Returns the dialog text (if found) so it can be included in the error message.
 */
export async function dismissExtendSimDialog(timeoutSec: number = 5): Promise<DialogWatcherResult> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      resolve({ found: false });
    }, (timeoutSec + 2) * 1000);

    execFile(
      "python",
      ["-u", DIALOG_WATCHER_SCRIPT, String(timeoutSec), "0.5"],
      { timeout: (timeoutSec + 3) * 1000 },
      (error, stdout, _stderr) => {
        clearTimeout(timer);
        // execFile reports an error for dialog_watcher.py's own exit code 1, which it
        // uses for the normal "no dialog found" case (see dialog_watcher.py). If stdout
        // still parsed as the expected JSON, treat it like any other result instead of
        // logging an error on every routine "no dialog" check.
        const result = parseDialogWatcherOutput(stdout);
        if (!result) {
          if (error?.killed) {
            // execFile's timeout stopped the watcher before it could print anything.
            // Measured live 2026-09-25: this happens because ExtendSim was busy (a long
            // ModL call or a simulation run), not because anything went wrong.
            console.error(`Dialog watcher gave up after ${timeoutSec} s - ExtendSim was busy`);
          } else if (error) {
            console.error(`Dialog watcher error: ${error.message}`);
          } else {
            console.error(`Dialog watcher invalid output: ${stdout}`);
          }
          resolve({ found: false });
          return;
        }
        if (result.found && (result.dialogs?.length ?? 0) > 0) {
          // Combine all dialog texts into a single string
          const allTexts = (result.dialogs ?? [])
            .flatMap((d: any) => d.texts || [])
            .join("; ");
          resolve({
            found: true,
            dialogText: allTexts,
            dialogs: result.dialogs,
          });
        } else {
          resolve({ found: false });
        }
      }
    );
  });
}

// ============================================================================
// HEARTBEAT
// ============================================================================

function startHeartbeat(): void {
  stopHeartbeat();
  heartbeatTimer = setInterval(() => {
    if (!pythonProcess || pythonProcess.killed) {
      console.error("Heartbeat: Python process is dead");
      stopHeartbeat();
      return;
    }
    // Check if process is responsive by verifying it hasn't exited
    if (pythonProcess.exitCode !== null) {
      console.error(`Heartbeat: Python process exited with code ${pythonProcess.exitCode}`);
      handleProcessDeath();
    }
  }, HEARTBEAT_INTERVAL);
}

function stopHeartbeat(): void {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function handleProcessDeath(): void {
  if (isHandlingProcessDeath) return; // Re-entrancy guard (C4)
  isHandlingProcessDeath = true;
  stuck = null; // a new Python process is not stuck
  stopStuckPolling();
  isInitialized = false;
  pythonProcess = null;
  rl = null;
  stopHeartbeat();

  // Reject only the current active request; re-queue the rest for retry
  const failed = requestQueue.shift();
  if (failed) {
    isProcessingRequest = false;
    if (failed.retryCount < MAX_RETRIES) {
      console.error(`Retrying command '${failed.command}' (attempt ${failed.retryCount + 1}/${MAX_RETRIES})`);
      // W2-1: drop the timers armed for the original (now-dead) attempt so the
      // retry doesn't inherit a countdown that's already partially elapsed.
      // Fresh timers are re-armed in processNextRequest() when the retry is
      // actually resent.
      clearRequestTimers(failed);
      failed.retryCount++;
      requestQueue.unshift(failed);
      // Will be picked up after reinit
      retryPendingRequests();
    } else {
      failed.reject(new Error(`Python process died after ${MAX_RETRIES} retries for command: ${failed.command}`));
      retryPendingRequests();
    }
  }
  isHandlingProcessDeath = false;
}

async function retryPendingRequests(): Promise<void> {
  if (requestQueue.length === 0) return;

  try {
    await initBackend();
    processNextRequest();
  } catch (e) {
    // Backend failed to restart - reject all pending
    while (requestQueue.length > 0) {
      const pending = requestQueue.shift();
      if (pending) {
        pending.reject(new Error(`Failed to restart Python backend: ${e}`));
      }
    }
  }
}

// ============================================================================
// INITIALIZATION
// ============================================================================

/**
 * Initializes the Python backend (singleton)
 */
export async function initBackend(): Promise<void> {
  if (isInitialized && pythonProcess && !pythonProcess.killed) {
    return; // Already initialized and running
  }

  console.error(`Starting Python COM backend: ${PYTHON_SCRIPT}`);

  // Start Python with unbuffered output for faster response
  pythonProcess = spawn("python", ["-u", PYTHON_SCRIPT], {
    stdio: ["pipe", "pipe", "pipe"],
    // Keep process alive
    detached: false,
  });

  // Handle stderr (for debugging)
  pythonProcess.stderr?.on("data", (data) => {
    console.error(`Python: ${data.toString().trim()}`);
  });

  // Handle stdout with readline
  if (pythonProcess.stdout) {
    rl = readline.createInterface({
      input: pythonProcess.stdout,
      crlfDelay: Infinity,
    });

    rl.on("line", (line) => {
      if (!line.trim()) return;

      try {
        const response = JSON.parse(line);
        processResponse(response);
      } catch {
        console.error(`Failed to parse Python response: ${line}`);
        // Still try to process next request
        processResponse({ error: `Invalid JSON response: ${line}`, errorCode: "INVALID_JSON" });
      }
    });
  }

  // Handle process exit
  pythonProcess.on("exit", (code) => {
    console.error(`Python process exited with code ${code}`);
    handleProcessDeath();
  });

  pythonProcess.on("error", (err) => {
    console.error(`Python process error: ${err.message}`);
    isInitialized = false;
  });

  isInitialized = true;

  // Start heartbeat monitoring
  startHeartbeat();

  // Wait a bit for process to start
  await new Promise((resolve) => setTimeout(resolve, 200));
}

// ============================================================================
// REQUEST/RESPONSE HANDLING
// ============================================================================

/**
 * Handles response from Python
 */
export function processResponse(response: any): void {
  // The answer to a command that was already settled (see `stuck`): ExtendSim is free again.
  if (stuck) {
    leaveStuck();
    processNextRequest();
    return;
  }

  if (requestQueue.length > 0) {
    const pending = requestQueue.shift();
    if (pending) {
      isProcessingRequest = false;
      pending.resolve(response);
      // Process next request if available
      processNextRequest();
    }
  }
}

/**
 * Processes the next request in queue
 */
function processNextRequest(): void {
  if (stuck) { failQueuedAsBusy(); return; }

  if (isProcessingRequest || requestQueue.length === 0) {
    return;
  }

  if (!pythonProcess || !pythonProcess.stdin || pythonProcess.killed) {
    // Python process is dead - try to recover
    retryPendingRequests();
    return;
  }

  // Send next request (first in queue is active)
  const current = requestQueue[0];
  if (current) {
    isProcessingRequest = true;
    // W2-1: (re-)arm fresh timers for this send attempt. This is the single
    // choke point where a command is actually written to Python's stdin, so
    // both first sends and retries after a process death get a full,
    // un-decayed timeout/early-dialog-check window.
    armRequestTimers(current);
    const request = JSON.stringify({
      command: current.command,
      params: current.params,
    });
    try {
      pythonProcess.stdin.write(request + "\n");
    } catch (e) {
      console.error(`Failed to write to Python stdin: ${e}`);
      handleProcessDeath();
    }
  }
}

function enterStuck(command: string, lastCheck: ProbeCheck | null): void {
  stuck = { command, since: Date.now(), lastCheck };
  console.error(`ExtendSim is stuck in '${command}'; answering further commands with EXTENDSIM_BUSY until it returns`);
  startStuckPolling();
}

function leaveStuck(): void {
  if (stuck) console.error(`'${stuck.command}' answered after ${Math.round((Date.now() - stuck.since) / 1000)} s; ExtendSim is free again`);
  stuck = null;
  stopStuckPolling();
}

/** While stuck, nothing waits in the queue: every waiting command is answered at once. */
function failQueuedAsBusy(): void {
  while (stuck && requestQueue.length > 0) {
    const pending = requestQueue.shift();
    pending?.resolve(busyResult(stuck, Date.now()));
  }
}

/** A dialog that was found but not clicked away is the best "last check" we have. */
function checkFromDialog(dialogInfo: DialogInfo): ProbeCheck | null {
  if (!dialogInfo.found || dialogInfo.dismissed) return null;
  return {
    dialogs: [{ title: "ExtendSim", texts: dialogInfo.text ? [dialogInfo.text] : [], buttons: [] }],
    windowFound: true, windowResponding: null, at: Date.now(), durationMs: 0,
  };
}

/**
 * Gets the timeout for a specific command
 */
function getTimeout(command: string): number {
  return COMMAND_TIMEOUTS[command] ?? DEFAULT_TIMEOUT;
}

export type DialogInfo = { found: boolean; text?: string; dismissed?: boolean; details?: any[] };

/**
 * Clears every timer that may be armed for a request (main timeout, early
 * dialog check, dialog-dismiss grace window) and resets their bookkeeping.
 * Safe to call repeatedly / when some timers were never armed (W2-1, W2-2).
 *
 * Also bumps `attempt`: this is called at every point that can
 * invalidate in-flight callbacks - a fresh arm, a resolve/reject, AND (via
 * handleProcessDeath) the moment a dying attempt's timers are torn down for retry, closing
 * the gap that a bump-on-re-arm-only left between the death and the retry actually being
 * re-armed.
 */
export function clearRequestTimers(req: PendingRequest): void {
  clearTimeout(req.timeoutId);
  clearTimeout(req.earlyDialogTimerId);
  clearTimeout(req.graceTimerId);
  clearTimeout(req.watchdogTimerId);
  req.timeoutId = undefined;
  req.earlyDialogTimerId = undefined;
  req.graceTimerId = undefined;
  req.watchdogTimerId = undefined;
  req.earlyResolved = false;
  req.timeoutChecking = false;
  req.watchdogActing = false;
  req.timeoutDeferred = false;
  req.attempt = (req.attempt ?? 0) + 1;
}

/**
 * Resolves a pending request with a synthetic error - EXTENDSIM_ERROR_DIALOG when a
 * dialog was found, COM_TIMEOUT only when none was - and
 * cleans it out of the request queue. Used for failed (or absent) dismisses
 * immediately, and as the fallback once a successful dismiss's grace window
 * lapses without a real response (W2-2).
 */
export function resolveWithDialogError(req: PendingRequest, dialogInfo: DialogInfo, source: string): void {
  const index = requestQueue.indexOf(req);
  if (index === -1) return; // already resolved (e.g. real response arrived during grace window)

  const wasInFlight = index === 0 && isProcessingRequest;
  requestQueue.splice(index, 1);
  isProcessingRequest = false;
  if (wasInFlight) enterStuck(req.command, checkFromDialog(dialogInfo) ?? req.lastCheck ?? null);

  const timeout = getTimeout(req.command);
  let message: string;
  let suggestion: string;
  // A dialog means ExtendSim itself reported an error - usually about the model
  // (a missing resource pool, an index out of range in a block). That is not a
  // timeout, and must not be labelled one: COM_TIMEOUT reads as "transient, try
  // again", and on 2026-09-14 a client did exactly that, re-running
  // simulation_run six times against the identical "[62]Queue" error dialog.
  // COM_TIMEOUT is kept for its real meaning: no answer, and no dialog to explain why.
  let errorCode: string;
  if (dialogInfo.found && dialogInfo.dismissed) {
    errorCode = "EXTENDSIM_ERROR_DIALOG";
    message = `ExtendSim reported an error while running '${req.command}' (dialog detected by ${source}, now dismissed). The error text is in 'dialog.text'.`;
    suggestion = "This is an error reported by ExtendSim, not a timeout: retrying the same call will hit the same error. " +
      "Read 'dialog.text' - it usually names the block (e.g. \"[62]Queue\") and what is wrong with it - and fix that first. " +
      "If a simulation is running, the dialog may have been raised by the run itself rather than by this command.";
  } else if (dialogInfo.found && !dialogInfo.dismissed) {
    errorCode = "EXTENDSIM_ERROR_DIALOG";
    message = `ExtendSim reported an error while running '${req.command}' (dialog detected by ${source}). The dialog could NOT be dismissed automatically.`;
    suggestion = "HUMAN INTERVENTION REQUIRED: The user must manually dismiss the dialog in ExtendSim before any further call can succeed.";
  } else {
    errorCode = "COM_TIMEOUT";
    message = `Command '${req.command}' timed out after ${timeout / 1000}s. No blocking dialog was detected.`;
    // The server is now stuck until ExtendSim answers this call, so an immediate retry only
    // meets EXTENDSIM_BUSY (and for a long blocking simulation_run it invites a loop).
    suggestion = "ExtendSim has not answered yet and no blocking dialog was found. The server waits for it: " +
      "poll extendsim_status until state is 'idle' before sending anything else, then retry if the command did not take effect. " +
      "For long simulations use waitForCompletion=false and read results with simulation_get_results.";
  }

  req.resolve({
    status: "error",
    errorCode,
    message,
    dialog: dialogInfo,
    suggestion,
  });
  processNextRequest();
}

/**
 * Handles a dialog-detection result from either the early check or the main
 * timeout. A failed (or absent) dismiss resolves immediately as before. A
 * SUCCESSFUL dismiss (W2-2) does not resolve right away: the popup may have
 * been benign and the underlying command can still complete, so we give the
 * in-flight request a grace window to deliver its real response first. If the
 * window lapses with no response, we fall back to the synthetic error.
 */
/**
 * Pure decision: does a dialog-detection result warrant deferring resolution
 * into the grace window (W2-2), rather than resolving immediately with a
 * synthetic error? True only for a CONFIRMED, successfully-dismissed dialog -
 * a benign popup shouldn't fail an otherwise-successful command. Extracted so
 * this branch can be unit-tested directly, without driving the full
 * timer/queue machinery.
 */
export function shouldDeferDialogError(dialogInfo: DialogInfo): boolean {
  return dialogInfo.found === true && dialogInfo.dismissed === true;
}

export function handleDialogResult(req: PendingRequest, dialogInfo: DialogInfo, source: string): void {
  if (!shouldDeferDialogError(dialogInfo)) {
    resolveWithDialogError(req, dialogInfo, source);
    return;
  }

  if (!requestQueue.includes(req)) return; // already resolved elsewhere

  const attempt = req.attempt;
  console.error(
    `Dialog dismissed for '${req.command}' (${source}); waiting up to ${DIALOG_DISMISS_GRACE_MS / 1000}s for the real response before treating this as an error...`,
  );
  req.graceTimerId = setTimeout(() => {
    req.graceTimerId = undefined;
    // Real response already resolved it, or a later attempt has since superseded this one.
    if (!requestQueue.includes(req) || req.attempt !== attempt) return;
    console.error(`Grace window lapsed for '${req.command}' with no response; falling back to synthetic dialog error`);
    resolveWithDialogError(req, dialogInfo, source);
  }, DIALOG_DISMISS_GRACE_MS);
}

/**
 * Runs the actual "is there a blocking dialog" check via dialog_watcher.py and settles (or
 * defers) the request from its result. This is the main timeout's own check - extracted so
 * the watchdog can run it on the timeout's behalf: if the timeout fires
 * while the watchdog is mid-`p.dismiss()`, the timeout defers instead of racing its own,
 * separate dialog_watcher check against a dialog the probe may already have clicked away: it
 * is the watchdog's job to call this once it knows the outcome, so the timeout's answer is
 * never simply lost.
 */
async function runTimeoutCheck(req: PendingRequest, attempt: number | undefined): Promise<void> {
  let dialogInfo: DialogInfo = { found: false };
  try {
    console.error(`Timeout on '${req.command}' - checking for blocking dialog...`);
    const dialogResult = await dismissExtendSimDialog(5);
    if (dialogResult.found && dialogResult.dialogs?.length) {
      const allTexts = dialogResult.dialogText || "";
      const allDismissed = dialogResult.dialogs.every((d: any) => d.dismissed);
      dialogInfo = {
        found: true,
        text: allTexts,
        dismissed: allDismissed,
        details: dialogResult.dialogs,
      };
      if (allDismissed) {
        console.error(`Dismissed ExtendSim dialog: ${allTexts}`);
      } else {
        console.error(`ExtendSim dialog found but NOT dismissed: ${allTexts}`);
      }
    } else {
      console.error(`No blocking dialog found`);
    }
  } catch (e) {
    console.error(`Dialog watcher failed: ${e}`);
  }

  // Re-validate after the await: a later attempt may have superseded this one, or the
  // watchdog/early check may have already settled or deferred this request.
  if (!requestQueue.includes(req) || req.graceTimerId || req.attempt !== attempt) return;
  handleDialogResult(req, dialogInfo, "timeout");
}

/**
 * Arms the early-dialog-check and main-timeout timers for one send attempt of
 * a request. Called from processNextRequest() at the moment the command is
 * actually written to Python's stdin, so every attempt - including retries
 * after a process death (W2-1) - gets a full, fresh window instead of
 * inheriting a countdown left over from a previous attempt.
 */
export function armRequestTimers(req: PendingRequest): void {
  clearRequestTimers(req); // also bumps req.attempt, invalidating the previous attempt's callbacks
  const attempt = req.attempt;

  const timeout = getTimeout(req.command);

  // Early dialog check: fires before main timeout while ExtendSim is still responsive.
  // A dialog appearing within 3s always indicates a config error, even for long-running ops.
  // Skip for commands that intentionally trigger dialogs (SM, optimizer).
  req.earlyDialogTimerId = SKIP_EARLY_DIALOG_CHECK.has(req.command)
    ? undefined
    : setTimeout(async () => {
        // Skip if request already completed, or a later attempt has superseded this one
        // (a retry after a process death re-sends the same req).
        if (!requestQueue.includes(req) || req.attempt !== attempt) return;

        try {
          console.error(`Early dialog check on '${req.command}' (${EARLY_DIALOG_CHECK_MS / 1000}s)...`);
          const dialogResult = await dismissExtendSimDialog(3);
          // Skip if request completed, or was superseded, while we were checking
          if (!requestQueue.includes(req) || req.attempt !== attempt) return;

          if (dialogResult.found && dialogResult.dialogs?.length) {
            const allTexts = dialogResult.dialogText || "";
            const allDismissed = dialogResult.dialogs.every((d: any) => d.dismissed);
            console.error(`Early dialog check found dialog: ${allTexts} (dismissed: ${allDismissed})`);
            req.earlyResolved = true;
            clearTimeout(req.timeoutId);
            handleDialogResult(
              req,
              { found: true, text: allTexts, dismissed: allDismissed, details: dialogResult.dialogs },
              "early check",
            );
          } else {
            console.error(`Early dialog check: no dialog found`);
          }
        } catch (e) {
          console.error(`Early dialog check failed: ${e}`);
        }
      }, EARLY_DIALOG_CHECK_MS);

  // Main timeout: fires after full timeout period (fallback if early check found nothing).
  // While this runs, `req.timeoutChecking` tells the watchdog to stand aside rather than run
  // its own check of the same dialog concurrently. Conversely, if the watchdog is already
  // mid-`p.dismiss()` (`req.watchdogActing`) when this fires, the timeout defers to it
  // instead of racing it with a second, separate check - `runTimeoutCheck` is then run by
  // the watchdog on the timeout's behalf once its own outcome is known (see scheduleWatchdog).
  req.timeoutId = setTimeout(async () => {
    if (req.earlyResolved || req.graceTimerId || !requestQueue.includes(req)) return;
    if (req.watchdogActing) { req.timeoutDeferred = true; return; }
    req.timeoutChecking = true;
    await runTimeoutCheck(req, attempt);
  }, timeout);

  scheduleWatchdog(req, WATCHDOG_INTERVAL_MS);
}

/**
 * Wraps a PendingRequest's resolve/reject so that whichever fires first also
 * clears every timer armed for this request (main timeout, early dialog
 * check, dialog-dismiss grace window). Extracted out of sendCommand so tests
 * can build a PendingRequest with the exact same wiring production uses,
 * without spawning a Python process.
 */
export function wrapResolveReject(
  pendingRequest: PendingRequest,
  resolve: (value: any) => void,
  reject: (error: Error) => void,
): void {
  pendingRequest.resolve = (value: any) => {
    clearRequestTimers(pendingRequest);
    resolve(value);
  };
  pendingRequest.reject = (error: Error) => {
    clearRequestTimers(pendingRequest);
    reject(error);
  };
}

/**
 * Sends a command to Python backend
 */
async function sendCommand(command: string, params: object): Promise<any> {
  if (stuck) return busyResult(stuck, Date.now());

  // Ensure backend is initialized
  if (!isInitialized || !pythonProcess || pythonProcess.killed) {
    await initBackend();
  }

  return new Promise((resolve, reject) => {
    const pendingRequest: PendingRequest = {
      resolve,
      reject,
      command,
      params,
      retryCount: 0,
    };

    // Wrap resolve/reject to clear any timers armed for this request (main
    // timeout, early dialog check, dialog-dismiss grace window).
    wrapResolveReject(pendingRequest, resolve, reject);

    // Add to queue. Timers are armed by processNextRequest() at the moment the
    // command is actually sent (covers both the immediate case and any wait
    // behind an in-flight command).
    requestQueue.push(pendingRequest);
    processNextRequest();
  });
}

// ============================================================================
// TEST-ONLY SEAMS
// ============================================================================
// Exposed only so tests/unit/backend-lifecycle.test.ts can drive the real
// request-queue/timer/grace-window machinery directly (constructing
// PendingRequest objects and inspecting the queue) without spawning a real
// Python subprocess. Production code (index.ts) never imports these; they
// only read/reset existing module state and change no runtime behavior.

/** The live request queue (same array instance the module mutates). */
export function __getRequestQueueForTests(): PendingRequest[] {
  return requestQueue;
}

/** The current stuck state, or null. */
export function __getStuckForTests(): StuckInfo | null {
  return stuck;
}

/** Marks the head of the queue as written to Python (as processNextRequest would). */
export function __markInFlightForTests(): void {
  isProcessingRequest = true;
}

/** Resets all mutable backend queue/process-flag state between tests. */
export function __resetBackendStateForTests(): void {
  requestQueue.length = 0;
  isProcessingRequest = false;
  stuck = null;
  stopStuckPolling();
  isHandlingProcessDeath = false;
}

/** Sets (or clears) the probe the watchdog uses, bypassing the lazy real-ProbeClient creation. */
export function __setProbeForTests(p: ProbeLike | null): void {
  probe = p;
  probeCreated = true;
}

/** Drives the real process-death path (handleProcessDeath is otherwise private) so tests can
 * verify it clears `stuck` without spawning or killing a real Python process. */
export function __simulateProcessDeathForTests(): void {
  handleProcessDeath();
}

// ============================================================================
// SHUTDOWN
// ============================================================================

/**
 * Gracefully shuts down the Python backend process.
 * Kills the process and cleans up all state.
 */
export function shutdownBackend(): void {
  stopHeartbeat();
  stopStuckPolling();
  probe?.stop();
  probe = null;
  probeCreated = false;

  if (pythonProcess && !pythonProcess.killed) {
    try {
      pythonProcess.kill("SIGTERM");
    } catch {
      // Process may already be dead
    }
  }

  pythonProcess = null;
  rl = null;
  isInitialized = false;
  isProcessingRequest = false;
  stuck = null;
  isHandlingProcessDeath = false;

  // Reject all pending requests
  while (requestQueue.length > 0) {
    const pending = requestQueue.shift();
    if (pending) {
      pending.reject(new Error("Backend shutdown"));
    }
  }
}

// ============================================================================
// STATUS OPERATIONS
// ============================================================================

/** Every extendsim_status answer reports queue state (spec: `state` is always present),
 * even a genuine Python-side error - it is not itself a sign the queue is stuck. */
export function withQueueState<T extends object>(r: T, isStuck: boolean): T & { state: "stuck" | "idle" } {
  return { ...r, state: isStuck ? "stuck" : "idle" };
}

export async function extendsimStatus() {
  if (stuck) return stuckStatus(stuck, Date.now());
  const r = await sendCommand("extendsim_status", {});
  return withQueueState(r, stuck !== null);
}

export async function extendsimStart() {
  return await sendCommand("extendsim_start", {});
}

export async function detectLicense(params: {
  modelId?: string;
}) {
  return await sendCommand("detect_license", params);
}

// ============================================================================
// MODEL OPERATIONS
// ============================================================================

export async function modelOpen(params: {
  filePath: string;
  readOnly?: boolean;
}) {
  return await sendCommand("model_open", params);
}

export async function modelSave(params: {
  modelId?: string;
  filePath?: string;
}) {
  return await sendCommand("model_save", params);
}

export async function modelList() {
  return await sendCommand("model_list", {});
}

export async function modelInfo(params: {
  modelId?: string;
  includeStatistics?: boolean;
}) {
  return await sendCommand("model_info", params);
}

export async function modelClose(params: {
  modelId?: string;
  saveFirst?: boolean;
}) {
  return await sendCommand("model_close", params);
}

export async function modelNew(params: { savePath?: string }) {
  return await sendCommand("model_new", params);
}

// ============================================================================
// BLOCK OPERATIONS
// ============================================================================

export async function blockAdd(params: {
  modelId?: string;
  libraryName: string;
  blockName: string;
  x?: number;
  y?: number;
  neighbor?: number;
  side?: number;
  label?: string;
}) {
  return await sendCommand("block_add", params);
}

export async function blockAddBatch(params: {
  modelId?: string;
  blocks: Array<{
    libraryName: string;
    blockName: string;
    x?: number;
    y?: number;
    neighbor?: number;
    side?: number;
    label?: string;
  }>;
}) {
  return await sendCommand("block_add_batch", params);
}

export async function blockConnect(params: {
  modelId?: string;
  sourceBlockId: number;
  sourceConnector: number | string;
  targetBlockId: number;
  targetConnector: number | string;
}) {
  return await sendCommand("block_connect", params);
}

export async function blockDisconnect(params: {
  modelId?: string;
  sourceBlockId: number;
  sourceConnector: number | string;
  targetBlockId: number;
  targetConnector: number | string;
}) {
  return await sendCommand("block_disconnect", params);
}

export async function connectChain(params: {
  modelId?: string;
  blockIds: number[];
  sourceConnector?: number | string;
  targetConnector?: number | string;
}) {
  return await sendCommand("connect_chain", params);
}

export async function connectGraph(params: {
  modelId?: string;
  connections: Array<{
    sourceBlockId: number;
    targetBlockId: number;
    sourceConnector?: number | string;
    targetConnector?: number | string;
  }>;
}) {
  return await sendCommand("connect_graph", params);
}

export async function blockRemove(params: {
  modelId?: string;
  blockId: number;
  allowUndo?: boolean;
}) {
  return await sendCommand("block_remove", params);
}

export async function blockList(params: { modelId?: string; detail?: string }) {
  return await sendCommand("block_list", params);
}

export async function connectionList(params: { modelId?: string }) {
  return await sendCommand("connection_list", params);
}

export async function blockInfo(params: {
  modelId?: string;
  query?: string;
  blockId?: number;
}) {
  return await sendCommand("block_info", params);
}

export async function blockDiscover(params: {
  modelId?: string;
  libraryName: string;
  blockName: string;
}) {
  return await sendCommand("block_discover", params);
}

export async function blockDiscoverVariables(params: {
  modelId?: string;
  blockId?: number;
  libraryName?: string;
  blockName?: string;
  maxDialogId?: number;
}) {
  return await sendCommand("block_discover_variables", params);
}

export async function blockIntrospect(params: {
  modelId?: string; blockId?: number; libraryName?: string;
  blockName?: string; readScalarValues?: boolean;
}) {
  return await sendCommand("block_introspect", params);
}

export async function blockSetValue(params: {
  modelId?: string;
  blockId: number;
  dialogNumber: number | string;
  value: number | string;
  row?: number;
  col?: number;
}) {
  return await sendCommand("block_set_value", params);
}

export async function blockGetValue(params: {
  modelId?: string;
  blockId: number;
  dialogNumber: number | string;
  row?: number;
  col?: number;
  asString?: boolean;
}) {
  return await sendCommand("block_get_value", params);
}

export async function executeCommand(params: {
  command: string;
  getResult?: boolean;
  resultType?: string;
}) {
  return await sendCommand("execute_command", params);
}

export async function templateList() {
  return await sendCommand("template_list", {});
}

export async function blockTemplate(params: {
  modelId?: string;
  templateName: string;
  startX?: number;
  startY?: number;
  spacing?: number;
  parameters?: Record<string, number | string>;
}) {
  return await sendCommand("block_template", params);
}

// ============================================================================
// SIMULATION OPERATIONS
// ============================================================================

export async function simulationRun(params: {
  modelId?: string;
  endTime?: number;
  runMode?: string;
  resetFirst?: boolean;
  waitForCompletion?: boolean;
  includeStats?: boolean;
  statsBlockIds?: number[];
}) {
  return await sendCommand("simulation_run", params);
}

export async function simulationStop(params: { modelId?: string }) {
  return await sendCommand("simulation_stop", params);
}

export async function simulationPause(params: { modelId?: string }) {
  return await sendCommand("simulation_pause", params);
}

export async function simulationResume(params: { modelId?: string }) {
  return await sendCommand("simulation_resume", params);
}

export async function simulationStatus(params: { modelId?: string }) {
  return await sendCommand("simulation_status", params);
}

export async function simulationGetResults(params: { modelId?: string }) {
  return await sendCommand("simulation_get_results", params);
}

// ============================================================================
// BLOCK CONFIGURATION OPERATIONS
// ============================================================================

// ============================================================================
// ATTRIBUTE OPERATIONS
// ============================================================================

export async function attributeSet(params: {
  modelId?: string;
  blockId: number;
  attributeName: string;
  valueType?: string;
  value?: number;
  distribution?: string;
  arg1?: number;
  arg2?: number;
  arg3?: number;
}) {
  return await sendCommand("attribute_set", params);
}

export async function attributeGet(params: {
  modelId?: string;
  blockId: number;
  attributeName: string;
}) {
  return await sendCommand("attribute_get", params);
}

// ============================================================================
// VALIDATION OPERATIONS
// ============================================================================

export async function modelValidate(params: { modelId?: string }) {
  return await sendCommand("model_validate", params);
}

export async function modelOverview(params: { modelId?: string } = {}) {
  return await sendCommand("model_overview", params);
}

export async function modelSnapshot(params: { modelId?: string }) {
  return await sendCommand("model_snapshot", params);
}

export async function modelExtract(params: {
  savePath?: string;
  sections?: string[];
  modelId?: string;
}) {
  return await sendCommand("model_extract", params);
}

export async function extractPsg(params: {
  filePath?: string;
  savePath?: string;
  modelId?: string;
}) {
  return await sendCommand("extract_psg", params);
}

export async function mineCandidates(params: {
  filePath?: string;
  psgPath?: string;
  savePath?: string;
  modelId?: string;
}) {
  return await sendCommand("mine_candidates", params);
}

export async function clusterPatterns(params: {
  candidatesPaths?: string[];
  filePaths?: string[];
  psgPaths?: string[];
  savePath?: string;
}) {
  return await sendCommand("cluster_patterns", params);
}

export async function approvePattern(params: {
  candidate?: Record<string, any>;
  patternsPath?: string;
  patternFingerprint?: string;
  naming?: Record<string, any>;
  dryRun?: boolean;
  overwrite?: boolean;
}) {
  return await sendCommand("approve_pattern", params);
}

// ============================================================================
// DATABASE OPERATIONS
// ============================================================================

export async function dbList(params: { modelId?: string }) {
  return await sendCommand("db_list", params);
}

export async function dbTableInfo(params: {
  databaseName: string;
  tableName: string;
  modelId?: string;
}) {
  return await sendCommand("db_table_info", params);
}

export async function dbGetValue(params: {
  databaseName: string;
  tableName: string;
  fieldName: string;
  record: number;
  asString?: boolean;
  modelId?: string;
}) {
  return await sendCommand("db_get_value", params);
}

export async function dbSetValue(params: {
  databaseName: string;
  tableName: string;
  fieldName: string;
  record: number;
  value: number | string;
  modelId?: string;
}) {
  return await sendCommand("db_set_value", params);
}

export async function dbGetRecords(params: {
  databaseName: string;
  tableName: string;
  startRecord?: number;
  endRecord?: number;
  fields?: string[];
  maxRecords?: number;
  modelId?: string;
}) {
  return await sendCommand("db_get_records", params);
}

export async function dbAddRecords(params: {
  databaseName: string;
  tableName: string;
  count?: number;
  position?: number;
  modelId?: string;
}) {
  return await sendCommand("db_add_records", params);
}

export async function dbDeleteRecords(params: {
  databaseName: string;
  tableName: string;
  startRecord: number;
  endRecord: number;
  modelId?: string;
}) {
  return await sendCommand("db_delete_records", params);
}

// ============================================================================
// RESOURCE POOL OPERATIONS
// ============================================================================

export async function resourcePoolGetStats(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("resource_pool_get_stats", params);
}

// ============================================================================
// SIMULATION SETUP OPERATIONS
// ============================================================================

export async function simulationSetupGet(params: { modelId?: string }) {
  return await sendCommand("simulation_setup_get", params);
}

export async function simulationSetupSet(params: {
  modelId?: string;
  endTime?: number;
  startTime?: number;
  numberOfRuns?: number;
  randomSeed?: number;
  seedControl?: number;
  timeUnits?: number;
  deltaTime?: number;
  numSteps?: number;
  simulationOrder?: number;
}) {
  return await sendCommand("simulation_setup_set", params);
}

// ============================================================================
// BLOCK STATISTICS OPERATIONS
// ============================================================================

export async function blockGetStats(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("block_get_stats", params);
}

export async function simulationGetBlockStats(params: {
  modelId?: string;
  blockIds: number[];
}) {
  return await sendCommand("simulation_get_block_stats", params);
}

// ============================================================================
// MULTI-RUN AND SCENARIO OPERATIONS
// ============================================================================

export async function simulationRunMulti(params: {
  modelId?: string;
  numberOfRuns: number;
  endTime?: number;
  randomSeed?: number;
  runMode?: string;
  collectPerRun?: boolean;
  blockIds?: number[];
}) {
  return await sendCommand("simulation_run_multi", params);
}

export async function simulationRunScenarios(params: {
  modelId?: string;
  blockId: number;
  dialogVariable: string;
  values: (number | string)[];
  endTime?: number;
  runMode?: string;
}) {
  return await sendCommand("simulation_run_scenarios", params);
}

// v1.5 tools - Hierarchies, Optimizer, Scenario Manager, Analysis Manager

export async function hierarchyList(params: {
  modelId?: string;
}) {
  return await sendCommand("hierarchy_list", params);
}

export async function hierarchyGetContents(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("hierarchy_get_contents", params);
}

export async function optimizerRun(params: {
  modelId?: string;
  timeout?: number;
  waitForCompletion?: boolean;
}) {
  return await sendCommand("optimizer_run", params);
}

export async function optimizerGetResults(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("optimizer_get_results", params);
}

export async function scenarioManagerRun(params: {
  modelId?: string;
  timeout?: number;
  waitForCompletion?: boolean;
}) {
  return await sendCommand("scenario_manager_run", params);
}

export async function scenarioManagerStatus(params: {
  modelId?: string;
}) {
  return await sendCommand("scenario_manager_status", params);
}

export async function scenarioManagerGetResults(params: {
  modelId?: string;
}) {
  return await sendCommand("scenario_manager_get_results", params);
}

// v1.7 - Universal block configuration

export async function blockConfigure(params: {
  modelId?: string;
  blockId: number;
  config?: Record<string, any>;
}) {
  return await sendCommand("block_configure", params);
}

// v1.9.5 - AI Context Persistence

export async function contextGet(params: {
  modelId?: string;
}) {
  return await sendCommand("context_get", params);
}

export async function contextSet(params: {
  modelId?: string;
  purpose?: string;
  keyBlocks?: Array<{ blockId: number; label: string; role: string }>;
  assumptions?: string[];
  notes?: string;
  tags?: string[];
  custom?: Record<string, any>;
  changeEntry?: { summary: string; details?: string };
}) {
  return await sendCommand("context_set", params);
}

export async function contextClear(params: {
  modelId?: string;
  confirm: boolean;
}) {
  return await sendCommand("context_clear", params);
}

// v1.10.0 — Block tools

export async function blockMove(params: {
  modelId?: string;
  blockId: number;
  x: number;
  y: number;
}) {
  return await sendCommand("block_move", params);
}

export async function blockGetPosition(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("block_get_position", params);
}

export async function blockAlign(params: {
  modelId?: string;
  sourceBlockId: number;
  sourceConnector: number | string;
  targetBlockId: number;
  targetConnector: number | string;
  vertical?: boolean;
}) {
  return await sendCommand("block_align", params);
}

export async function blockDuplicate(params: {
  modelId?: string;
  blockId: number;
  label?: string;
}) {
  return await sendCommand("block_duplicate", params);
}

export async function blockFind(params: {
  modelId?: string;
  searchStr: string;
  which?: number;
}) {
  return await sendCommand("block_find", params);
}

// v1.10.0 — DB tools

export async function dbCreate(params: {
  modelId?: string;
  databaseName: string;
  tables?: Array<{
    name: string;
    fields?: Array<{ name: string; type?: string }>;
  }>;
}) {
  return await sendCommand("db_create", params);
}

export async function dbImport(params: {
  modelId?: string;
  filePath: string;
  databaseName: string;
  tableName: string;
  delimiter?: string;
  hasHeader?: boolean;
}) {
  return await sendCommand("db_import", params);
}

export async function dbExport(params: {
  modelId?: string;
  filePath: string;
  databaseName: string;
  tableName: string;
  delimiter?: string;
  includeHeader?: boolean;
}) {
  return await sendCommand("db_export", params);
}

export async function dbFindRecord(params: {
  modelId?: string;
  databaseName: string;
  tableName: string;
  fieldName: string;
  findValue: number | string;
  exactMatch?: boolean;
  startRecord?: number;
}) {
  return await sendCommand("db_find_record", params);
}

export async function dbSort(params: {
  modelId?: string;
  databaseName: string;
  tableName: string;
  field1: string;
  direction1?: number;
  field2?: string;
  direction2?: number;
  field3?: string;
  direction3?: number;
}) {
  return await sendCommand("db_sort", params);
}

// v1.10.0 — Simulation tools

export async function simulationStep(params: {
  modelId?: string;
}) {
  return await sendCommand("simulation_step", params);
}

export async function simulationGetState(params: {
  modelId?: string;
}) {
  return await sendCommand("simulation_get_state", params);
}

// v1.10.0 — Global Array tools

export async function gaList(params: {
  modelId?: string;
}) {
  return await sendCommand("ga_list", params);
}

export async function gaCreate(params: {
  modelId?: string;
  name: string;
  type?: string;
  cols?: number;
  rows?: number;
}) {
  return await sendCommand("ga_create", params);
}

export async function gaRead(params: {
  modelId?: string;
  name: string;
  row?: number;
  col?: number;
  endRow?: number;
  endCol?: number;
}) {
  return await sendCommand("ga_read", params);
}

export async function gaWrite(params: {
  modelId?: string;
  name: string;
  row: number;
  col: number;
  value: number | string;
}) {
  return await sendCommand("ga_write", params);
}

// v1.10.0 — Text block

export async function textBlockAdd(params: {
  modelId?: string;
  text: string;
  x?: number;
  y?: number;
  neighbor?: number;
  side?: number;
  width?: number;
}) {
  return await sendCommand("text_block_add", params);
}

// v1.10.0 — DB Relations

export async function dbRelationsList(params: {
  modelId?: string;
  databaseName: string;
}) {
  return await sendCommand("db_relations_list", params);
}

export async function dbRelationCreate(params: {
  modelId?: string;
  databaseName: string;
  childTable: string;
  childField: string;
  parentTable: string;
  parentField: string;
}) {
  return await sendCommand("db_relation_create", params);
}

// v1.10.0 — Time convert

export async function timeConvert(params: {
  modelId?: string;
  operation: string;
  value?: number;
  fromType?: number;
  toType?: number;
  simTime?: number;
  timeUnits?: number;
  date?: string;
}) {
  return await sendCommand("time_convert", params);
}

export async function instantiatePattern(params: {
  moleculeId: string;
  params?: Record<string, unknown>;
  modelId?: string;
}) {
  return await sendCommand("instantiate_pattern", params);
}

export async function composeFlow(params: {
  flow: {
    id?: string;
    instances: { ref: string; pattern: string; params?: Record<string, unknown> }[];
    wiring?: { from: string; to: string }[];
  };
  modelId?: string;
}) {
  return await sendCommand("compose_flow", params);
}

export async function listPatterns(params: {
  intent?: string;
}) {
  return await sendCommand("list_patterns", params);
}

export async function getPattern(params: {
  patternId: string;
}) {
  return await sendCommand("get_pattern", params);
}

export async function tableGet(params: {
  blockId: number;
  variableName: string;
  row?: number;
  col?: number;
}) {
  return await sendCommand("table_get", params);
}

export async function tableSet(params: {
  blockId: number;
  variableName: string;
  value: string;
  row?: number;
  col?: number;
}) {
  return await sendCommand("table_set", params);
}

export async function detectAttributes(params: {
  blockId: number;
  modelId?: string;
}) {
  return await sendCommand("detect_attributes", params);
}

