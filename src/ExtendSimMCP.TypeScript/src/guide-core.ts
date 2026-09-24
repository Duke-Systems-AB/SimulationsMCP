/**
 * Pure decisions for the web guide lookup: is it on, is a check due, is a file valid,
 * which file wins, which scenarios this server may show. No I/O and no network here -
 * guide-source.ts injects all of that - so every rule is testable offline.
 * Spec: docs/superpowers/specs/2026-09-23-web-guide-lookup-design.md
 */
import { GuideFileSchema, LIMITS, SCHEMA_VERSION, type GuideFile } from "./guide-schema.js";
import type { LocalGuideError, LocalGuideRename } from "./local-guides-core.js";

export const GUIDE_URL = "https://duke.se/simulationsmcp/v1/modeling_guides.json";
export const CHECK_INTERVAL_MS = 30 * 24 * 60 * 60 * 1000;  // D7: at most once a month
export const FAILURE_BACKOFF_MS = 24 * 60 * 60 * 1000;      // D8: retry a day after a failure
export const FETCH_TIMEOUT_MS = 3000;
export const ENV_SWITCH = "SIMULATIONSMCP_WEB_LOOKUP";

export type Setting = "on" | "off";
export type PolicyRead = { kind: "absent" } | { kind: "present"; text: string } | { kind: "unreadable" };

/** Any "off" wins. A policy file that is not clearly `{"webLookup": true}` means off:
 *  whoever created it meant to switch the lookup off. */
export function effectiveSetting(env: Record<string, string | undefined>, policy: PolicyRead): Setting {
  if (policy.kind === "unreadable") return "off";
  if (policy.kind === "present") {
    try {
      const p = JSON.parse(policy.text) as unknown;
      if (!p || typeof p !== "object" || (p as Record<string, unknown>).webLookup !== true) return "off";
    } catch {
      return "off";
    }
  }
  const v = (env[ENV_SWITCH] ?? "").trim().toLowerCase();
  return v === "off" || v === "0" || v === "false" ? "off" : "on";
}

export interface FetchState { lastCheck?: number; lastFailure?: number; etag?: string }

/** Anything unreadable is "never checked" - a corrupt state file must not stop checks. */
export function parseFetchState(text: string | null): FetchState {
  if (!text) return {};
  try {
    const o = JSON.parse(text) as Record<string, unknown>;
    const s: FetchState = {};
    if (typeof o.lastCheck === "number" && Number.isFinite(o.lastCheck)) s.lastCheck = o.lastCheck;
    if (typeof o.lastFailure === "number" && Number.isFinite(o.lastFailure)) s.lastFailure = o.lastFailure;
    if (typeof o.etag === "string" && o.etag.length <= 256) s.etag = o.etag;
    return s;
  } catch {
    return {};
  }
}

/** A timestamp in the future (clock set back) counts as absent, never as "recent". */
export function shouldFetch(now: number, s: FetchState): boolean {
  const within = (t: number | undefined, windowMs: number) =>
    t !== undefined && t <= now && now - t < windowMs;
  if (within(s.lastFailure, FAILURE_BACKOFF_MS)) return false;
  return !within(s.lastCheck, CHECK_INTERVAL_MS);
}

export type Validated =
  | { ok: true; file: GuideFile }
  | { ok: false; reason: "too-large" | "not-json" | "wrong-schema-version" | "schema" };

export function validateGuideText(text: string): Validated {
  if (Buffer.byteLength(text, "utf8") > LIMITS.bytes) return { ok: false, reason: "too-large" };
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    return { ok: false, reason: "not-json" };
  }
  if ((json as Record<string, unknown> | null)?.schemaVersion !== SCHEMA_VERSION) {
    return { ok: false, reason: "wrong-schema-version" };
  }
  const r = GuideFileSchema.safeParse(json);
  return r.success ? { ok: true, file: r.data } : { ok: false, reason: "schema" };
}

export function compareSemver(a: string, b: string): number {
  const pa = a.split(".").map(Number), pb = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d;
  }
  return 0;
}

/** The newer valid file wins; a cached file never wins over an equal or newer bundled
 *  one, so a server upgrade's bundled guides take over from an older cache. */
export function choose(
  bundled: GuideFile | null, cached: GuideFile | null,
): { file: GuideFile; source: "bundled" | "web" } | null {
  if (cached && (!bundled || compareSemver(cached.version, bundled.version) > 0)) {
    return { file: cached, source: "web" };
  }
  return bundled ? { file: bundled, source: "bundled" } : null;
}

/** FR-V1: hide scenarios whose `since` is above this server's version, and remove every
 *  reference to them so the AI is never pointed at a guide it cannot open. */
export function filterBySince(file: GuideFile, serverVersion: string): { file: GuideFile; hiddenCount: number } {
  const out = structuredClone(file);
  const hidden = new Set(
    Object.entries(out.scenarios)
      .filter(([, s]) => s.since !== undefined && compareSemver(s.since, serverVersion) > 0)
      .map(([k]) => k),
  );
  for (const k of hidden) delete out.scenarios[k];
  for (const c of Object.values(out.categories)) c.scenarios = c.scenarios.filter((k) => !hidden.has(k));
  for (const s of Object.values(out.scenarios)) {
    for (const v of s.variations) if (v.scenario && hidden.has(v.scenario)) v.scenario = null;
  }
  return { file: out, hiddenCount: hidden.size };
}

export interface GuideView {
  file: GuideFile | null;
  source: "bundled" | "web";
  version: string | null;
  hiddenCount: number;
  /** The user's own guides merged in by combineGuides (own guides spec). */
  local?: { count: number; errors: LocalGuideError[]; errorTotal: number; renames: LocalGuideRename[] };
}

/** What modeling_guide and MCP_init tell the AI about the guide set (FR-N6). */
export function guideMeta(view: GuideView): Record<string, unknown> {
  const m: Record<string, unknown> = { guideSource: view.source, guideVersion: view.version };
  if (view.hiddenCount > 0) {
    m.newerGuides = `${view.hiddenCount} newer guide(s) need a newer SimulationsMCP server; upgrade to see them.`;
  }
  const local = view.local;
  if (local && local.count > 0) m.localGuides = local.count;
  if (local && local.errors.length > 0) {
    m.localGuideErrors = local.errors;
    m.localGuideNote = `${local.errorTotal} problem(s) with your own guide files; see ` +
      "localGuideErrors and tell the user which file and why.";
  }
  if (local && local.renames.length > 0) m.localGuideRenames = local.renames;
  return m;
}
