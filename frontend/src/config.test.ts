import { describe, expect, it } from "vitest";

import { ConfigError, parseConfig, type RawEnv } from "./config";

const b64url = (value: object) => btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const jwtWithRole = (role: string) => `${b64url({ alg: "none" })}.${b64url({ role })}.signature`;

const valid: RawEnv = {
  VITE_SUPABASE_URL: "https://example-project.supabase.co",
  VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_syntheticplaceholder",
  VITE_API_BASE_URL: "https://api.example.test",
};

function problemsFor(env: RawEnv): readonly string[] {
  try {
    parseConfig(env);
  } catch (error) {
    if (error instanceof ConfigError) return error.problems;
    throw error;
  }
  throw new Error("expected the configuration to be rejected");
}

describe("parseConfig", () => {
  it("accepts a complete configuration", () => {
    expect(parseConfig(valid)).toEqual({
      supabaseUrl: "https://example-project.supabase.co",
      supabasePublishableKey: "sb_publishable_syntheticplaceholder",
      apiBaseUrl: "https://api.example.test",
    });
  });

  it("fails clearly, naming every missing variable", () => {
    const problems = problemsFor({});
    expect(problems).toEqual(
      expect.arrayContaining([
        "VITE_SUPABASE_URL is required.",
        "VITE_SUPABASE_PUBLISHABLE_KEY is required.",
        expect.stringContaining("VITE_API_BASE_URL is required"),
      ]),
    );
  });

  it("treats blank values as missing", () => {
    expect(problemsFor({ ...valid, VITE_SUPABASE_URL: "   " })).toContain("VITE_SUPABASE_URL is required.");
  });

  it('uses "/" for the API to mean the page\'s own origin (development proxy)', () => {
    expect(parseConfig({ ...valid, VITE_API_BASE_URL: "/" }).apiBaseUrl).toBe("");
  });

  it("accepts the older anon key format and the publishable format", () => {
    expect(parseConfig({ ...valid, VITE_SUPABASE_PUBLISHABLE_KEY: jwtWithRole("anon") })).toBeTruthy();
  });

  it.each([
    ["a secret key", "sb_secret_syntheticplaceholder", "secret key"],
    ["a service-role JWT", jwtWithRole("service_role"), "service-role key"],
    ["an unrecognised value", "not-a-key", "not a publishable key"],
    ["a JWT with another role", jwtWithRole("authenticated"), "not a publishable key"],
  ])("rejects %s as the publishable key", (_name, key, expected) => {
    const problems = problemsFor({ ...valid, VITE_SUPABASE_PUBLISHABLE_KEY: key });
    expect(problems.join(" ")).toContain(expected);
  });

  it("never puts a rejected key in the error", () => {
    const secret = "sb_secret_syntheticplaceholder";
    const problems = problemsFor({ ...valid, VITE_SUPABASE_PUBLISHABLE_KEY: secret });
    expect(problems.join(" ")).not.toContain(secret);
  });

  it.each([
    ["http://example-project.supabase.co", "https"],
    ["ftp://example-project.supabase.co", "https"],
    ["not a url", "must be a URL"],
    ["https://user:pass@example-project.supabase.co", "credentials"],
    ["https://example-project.supabase.co/auth/v1", "origin only"],
    ["https://example-project.supabase.co?x=1", "origin only"],
  ])("rejects the Supabase URL %s", (url, expected) => {
    expect(problemsFor({ ...valid, VITE_SUPABASE_URL: url }).join(" ")).toContain(expected);
  });

  it("allows plain http only for loopback", () => {
    for (const host of ["http://localhost:54321", "http://127.0.0.1:54321", "http://[::1]:54321"]) {
      expect(parseConfig({ ...valid, VITE_SUPABASE_URL: host }).supabaseUrl).toBe(host);
    }
    expect(problemsFor({ ...valid, VITE_SUPABASE_URL: "http://localhost.evil.example" }).join(" ")).toContain("https");
  });

  it("rejects an API URL with a path or without https", () => {
    expect(problemsFor({ ...valid, VITE_API_BASE_URL: "https://api.example.test/api" }).join(" ")).toContain(
      "origin only",
    );
    expect(problemsFor({ ...valid, VITE_API_BASE_URL: "http://api.example.test" }).join(" ")).toContain("https");
  });
});
