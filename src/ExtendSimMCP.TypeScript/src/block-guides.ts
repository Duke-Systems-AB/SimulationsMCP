/**
 * Attaches block guides (modeling_guides.json `blocks`, spec 2026-09-30) to block_search
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
