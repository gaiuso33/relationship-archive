import { describe, expect, it } from "vitest";

import { safeInternalPath } from "./safeRedirect";

describe("safeInternalPath", () => {
  it.each(["/", "/people", "/people/abc?tab=notes#top", "/a/b/c"])("keeps the internal path %s", (path) => {
    expect(safeInternalPath(path)).toBe(path);
  });

  it.each([
    ["an absolute URL", "https://evil.example/"],
    ["a protocol-relative URL", "//evil.example/path"],
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,x"],
    ["a backslash host", "/\\evil.example"],
    ["a backslash anywhere", "/ok\\path"],
    ["a tab hidden in the slashes", "/\t/evil.example"],
    ["a line break", "/ok\nSet-Cookie: x"],
    ["a space", "/a b"],
    ["a control character", "/a\u0000b"],
    ["no leading slash", "people"],
    ["an empty string", ""],
    ["an over-long value", `/${"a".repeat(3000)}`],
    ["the sign-in page (loop)", "/sign-in"],
    ["a sign-in sub-path", "/sign-in/x"],
  ])("rejects %s", (_name, value) => {
    expect(safeInternalPath(value)).toBe("/");
  });

  it("handles missing values", () => {
    expect(safeInternalPath(null)).toBe("/");
    expect(safeInternalPath(undefined)).toBe("/");
  });
});
