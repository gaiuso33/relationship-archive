import type { ApiErrorKind, ApiFieldError, HttpMethod, MeResponse } from "./types";

/** Fixed, user-safe text for each kind. Nothing from a response body is ever put in a message. */
const MESSAGES: Record<ApiErrorKind, string> = {
  unauthorized: "Your session has ended. Please sign in again.",
  session_changed: "Your session was just refreshed. Please try again.",
  unavailable: "The service is temporarily unavailable. Please try again in a moment.",
  network: "We could not reach the service. Check your connection and try again.",
  not_found: "That could not be found.",
  invalid: "The request was not accepted.",
  server: "Something went wrong on our side. Please try again.",
  unexpected: "The service sent an unexpected response.",
  aborted: "The request was cancelled.",
};

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** The API's own error code (for example `not_found`), only if it has the expected shape. */
  readonly code: string | null;
  /** The support reference from the API, only if it has the expected shape. */
  readonly requestId: string | null;
  /** Where the API says a request was wrong (422), as short validated entries. Empty otherwise. */
  readonly fields: readonly ApiFieldError[];

  constructor(
    kind: ApiErrorKind,
    details: { status?: number; code?: string | null; requestId?: string | null; fields?: readonly ApiFieldError[] } = {},
  ) {
    super(MESSAGES[kind]);
    this.name = "ApiError";
    this.kind = kind;
    this.status = details.status ?? null;
    this.code = details.code ?? null;
    this.requestId = details.requestId ?? null;
    this.fields = details.fields ?? [];
  }
}

export interface ApiClientOptions {
  /** The API origin, or `""` for the page's own origin (development proxy). */
  baseUrl: string;
  /** Supplies the access token. `null` means there is no session. */
  getAccessToken: () => Promise<string | null>;
  /** Called when the API rejects the session (401). The client never retries. */
  onUnauthorized: () => void;
  fetchImpl?: typeof fetch;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

/** One authenticated call. `path` is checked as described below; `query` and `body` are separate so neither can alter it. */
export interface JsonRequest {
  method: HttpMethod;
  path: string;
  /** Non-negative whole numbers only (such as `limit` and `offset`), under simple lower-case names. */
  query?: Record<string, number>;
  /** Sent as JSON. Only `POST` and `PATCH` may have one. */
  body?: unknown;
  signal?: AbortSignal;
}

const METHODS: ReadonlySet<string> = new Set(["GET", "POST", "PATCH", "DELETE"]);
const BODY_METHODS: ReadonlySet<string> = new Set(["POST", "PATCH"]);
const QUERY_NAME = /^[a-z][a-z_]{0,31}$/;
const MAX_QUERY_PARAMETERS = 8;
const MAX_BODY_LENGTH = 256 * 1024;

/** The query string for `query`, or `null` if anything in it is not a plain non-negative integer under a plain name. */
function buildQuery(query: Record<string, number> | undefined): string | null {
  if (query === undefined) return "";
  const entries = Object.entries(query);
  if (entries.length > MAX_QUERY_PARAMETERS) return null;
  const params = new URLSearchParams();
  for (const [name, value] of entries) {
    if (!QUERY_NAME.test(name) || typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) return null;
    params.set(name, String(value));
  }
  const text = params.toString();
  return text === "" ? "" : `?${text}`;
}

/** The JSON text to send, or `null` if there is no body, or `false` if the body cannot be sent. */
function encodeBody(method: string, body: unknown): string | null | false {
  if (body === undefined) return null;
  if (!BODY_METHODS.has(method)) return false;
  try {
    const text = JSON.stringify(body);
    return typeof text === "string" && text.length <= MAX_BODY_LENGTH ? text : false;
  } catch {
    return false;
  }
}

/**
 * Only plain `/api/v1/...` paths may be requested, so a bug cannot aim the token at another
 * resource. Every segment starts with a letter, digit, `_`, `~` or `-`, which rules out `.` and
 * `..` (and any hidden-file style name). `%` is not allowed at all, so no encoded form of a dot
 * segment (`%2e`, `%2E%2e`, `%252e`) or of a slash can get through; neither can `\`, `?`, `#`,
 * an empty segment (`//`) or a trailing slash.
 */
const ALLOWED_PATH = /^\/api\/v1\/[A-Za-z0-9_~-][A-Za-z0-9._~-]*(?:\/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)*$/;

export function isAllowedApiPath(path: string): boolean {
  return ALLOWED_PATH.test(path);
}

const CODE_SHAPE = /^[a-z][a-z0-9_]{0,63}$/;
const REQUEST_ID_SHAPE = /^[A-Za-z0-9-]{1,64}$/;
const FIELD_NAME_SHAPE = /^[A-Za-z0-9_<> -]{1,64}$/;
const FIELD_TYPE_SHAPE = /^[a-z0-9_.]{1,64}$/;
const MAX_FIELD_ERRORS = 50;
const MAX_LOC_LENGTH = 6;

/** Keeps only well-formed `{loc, type}` entries. Anything else is dropped, never repaired or shown. */
function readFieldErrors(value: unknown): ApiFieldError[] {
  if (!Array.isArray(value)) return [];
  const result: ApiFieldError[] = [];
  for (const entry of value.slice(0, MAX_FIELD_ERRORS)) {
    if (typeof entry !== "object" || entry === null) continue;
    const { loc, type } = entry as { loc?: unknown; type?: unknown };
    if (!Array.isArray(loc) || loc.length === 0 || loc.length > MAX_LOC_LENGTH) continue;
    if (typeof type !== "string" || !FIELD_TYPE_SHAPE.test(type)) continue;
    const parts: (string | number)[] = [];
    for (const part of loc) {
      if (typeof part === "string" && FIELD_NAME_SHAPE.test(part)) parts.push(part);
      else if (typeof part === "number" && Number.isSafeInteger(part) && part >= 0 && part <= 100_000) parts.push(part);
    }
    if (parts.length === loc.length) result.push({ loc: parts, type });
  }
  return result;
}

interface ErrorDetails {
  code: string | null;
  requestId: string | null;
  fields: ApiFieldError[];
}

/** Reads the documented error body `{"error": {"code", "request_id", "fields"}}`, keeping only safe fields. */
async function readErrorDetails(response: Response): Promise<ErrorDetails> {
  const none: ErrorDetails = { code: null, requestId: null, fields: [] };
  try {
    const body: unknown = await response.json();
    const error = typeof body === "object" && body !== null ? (body as { error?: unknown }).error : null;
    if (typeof error !== "object" || error === null) return none;
    const { code, request_id: requestId, fields } = error as { code?: unknown; request_id?: unknown; fields?: unknown };
    return {
      code: typeof code === "string" && CODE_SHAPE.test(code) ? code : null,
      requestId: typeof requestId === "string" && REQUEST_ID_SHAPE.test(requestId) ? requestId : null,
      fields: readFieldErrors(fields),
    };
  } catch {
    return none;
  }
}

export interface ApiClient {
  /**
   * One authenticated call, returning the parsed JSON, or `undefined` for `204 No Content`. Every
   * call goes through the same checks: an allowed path, the configured origin, a plain query, a
   * JSON body only where one belongs, and the stale-token rule for a 401. Nothing is retried.
   */
  request(request: JsonRequest): Promise<unknown>;
  /** Authenticated `GET` of a `/api/v1/...` path, returning the parsed JSON. */
  getJson(path: string, options?: RequestOptions & { query?: Record<string, number> }): Promise<unknown>;
  getMe(options?: RequestOptions): Promise<MeResponse>;
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const { baseUrl, getAccessToken, onUnauthorized } = options;
  const fetchImpl = options.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));

  /** True only if there is a current token and it differs from the one that was sent. */
  async function tokenWasReplaced(sent: string): Promise<boolean> {
    try {
      const current = await getAccessToken();
      return current !== null && current !== sent;
    } catch {
      return false;
    }
  }

  async function requestJson({ method, path, query, body, signal }: JsonRequest): Promise<unknown> {
    // Everything is checked before anything else: a rejected request never reaches the token
    // provider or `fetch`.
    if (!METHODS.has(method)) throw new ApiError("unexpected");
    if (!isAllowedApiPath(path)) throw new ApiError("unexpected");
    const queryText = buildQuery(query);
    if (queryText === null) throw new ApiError("unexpected");
    const payload = encodeBody(method, body);
    if (payload === false) throw new ApiError("unexpected");
    const origin = baseUrl === "" ? globalThis.location.origin : baseUrl;
    const url = new URL(`${path}${queryText}`, origin);
    // The token goes only to the configured API origin, and the URL must be exactly the path and
    // query that were checked (no normalisation may have changed them).
    if (url.origin !== origin || url.pathname !== path || url.search !== queryText || url.hash !== "") {
      throw new ApiError("unexpected");
    }

    let token: string | null;
    try {
      token = await getAccessToken();
    } catch {
      throw new ApiError("unavailable");
    }
    if (token === null) {
      // No session, so nothing to send. Same outcome as the API saying 401.
      onUnauthorized();
      throw new ApiError("unauthorized", { status: 401 });
    }

    let response: Response;
    try {
      const headers: Record<string, string> = { Accept: "application/json", Authorization: `Bearer ${token}` };
      if (payload !== null) headers["Content-Type"] = "application/json";
      response = await fetchImpl(url, {
        method,
        headers,
        body: payload ?? undefined,
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        signal,
      });
    } catch {
      if (signal?.aborted) throw new ApiError("aborted");
      throw new ApiError("network");
    }

    if (response.status === 204) return undefined;
    if (response.ok) {
      try {
        return await response.json();
      } catch {
        if (signal?.aborted) throw new ApiError("aborted");
        throw new ApiError("unexpected", { status: response.status });
      }
    }

    const details = await readErrorDetails(response);
    const info = { status: response.status, ...details };
    if (response.status === 401) {
      // The API refused the token we sent. If the session has since been refreshed, the newer
      // token is not the one that was refused, so the session is not ended. The caller may try
      // again; this client never retries by itself.
      if (await tokenWasReplaced(token)) throw new ApiError("session_changed", info);
      onUnauthorized();
      throw new ApiError("unauthorized", info);
    }
    if (response.status === 503) throw new ApiError("unavailable", info);
    if (response.status === 404) throw new ApiError("not_found", info);
    if (response.status >= 400 && response.status < 500) throw new ApiError("invalid", info);
    throw new ApiError("server", info);
  }

  return {
    request: requestJson,
    getJson: (path, options = {}) => requestJson({ method: "GET", path, ...options }),
    async getMe(requestOptions) {
      const body = await requestJson({ method: "GET", path: "/api/v1/me", ...requestOptions });
      const userId = typeof body === "object" && body !== null ? (body as { user_id?: unknown }).user_id : undefined;
      if (typeof userId !== "string" || userId.length === 0) throw new ApiError("unexpected");
      return { user_id: userId };
    },
  };
}
