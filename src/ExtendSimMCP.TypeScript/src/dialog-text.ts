/**
 * Dialog text for telemetry (spec S8): keeps the shape of ExtendSim's message, drops
 * everything that could be the user's - quoted names, paths, numbers. Pure.
 */
export const DIALOG_TEXT_MAX = 200;

export function sanitizeDialogText(text: string): string {
  let s = text;
  s = s.replace(/"[^"]*"|\u201C[^\u201D]*\u201D/g, '"…"');
  s = s.replace(/(^|[^\p{L}])['\u2018\u2019][^\n]*?['\u2018\u2019](?![\p{L}])/gu, '$1"…"');      // an apostrophe inside a word is not a quote
  s = s.replace(/[^\s"…]*[\\/][^\s"…]*/g, "<path>");
  s = s.replace(/\d+/g, "#");
  s = s.replace(/\s+/g, " ").trim();
  return s.length > DIALOG_TEXT_MAX ? s.slice(0, DIALOG_TEXT_MAX - 1) + "…" : s;
}
