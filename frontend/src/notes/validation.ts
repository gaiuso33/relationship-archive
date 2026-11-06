import { countCharacters, type Checked } from "../people/validation";

export const NOTE_MAX = 10_000;

const ALLOWED_CONTROL = new Set(["\n", "\r", "\t"]);

/**
 * Mirrors the API's note rules (docs/notes-api.md): not blank, at most 10,000 characters, no NUL
 * or other control characters (newline, carriage return and tab are fine), no lone surrogates. The
 * text is kept exactly as typed, so the value returned is the input itself, never a trimmed copy.
 */
export function checkNoteContent(raw: string): Checked<string> {
  if (raw.trim() === "") return { ok: false, error: "Write something before adding the note." };
  if (countCharacters(raw) > NOTE_MAX) {
    return { ok: false, error: `Notes can be up to ${NOTE_MAX.toLocaleString("en-US")} characters.` };
  }
  for (const character of raw) {
    if (/^\p{Cs}$/u.test(character) || (/^\p{Cc}$/u.test(character) && !ALLOWED_CONTROL.has(character))) {
      return { ok: false, error: "The note contains characters that cannot be saved." };
    }
  }
  // A lone surrogate is not a whole character to the iterator above, so look for it directly.
  if (/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(raw)) {
    return { ok: false, error: "The note contains characters that cannot be saved." };
  }
  return { ok: true, value: raw };
}
