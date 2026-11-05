/**
 * Public frontend configuration, read from `VITE_*` variables.
 *
 * Everything here ends up in the browser bundle, so it must be public. The only Supabase value the
 * browser may hold is the project URL and the *publishable* key. A secret or service-role key is
 * rejected on purpose: it would give anyone who opens the site full access to the project.
 *
 * Error messages name the variable and the rule, never the value.
 */

export interface AppConfig {
  /** The Supabase project origin, for example `https://<project-ref>.supabase.co`. */
  supabaseUrl: string;
  /** The publishable (public) key. */
  supabasePublishableKey: string;
  /**
   * The origin of the API, or `""` for "the same origin as this page" (used with the Vite
   * development proxy). Never has a path or a trailing slash.
   */
  apiBaseUrl: string;
}

export class ConfigError extends Error {
  readonly problems: readonly string[];

  constructor(problems: readonly string[]) {
    super("The application is not configured correctly.");
    this.name = "ConfigError";
    this.problems = problems;
  }
}

export type RawEnv = Record<string, string | undefined>;

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** Parses an origin. `https` always; `http` only for loopback (local development). */
function parseOrigin(name: string, raw: string, problems: string[]): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    problems.push(`${name} must be a URL such as https://example.com.`);
    return null;
  }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && LOOPBACK_HOSTS.has(url.hostname))) {
    problems.push(`${name} must use https (http is allowed only for localhost).`);
    return null;
  }
  if (url.username || url.password) {
    problems.push(`${name} must not contain credentials.`);
    return null;
  }
  if ((url.pathname !== "/" && url.pathname !== "") || url.search || url.hash) {
    problems.push(`${name} must be an origin only, with no path, query or fragment.`);
    return null;
  }
  return url.origin;
}

/** Reads the `role` claim of a JWT-shaped key *without verifying it*. Only used to refuse a key. */
function jwtRole(key: string): string | null {
  const parts = key.split(".");
  if (parts.length !== 3) return null;
  try {
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64 + "=".repeat((4 - (base64.length % 4)) % 4);
    const payload: unknown = JSON.parse(atob(padded));
    if (typeof payload === "object" && payload !== null && "role" in payload) {
      const role = (payload as { role: unknown }).role;
      return typeof role === "string" ? role : null;
    }
  } catch {
    return null;
  }
  return null;
}

function checkPublishableKey(key: string, problems: string[]): boolean {
  const name = "VITE_SUPABASE_PUBLISHABLE_KEY";
  if (key.startsWith("sb_secret_")) {
    problems.push(`${name} is a secret key. Use the publishable key; never put a secret key in the frontend.`);
    return false;
  }
  if (key.startsWith("sb_publishable_")) return true;
  const role = jwtRole(key);
  if (role === "service_role") {
    problems.push(`${name} is a service-role key. Use the publishable key; never put it in the frontend.`);
    return false;
  }
  if (role === "anon") return true; // The older public "anon" key format.
  problems.push(`${name} is not a publishable key.`);
  return false;
}

export function parseConfig(env: RawEnv): AppConfig {
  const problems: string[] = [];
  const read = (name: string): string => (env[name] ?? "").trim();

  const rawUrl = read("VITE_SUPABASE_URL");
  let supabaseUrl: string | null = null;
  if (!rawUrl) problems.push("VITE_SUPABASE_URL is required.");
  else supabaseUrl = parseOrigin("VITE_SUPABASE_URL", rawUrl, problems);

  const key = read("VITE_SUPABASE_PUBLISHABLE_KEY");
  let keyOk = false;
  if (!key) problems.push("VITE_SUPABASE_PUBLISHABLE_KEY is required.");
  else keyOk = checkPublishableKey(key, problems);

  const rawApi = read("VITE_API_BASE_URL");
  let apiBaseUrl: string | null = null;
  if (!rawApi) problems.push('VITE_API_BASE_URL is required (use "/" with the development proxy).');
  else if (rawApi === "/") apiBaseUrl = "";
  else apiBaseUrl = parseOrigin("VITE_API_BASE_URL", rawApi, problems);

  if (problems.length > 0 || supabaseUrl === null || !keyOk || apiBaseUrl === null) {
    throw new ConfigError(problems);
  }
  return { supabaseUrl, supabasePublishableKey: key, apiBaseUrl };
}

/**
 * Reads the configuration Vite embedded at build time.
 *
 * Each variable is named explicitly. Vite replaces `import.meta.env.VITE_NAME` with just that
 * value, but a bare `import.meta.env` would embed *every* `VITE_*` variable present at build
 * time, including one added by mistake. Do not pass or spread the whole object.
 */
export function loadConfig(
  env: RawEnv = {
    VITE_SUPABASE_URL: import.meta.env.VITE_SUPABASE_URL,
    VITE_SUPABASE_PUBLISHABLE_KEY: import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
    VITE_API_BASE_URL: import.meta.env.VITE_API_BASE_URL,
  },
): AppConfig {
  return parseConfig(env);
}
