/**
 * Pure rules for the watchdog that looks at ExtendSim while a command runs (spec
 * 2026-09-24-extendsim-stuck-recovery-design.md, S4-S7). No I/O: backend.ts owns the
 * timers, probe-client.ts talks to the probe process.
 */
export interface ProbeDialog { title: string; texts: string[]; buttons: string[] }
export interface ProbeCheck {
  dialogs: ProbeDialog[];
  windowFound: boolean;
  windowResponding: boolean | null;   // null when the window was not found
  at: number;                         // epoch ms when the check finished
  durationMs: number;
}

export const WATCHDOG_INTERVAL_MS = 10_000;
export const WATCHDOG_MAX_INTERVAL_MS = 30_000;
export const SLOW_CHECK_MS = 1_000;

/** Commands whose dialogs can belong to the run itself: report, never dismiss (S7). */
export const REPORT_ONLY_COMMANDS: ReadonlySet<string> = new Set([
  "scenario_manager_run", "scenario_manager_status", "optimizer_run",
]);

/** 10 s after a fast check; doubled (max 30 s) after a slow check or a failed one (null). */
export function nextInterval(current: number, checkDurationMs: number | null): number {
  if (checkDurationMs === null || checkDurationMs > SLOW_CHECK_MS) {
    return Math.min(current * 2, WATCHDOG_MAX_INTERVAL_MS);
  }
  return WATCHDOG_INTERVAL_MS;
}

/** Only a dialog is acted on; an unresponsive window alone never is (S6). */
export function shouldDismiss(command: string, check: ProbeCheck): boolean {
  return !REPORT_ONLY_COMMANDS.has(command) && check.dialogs.length > 0;
}

export function dialogLine(d: ProbeDialog): string {
  return d.texts.join(" ").trim() || d.title;
}
