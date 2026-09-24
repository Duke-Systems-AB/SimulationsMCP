/**
 * Schema of modeling_guides.json - the file the server ships AND the file it may fetch
 * from duke.se. Everything fetched passes through GuideFileSchema before an AI sees it:
 * unknown keys are stripped (zod/v4 objects strip by default) and every string and list
 * is length-capped, so a tampered file cannot smuggle extra fields or bulk text in.
 *
 * zod/v4 (shipped inside zod 3.25) is used for its built-in toJSONSchema(), which gives
 * the web repo a standard JSON Schema without a new dependency.
 */
import { z } from "zod/v4";

export const SCHEMA_VERSION = 1;

/**
 * Each limit is at least twice what the bundled file uses (guide-schema.test.ts checks).
 * `bytes` is not referenced in this file - it caps the raw response body and is enforced by
 * the fetcher (guide-core/guide-source, later tasks) while streaming, before JSON.parse ever runs.
 */
export const LIMITS = {
  string: 2000,        // bundled 1.13.0: longest string 317 characters
  list: 50,            // bundled: longest list 9 items
  scenarios: 500,      // bundled: 12
  categories: 100,     // bundled: 6
  bytes: 1_000_000,    // bundled: ~70 KB
} as const;

const str = z.string().max(LIMITS.string);
const list = <T extends z.ZodType>(item: T) => z.array(item).max(LIMITS.list);
const key = z.string().regex(/^[a-z0-9_]{1,64}$/);
const semver = z.string().regex(/^\d+\.\d+\.\d+$/);

const Block = z.object({ name: str, library: str, label: str, purpose: str });

export const ScenarioSchema = z.object({
  name: str,
  category: key,
  description: str,
  useWhen: list(str),
  pattern: z.object({
    blocks: list(Block),
    connections: list(str),
    template: str.nullable().optional(),
  }),
  keyParameters: list(z.object({ block: str, parameter: str, typical: str, description: str })),
  keyMetrics: list(str),
  commonMistakes: list(str),
  variations: list(z.object({ name: str, change: str, scenario: key.nullable().optional() })),
  exampleModel: str.nullable().optional(),
  // FR-G2 - optional, ignored by older servers
  since: semver.optional(),                                    // lowest server version
  verified: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional(), // date the example was run
  errorCodes: list(z.string().regex(/^[A-Z][A-Z0-9_]{0,63}$/)).optional(),
});

export const GuideFileSchema = z.object({
  schemaVersion: z.literal(SCHEMA_VERSION),
  version: semver,
  description: str,
  categories: z
    .record(key, z.object({ label: str, scenarios: list(key) }))
    .refine((o) => Object.keys(o).length <= LIMITS.categories, "too many categories"),
  scenarios: z
    .record(key, ScenarioSchema)
    .refine((o) => Object.keys(o).length <= LIMITS.scenarios, "too many scenarios"),
});

export type GuideFile = z.infer<typeof GuideFileSchema>;
export type Scenario = z.infer<typeof ScenarioSchema>;

/**
 * JSON Schema for the web repo (written to src/guide-schema.json by `npm run schema:emit`).
 *
 * z.toJSONSchema() cannot translate the `.refine()` count caps on `categories` and `scenarios`
 * into JSON Schema (refinements are arbitrary predicates, not representable constructs), so
 * without help the emitted schema would have no cap on how many category/scenario entries a
 * file may declare even though GuideFileSchema itself rejects an over-count file at runtime.
 * The `override` hook runs for every subschema during traversal; `ctx.path` is the JSON
 * Schema pointer path (as string/number segments) to that subschema, so `["properties",
 * "categories"]` / `["properties", "scenarios"]` identify the two top-level fields and get an
 * explicit `maxProperties` added to match LIMITS, keeping the emitted schema in sync with the
 * Zod runtime check.
 */
export function guideJsonSchema(): unknown {
  return z.toJSONSchema(GuideFileSchema, {
    override: (ctx) => {
      const { path, jsonSchema } = ctx;
      if (path.length === 2 && path[0] === "properties" && path[1] === "categories") {
        jsonSchema.maxProperties = LIMITS.categories;
      }
      if (path.length === 2 && path[0] === "properties" && path[1] === "scenarios") {
        jsonSchema.maxProperties = LIMITS.scenarios;
      }
    },
  });
}
