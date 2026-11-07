import { describe, expect, it } from "vitest";

import { checkNoteContent, NOTE_MAX } from "./validation";

describe("checkNoteContent", () => {
  it("returns the text exactly as typed, never a trimmed copy", () => {
    const text = "  first line\n\n\tsecond line  \n";
    expect(checkNoteContent(text)).toEqual({ ok: true, value: text });
  });
  it.each(["", "   ", "\n\t \r\n"])("rejects blank content %j", (raw) => {
    expect(checkNoteContent(raw)).toEqual({ ok: false, error: "Write something before adding the note." });
  });
  it("allows exactly 10,000 characters and rejects 10,001, counting emoji once", () => {
    expect(checkNoteContent("a".repeat(NOTE_MAX)).ok).toBe(true);
    expect(checkNoteContent("a".repeat(NOTE_MAX + 1)).ok).toBe(false);
    expect(checkNoteContent("😀".repeat(NOTE_MAX)).ok).toBe(true);
    expect(checkNoteContent("😀".repeat(NOTE_MAX + 1)).ok).toBe(false);
  });
  it.each(["a\u0000b", "a\u0007b", "a\u001bb", "a\u007fb"])("rejects control characters in %j", (raw) => {
    expect(checkNoteContent(raw).ok).toBe(false);
  });
  it("allows newline, carriage return and tab", () => {
    expect(checkNoteContent("a\nb\rc\td").ok).toBe(true);
  });
  it.each(["a\uD800b", "a\uDC00b", "trailing\uD83D"])("rejects a lone surrogate in %j", (raw) => {
    expect(checkNoteContent(raw).ok).toBe(false);
  });
  it("accepts a well-formed surrogate pair", () => {
    expect(checkNoteContent("ok 😀").ok).toBe(true);
  });
});
