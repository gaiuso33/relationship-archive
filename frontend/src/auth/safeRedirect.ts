const MAX_LENGTH = 2048;
const SIGN_IN_PATH = "/sign-in";

/**
 * Returns `value` if it is a safe in-application path, otherwise `/`.
 *
 * Safe means one leading `/` followed by something other than `/` or `\` (browsers treat both as
 * "another site"), with no control characters (browsers delete tabs and line breaks inside URLs,
 * so `/\t/evil.example` would become `//evil.example`), and nothing that resolves to another
 * origin. The sign-in page itself is refused to avoid a redirect loop.
 */
export function safeInternalPath(value: string | null | undefined): string {
  if (typeof value !== "string" || value.length === 0 || value.length > MAX_LENGTH) return "/";
  if (!value.startsWith("/") || value.startsWith("//")) return "/";
  if (value.includes("\\")) return "/";
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f\s]/.test(value)) return "/";
  let resolved: URL;
  try {
    resolved = new URL(value, "https://app.invalid");
  } catch {
    return "/";
  }
  if (resolved.origin !== "https://app.invalid") return "/";
  if (resolved.pathname === SIGN_IN_PATH || resolved.pathname.startsWith(`${SIGN_IN_PATH}/`)) return "/";
  return value;
}
