import { describe, expect, it } from "vitest";

import { formatDate, formatTimestamp, todayIso } from "./format";

describe("formatDate", () => {
  it("shows the stored calendar date, whatever the time zone", () => {
    expect(formatDate("2000-01-01", "en-US")).toBe("January 1, 2000");
    expect(formatDate("1999-12-31", "en-US")).toBe("December 31, 1999");
    expect(formatDate("2024-02-29", "en-US")).toBe("February 29, 2024");
  });
  it("honours the locale", () => {
    expect(formatDate("2000-03-05", "en-GB")).toBe("5 March 2000");
  });
  it("returns anything that is not a date unchanged", () => {
    expect(formatDate("not a date")).toBe("not a date");
    expect(formatDate("2000-13-45")).toBe("2000-13-45");
  });
});

describe("formatTimestamp", () => {
  it("formats a moment and falls back for nonsense", () => {
    expect(formatTimestamp("2026-01-01T09:00:00.000Z", "en-US")).toMatch(/2026/);
    expect(formatTimestamp("garbage")).toBe("garbage");
  });
});

describe("todayIso", () => {
  it("uses the local calendar date", () => {
    expect(todayIso(new Date(2026, 0, 5, 23, 59))).toBe("2026-01-05");
    expect(todayIso(new Date(2026, 11, 31, 0, 0))).toBe("2026-12-31");
  });
});
