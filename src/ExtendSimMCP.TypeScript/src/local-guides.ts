/**
 * The user's own guide folder on disk: %APPDATA%\SimulationsMCP\guides\, one
 * <key>.json per guide. Reading never creates the folder and never throws - problems
 * become LocalGuideError entries so the official guides always work. Writes are atomic
 * (temp file + rename). Paths are only ever built from a validated key.
 * Spec: docs/superpowers/specs/2026-09-23-own-guides-design.md
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import {
  LOCAL_MAX_BYTES, LOCAL_MAX_ERRORS, LOCAL_MAX_FILES, parseLocalGuide, validateLocalKey, type LocalLoad,
} from "./local-guides-core.js";

export class GuideStoreError extends Error {
  constructor(readonly code: string, message: string) {
    super(message);
    this.name = "GuideStoreError";
  }
}

export function defaultLocalGuideDir(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.APPDATA || join(homedir(), "AppData", "Roaming");
  return join(base, "SimulationsMCP", "guides");
}

const errno = (e: unknown) => (e as NodeJS.ErrnoException).code ?? (e as Error).message;

export function loadLocalGuides(dir: string): LocalLoad {
  const out: LocalLoad = { guides: [], errors: [], errorTotal: 0 };
  let names: string[];
  try {
    names = readdirSync(dir);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return out;
    out.errors.push({ file: dir, error: `guide folder could not be read: ${errno(e)}` });
    return out;
  }
  let read = 0;
  for (const name of names.filter((n) => n.toLowerCase().endsWith(".json")).sort()) {
    const path = join(dir, name);
    let size: number;
    try {
      const st = statSync(path);
      if (!st.isFile()) continue;
      size = st.size;
    } catch (e) {
      out.errors.push({ file: name, error: `could not be read: ${errno(e)}` });
      continue;
    }
    if (!name.endsWith(".json")) {   // Foo.JSON: reported, not silently ignored
      out.errors.push({ file: name, error: "not a guide file name: use a lower-case .json extension" });
      continue;
    }
    const keyProblem = validateLocalKey(name.slice(0, -".json".length));
    if (keyProblem) {
      out.errors.push({ file: name, error: `not a guide file name: ${keyProblem}` });
      continue;
    }
    if (read >= LOCAL_MAX_FILES) {
      out.errors.push({ file: name, error: `skipped: only the first ${LOCAL_MAX_FILES} guide files are read` });
      continue;
    }
    read++;
    if (size > LOCAL_MAX_BYTES) {
      out.errors.push({ file: name, error: `larger than 100 KB (${size} bytes)` });
      continue;
    }
    let text: string;
    try {
      text = readFileSync(path, "utf-8").replace(/^\uFEFF/, "");   // Notepad writes a BOM
    } catch (e) {
      out.errors.push({ file: name, error: `could not be read: ${errno(e)}` });
      continue;
    }
    const r = parseLocalGuide(name, text);
    if ("error" in r) out.errors.push(r.error);
    else out.guides.push(r.guide);
  }
  out.errorTotal = out.errors.length;
  if (out.errors.length > LOCAL_MAX_ERRORS) {
    const more = out.errors.length - LOCAL_MAX_ERRORS;
    out.errors = [...out.errors.slice(0, LOCAL_MAX_ERRORS), { file: "…", error: `${more} more guide files could not be loaded` }];
  }
  return out;
}

function pathFor(dir: string, key: string): string {
  const problem = validateLocalKey(key);
  if (problem) throw new GuideStoreError("GUIDE_INVALID_KEY", problem);
  return join(dir, `${key}.json`);
}

export function saveLocalGuide(dir: string, key: string, text: string, overwrite: boolean): { path: string; overwritten: boolean } {
  const path = pathFor(dir, key);
  const exists = existsSync(path);
  if (exists && !overwrite) {
    throw new GuideStoreError("GUIDE_EXISTS", `You already have a guide named '${key}' (${path}). Pass overwrite: true to replace it.`);
  }
  const tmp = `${path}.${process.pid}.tmp`;   // ends in .tmp, so the loader ignores it
  try {
    mkdirSync(dir, { recursive: true });
    writeFileSync(tmp, text, "utf-8");
    renameSync(tmp, path);
  } catch (e) {
    try { rmSync(tmp, { force: true }); } catch { /* ignore */ }
    throw new GuideStoreError("GUIDE_WRITE_FAILED", `Could not write ${path}: ${errno(e)}. Nothing was saved.`);
  }
  return { path, overwritten: exists };
}

export function deleteLocalGuide(dir: string, key: string): { path: string } {
  const path = pathFor(dir, key);
  if (!existsSync(path)) {
    throw new GuideStoreError("GUIDE_NOT_FOUND",
      `You have no guide saved as '${key}'. Use the key it was saved under - a guide shown as <key>_local is saved as <key>.`);
  }
  try {
    rmSync(path);
  } catch (e) {
    throw new GuideStoreError("GUIDE_WRITE_FAILED", `Could not delete ${path}: ${errno(e)}.`);
  }
  return { path };
}
