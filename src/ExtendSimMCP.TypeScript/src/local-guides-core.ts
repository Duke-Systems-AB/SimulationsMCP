/**
 * The user's own modelling guides - pure rules, no I/O (local-guides.ts does the disk).
 * One file per guide in the user's folder; the file name is the scenario key; every file
 * passes the same ScenarioSchema as the official guides. Local guides are merged into the
 * official set, never replace an official guide, and are marked source "local".
 * Spec: docs/superpowers/specs/2026-09-23-own-guides-design.md
 */
import { ScenarioSchema, type GuideFile, type Scenario } from "./guide-schema.js";
import { guideMeta, type GuideView } from "./guide-core.js";

export const LOCAL_MAX_BYTES = 100 * 1024;
export const LOCAL_MAX_FILES = 500;
export const LOCAL_MAX_ERRORS = 20;
export const DRAFT_MAX_BLOCKS = 50;
const PARSE_MAX_ISSUES = 5;   // per file in localGuideErrors; guide_save lists them all

const KEY_RE = /^[a-z0-9_]{1,64}$/;
// Windows device names cannot be file names; the JavaScript ones would be object keys
// with special meaning. Neither can ever be a guide key.
const RESERVED = new Set([
  "con", "prn", "aux", "nul",
  ...Array.from({ length: 9 }, (_, i) => `com${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `lpt${i + 1}`),
  "__proto__", "constructor", "prototype",
]);

export interface LocalGuide { key: string; file: string; scenario: Scenario }
export interface LocalGuideError { file: string; error: string }
export interface LocalGuideRename { file: string; shownAs: string }
/** errorTotal counts every problem found, before errors is capped at LOCAL_MAX_ERRORS. */
export interface LocalLoad { guides: LocalGuide[]; errors: LocalGuideError[]; errorTotal: number }

/** source and savedAs are output only: prepareForSave's schema parse strips them. */
type MergedScenario = Scenario & { source?: "local"; savedAs?: string };
export interface MergedGuides {
  [field: string]: unknown;
  categories: Record<string, { label: string; scenarios: string[]; local?: true }>;
  scenarios: Record<string, MergedScenario>;
}

/** null when the key is usable as a guide key and file name, else the reason. */
export function validateLocalKey(key: string): string | null {
  if (!KEY_RE.test(key)) return "A guide key is 1-64 characters of a-z, 0-9 and _.";
  if (RESERVED.has(key)) return `'${key}' is a reserved name and cannot be a guide key.`;
  return null;
}

/** zod issues as "pattern.blocks[3].library: message" - every issue, not just the first. */
export function formatIssues(
  issues: ReadonlyArray<{ path: ReadonlyArray<PropertyKey>; message: string }>,
): string[] {
  return issues.map((i) => {
    const path = i.path.reduce<string>(
      (s, k) => (typeof k === "number" ? `${s}[${k}]` : s ? `${s}.${String(k)}` : String(k)), "");
    return `${path || "(root)"}: ${i.message}`;
  });
}

export function parseLocalGuide(file: string, text: string): { guide: LocalGuide } | { error: LocalGuideError } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    return { error: { file, error: `not valid JSON: ${(e as Error).message}` } };
  }
  const r = ScenarioSchema.safeParse(raw);
  if (!r.success) {
    const issues = formatIssues(r.error.issues);
    const shown = issues.slice(0, PARSE_MAX_ISSUES);
    if (issues.length > PARSE_MAX_ISSUES) shown.push(`and ${issues.length - PARSE_MAX_ISSUES} more`);
    return { error: { file, error: shown.join("; ") } };
  }
  const categoryProblem = validateLocalKey(r.data.category);
  if (categoryProblem) return { error: { file, error: `category: ${categoryProblem}` } };
  return { guide: { key: file.replace(/\.json$/, ""), file, scenario: r.data } };
}

const has = (o: object, k: string) => Object.prototype.hasOwnProperty.call(o, k);

/** Official set first; each local guide is added under its key, or `<key>_local`,
 *  `<key>_local_2`, ... when that key is taken. Nothing official is ever replaced.
 *  Every local guide whose own key is free is placed before any clash is renamed, so a
 *  rename never takes the key of another own guide. */
export function mergeLocalGuides(
  official: GuideFile | null, locals: LocalGuide[],
): { guides: MergedGuides; renames: LocalGuideRename[] } {
  const guides: MergedGuides = official
    ? (structuredClone(official) as unknown as MergedGuides)
    : { description: "Only your own guides - the official guides could not be loaded.", categories: {}, scenarios: {} };
  const renames: LocalGuideRename[] = [];
  const place = (g: LocalGuide, shownAs: string) => {
    guides.scenarios[shownAs] = { ...structuredClone(g.scenario), source: "local", savedAs: g.key };
    const c = g.scenario.category;
    if (!has(guides.categories, c)) guides.categories[c] = { label: c, scenarios: [], local: true };
    guides.categories[c].scenarios.push(shownAs);
  };
  const sorted = [...locals].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const clashing = sorted.filter((g) => has(guides.scenarios, g.key));
  for (const g of sorted) if (!clashing.includes(g)) place(g, g.key);
  for (const g of clashing) {
    let shownAs = `${g.key}_local`;
    for (let n = 2; has(guides.scenarios, shownAs); n++) shownAs = `${g.key}_local_${n}`;
    renames.push({ file: g.file, shownAs });
    place(g, shownAs);
  }
  return { guides, renames };
}

/** The one place the guide tools get their set from: official view + the user's own. */
export function combineGuides(
  view: GuideView, load: LocalLoad,
): { guides: Record<string, unknown>; meta: Record<string, unknown> } {
  if (!view.file && load.guides.length === 0) {
    return {
      guides: { error: "Could not load modeling guides" },
      meta: guideMeta({ ...view, local: { count: 0, errors: load.errors, errorTotal: load.errorTotal, renames: [] } }),
    };
  }
  const { guides, renames } = mergeLocalGuides(view.file, load.guides);
  return { guides, meta: guideMeta({
    ...view, local: { count: load.guides.length, errors: load.errors, errorTotal: load.errorTotal, renames } }) };
}

/** guide_save: the file key of the own guide shown under `key`, when `key` is only its
 *  display name (a rename such as <key>_local); else null. Saving under the display name
 *  would create a second file instead of updating the guide. */
export function renamedFrom(key: string, renames: LocalGuideRename[]): string | null {
  const r = renames.find((x) => x.shownAs === key);
  return r ? r.file.replace(/\.json$/, "") : null;
}

// ---------------------------------------------------------------------------
// Drafting a guide from model_extract, and preparing a guide for saving
// ---------------------------------------------------------------------------

export interface ExtractBlock { id: number; type: string; library: string; label: string; parentBlockId?: number | null }
export interface ExtractResult {
  modelName?: string;
  sections: {
    blocks?: ExtractBlock[];
    connections?: Array<{ sourceBlockId: number; sourceConnector: string; targetBlockId: number; targetConnector: string }>;
    parameters?: { blocks?: Record<string, Record<string, unknown>> };
    /** parentBlockId is null (or absent) for an H-block at the top level. */
    hierarchies?: Array<{ blockId: number; label: string; parentBlockId?: number | null }>;
    /** Connection nodes model_extract could not pair up - usually lines into an H-block. */
    unresolvedConnectionNodes?: Array<{
      nodeIndex: number; endpoints: Array<{ blockId: number; connector: string; direction: string }>;
    }>;
  };
}
export interface DraftResult { draft: Scenario; needsInput: string[]; notes: string[]; suggestedKey: string }
export type Failure = { ok: false; errorCode: string; error: string; issues?: string[] };

const LIST_MAX = 50;      // guide-schema LIMITS.list
const STRING_MAX = 2000;  // guide-schema LIMITS.string
const H_BLOCK = "Hierarchical block";

/** The model's purpose from a context_get result, or null - a failed or empty
 *  context_get never stops a draft, the description is then left for the user. */
export function draftPurpose(ctx: unknown): { purpose: string } | null {
  const c = ctx as { success?: unknown; exists?: unknown; context?: { purpose?: unknown } } | null | undefined;
  if (!c || c.success === false || !c.exists) return null;
  const purpose = c.context?.purpose;
  return typeof purpose === "string" && purpose ? { purpose } : null;
}

/** A model name as a guide key: lower case, runs of anything else become _. */
export function suggestKey(modelName: string): string {
  const k = modelName.replace(/\.mox$/i, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 64).replace(/_+$/, "");
  if (!k) return "my_guide";
  return validateLocalKey(k) ? `my_${k}`.slice(0, 64) : k;
}

/** ExtendSim's node index spans the whole model hierarchy, so a connection can name blocks
 *  several hierarchical-block boundaries away from where a guide is being drafted. Returns a
 *  function that climbs from any block id to the id of its ancestor sitting directly at the
 *  drafted level `parent` - a coded block or H-block in `levelIds` - or null if the walk never
 *  reaches one (it left the model, or reached the top without passing through the level).
 *  Guards against a cycle in the parent chain by giving up after 64 steps. */
function makeLevelAncestor(
  blocks: ExtractBlock[], hierarchies: Array<{ blockId: number; parentBlockId?: number | null }>,
  parent: number | null, levelIds: Set<number>,
): (id: number) => number | null {
  const parentOf = new Map<number, number | null>();
  for (const b of blocks) parentOf.set(b.id, b.parentBlockId ?? null);
  for (const h of hierarchies) parentOf.set(h.blockId, h.parentBlockId ?? null);
  return (id: number): number | null => {
    let cur = id;
    for (let step = 0; step < 64; step++) {
      const p = parentOf.get(cur);
      if (p === undefined) return null;          // not part of this model's hierarchy
      if (p === parent) return levelIds.has(cur) ? cur : null;
      if (p === null) return null;                // reached the top without passing through the level
      cur = p;
    }
    return null;                                  // cycle guard
  };
}

/** Turns one level of a model into a guide draft. The model supplies blocks, connections
 *  and set parameters; everything only a person knows is left empty and listed. */
export function draftFromExtract(
  extract: ExtractResult, context: { purpose?: string } | null, opts: { hierarchyBlockId?: number } = {},
): { ok: true; value: DraftResult } | Failure {
  const s = extract.sections;
  const parent = opts.hierarchyBlockId ?? null;
  if (parent !== null && !(s.hierarchies ?? []).some((h) => h.blockId === parent)) {
    return { ok: false, errorCode: "BLOCK_NOT_FOUND",
      error: `Block ${parent} is not a hierarchical block in this model (see hierarchy_list).` };
  }
  // Coded blocks first, then the hierarchical blocks at this level (model_extract lists
  // those only under hierarchies). Lines into an H-block cannot be resolved; see below.
  const coded = (s.blocks ?? []).filter((b) => (b.parentBlockId ?? null) === parent && b.type !== "Executive");
  const hblocks = (s.hierarchies ?? []).filter((h) => (h.parentBlockId ?? null) === parent);
  const blocks: Array<{ id: number; type: string; library: string; label: string }> = [
    ...coded.map((b) => ({ id: b.id, type: b.type, library: b.library.split(/[\\/]/).pop() ?? b.library, label: b.label })),
    ...hblocks.map((h) => ({ id: h.blockId, type: H_BLOCK, library: "", label: h.label })),
  ];
  if (blocks.length === 0) {
    return { ok: false, errorCode: "GUIDE_NOTHING_TO_DRAFT", error: "There are no blocks at this level of the model to draft a guide from." };
  }
  if (blocks.length > DRAFT_MAX_BLOCKS) {
    const hs = (s.hierarchies ?? []).slice(0, 10).map((h) => `${h.blockId} (${h.label || "unlabelled"})`).join(", ");
    return { ok: false, errorCode: "GUIDE_TOO_LARGE_MODEL",
      error: `${blocks.length} blocks at this level; a guide shows a pattern of at most ${DRAFT_MAX_BLOCKS}. ` +
        `Pass hierarchyBlockId to draft the contents of one hierarchical block${hs ? ` - for example ${hs}` : ""}.` };
  }

  // A connection names its blocks by label, so a label must be unique: an unlabelled block,
  // or one whose label is repeated, is named <label or type>#<id> and the user asked for a label.
  const labelCount = new Map<string, number>();
  for (const b of blocks) if (b.label) labelCount.set(b.label, (labelCount.get(b.label) ?? 0) + 1);
  const unique = (b: { label: string }) => !!b.label && labelCount.get(b.label) === 1;
  const nameOf = new Map(blocks.map((b) => [b.id, unique(b) ? b.label : `${b.label || b.type}#${b.id}`]));
  const needsInput = ["name", "category"];
  if (!context?.purpose) needsInput.push("description");
  needsInput.push("useWhen", "pattern.blocks[*].purpose");
  blocks.forEach((b, i) => { if (!unique(b)) needsInput.push(`pattern.blocks[${i}].label`); });
  const notes: string[] = hblocks.map((h) =>
    `${nameOf.get(h.blockId)} (block ${h.blockId}) is a hierarchical block - call guide_draft with hierarchyBlockId: ${h.blockId} to draft its contents.`);
  const unresolved = (s.unresolvedConnectionNodes ?? [])
    .filter((n) => (n.endpoints ?? []).some((e) => nameOf.has(e.blockId))).length;
  if (unresolved > 0) {
    notes.push(`${unresolved} connection(s) at this level could not be resolved (usually lines into a hierarchical block) ` +
      "and are not listed - add them to pattern.connections by hand.");
  }

  // Resolve each connection endpoint to its ancestor at this level, climbing through any
  // hierarchical-block boundaries in between (see makeLevelAncestor). Both sides resolved and
  // equal: the line is entirely inside one nested H-block - drafting that H-block shows it. Both
  // resolved and different: keep it, rendered "via" the inner block when the level block isn't
  // the endpoint itself. Only one resolved: the line leaves this H-block through its own
  // connectors and is not listed, but is counted.
  const levelIds = new Set(blocks.map((b) => b.id));
  const levelAncestor = makeLevelAncestor(s.blocks ?? [], s.hierarchies ?? [], parent, levelIds);
  const rawNameOf = new Map<number, string>();
  for (const b of s.blocks ?? []) rawNameOf.set(b.id, b.label || `${b.type}#${b.id}`);
  for (const h of s.hierarchies ?? []) rawNameOf.set(h.blockId, h.label || `${H_BLOCK}#${h.blockId}`);
  let anyVia = false;
  const sideLabel = (ancestorId: number, endpointId: number, connector: string): string => {
    if (ancestorId === endpointId) return `${nameOf.get(ancestorId)} (${connector})`;
    anyVia = true;
    return `${nameOf.get(ancestorId)} (via ${rawNameOf.get(endpointId) ?? `#${endpointId}`} ${connector})`;
  };
  let leaving = 0;
  const connectionLines: string[] = [];
  for (const c of s.connections ?? []) {
    const a = levelAncestor(c.sourceBlockId);
    const b = levelAncestor(c.targetBlockId);
    if (a !== null && b !== null) {
      if (a === b) continue;
      connectionLines.push(`${sideLabel(a, c.sourceBlockId, c.sourceConnector)} → ${sideLabel(b, c.targetBlockId, c.targetConnector)}`);
    } else if ((a !== null) !== (b !== null)) {
      leaving++;
    }
  }
  if (leaving > 0) {
    notes.push(`${leaving} connection(s) leave this hierarchical block through its connectors and are not listed - ` +
      "add them by hand if they matter to the pattern.");
  }
  if (anyVia) needsInput.push("pattern.connections");
  const allConnections = [...new Set(connectionLines)];
  if (allConnections.length > LIST_MAX) {
    notes.push(`${allConnections.length} connections; the first ${LIST_MAX} are kept - a guide lists at most ${LIST_MAX}.`);
  }

  const allParams = blocks.flatMap((b) =>
    Object.entries(s.parameters?.blocks?.[String(b.id)] ?? {})
      .filter(([k, v]) => k !== "blockType" && v !== null && v !== undefined && v !== "")
      .map(([k, v]) => ({
        block: nameOf.get(b.id)!, parameter: k,
        typical: (typeof v === "object" ? JSON.stringify(v) : String(v)).slice(0, STRING_MAX),
        description: "",
      })));
  if (allParams.length > LIST_MAX) {
    notes.push(`${allParams.length} key parameters; the first ${LIST_MAX} are kept - keep the ones that matter.`);
  }
  if (allParams.length > 0) needsInput.push("keyParameters[*].description");
  needsInput.push("keyMetrics", "commonMistakes");

  const draft: Scenario = {
    name: "",
    category: "",
    description: (context?.purpose ?? "").slice(0, STRING_MAX),
    useWhen: [],
    pattern: {
      blocks: blocks.map((b) => ({ name: b.type, library: b.library, label: nameOf.get(b.id)!, purpose: "" })),
      connections: allConnections.slice(0, LIST_MAX),
      template: null,
    },
    keyParameters: allParams.slice(0, LIST_MAX),
    keyMetrics: [],
    commonMistakes: [],
    variations: [],
    exampleModel: extract.modelName || null,
  };
  return { ok: true, value: { draft, needsInput, notes, suggestedKey: suggestKey(extract.modelName ?? "") } };
}

/** What a guide needs to be useful, beyond passing the schema. */
export function checkMinimumContent(s: Scenario): string[] {
  const missing: string[] = [];
  if (!s.name.trim()) missing.push("name: required");
  if (!s.description.trim()) missing.push("description: required");
  if (s.useWhen.length < 1) missing.push("useWhen: at least one entry");
  if (s.pattern.blocks.length < 2) missing.push("pattern.blocks: at least two blocks");
  return missing;
}

/** Today in local time as YYYY-MM-DD (the date a user confirmed a guide). */
export function localDate(d: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Validate and normalise a guide for disk: schema-stripped (drops e.g. source), no
 *  `since` (own guides are for this installation), `verified` only when the user
 *  confirmed a run. Refuses anything the loader would later skip. */
export function prepareForSave(
  input: unknown, opts: { userConfirmedRun: boolean; today: string },
): { ok: true; scenario: Scenario; text: string } | Failure {
  const r = ScenarioSchema.safeParse(input);
  if (!r.success) {
    return { ok: false, errorCode: "GUIDE_INVALID", error: "The guide does not match the guide schema; nothing was saved.",
      issues: formatIssues(r.error.issues) };
  }
  const categoryProblem = validateLocalKey(r.data.category);
  if (categoryProblem) {
    return { ok: false, errorCode: "GUIDE_INVALID", error: "The guide's category cannot be used; nothing was saved.",
      issues: [`category: ${categoryProblem}`] };
  }
  const missing = checkMinimumContent(r.data);
  if (missing.length > 0) {
    return { ok: false, errorCode: "GUIDE_INVALID", error: "The guide is missing content every guide needs; nothing was saved.",
      issues: missing };
  }
  const scenario: Scenario = { ...r.data };
  delete scenario.since;
  delete scenario.verified;
  if (opts.userConfirmedRun) scenario.verified = opts.today;
  const text = JSON.stringify(scenario, null, 2) + "\n";
  if (Buffer.byteLength(text, "utf-8") > LOCAL_MAX_BYTES) {
    return { ok: false, errorCode: "GUIDE_INVALID",
      error: `The guide would be ${Buffer.byteLength(text, "utf-8")} bytes; a guide file is at most ${LOCAL_MAX_BYTES}. Nothing was saved.` };
  }
  return { ok: true, scenario, text };
}
