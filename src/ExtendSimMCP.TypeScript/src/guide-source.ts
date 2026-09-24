/**
 * Web guide lookup - the ONLY module allowed to make network calls
 * (tests/unit/network-guard.test.ts enforces that).
 *
 * GuideSource decides nothing itself: guide-core.ts holds the rules. It loads the
 * bundled and cached files, asks the core whether a check is due, runs at most one
 * fetch at a time through an injected Fetcher, and stores results through an injected
 * GuideStore. A network or disk problem never reaches the caller - it only shows up in
 * the telemetry event.
 */
import { mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  choose, compareSemver, effectiveSetting, filterBySince, FETCH_TIMEOUT_MS, GUIDE_URL, parseFetchState,
  shouldFetch, validateGuideText,
  type FetchState, type GuideView, type PolicyRead, type Setting,
} from "./guide-core.js";
import { LIMITS, type GuideFile } from "./guide-schema.js";

export interface GuideStore {
  readCache(): string | null;
  writeCache(text: string): void;
  deleteCache(): void;
  readState(): string | null;
  writeState(text: string): void;
}

export type FetchResult =
  | { kind: "ok"; body: string; etag?: string }
  | { kind: "not-modified" }
  | { kind: "error"; outcome: string };

export type Fetcher = (url: string, etag: string | undefined) => Promise<FetchResult>;

export interface FetchEvent { outcome: string; durMs: number; version?: string }

export interface GuideSourceDeps {
  url: string;
  serverVersion: string;
  bundledText: string | null;
  setting: Setting;
  fetcher: Fetcher;
  store: GuideStore;
  now: () => number;
  report: (e: FetchEvent) => void;
}

export class GuideSource {
  private loaded = false;
  private bundled: GuideFile | null = null;
  private cached: GuideFile | null = null;
  private state: FetchState = {};
  private inFlight: Promise<void> | null = null;
  private reportedOff = false;

  constructor(private readonly deps: GuideSourceDeps) {}

  async getGuides(): Promise<GuideView> {
    this.load();
    if (this.deps.setting === "off") {
      if (!this.reportedOff) {
        this.reportedOff = true;
        this.safe(() => this.deps.report({ outcome: "off", durMs: 0 }), undefined);
      }
    } else if (shouldFetch(this.deps.now(), this.state)) {
      this.inFlight ??= this.fetchOnce().finally(() => { this.inFlight = null; });
      await this.inFlight;
    }
    return this.view();
  }

  /** The current view without triggering a fetch (guide_save uses it to tell the AI
   *  under which key a newly saved guide will be shown). */
  peek(): GuideView {
    this.load();
    return this.view();
  }

  private load(): void {
    if (this.loaded) return;
    this.loaded = true;
    if (this.deps.bundledText !== null) {
      const b = validateGuideText(this.deps.bundledText);
      if (b.ok) this.bundled = b.file;
    }
    const cachedText = this.safe(() => this.deps.store.readCache(), null);
    if (cachedText !== null) {
      const c = validateGuideText(cachedText);
      if (c.ok) this.cached = c.file;
      else this.safe(() => this.deps.store.deleteCache(), undefined);
    }
    this.state = parseFetchState(this.safe(() => this.deps.store.readState(), null));
  }

  private async fetchOnce(): Promise<void> {
    const start = this.deps.now();
    let outcome: string;
    let version: string | undefined;
    // Send the ETag only for a file we actually hold; otherwise a 304 would strand us
    // on the bundled file for a month.
    const etag = this.cached ? this.state.etag : undefined;
    try {
      const r = await this.deps.fetcher(this.deps.url, etag);
      if (r.kind === "not-modified") {
        outcome = "not-modified";
        this.succeeded(start, this.state.etag);
      } else if (r.kind === "error") {
        outcome = r.outcome;
        this.state.lastFailure = start;
      } else {
        const v = validateGuideText(r.body);
        if (!v.ok) {
          outcome = v.reason === "too-large" ? "too-large" : "invalid";
          this.state.lastFailure = start;
        } else {
          version = v.file.version;
          const current = choose(this.bundled, this.cached);
          if (!current || compareSemver(v.file.version, current.file.version) > 0) {
            this.safe(() => this.deps.store.writeCache(r.body), undefined);
            this.cached = v.file;          // used this session even if the write failed
            outcome = "ok";
          } else {
            outcome = "not-newer";
          }
          this.succeeded(start, r.etag);
        }
      }
    } catch {
      outcome = "network-error";
      this.state.lastFailure = start;
    }
    this.safe(() => this.deps.store.writeState(JSON.stringify(this.state)), undefined);
    this.safe(() => this.deps.report({ outcome, durMs: this.deps.now() - start, version }), undefined);
  }

  private succeeded(at: number, etag: string | undefined): void {
    this.state = { lastCheck: at, ...(etag ? { etag } : {}) };
  }

  private view(): GuideView {
    // Off means bundled guides only (decided 2026-09-23): an admin who switches the
    // lookup off for content trust must not keep getting a copy fetched earlier.
    const c = choose(this.bundled, this.deps.setting === "off" ? null : this.cached);
    if (!c) return { file: null, source: "bundled", version: null, hiddenCount: 0 };
    const f = filterBySince(c.file, this.deps.serverVersion);
    return { file: f.file, source: c.source, version: c.file.version, hiddenCount: f.hiddenCount };
  }

  private safe<T>(fn: () => T, fallback: T): T {
    try { return fn(); } catch { return fallback; }
  }
}

// ---------------------------------------------------------------------------
// Real adapters. Production passes only GUIDE_URL; the URL parameter exists so the
// fetcher can be tested against a local server.
// ---------------------------------------------------------------------------

function isRedirect(e: unknown): boolean {
  const msg = String((e as { cause?: { message?: string } })?.cause?.message ?? (e as Error)?.message ?? "");
  return /redirect/i.test(msg);
}

/** GET only, no query, no identifying headers, no redirects, a hard size cap enforced
 *  while streaming, and one timeout covering connect + headers + body. */
export function httpsFetcher(timeoutMs = FETCH_TIMEOUT_MS, maxBytes = LIMITS.bytes): Fetcher {
  return async (url, etag) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const headers: Record<string, string> = { Accept: "application/json" };
      if (etag) headers["If-None-Match"] = etag;
      let res: Response;
      try {
        res = await fetch(url, { method: "GET", headers, redirect: "error", signal: ctrl.signal });
      } catch (e) {
        if (ctrl.signal.aborted) return { kind: "error", outcome: "timeout" };
        return { kind: "error", outcome: isRedirect(e) ? "redirect" : "network-error" };
      }
      if (res.status === 304) return { kind: "not-modified" };
      if (res.status !== 200) return { kind: "error", outcome: `http-${res.status}` };
      if (Number(res.headers.get("content-length") ?? 0) > maxBytes) {
        ctrl.abort();
        return { kind: "error", outcome: "too-large" };
      }
      const chunks: Uint8Array[] = [];
      let total = 0;
      const reader = res.body?.getReader();
      if (reader) {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          total += value.byteLength;
          if (total > maxBytes) {
            ctrl.abort();
            return { kind: "error", outcome: "too-large" };
          }
          chunks.push(value);
        }
      }
      return { kind: "ok", body: Buffer.concat(chunks).toString("utf8"), etag: res.headers.get("etag") ?? undefined };
    } catch {
      return { kind: "error", outcome: ctrl.signal.aborted ? "timeout" : "network-error" };
    } finally {
      clearTimeout(timer);
      // Every return path above has already decided the outcome; abort now closes the
      // underlying connection instead of leaving it open. Harmless once the body has
      // already been fully read (200) - it is the 304/non-200 paths, which never touch
      // the body, that this actually protects: without it a hostile endless body holds
      // the socket open with nothing left to time it out.
      ctrl.abort();
    }
  };
}

/** Per-user cache directory; writes are atomic (temp file + rename). */
export function fileStore(dir: string): GuideStore {
  const cache = join(dir, "modeling_guides.json");
  const state = join(dir, "fetch-state.json");
  const read = (p: string): string | null => {
    try { return readFileSync(p, "utf8"); } catch { return null; }
  };
  const write = (p: string, text: string): void => {
    mkdirSync(dir, { recursive: true });
    // Per-process temp name: two processes (e.g. two MCP sessions) writing at the same
    // moment must never share a temp path.
    const tmp = `${p}.${process.pid}.tmp`;
    try {
      writeFileSync(tmp, text, "utf8");
      renameSync(tmp, p);
    } catch (e) {
      try { rmSync(tmp, { force: true }); } catch { /* ignore */ }
      throw e;
    }
  };
  return {
    readCache: () => read(cache),
    writeCache: (t) => write(cache, t),
    deleteCache: () => { try { rmSync(cache, { force: true }); } catch { /* ignore */ } },
    readState: () => read(state),
    writeState: (t) => write(state, t),
  };
}

export function defaultCacheDir(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.LOCALAPPDATA || join(homedir(), "AppData", "Local");
  return join(base, "SimulationsMCP", "guides");
}

/** {app}\policy.json - the install directory is admin-writable only, so this is the
 *  switch an admin can lock. Anything other than a readable file counts as unreadable.
 *
 *  Deliberately does not use existsSync: it returns false on ANY stat error, not just
 *  "not found" - a permission error, a traversal error, or a race would then read as
 *  "absent", which means the lookup is ON. That would let a broken-but-present policy
 *  file silently fail open. Only ENOENT/ENOTDIR mean "there really is no file here". */
export function readPolicy(path: string): PolicyRead {
  let st;
  try {
    st = statSync(path);
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    return code === "ENOENT" || code === "ENOTDIR" ? { kind: "absent" } : { kind: "unreadable" };
  }
  if (!st.isFile()) return { kind: "unreadable" };
  try {
    return { kind: "present", text: readFileSync(path, "utf8") };
  } catch {
    return { kind: "unreadable" };
  }
}

/** Production wiring. distDir is the directory holding index.js and the bundled guides. */
export function createGuideSource(opts: {
  distDir: string;
  serverVersion: string;
  report: (e: FetchEvent) => void;
}): GuideSource {
  let bundledText: string | null = null;
  try { bundledText = readFileSync(join(opts.distDir, "modeling_guides.json"), "utf8"); } catch { /* none */ }
  return new GuideSource({
    url: GUIDE_URL,
    serverVersion: opts.serverVersion,
    bundledText,
    setting: effectiveSetting(process.env, readPolicy(join(opts.distDir, "..", "policy.json"))),
    fetcher: httpsFetcher(),
    store: fileStore(defaultCacheDir()),
    now: Date.now,
    report: opts.report,
  });
}
