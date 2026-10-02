/**
 * Attaches block guides (modeling_guides.json `blocks`) to block_search
 * results: `hasGuide` always, the full guide only with `detail`, so a normal search stays small.
 */
import { blockGuideKey, type BlockGuide } from "./guide-schema.js";

export function attachGuides<T extends { library: string; name: string }>(
  results: T[], blocks: Record<string, BlockGuide> | undefined, detail: boolean,
): Array<T & { hasGuide?: true; guide?: BlockGuide }> {
  if (!blocks) return results;
  return results.map((r) => {
    const g = blocks[blockGuideKey(r.library, r.name)];
    if (!g) return r;
    return detail ? { ...r, hasGuide: true as const, guide: g } : { ...r, hasGuide: true as const };
  });
}

/**
 * Like attachGuides, but also considers the user's own local block guides
 * (loadLocalBlockGuides). The official guide always wins; when both
 * exist the result is flagged with `guideSource: "official"` and
 * `localGuideIgnored: true` so the shadowed local guide isn't silently lost.
 * A block with only a local guide gets `guideSource: "local"` and, with
 * `detail`, the guide body tagged `source: "local"`. A local guide is matched
 * case-insensitively on library and block, so one saved as "item.lbr" is still
 * found for "Item.lbr".
 */
export function attachGuidesWithLocal<T extends { library: string; name: string }>(
  results: T[],
  official: Record<string, BlockGuide> | undefined,
  local: { guides: Record<string, BlockGuide> },
  detail: boolean,
): Array<
  T & {
    hasGuide?: true;
    guideSource?: "official" | "local";
    localGuideIgnored?: true;
    guide?: BlockGuide & { source?: "local" };
  }
> {
  const localByLowerKey = new Map(Object.entries(local.guides).map(([k, g]) => [k.toLowerCase(), g]));
  return results.map((r) => {
    const key = blockGuideKey(r.library, r.name);
    const o = official?.[key];
    const l = local.guides[key] ?? localByLowerKey.get(key.toLowerCase());
    if (o) {
      return {
        ...r,
        hasGuide: true as const,
        guideSource: "official" as const,
        ...(l ? { localGuideIgnored: true as const } : {}),
        ...(detail ? { guide: o } : {}),
      };
    }
    if (l) {
      return {
        ...r,
        hasGuide: true as const,
        guideSource: "local" as const,
        ...(detail ? { guide: { ...l, source: "local" as const } } : {}),
      };
    }
    return r;
  });
}

/** A block_search result entry synthesized from a local block guide. */
export interface LocalGuideSearchResult {
  library: string;
  category: string;
  name: string;
  description: string;
}

/**
 * Extra block_search results for local guides whose block or library name
 * matches the query (case-insensitive substring), so a customer's own block -
 * not in block_reference.json - can still be found through its local guide.
 * Respects block_search's library filter the same way the reference search
 * does (substring match against the library name).
 */
export function localGuideResults(
  query: string,
  local: { guides: Record<string, BlockGuide> },
  library?: string,
): LocalGuideSearchResult[] {
  const q = query.toLowerCase();
  const libFilter = library?.toLowerCase();
  const out: LocalGuideSearchResult[] = [];
  for (const g of Object.values(local.guides)) {
    if (libFilter && !g.library.toLowerCase().includes(libFilter)) continue;
    if (!g.block.toLowerCase().includes(q) && !g.library.toLowerCase().includes(q)) continue;
    out.push({ library: g.library, category: "Local block guide", name: g.block, description: g.summary });
  }
  return out;
}

const searchKey = (r: { library: string; name: string }) => `${r.library.toLowerCase()}/${r.name.toLowerCase()}`;

/**
 * Merges the reference search's official results with the extra local-guide
 * results, for block_search. Local matches are the user's own blocks, so they
 * are placed first and always survive even when official matches already
 * fill `cap`; official results fill whatever room is left, up to `cap`. A
 * local result that duplicates an official one (case-insensitive on library
 * and name) is dropped in favor of the official entry. `totalMatches` counts
 * every match found (official's own total, uncapped, plus the surviving
 * local matches) so `truncated` reflects the combined total against `cap`.
 */
export function mergeSearchResults<T extends { library: string; name: string }>(
  official: T[],
  officialTotal: number,
  extraLocal: T[],
  cap: number,
): { results: T[]; totalMatches: number; truncated: boolean } {
  const officialKeys = new Set(official.map(searchKey));
  const dedupedLocal = extraLocal.filter((r) => !officialKeys.has(searchKey(r)));
  const results = [...dedupedLocal, ...official].slice(0, cap);
  const totalMatches = officialTotal + dedupedLocal.length;
  const truncated = totalMatches > cap;
  return { results, totalMatches, truncated };
}
