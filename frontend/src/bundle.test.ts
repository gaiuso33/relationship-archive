// @vitest-environment node
import { build } from "vite";
import { describe, expect, it } from "vitest";

type Env = Record<string, string | undefined>;
const processEnv = (globalThis as unknown as { process: { env: Env } }).process.env;

interface Chunk {
  type: string;
  code?: string;
  source?: string | Uint8Array;
}

describe("production build", () => {
  it("contains only public configuration and no secret or service-role key", async () => {
    const publishable = "sb_publishable_syntheticplaceholder";
    const canaries = {
      // Not VITE_ prefixed: Vite must not embed these in the bundle.
      SUPABASE_SERVICE_ROLE_KEY: "canary-service-role-value-7f3a",
      SUPABASE_SECRET_KEY: "canary-secret-key-value-9b1c",
      DATABASE_URL: "postgresql://canary-user:canary-password@db.example.test/canary",
      DEV_API_PROXY_TARGET: "http://canary-backend.internal:8000",
    };
    const settings: Env = {
      VITE_SUPABASE_URL: "https://synthetic-project.supabase.co",
      VITE_SUPABASE_PUBLISHABLE_KEY: publishable,
      VITE_API_BASE_URL: "/",
      ...canaries,
    };
    const previous: Env = {};
    for (const [key, value] of Object.entries(settings)) {
      previous[key] = processEnv[key];
      processEnv[key] = value;
    }
    let output: Chunk[];
    try {
      const result = await build({ logLevel: "silent", build: { write: false, minify: true } });
      const bundles = Array.isArray(result) ? result : [result];
      output = bundles.flatMap((bundle) => ("output" in bundle ? (bundle.output as Chunk[]) : []));
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete processEnv[key];
        else processEnv[key] = value;
      }
    }

    const text = output
      .map((chunk) => (chunk.type === "chunk" ? chunk.code : typeof chunk.source === "string" ? chunk.source : ""))
      .join("\n");
    expect(text.length).toBeGreaterThan(1000);

    // The public values are embedded, as intended...
    expect(text).toContain(publishable);
    expect(text).toContain("synthetic-project.supabase.co");
    // ...and nothing else from the environment is.
    for (const value of Object.values(canaries)) expect(text).not.toContain(value);
    // No secret-key shaped value and no service-role JWT, whatever its source.
    expect(text).not.toMatch(/sb_secret_[A-Za-z0-9_-]{8,}/);
    const jwts = text.match(/eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*/g) ?? [];
    for (const jwt of jwts) {
      expect(atob(jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).not.toContain("service_role");
    }
  }, 120_000);
});
