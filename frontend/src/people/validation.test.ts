import { describe, expect, it } from "vitest";

import {
  checkBirthday,
  checkDisplayName,
  checkEntry,
  checkPersonForm,
  countCharacters,
  type PersonFormValues,
} from "./validation";

const TODAY = "2026-06-15";

describe("countCharacters", () => {
  it("counts code points, not UTF-16 units", () => {
    expect(countCharacters("abc")).toBe(3);
    expect(countCharacters("😀")).toBe(1);
    expect("😀".length).toBe(2);
  });
});

describe("checkDisplayName", () => {
  it("trims and accepts a normal name", () => {
    expect(checkDisplayName("  Ada Example  ")).toEqual({ ok: true, value: "Ada Example" });
  });
  it.each(["", "   ", "\t\n"])("rejects blank input %j", (raw) => {
    expect(checkDisplayName(raw)).toEqual({ ok: false, error: "Enter a name." });
  });
  it("allows exactly 120 characters and rejects 121, counting emoji once", () => {
    expect(checkDisplayName("😀".repeat(120)).ok).toBe(true);
    expect(checkDisplayName("😀".repeat(121)).ok).toBe(false);
    expect(checkDisplayName("a".repeat(121))).toEqual({ ok: false, error: "Use 120 characters or fewer." });
  });
  it.each(["a\u0000b", "a\u0007b", "a\nb", "a b"])("rejects control or separator characters in %j", (raw) => {
    expect(checkDisplayName(raw).ok).toBe(false);
  });
});

describe("checkBirthday", () => {
  it("treats empty as no birthday", () => {
    expect(checkBirthday("", TODAY)).toEqual({ ok: true, value: null });
  });
  it("accepts the boundaries: 1900-01-01 and today", () => {
    expect(checkBirthday("1900-01-01", TODAY)).toEqual({ ok: true, value: "1900-01-01" });
    expect(checkBirthday(TODAY, TODAY)).toEqual({ ok: true, value: TODAY });
  });
  it("rejects dates before 1900 and in the future", () => {
    expect(checkBirthday("1899-12-31", TODAY).ok).toBe(false);
    expect(checkBirthday("2026-06-16", TODAY)).toEqual({ ok: false, error: "The date cannot be in the future." });
  });
  it.each(["2026-02-30", "2025-02-29", "2026-13-01", "2000-1-1", "yesterday", "2000-01-01T00:00"])(
    "rejects the invalid date %j",
    (raw) => {
      expect(checkBirthday(raw, TODAY)).toEqual({ ok: false, error: "Enter a valid date." });
    },
  );
  it("accepts a real leap day", () => {
    expect(checkBirthday("2024-02-29", TODAY).ok).toBe(true);
  });
});

describe("checkEntry", () => {
  it("trims and accepts", () => {
    expect(checkEntry("  Hiking ", [])).toEqual({ ok: true, value: "Hiking" });
  });
  it("rejects blank, too long, control characters and repeats ignoring case", () => {
    expect(checkEntry("  ", []).ok).toBe(false);
    expect(checkEntry("x".repeat(100), []).ok).toBe(true);
    expect(checkEntry("x".repeat(101), []).ok).toBe(false);
    expect(checkEntry("a\u0000", []).ok).toBe(false);
    expect(checkEntry("hiking", ["Hiking"])).toEqual({ ok: false, error: "That is already in the list." });
  });
  it("allows at most 50 entries", () => {
    const fifty = Array.from({ length: 50 }, (_, i) => `item ${i}`);
    expect(checkEntry("one more", fifty)).toEqual({ ok: false, error: "You can add at most 50." });
    expect(checkEntry("one more", fifty.slice(1)).ok).toBe(true);
  });
});

describe("checkPersonForm", () => {
  const base: PersonFormValues = {
    displayName: "Ada Example",
    birthday: "",
    interests: [],
    interestsDraft: "",
    preferences: [],
    preferencesDraft: "",
  };

  it("returns exactly the four fields the API accepts, and never a time zone", () => {
    const result = checkPersonForm({ ...base, displayName: " Ada ", interests: ["Chess"] }, TODAY);
    expect(result).toEqual({
      ok: true,
      value: { display_name: "Ada", birthday: null, interests: ["Chess"], preferences: [] },
    });
    expect(Object.keys((result as { value: object }).value)).not.toContain("timezone");
  });

  it("collects every field error at once", () => {
    const result = checkPersonForm({ ...base, displayName: "", birthday: "2999-01-01" }, TODAY);
    expect(result).toMatchObject({ ok: false, errors: { displayName: "Enter a name.", birthday: expect.any(String) } });
  });

  it("refuses text that was typed in an entry box but never added", () => {
    const result = checkPersonForm({ ...base, interestsDraft: "Chess" }, TODAY);
    expect(result).toMatchObject({ ok: false, errors: { interests: expect.stringContaining("has not been added") } });
  });
});
