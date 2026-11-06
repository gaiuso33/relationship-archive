/** `GET /api/v1/me`. The user id is the only thing the API returns about the session. */
export interface MeResponse {
  user_id: string;
}

export type ApiErrorKind =
  | "unauthorized" // 401: the session is not valid
  | "session_changed" // 401 for a token that has since been replaced by a newer one
  | "unavailable" // 503: the API cannot verify tokens right now
  | "network" // the request never completed
  | "not_found" // 404
  | "invalid" // 422 or another 4xx the caller caused
  | "server" // other 5xx
  | "unexpected" // a response we cannot interpret
  | "aborted"; // cancelled by the caller

/**
 * One problem the API reported about a request field: where it is and what kind of problem it is.
 * The API never sends the submitted value, and the client keeps only short, well-formed entries.
 */
export interface ApiFieldError {
  loc: (string | number)[];
  type: string;
}

export type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";
