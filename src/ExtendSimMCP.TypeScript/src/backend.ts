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

// Path to Python scripts
const PYTHON_SCRIPT = path.join(__dirname, "simulation_backend.py");
const DIALOG_WATCHER_SCRIPT = path.join(__dirname, "dialog_watcher.py");

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
  model_new: 30_000,
  model_validate: 30_000,
  block_template: 30_000,
  block_add_batch: 30_000,
  block_discover: 30_000,
  block_discover_variables: 30_000,
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
  activity_set_delay: 60_000,
  create_set_arrivals: 60_000,
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

// ============================================================================
// PROCESS STATE
// ============================================================================

// Python process (singleton)
let pythonProcess: ChildProcess | null = null;
let rl: readline.Interface | null = null;
let isInitialized = false;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

// Request queue for handling sequential calls
interface PendingRequest {
  resolve: (value: any) => void;
  reject: (error: Error) => void;
  command: string;
  params: object;
  retryCount: number;
}
const requestQueue: PendingRequest[] = [];
let isProcessingRequest = false;
// Counts stale responses to discard after timeouts (see A4 fix)
let staleResponseCount = 0;
// Re-entrancy guard for handleProcessDeath (C4 fix)
let isHandlingProcessDeath = false;

// ============================================================================
// DIALOG WATCHER
// ============================================================================

/**
 * Spawns a separate Python process to detect and dismiss blocking ExtendSim dialogs.
 * Uses Windows UI Automation to find QMessageBox popups, read their text, and click OK.
 *
 * Called when a COM command times out - the dialog may be blocking ExtendSim.
 * Returns the dialog text (if found) so it can be included in the error message.
 */
async function dismissExtendSimDialog(timeoutSec: number = 5): Promise<{
  found: boolean;
  dialogText?: string;
  dialogs?: any[];
}> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      resolve({ found: false });
    }, (timeoutSec + 2) * 1000);

    execFile(
      "python",
      ["-u", DIALOG_WATCHER_SCRIPT, String(timeoutSec), "0.5"],
      { timeout: (timeoutSec + 3) * 1000 },
      (error, stdout, stderr) => {
        clearTimeout(timer);
        if (error) {
          console.error(`Dialog watcher error: ${error.message}`);
          resolve({ found: false });
          return;
        }
        try {
          const result = JSON.parse(stdout.trim());
          if (result.found && result.dialogs?.length > 0) {
            // Combine all dialog texts into a single string
            const allTexts = result.dialogs
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
        } catch {
          console.error(`Dialog watcher invalid output: ${stdout}`);
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
      } catch (e) {
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
function processResponse(response: any): void {
  // Discard stale responses from timed-out requests (A4 fix)
  if (staleResponseCount > 0) {
    staleResponseCount--;
    console.error(`Discarding stale response from timed-out request`);
    // Don't process next - the next request was already sent after timeout
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

/**
 * Gets the timeout for a specific command
 */
function getTimeout(command: string): number {
  return COMMAND_TIMEOUTS[command] ?? DEFAULT_TIMEOUT;
}

/**
 * Sends a command to Python backend
 */
async function sendCommand(command: string, params: object): Promise<any> {
  // Ensure backend is initialized
  if (!isInitialized || !pythonProcess || pythonProcess.killed) {
    await initBackend();
  }

  const timeout = getTimeout(command);

  return new Promise((resolve, reject) => {
    const pendingRequest: PendingRequest = {
      resolve,
      reject,
      command,
      params,
      retryCount: 0,
    };

    // Helper: resolve with dialog-based timeout error and clean up request queue
    const resolveWithDialogError = (
      dialogInfo: { found: boolean; text?: string; dismissed?: boolean; details?: any[] },
      source: string,
    ) => {
      const index = requestQueue.indexOf(pendingRequest);
      if (index === -1) return; // already resolved

      if (index === 0 && isProcessingRequest) {
        staleResponseCount++;
      }
      requestQueue.splice(index, 1);
      isProcessingRequest = false;

      let message: string;
      let suggestion: string;
      if (dialogInfo.found && dialogInfo.dismissed) {
        message = `Command '${command}' blocked by ExtendSim dialog (detected by ${source}). Dialog has been dismissed.`;
        suggestion = "Read the dialog text in the 'dialog.text' field to understand the error. Adjust your parameters and retry.";
      } else if (dialogInfo.found && !dialogInfo.dismissed) {
        message = `Command '${command}' blocked by ExtendSim dialog (detected by ${source}). Dialog could NOT be dismissed automatically.`;
        suggestion = "HUMAN INTERVENTION REQUIRED: The user must manually dismiss the dialog in ExtendSim before retrying.";
      } else {
        message = `Command '${command}' timed out after ${timeout / 1000}s. No blocking dialog was detected.`;
        suggestion = "ExtendSim may be busy or unresponsive. Check extendsim_status or retry the command.";
      }

      resolve({
        status: "error",
        errorCode: "COM_TIMEOUT",
        message,
        dialog: dialogInfo,
        suggestion,
      });
      processNextRequest();
    };

    // Track whether early check already resolved this request
    let earlyResolved = false;

    // Early dialog check: fires before main timeout while ExtendSim is still responsive.
    // A dialog appearing within 3s always indicates a config error, even for long-running ops.
    // Skip for commands that intentionally trigger dialogs (SM, optimizer).
    const earlyDialogTimerId = SKIP_EARLY_DIALOG_CHECK.has(command) ? undefined : setTimeout(async () => {
      // Skip if request already completed
      if (!requestQueue.includes(pendingRequest)) return;

      try {
        console.error(`Early dialog check on '${command}' (${EARLY_DIALOG_CHECK_MS / 1000}s)...`);
        const dialogResult = await dismissExtendSimDialog(3);
        // Skip if request completed while we were checking
        if (!requestQueue.includes(pendingRequest)) return;

        if (dialogResult.found && dialogResult.dialogs?.length) {
          const allTexts = dialogResult.dialogText || "";
          const allDismissed = dialogResult.dialogs.every((d: any) => d.dismissed);
          console.error(`Early dialog check found dialog: ${allTexts} (dismissed: ${allDismissed})`);
          earlyResolved = true;
          clearTimeout(timeoutId);
          resolveWithDialogError(
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

    // Main timeout: fires after full timeout period (fallback if early check found nothing)
    const timeoutId = setTimeout(async () => {
      if (earlyResolved) return; // early check already handled this

      const index = requestQueue.indexOf(pendingRequest);
      if (index > -1) {
        let dialogInfo: { found: boolean; text?: string; dismissed?: boolean; details?: any[] } = { found: false };
        try {
          console.error(`Timeout on '${command}' - checking for blocking dialog...`);
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

        resolveWithDialogError(dialogInfo, "timeout");
      }
    }, timeout);

    // Wrap resolve to clear both timers
    const originalResolve = resolve;
    pendingRequest.resolve = (value) => {
      clearTimeout(timeoutId);
      clearTimeout(earlyDialogTimerId);
      originalResolve(value);
    };

    // Wrap reject to clear both timers
    const originalReject = reject;
    pendingRequest.reject = (error) => {
      clearTimeout(timeoutId);
      clearTimeout(earlyDialogTimerId);
      originalReject(error);
    };

    // Add to queue
    requestQueue.push(pendingRequest);

    // If this is the first request, start processing
    if (requestQueue.length === 1) {
      // Send directly
      isProcessingRequest = true;
      const request = JSON.stringify({ command, params });
      try {
        pythonProcess!.stdin!.write(request + "\n");
      } catch (e) {
        console.error(`Failed to write to Python stdin: ${e}`);
        handleProcessDeath();
      }
    }
  });
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
  staleResponseCount = 0;
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

export async function extendsimStatus() {
  return await sendCommand("extendsim_status", {});
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

export async function activitySetDelay(params: {
  modelId?: string;
  blockId: number;
  delayType?: string;
  value?: number;
  distribution?: string;
  arg1?: number;
  arg2?: number;
  arg3?: number;
}) {
  return await sendCommand("activity_set_delay", params);
}

export async function queueSetPriority(params: {
  modelId?: string;
  blockId: number;
  rankType?: string;
  sortAttribute?: string;
  ascending?: boolean;
}) {
  return await sendCommand("queue_set_priority", params);
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

export async function createSetArrivals(params: {
  modelId?: string;
  blockId: number;
  arrivalType?: string;
  distribution?: string;
  arg1?: number;
  arg2?: number;
  arg3?: number;
  maxArrivals?: number;
}) {
  return await sendCommand("create_set_arrivals", params);
}

export async function gateSetCondition(params: {
  modelId?: string;
  blockId: number;
  demandType?: string;
  initialState?: string;
  openValue?: number;
  closeValue?: number;
}) {
  return await sendCommand("gate_set_condition", params);
}

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
// ROUTING OPERATIONS
// ============================================================================

export async function selectItemOutSetMode(params: {
  modelId?: string;
  blockId: number;
  mode: string;
  attributeName?: string;
  probabilities?: number[];
}) {
  return await sendCommand("select_item_out_set_mode", params);
}

export async function selectItemInSetMode(params: {
  modelId?: string;
  blockId: number;
  mode: string;
}) {
  return await sendCommand("select_item_in_set_mode", params);
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
// BATCH / UNBATCH OPERATIONS
// ============================================================================

export async function batchSetConfig(params: {
  modelId?: string;
  blockId: number;
  batchType?: string;
  batchSize?: number;
  preserveUniqueness?: boolean;
  matchAttribute?: string;
}) {
  return await sendCommand("batch_set_config", params);
}

export async function unbatchSetConfig(params: {
  modelId?: string;
  blockId: number;
  preserveUniqueness?: boolean;
  quantityPerOutput?: number;
}) {
  return await sendCommand("unbatch_set_config", params);
}

// ============================================================================
// RESOURCE POOL OPERATIONS
// ============================================================================

export async function resourcePoolSetConfig(params: {
  modelId?: string;
  blockId: number;
  poolName?: string;
  initialResources?: number;
  allocationRule?: string;
}) {
  return await sendCommand("resource_pool_set_config", params);
}

export async function resourcePoolGetStats(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("resource_pool_get_stats", params);
}

export async function resourcePoolReleaseSetConfig(params: {
  modelId?: string;
  blockId: number;
  releaseQuantity?: number;
}) {
  return await sendCommand("resource_pool_release_set_config", params);
}

export async function queueSetResourcePool(params: {
  modelId?: string;
  blockId: number;
  resourcePoolBlockId: number;
  resourcesNeeded?: number;
}) {
  return await sendCommand("queue_set_resource_pool", params);
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

// ============================================================================
// v1.3 TOOLS: WORKSTATION, EQUATION, SHIFT, TRANSPORT, CONVEY ITEM, SHUTDOWN
// ============================================================================

export async function workstationSetConfig(params: {
  modelId?: string;
  blockId: number;
  maxServers?: number;
  maxQueueLength?: number;
  delayType?: string;
  distribution?: string;
  arg1?: number;
  arg2?: number;
  arg3?: number;
  value?: number;
  costPerTime?: number;
  costPerItem?: number;
}) {
  return await sendCommand("workstation_set_config", params);
}

export async function equationSetFormula(params: {
  modelId?: string;
  blockId: number;
  equation: string;
}) {
  return await sendCommand("equation_set_formula", params);
}

export async function shiftSetSchedule(params: {
  modelId?: string;
  blockId: number;
  schedule: { startTime: number; endTime: number; capacity: number }[];
}) {
  return await sendCommand("shift_set_schedule", params);
}

export async function transportSetConfig(params: {
  modelId?: string;
  blockId: number;
  defaultDistance?: number;
  defaultSpeed?: number;
}) {
  return await sendCommand("transport_set_config", params);
}

export async function conveyItemSetConfig(params: {
  modelId?: string;
  blockId: number;
  conveyorLength?: number;
  defaultSpeed?: number;
  accumulating?: boolean;
}) {
  return await sendCommand("convey_item_set_config", params);
}

export async function shutdownSetConfig(params: {
  modelId?: string;
  blockId: number;
  tbfDistribution?: string;
  tbfArg1?: number;
  tbfArg2?: number;
  ttrDistribution?: string;
  ttrArg1?: number;
  ttrArg2?: number;
}) {
  return await sendCommand("shutdown_set_config", params);
}

export async function tankSetConfig(params: {
  modelId?: string;
  blockId: number;
  capacity?: number;
  initialLevel?: number;
  maxInputRate?: number;
  maxOutputRate?: number;
}) {
  return await sendCommand("tank_set_config", params);
}

export async function valveSetConfig(params: {
  modelId?: string;
  blockId: number;
  maxRate?: number;
  goal?: number;
}) {
  return await sendCommand("valve_set_config", params);
}

export async function mergeSetConfig(params: {
  modelId?: string;
  blockId: number;
  mode?: number;
}) {
  return await sendCommand("merge_set_config", params);
}

export async function divergeSetConfig(params: {
  modelId?: string;
  blockId: number;
  mode?: number;
}) {
  return await sendCommand("diverge_set_config", params);
}

export async function interchangeSetConfig(params: {
  modelId?: string;
  blockId: number;
  capacity?: number;
  initialLevel?: number;
  maxInputRate?: number;
  maxOutputRate?: number;
}) {
  return await sendCommand("interchange_set_config", params);
}

export async function conveyFlowSetConfig(params: {
  modelId?: string;
  blockId: number;
  speed?: number;
  length?: number;
  capacityMax?: number;
  accumulating?: number;
}) {
  return await sendCommand("convey_flow_set_config", params);
}

export async function changeUnitsSetConfig(params: {
  modelId?: string;
  blockId: number;
  factor?: number;
}) {
  return await sendCommand("change_units_set_config", params);
}

export async function biasSetConfig(params: {
  modelId?: string;
  blockId: number;
  biasOrder?: number;
}) {
  return await sendCommand("bias_set_config", params);
}

export async function catchFlowSetConfig(params: {
  modelId?: string;
  blockId: number;
  position?: number;
}) {
  return await sendCommand("catch_flow_set_config", params);
}

export async function throwFlowSetConfig(params: {
  modelId?: string;
  blockId: number;
  position?: number;
  connectorNum?: number;
}) {
  return await sendCommand("throw_flow_set_config", params);
}

export async function historyRSetConfig(params: {
  modelId?: string;
  blockId: number;
  maxRows?: number;
  enableDatabaseLog?: boolean;
}) {
  return await sendCommand("history_r_set_config", params);
}

export async function getRSetConfig(params: {
  modelId?: string;
  blockId: number;
  locationBlockId?: number;
}) {
  return await sendCommand("get_r_set_config", params);
}

export async function setRSetConfig(params: {
  modelId?: string;
  blockId: number;
}) {
  return await sendCommand("set_r_set_config", params);
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

export async function optimizerSetConfig(params: {
  modelId?: string;
  blockId: number;
  populationSize?: number;
  maxGenerations?: number;
  convergencePercent?: number;
  minGenerations?: number;
  maxSampleSize?: number;
  truncate?: boolean;
  truncatePercent?: number;
  antithetic?: boolean;
  showPlotter?: boolean;
}) {
  return await sendCommand("optimizer_set_config", params);
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

export async function scenarioManagerSetConfig(params: {
  modelId?: string;
  blockId: number;
  runsPerScenario?: number;
  confidenceInterval?: number;
  simStart?: number;
  simEnd?: number;
  reportDetails?: boolean;
  saveScenarios?: boolean;
}) {
  return await sendCommand("scenario_manager_set_config", params);
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

export async function analysisManagerSetConfig(params: {
  modelId?: string;
  blockId: number;
  enableDbResponses?: boolean;
  enableBlockResponses?: boolean;
  enableReliabilityResponses?: boolean;
  enableDbFactors?: boolean;
  enableBlockFactors?: boolean;
  enableReliabilityFactors?: boolean;
  enableResultsTable?: boolean;
  autoExport?: boolean;
}) {
  return await sendCommand("analysis_manager_set_config", params);
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

