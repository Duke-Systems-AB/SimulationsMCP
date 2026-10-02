/**
 * The user's own guide folder on disk: %APPDATA%\SimulationsMCP\guides\, one
 * <key>.json per guide. Reading never creates the folder and never throws - problems
 * become LocalGuideError entries so the official guides always work. Writes are atomic
 * (temp file + rename). Paths are only ever built from a validated key.
 * Spec: docs/superpowers/specs/2026-09-23-own-guides-design.md
 */
import { createHash } from "crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";
import {
  LOCAL_MAX_BYTES, LOCAL_MAX_ERRORS, LOCAL_MAX_FILES, formatIssues, parseLocalGuide, validateLocalKey,
  type LocalGuideError, type LocalLoad,
} from "./local-guides-core.js";
import { BlockGuideSchema, blockGuideKey, type BlockGuide } from "./guide-schema.js";

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

// ---------------------------------------------------------------------------
// Local block guides: one file per library/block in <dir>/blocks/, keyed by
// blockGuideFileKey. They never claim proof.
// ---------------------------------------------------------------------------

const BLOCKS = "blocks";

const KEY_MAX = 64;

/**
 * The file key of a local block guide: a lower-case a-z0-9_ slug of library and block.
 * When the slug cannot tell two names apart - a non-ASCII character was dropped, a name
 * slugs to nothing, or the key is cut at 64 characters - "_" and the first 6 hex digits of
 * sha1(library/block, lower-cased) are appended, so two different blocks never share a file.
 */
export function blockGuideFileKey(library: string, block: string): string {
  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const lib = slug(library), blk = slug(block);
  const full = `${lib}__${blk}`;
  const nonAscii = [...library + block].some((c) => c.charCodeAt(0) > 0x7f);
  const lossy = nonAscii || !lib || !blk || full.length > KEY_MAX;
  if (!lossy) return full;
  const hash = createHash("sha1").update(`${library}/${block}`.toLowerCase(), "utf8").digest("hex").slice(0, 6);
  const head = full.slice(0, KEY_MAX - hash.length - 1).replace(/_+$/, "");
  return `${head}_${hash}`;
}

export function validateLocalBlockGuide(g: unknown): { guide: BlockGuide } | { error: string } {
  const r = BlockGuideSchema.safeParse(g);
  if (!r.success) return { error: formatIssues(r.error.issues).join("; ") };
  if (r.data.provedOn.length > 0 || r.data.recipes.length > 0 || r.data.settings.some((s) => s.proved)) {
    return { error: "a local block guide cannot claim proof: provedOn and recipes must be empty and no setting "
      + "may be proved (only runs prove)" };
  }
  return { guide: r.data };
}

export function saveLocalBlockGuide(
  dir: string, guide: unknown, overwrite: boolean,
): { path: string; overwritten: boolean; key: string } {
  const v = validateLocalBlockGuide(guide);
  if ("error" in v) throw new GuideStoreError("GUIDE_INVALID", v.error);
  const key = blockGuideFileKey(v.guide.library, v.guide.block);
  const saved = saveLocalGuide(join(dir, BLOCKS), key, JSON.stringify(v.guide, null, 2) + "\n", overwrite);
  return { ...saved, key };
}

export function deleteLocalBlockGuide(dir: string, library: string, block: string): { path: string } {
  return deleteLocalGuide(join(dir, BLOCKS), blockGuideFileKey(library, block));
}

export function loadLocalBlockGuides(dir: string): { guides: Record<string, BlockGuide>; errors: LocalGuideError[] } {
  const out = { guides: {} as Record<string, BlockGuide>, errors: [] as LocalGuideError[] };
  const folder = join(dir, BLOCKS);
  let names: string[];
  try {
    names = readdirSync(folder);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return out;
    out.errors.push({ file: folder, error: `block guide folder could not be read: ${errno(e)}` });
    return out;
  }
  for (const name of names.filter((n) => n.endsWith(".json")).sort().slice(0, LOCAL_MAX_FILES)) {
    try {
      if (statSync(join(folder, name)).size > LOCAL_MAX_BYTES) {
        out.errors.push({ file: name, error: "larger than 100 KB" });
        continue;
      }
      const v = validateLocalBlockGuide(JSON.parse(readFileSync(join(folder, name), "utf-8").replace(/^\uFEFF/, "")));
      if ("error" in v) { out.errors.push({ file: name, error: v.error }); continue; }
      if (`${blockGuideFileKey(v.guide.library, v.guide.block)}.json` !== name) {
        out.errors.push({ file: name, error: `library/block (${v.guide.library}/${v.guide.block}) do not match the file name` });
        continue;
      }
      out.guides[blockGuideKey(v.guide.library, v.guide.block)] = v.guide;
    } catch (e) {
      out.errors.push({ file: name, error: `could not be read: ${errno(e)}` });
    }
  }
  out.errors = out.errors.slice(0, LOCAL_MAX_ERRORS);
  return out;
}
