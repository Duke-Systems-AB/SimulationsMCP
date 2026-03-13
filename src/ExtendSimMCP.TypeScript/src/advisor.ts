/**
 * Model advisor analysis functions.
 * Pure functions — no backend/MCP dependencies.
 */

export interface AdvisorBlock {
  blockId: number;
  blockName: string;
  label: string;
}

export interface AdvisorConnection {
  from: { blockId: number; blockName: string };
  to: { blockId: number; blockName: string };
}

export interface AdvisorIssue {
  type: "error" | "warning";
  blockId?: number;
  message: string;
}

export interface AdvisorSuggestion {
  type: "suggestion";
  message: string;
  source: "pattern_library" | "modeling_guide";
  scenario?: string;
  overlap?: number;
}

export interface AdvisorCompletion {
  type: "completion";
  message: string;
}

export function analyzeWarnings(
  blocks: AdvisorBlock[],
  connections: AdvisorConnection[]
): AdvisorIssue[] {
  const issues: AdvisorIssue[] = [];
  const connectedBlocks = new Set<number>();
  const blocksWithOutput = new Set<number>();
  const blocksWithInput = new Set<number>();
  const predecessors = new Map<number, Set<string>>();

  for (const c of connections) {
    const fromId = c.from?.blockId;
    const toId = c.to?.blockId;
    if (fromId != null) { connectedBlocks.add(fromId); blocksWithOutput.add(fromId); }
    if (toId != null) { connectedBlocks.add(toId); blocksWithInput.add(toId); }
    if (fromId != null && toId != null) {
      if (!predecessors.has(toId)) predecessors.set(toId, new Set());
      predecessors.get(toId)!.add(c.from.blockName);
    }
  }

  const downstream = new Map<number, Set<string>>();
  for (const c of connections) {
    const fromId = c.from?.blockId;
    const toName = c.to?.blockName;
    if (fromId != null && toName) {
      if (!downstream.has(fromId)) downstream.set(fromId, new Set());
      downstream.get(fromId)!.add(toName);
    }
  }

  const allBlockNames = new Set(blocks.map(b => b.blockName));

  for (const b of blocks) {
    const { blockId, blockName, label } = b;

    // Check specific structural errors first (these take priority)
    if (blockName === "Create" && !blocksWithOutput.has(blockId)) {
      issues.push({ type: "error", blockId, message: `Create block '${label}' has no output connection` });
    }

    if (blockName === "Exit" && !blocksWithInput.has(blockId)) {
      issues.push({ type: "error", blockId, message: `Exit block '${label}' has no input connection` });
    }

    // Generic unconnected warning (skip blocks already reported with specific errors above)
    if (!connectedBlocks.has(blockId)) {
      if (blockName !== "Create" && blockName !== "Exit") {
        issues.push({ type: "warning", blockId, message: `Block '${label}' (${blockName}) has no connections` });
      }
      continue;
    }

    if (blockName === "Activity" && predecessors.has(blockId)) {
      const preds = predecessors.get(blockId)!;
      if (!preds.has("Queue") && !preds.has("Workstation")) {
        issues.push({ type: "warning", blockId, message: `Activity block '${label}' has no Queue predecessor — may cause simulation issues` });
      }
    }

    if (blockName === "Batch") {
      if (!allBlockNames.has("Unbatch")) {
        issues.push({ type: "warning", blockId, message: `Batch block '${label}' has no Unbatch block in model — batched items may never be separated` });
      }
    }

    if (blockName === "Select Item Out") {
      const outCount = connections.filter(c => c.from?.blockId === blockId).length;
      if (outCount <= 1) {
        issues.push({ type: "warning", blockId, message: `Select Item Out '${label}' has only ${outCount} output connection — consider using a direct connection instead` });
      }
    }

    if (blockName === "Select Item In") {
      const inCount = connections.filter(c => c.to?.blockId === blockId).length;
      if (inCount <= 1) {
        issues.push({ type: "warning", blockId, message: `Select Item In '${label}' has only ${inCount} input connection — consider using a direct connection instead` });
      }
    }
  }

  return issues;
}

/**
 * Pass 2: Suggest relevant patterns and guides based on block-type overlap.
 */
export function analyzeSuggestions(
  blocks: AdvisorBlock[],
  _connections: AdvisorConnection[],
  patterns: Array<Record<string, unknown>>,
  guides: Record<string, unknown>
): AdvisorSuggestion[] {
  if (blocks.length === 0) return [];

  const suggestions: AdvisorSuggestion[] = [];
  const modelTypes = new Set(blocks.map(b => b.blockName));
  const THRESHOLD = 0.5;
  const MAX_RESULTS = 3;

  // Match against pattern library
  const patternScores: Array<{ pattern: Record<string, unknown>; overlap: number }> = [];
  for (const p of patterns) {
    const summary = p.blockTypeSummary as Record<string, number> | undefined;
    if (!summary) continue;
    const patternTypes = new Set(Object.keys(summary));
    if (patternTypes.size === 0) continue;

    let intersection = 0;
    for (const t of patternTypes) {
      if (modelTypes.has(t)) intersection++;
    }
    const overlap = intersection / patternTypes.size;
    if (overlap >= THRESHOLD) {
      patternScores.push({ pattern: p, overlap });
    }
  }

  patternScores.sort((a, b) => b.overlap - a.overlap);
  for (const { pattern, overlap } of patternScores.slice(0, MAX_RESULTS)) {
    suggestions.push({
      type: "suggestion",
      message: `Model matches '${pattern.name}' pattern (${Math.round(overlap * 100)}% block-type overlap)`,
      source: "pattern_library",
      overlap,
    });
  }

  // Match against modeling guides
  const scenarios = (guides as any).scenarios as Record<string, Record<string, unknown>> | undefined;
  if (scenarios) {
    const guideScores: Array<{ key: string; scenario: Record<string, unknown>; overlap: number }> = [];
    for (const [key, scenario] of Object.entries(scenarios)) {
      const patternBlocks = ((scenario.pattern as any)?.blocks as Array<{ name: string }>) || [];
      const guideTypes = new Set(patternBlocks.map(b => b.name));
      if (guideTypes.size === 0) continue;

      let intersection = 0;
      for (const t of guideTypes) {
        if (modelTypes.has(t)) intersection++;
      }
      const overlap = intersection / guideTypes.size;
      if (overlap >= THRESHOLD) {
        guideScores.push({ key, scenario, overlap });
      }
    }

    guideScores.sort((a, b) => b.overlap - a.overlap);
    for (const { key, scenario, overlap } of guideScores.slice(0, MAX_RESULTS)) {
      const mistakes = (scenario.commonMistakes as string[]) || [];
      const mistakeHint = mistakes.length > 0 ? ` Common pitfall: ${mistakes[0]}` : "";
      suggestions.push({
        type: "suggestion",
        message: `Model matches '${scenario.name}' guide (${Math.round(overlap * 100)}% overlap).${mistakeHint}`,
        source: "modeling_guide",
        scenario: key,
        overlap,
      });
    }
  }

  return suggestions;
}

/**
 * Pass 3: Detect incomplete chains and suggest next blocks.
 */
export function analyzeCompletions(
  blocks: AdvisorBlock[],
  connections: AdvisorConnection[]
): AdvisorCompletion[] {
  if (blocks.length === 0) return [];

  const completions: AdvisorCompletion[] = [];
  const blockNames = new Set(blocks.map(b => b.blockName));

  const downstream = new Map<number, Set<string>>();
  for (const c of connections) {
    const fromId = c.from?.blockId;
    const toName = c.to?.blockName;
    if (fromId != null && toName) {
      if (!downstream.has(fromId)) downstream.set(fromId, new Set());
      downstream.get(fromId)!.add(toName);
    }
  }

  const predecessors = new Map<number, Set<string>>();
  for (const c of connections) {
    const toId = c.to?.blockId;
    const fromName = c.from?.blockName;
    if (toId != null && fromName) {
      if (!predecessors.has(toId)) predecessors.set(toId, new Set());
      predecessors.get(toId)!.add(fromName);
    }
  }

  const hasCreate = blockNames.has("Create");
  const hasExit = blockNames.has("Exit");
  const hasQueue = blockNames.has("Queue");
  const hasActivity = blockNames.has("Activity") || blockNames.has("Workstation");

  if (hasCreate && !hasExit) {
    completions.push({
      type: "completion",
      message: "Model has Create but no Exit — typical next step is to add an Exit block at the end of the flow",
    });
  }

  if (hasQueue) {
    for (const b of blocks) {
      if (b.blockName === "Queue") {
        const ds = downstream.get(b.blockId);
        if (ds && !ds.has("Activity") && !ds.has("Workstation")) {
          completions.push({
            type: "completion",
            message: `Queue '${b.label}' has no downstream Activity or Workstation — typical next step is to add an Activity block after the Queue`,
          });
        }
      }
    }
  }

  if (hasActivity) {
    for (const b of blocks) {
      if (b.blockName === "Activity" || b.blockName === "Workstation") {
        const preds = predecessors.get(b.blockId);
        if (preds && !preds.has("Queue")) {
          completions.push({
            type: "completion",
            message: `${b.blockName} '${b.label}' has no Queue predecessor — consider adding a Queue block before it to buffer arriving items`,
          });
        }
      }
    }
  }

  if (hasActivity && !hasExit) {
    completions.push({
      type: "completion",
      message: "Model has processing blocks but no Exit — add an Exit block to collect throughput and cycle time statistics",
    });
  }

  if (hasCreate && hasQueue && hasActivity && hasExit) {
    completions.push({
      type: "completion",
      message: "Basic flow (Create→Queue→Activity/Workstation→Exit) looks complete",
    });
  }

  return completions;
}
