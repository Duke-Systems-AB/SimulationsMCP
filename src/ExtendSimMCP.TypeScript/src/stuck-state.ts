/**
 * What the server says while ExtendSim is stuck: a command's outcome was settled (timeout,
 * or a dialog and the grace window lapsed) but Python has not answered it yet. Pure.
 * Spec: docs/superpowers/specs/2026-09-24-extendsim-stuck-recovery-design.md §5.1
 */
import { dialogLine, type ProbeCheck } from "./watchdog-core.js";

export interface StuckInfo { command: string; since: number; lastCheck: ProbeCheck | null }

const DO_NOT_CHANGE = " Do not change the model until extendsim_status reports state 'idle'.";

export function busySuggestion(check: ProbeCheck | null): string {
  if (check && check.dialogs.length > 0) {
    return `Click OK in ExtendSim's dialog: ${dialogLine(check.dialogs[0])}.` + DO_NOT_CHANGE;
  }
  if (check && check.windowFound && check.windowResponding === false) {
    return "ExtendSim is not responding - usually it is just busy with a long call. Wait; if it stays like this for many minutes, restart ExtendSim by hand; the server should reconnect by itself." + DO_NOT_CHANGE;
  }
  if (check && !check.windowFound) {
    return "ExtendSim's window was not found - it may have been closed. Start ExtendSim again; the server should reconnect by itself." + DO_NOT_CHANGE;
  }
  return "A long ExtendSim call is still running. Wait and check extendsim_status." + DO_NOT_CHANGE;
}

export function describeStuck(info: StuckInfo, now: number) {
  const c = info.lastCheck;
  return {
    command: info.command,
    sinceMs: now - info.since,
    lastCheck: c
      ? {
          dialogs: c.dialogs.map((d) => ({ text: dialogLine(d) })),
          windowFound: c.windowFound,
          windowResponding: c.windowResponding,
          at: new Date(c.at).toISOString(),
        }
      : null,
  };
}

export function busyResult(info: StuckInfo, now: number) {
  return {
    success: false,
    errorCode: "EXTENDSIM_BUSY",
    error: `ExtendSim has not finished '${info.command}' (${Math.round((now - info.since) / 1000)} s). ` +
      "No command is sent to it until it answers.",
    stuck: describeStuck(info, now),
    suggestion: busySuggestion(info.lastCheck),
  };
}

/** extendsim_status while stuck - answered by TypeScript, Python is busy. */
export function stuckStatus(info: StuckInfo, now: number) {
  return { success: true, state: "stuck", stuck: describeStuck(info, now), suggestion: busySuggestion(info.lastCheck) };
}
