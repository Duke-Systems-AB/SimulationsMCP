/**
 * Talks to extendsim_probe.py - a long-lived Python process with NO ExtendSim COM that
 * looks at ExtendSim's windows while the backend is blocked in a COM call. Every failure
 * here is quiet: a check that cannot be made returns null and the watchdog carries on
 * without it (spec §5.3, §6). Killing this process is safe - it holds no COM connection.
 */
import { spawn as nodeSpawn, type ChildProcess } from "child_process";
import * as readline from "readline";
import type { ProbeCheck, ProbeDialog } from "./watchdog-core.js";

export interface ProbeDismissed { title?: string; texts?: string[]; dismissed?: boolean; method?: string }
export type SpawnFn = (command: string, args: string[]) => ChildProcess;

export const PROBE_REQUEST_TIMEOUT_MS = 5_000;
export const PROBE_MAX_FAILED_CHECKS = 3;
export const PROBE_MAX_DEATHS = 2;
export const PROBE_RESTARTS_PER_DEATH = 3;

type Answer = { id?: number; ok?: boolean; [k: string]: unknown } | null;

export class ProbeClient {
  disabled = false;
  private proc: ChildProcess | null = null;
  private rl: readline.Interface | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, (a: Answer) => void>();
  private failedInRow = 0;
  private deaths = 0;
  private forcedRestarts = 0;

  constructor(
    private readonly script: string,
    private readonly spawnFn: SpawnFn = (c, a) => nodeSpawn(c, a, { stdio: ["pipe", "pipe", "pipe"] }),
    private readonly now: () => number = Date.now,
  ) {}

  async check(): Promise<ProbeCheck | null> {
    const t0 = this.now();
    const a = await this.request("check");
    if (!a) {
      this.noteFailedCheck();
      return null;
    }
    this.failedInRow = 0;
    const t1 = this.now();
    return {
      dialogs: (a.dialogs as ProbeDialog[] | undefined) ?? [],
      windowFound: a.windowFound === true,
      windowResponding: typeof a.windowResponding === "boolean" ? a.windowResponding : null,
      at: t1,
      durationMs: t1 - t0,
    };
  }

  async dismiss(): Promise<ProbeDismissed[] | null> {
    const a = await this.request("dismiss");
    return a ? ((a.dismissed as ProbeDismissed[] | undefined) ?? []) : null;
  }

  stop(): void {
    this.disabled = true;
    const p = this.proc;
    this.proc = null;
    this.closeReadline();
    this.failAllPending();
    if (p) { try { p.kill(); } catch { /* already gone */ } }
  }

  private ensureStarted(): boolean {
    if (this.disabled) return false;
    if (this.proc) return true;
    let p: ChildProcess;
    try {
      p = this.spawnFn("python", ["-u", this.script]);
    } catch {
      this.noteDeath();
      return false;
    }
    if (!p) {                                    // e.g. a mocked spawn in unit tests
      this.noteDeath();
      return false;
    }
    this.proc = p;
    if (p.stdout) this.rl = readline.createInterface({ input: p.stdout, crlfDelay: Infinity }).on("line", (l) => this.onLine(l));
    // A write after the probe has already died fails asynchronously with EPIPE; with no
    // listener Node treats it as an uncaught error and crashes the whole MCP server. The
    // exit handler (below) is what actually reports the death - this listener just stops
    // the throw.
    p.stdin?.on("error", () => { /* the exit handler reports the death */ });
    p.stderr?.on("data", (data) => console.error(`Probe: ${String(data).trim()}`));
    p.on("exit", () => this.onExit(p));
    p.on("error", () => this.onExit(p));
    return true;
  }

  private closeReadline(): void {
    if (this.rl) { this.rl.close(); this.rl = null; }
  }

  private request(op: string): Promise<Answer> {
    if (!this.ensureStarted() || !this.proc?.stdin) return Promise.resolve(null);
    const id = this.nextId++;
    return new Promise((resolve) => {
      const timer = setTimeout(() => { this.pending.delete(id); resolve(null); }, PROBE_REQUEST_TIMEOUT_MS);
      this.pending.set(id, (a) => { clearTimeout(timer); resolve(a && a.ok === true ? a : null); });
      try {
        this.proc!.stdin!.write(JSON.stringify({ id, op }) + "\n");
      } catch {
        this.pending.delete(id);
        clearTimeout(timer);
        resolve(null);
      }
    });
  }

  private onLine(line: string): void {
    let a: Answer;
    try { a = JSON.parse(line) as Answer; } catch { return; }
    if (!a || typeof a.id !== "number") return;
    const cb = this.pending.get(a.id);
    if (!cb) return;
    this.pending.delete(a.id);
    cb(a);
  }

  private onExit(p: ChildProcess): void {
    if (p !== this.proc) return;                 // an old process, or stop() already ran
    this.proc = null;
    this.closeReadline();
    this.failAllPending();
    this.noteDeath();
  }

  private failAllPending(): void {
    for (const cb of this.pending.values()) cb(null);
    this.pending.clear();
  }

  private noteDeath(): void {
    this.deaths++;
    if (this.deaths >= PROBE_MAX_DEATHS) this.disabled = true;
  }

  private noteFailedCheck(): void {
    this.failedInRow++;
    if (this.failedInRow < PROBE_MAX_FAILED_CHECKS || !this.proc) return;
    this.failedInRow = 0;
    const p = this.proc;
    this.proc = null;                            // the next request starts a fresh probe
    this.closeReadline();
    this.failAllPending();
    try { p.kill(); } catch { /* already gone */ }
    // A probe that keeps hanging (e.g. on a frozen ExtendSim) would otherwise be respawned
    // forever. Every PROBE_RESTARTS_PER_DEATH-th forced restart counts as one death, so the
    // probe is eventually disabled like one that keeps crashing.
    this.forcedRestarts++;
    if (this.forcedRestarts >= PROBE_RESTARTS_PER_DEATH) {
      this.forcedRestarts = 0;
      this.noteDeath();
    }
  }
}

export type ProbeLike = Readonly<Pick<ProbeClient, "check" | "dismiss" | "stop" | "disabled">>;
