import type { ApiErrorKind, MeResponse } from "./types";

/** Fixed, user-safe text for each kind. Nothing from a response body is ever put in a message. */
const MESSAGES: Record<ApiErrorKind, string> = {
  unauthorized: "Your session has ended. Please sign in again.",
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

  constructor(kind: ApiErrorKind, details: { status?: number; code?: string | null; requestId?: string | null } = {}) {
    super(MESSAGES[kind]);
    this.name = "ApiError";
    this.kind = kind;
    this.status = details.status ?? null;
    this.code = details.code ?? null;
    this.requestId = details.requestId ?? null;
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

/** Only these API paths may be requested, so a bug cannot aim the token at another resource. */
const ALLOWED_PATH = /^\/api\/v1\/[A-Za-z0-9._~\-/]*$/;

const CODE_SHAPE = /^[a-z][a-z0-9_]{0,63}$/;
const REQUEST_ID_SHAPE = /^[A-Za-z0-9-]{1,64}$/;

/** Reads the documented error body `{"error": {"code", "request_id"}}`, keeping only safe fields. */
async function readErrorDetails(response: Response): Promise<{ code: string | null; requestId: string | null }> {
  try {
    const body: unknown = await response.json();
    const error = typeof body === "object" && body !== null ? (body as { error?: unknown }).error : null;
    if (typeof error !== "object" || error === null) return { code: null, requestId: null };
    const { code, request_id: requestId } = error as { code?: unknown; request_id?: unknown };
    return {
      code: typeof code === "string" && CODE_SHAPE.test(code) ? code : null,
      requestId: typeof requestId === "string" && REQUEST_ID_SHAPE.test(requestId) ? requestId : null,
    };
  } catch {
    return { code: null, requestId: null };
  }
}

export interface ApiClient {
  getMe(options?: RequestOptions): Promise<MeResponse>;
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const { baseUrl, getAccessToken, onUnauthorized } = options;
  const fetchImpl = options.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args));

  async function requestJson(path: string, { signal }: RequestOptions = {}): Promise<unknown> {
    if (!ALLOWED_PATH.test(path)) throw new ApiError("unexpected");
    const origin = baseUrl === "" ? globalThis.location.origin : baseUrl;
    const url = new URL(path, origin);
    // The token goes only to the configured API origin.
    if (url.origin !== origin) throw new ApiError("unexpected");

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
      response = await fetchImpl(url, {
        method: "GET",
        headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
        credentials: "omit",
        cache: "no-store",
        redirect: "error",
        signal,
      });
    } catch {
      if (signal?.aborted) throw new ApiError("aborted");
      throw new ApiError("network");
    }

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
      onUnauthorized();
      throw new ApiError("unauthorized", info);
    }
    if (response.status === 503) throw new ApiError("unavailable", info);
    if (response.status === 404) throw new ApiError("not_found", info);
    if (response.status >= 400 && response.status < 500) throw new ApiError("invalid", info);
    throw new ApiError("server", info);
  }

  return {
    async getMe(requestOptions) {
      const body = await requestJson("/api/v1/me", requestOptions);
      const userId = typeof body === "object" && body !== null ? (body as { user_id?: unknown }).user_id : undefined;
      if (typeof userId !== "string" || userId.length === 0) throw new ApiError("unexpected");
      return { user_id: userId };
    },
  };
}
