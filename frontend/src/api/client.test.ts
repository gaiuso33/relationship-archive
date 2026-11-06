import { describe, expect, it, vi } from "vitest";

import { ApiError, createApiClient, isAllowedApiPath } from "./client";

const BASE = "https://api.example.test";
const TOKEN = "synthetic-access-token-abc123";
const USER_ID = "11111111-1111-4111-8111-111111111111";

function json(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function setup(options: { response?: Response | (() => Response); token?: string | null; baseUrl?: string } = {}) {
  const fetchImpl = vi.fn(async () => {
    const response = options.response ?? json(200, { user_id: USER_ID });
    return typeof response === "function" ? response() : response;
  });
  const onUnauthorized = vi.fn();
  const client = createApiClient({
    baseUrl: options.baseUrl ?? BASE,
    getAccessToken: async () => (options.token === undefined ? TOKEN : options.token),
    onUnauthorized,
    fetchImpl: fetchImpl as unknown as typeof fetch,
  });
  return { client, fetchImpl, onUnauthorized };
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the request to fail");
}

describe("api client", () => {
  it("sends the bearer token to the configured API origin only", async () => {
    const { client, fetchImpl } = setup();
    await expect(client.getMe()).resolves.toEqual({ user_id: USER_ID });

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.href).toBe(`${BASE}/api/v1/me`);
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    // The token is in the header and nowhere else.
    expect(url.href).not.toContain(TOKEN);
    expect(init.credentials).toBe("omit");
    expect(init.redirect).toBe("error");
    expect(init.cache).toBe("no-store");
  });

  it("uses the page's own origin when the base URL is empty (development proxy)", async () => {
    const { client, fetchImpl } = setup({ baseUrl: "" });
    await client.getMe();
    const [url] = fetchImpl.mock.calls[0] as unknown as [URL];
    expect(url.origin).toBe(globalThis.location.origin);
    expect(url.pathname).toBe("/api/v1/me");
  });

  it("only ever requests the API's own /api/v1 paths on the configured origin", async () => {
    const { client, fetchImpl } = setup();
    await client.getMe();
    await client.getMe();
    expect(fetchImpl.mock.calls.length).toBe(2);
    for (const [url] of fetchImpl.mock.calls as unknown as [URL][]) {
      expect(url.origin).toBe(BASE);
      expect(url.pathname.startsWith("/api/v1/")).toBe(true);
      expect(url.search).toBe("");
      expect(url.username + url.password).toBe("");
    }
  });

  it("does not call the API at all when there is no session", async () => {
    const { client, fetchImpl, onUnauthorized } = setup({ token: null });
    const error = await failure(client.getMe());
    expect(error.kind).toBe("unauthorized");
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("on 401 ends the session locally once and does not retry", async () => {
    const { client, fetchImpl, onUnauthorized } = setup({
      response: () => json(401, { error: { code: "unauthorized", message: "Unauthorized", request_id: "req-1" } }),
    });
    const error = await failure(client.getMe());
    expect(error.kind).toBe("unauthorized");
    expect(error.status).toBe(401);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  describe("401 and a refreshed session", () => {
    function withTokens(tokens: (string | null)[], status = 401) {
      let call = 0;
      const getAccessToken = vi.fn(async () => tokens[Math.min(call++, tokens.length - 1)]);
      const fetchImpl = vi.fn(async () => json(status, { error: { code: "unauthorized", request_id: "req-3" } }));
      const onUnauthorized = vi.fn();
      const client = createApiClient({
        baseUrl: BASE,
        getAccessToken,
        onUnauthorized,
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });
      return { client, fetchImpl, onUnauthorized, getAccessToken };
    }

    it("does not end the session when the token that was refused has been replaced", async () => {
      const { client, fetchImpl, onUnauthorized } = withTokens(["old-token", "new-token"]);
      const error = await failure(client.getMe());
      expect(error.kind).toBe("session_changed");
      expect(error.message).not.toContain("token");
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(fetchImpl).toHaveBeenCalledTimes(1); // and no automatic retry
    });

    it("ends the session when the token that was refused is still the current one", async () => {
      const { client, fetchImpl, onUnauthorized } = withTokens(["same-token", "same-token"]);
      expect((await failure(client.getMe())).kind).toBe("unauthorized");
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("ends the session when there is no session any more", async () => {
      const { client, onUnauthorized } = withTokens(["old-token", null]);
      expect((await failure(client.getMe())).kind).toBe("unauthorized");
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
    });

    it("ends the session when the current token cannot be read", async () => {
      let call = 0;
      const onUnauthorized = vi.fn();
      const client = createApiClient({
        baseUrl: BASE,
        getAccessToken: async () => {
          if (call++ === 0) return "old-token";
          throw new Error("storage unavailable");
        },
        onUnauthorized,
        fetchImpl: (async () => json(401, {})) as unknown as typeof fetch,
      });
      expect((await failure(client.getMe())).kind).toBe("unauthorized");
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
    });
  });

  it("on 503 reports the service as unavailable and keeps the session", async () => {
    const { client, onUnauthorized } = setup({
      response: () => json(503, { error: { code: "service_unavailable", message: "x", request_id: "req-2" } }),
    });
    const error = await failure(client.getMe());
    expect(error.kind).toBe("unavailable");
    expect(error.requestId).toBe("req-2");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("on a network failure reports it and keeps the session", async () => {
    const onUnauthorized = vi.fn();
    const client = createApiClient({
      baseUrl: BASE,
      getAccessToken: async () => TOKEN,
      onUnauthorized,
      fetchImpl: (async () => {
        throw new TypeError(`Failed to fetch ${BASE} with ${TOKEN}`);
      }) as unknown as typeof fetch,
    });
    const error = await failure(client.getMe());
    expect(error.kind).toBe("network");
    expect(error.message).not.toContain(TOKEN);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it.each([
    [404, "not_found"],
    [422, "invalid"],
    [429, "invalid"],
    [500, "server"],
    [502, "server"],
  ])("maps %s to %s", async (status, kind) => {
    const { client, onUnauthorized } = setup({ response: () => json(status, {}) });
    expect((await failure(client.getMe())).kind).toBe(kind);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("never echoes a response body, a message or a token in the error", async () => {
    const leak = "SECRET-NOTE-TEXT and the token " + TOKEN;
    const { client } = setup({
      response: () =>
        json(500, { error: { code: "Not A Code!", message: leak, request_id: "bad id with spaces" }, detail: leak }),
    });
    const error = await failure(client.getMe());
    const everything = JSON.stringify({ message: error.message, code: error.code, requestId: error.requestId });
    expect(everything).not.toContain("SECRET-NOTE-TEXT");
    expect(everything).not.toContain(TOKEN);
    expect(error.code).toBeNull();
    expect(error.requestId).toBeNull();
  });

  it("copes with an error body that is not JSON", async () => {
    const { client } = setup({ response: () => new Response("<html>oops</html>", { status: 500 }) });
    const error = await failure(client.getMe());
    expect(error.kind).toBe("server");
    expect(error.message).not.toContain("html");
  });

  it.each([
    ["a body that is not JSON", () => new Response("nope", { status: 200 })],
    ["a body without a user id", () => json(200, {})],
    ["a user id of the wrong type", () => json(200, { user_id: 7 })],
    ["an empty user id", () => json(200, { user_id: "" })],
  ])("rejects %s as unexpected", async (_name, response) => {
    const { client } = setup({ response });
    expect((await failure(client.getMe())).kind).toBe("unexpected");
  });

  it("supports cancellation", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn(
      (_url: URL, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const onUnauthorized = vi.fn();
    const client = createApiClient({
      baseUrl: BASE,
      getAccessToken: async () => TOKEN,
      onUnauthorized,
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    const pending = client.getMe({ signal: controller.signal });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    controller.abort();
    expect((await failure(pending)).kind).toBe("aborted");
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("reports an unreadable token source as unavailable, without calling the API", async () => {
    const fetchImpl = vi.fn();
    const client = createApiClient({
      baseUrl: BASE,
      getAccessToken: async () => {
        throw new Error("storage is broken");
      },
      onUnauthorized: vi.fn(),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });
    expect((await failure(client.getMe())).kind).toBe("unavailable");
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  describe("path restrictions", () => {
    const rejected = [
      "/api/v1/../admin",
      "/api/v1/./me",
      "/api/v1/..",
      "/api/v1/.",
      "/api/v1/people/../../admin",
      "/api/v1/%2e%2e/admin",
      "/api/v1/%2E%2E/admin",
      "/api/v1/%2e%2E/admin",
      "/api/v1/.%2e/admin",
      "/api/v1/%2e./admin",
      "/api/v1/%252e%252e/admin",
      "/api/v1/..%2fadmin",
      "/api/v1/%2e%2e%2fadmin",
      "/api/v1/..%5cadmin",
      "/api/v1/..\\admin",
      "/api/v1/people/%2f/x",
      "/api/v1/.hidden",
      "/api/v1/me?x=1",
      "/api/v1/me#frag",
      "/api/v1/me/",
      "/api/v1//me",
      "/api/v1/",
      "/api/v1",
      "/api/v2/me",
      "/api/v10/me",
      "/api",
      "/admin",
      "//evil.example/api/v1/me",
      "https://evil.example/api/v1/me",
      "api/v1/me",
      "/api/v1/me ",
      "/api/v1/m e",
      "/api/v1/me\n",
      "/api/v1/\u2025/x",
      "",
    ];

    it.each(rejected)("rejects %j before the token is read or anything is sent", async (path) => {
      const getAccessToken = vi.fn(async () => TOKEN);
      const fetchImpl = vi.fn();
      const onUnauthorized = vi.fn();
      const client = createApiClient({
        baseUrl: BASE,
        getAccessToken,
        onUnauthorized,
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });
      expect((await failure(client.getJson(path))).kind).toBe("unexpected");
      expect(getAccessToken).not.toHaveBeenCalled();
      expect(fetchImpl).not.toHaveBeenCalled();
      expect(onUnauthorized).not.toHaveBeenCalled();
    });

    it.each(["/api/v1/me", "/api/v1/people", "/api/v1/people/11111111-1111-4111-8111-111111111111/notes", "/api/v1/a.b/c_d~e-f"])(
      "accepts the plain path %s",
      async (path) => {
        const { client, fetchImpl } = setup({ response: () => json(200, { ok: true }) });
        await expect(client.getJson(path)).resolves.toEqual({ ok: true });
        const [url] = fetchImpl.mock.calls[0] as unknown as [URL];
        expect(url.pathname).toBe(path);
        expect(url.origin).toBe(BASE);
      },
    );

    it("agrees with the guard that the helper reports", () => {
      for (const path of rejected) expect(isAllowedApiPath(path)).toBe(false);
      expect(isAllowedApiPath("/api/v1/me")).toBe(true);
    });
  });

  describe("methods, bodies and 204", () => {
    const PERSON_PATH = "/api/v1/people/11111111-1111-4111-8111-111111111111";

    function call(init: Parameters<ReturnType<typeof createApiClient>["request"]>[0], response?: Response | (() => Response)) {
      const ctx = setup({ response: response ?? (() => json(200, { ok: true })) });
      return { ...ctx, result: ctx.client.request(init) };
    }

    function lastCall(fetchImpl: ReturnType<typeof vi.fn>) {
      const [url, init] = fetchImpl.mock.calls.at(-1) as unknown as [URL, RequestInit];
      return { url, init, headers: init.headers as Record<string, string> };
    }

    it("sends a JSON body with the right method and content type for POST and PATCH", async () => {
      for (const method of ["POST", "PATCH"] as const) {
        const { result, fetchImpl } = call({ method, path: "/api/v1/people", body: { display_name: "Ada Example" } });
        await expect(result).resolves.toEqual({ ok: true });
        const { url, init, headers } = lastCall(fetchImpl);
        expect(init.method).toBe(method);
        expect(init.body).toBe('{"display_name":"Ada Example"}');
        expect(headers["Content-Type"]).toBe("application/json");
        expect(headers.Authorization).toBe(`Bearer ${TOKEN}`);
        expect(url.origin).toBe(BASE);
        expect(init.credentials).toBe("omit");
        expect(init.redirect).toBe("error");
      }
    });

    it("sends no body and no content type for GET and DELETE", async () => {
      for (const method of ["GET", "DELETE"] as const) {
        const { result, fetchImpl } = call({ method, path: PERSON_PATH });
        await result;
        const { init, headers } = lastCall(fetchImpl);
        expect(init.method).toBe(method);
        expect(init.body).toBeUndefined();
        expect(headers["Content-Type"]).toBeUndefined();
      }
    });

    it("returns undefined for 204 No Content, and does not try to read a body", async () => {
      const { result, onUnauthorized } = call({ method: "DELETE", path: PERSON_PATH }, () => new Response(null, { status: 204 }));
      await expect(result).resolves.toBeUndefined();
      expect(onUnauthorized).not.toHaveBeenCalled();
    });

    it("still treats an unreadable 200 body as unexpected", async () => {
      const { result } = call({ method: "POST", path: "/api/v1/people", body: {} }, () => new Response("nope", { status: 201 }));
      expect((await failure(result)).kind).toBe("unexpected");
    });

    it("accepts 201 with a JSON body", async () => {
      const { result } = call({ method: "POST", path: "/api/v1/people", body: {} }, () => json(201, { id: "x" }));
      await expect(result).resolves.toEqual({ id: "x" });
    });

    async function neverSent(init: Parameters<ReturnType<typeof createApiClient>["request"]>[0]) {
      const getAccessToken = vi.fn(async () => TOKEN);
      const fetchImpl = vi.fn();
      const onUnauthorized = vi.fn();
      const client = createApiClient({ baseUrl: BASE, getAccessToken, onUnauthorized, fetchImpl: fetchImpl as unknown as typeof fetch });
      expect((await failure(client.request(init))).kind).toBe("unexpected");
      expect(getAccessToken).not.toHaveBeenCalled();
      expect(fetchImpl).not.toHaveBeenCalled();
      expect(onUnauthorized).not.toHaveBeenCalled();
    }

    it.each(["PUT", "TRACE", "OPTIONS", "HEAD", "get", "post", "CONNECT", ""])("refuses the method %j before the token is read", async (method) => {
      await neverSent({ method: method as "GET", path: "/api/v1/me" });
    });

    it.each(["GET", "DELETE"] as const)("refuses a body on %s", async (method) => {
      await neverSent({ method, path: PERSON_PATH, body: { a: 1 } });
    });

    it("refuses a body that cannot be turned into JSON, or is too large", async () => {
      const circular: Record<string, unknown> = {};
      circular.self = circular;
      await neverSent({ method: "POST", path: "/api/v1/people", body: circular });
      await neverSent({ method: "POST", path: "/api/v1/people", body: { n: BigInt(1) } });
      await neverSent({ method: "POST", path: "/api/v1/people", body: () => 1 });
      await neverSent({ method: "POST", path: "/api/v1/people", body: { text: "x".repeat(300 * 1024) } });
    });

    it("applies the path rules to every method", async () => {
      for (const method of ["GET", "POST", "PATCH", "DELETE"] as const) {
        await neverSent({ method, path: "/api/v1/../admin" });
        await neverSent({ method, path: "/api/v1/%2e%2e/admin" });
        await neverSent({ method, path: "https://evil.example/api/v1/me" });
        await neverSent({ method, path: "/api/v1/people?x=1" });
      }
    });
  });

  describe("query parameters", () => {
    it("adds plain non-negative integers after the checked path", async () => {
      const { client, fetchImpl } = setup({ response: () => json(200, { items: [] }) });
      await client.getJson("/api/v1/people/11111111-1111-4111-8111-111111111111/notes", { query: { limit: 20, offset: 40 } });
      const [url] = fetchImpl.mock.calls[0] as unknown as [URL];
      expect(url.pathname).toBe("/api/v1/people/11111111-1111-4111-8111-111111111111/notes");
      expect(url.search).toBe("?limit=20&offset=40");
      expect(url.origin).toBe(BASE);
    });

    it("sends no query string when there is none, or an empty one", async () => {
      const { client, fetchImpl } = setup();
      await client.getJson("/api/v1/me", { query: {} });
      expect((fetchImpl.mock.calls[0] as unknown as [URL])[0].search).toBe("");
    });

    const bad: [string, Record<string, number>][] = [
      ["a negative number", { offset: -1 }],
      ["a fraction", { limit: 1.5 }],
      ["NaN", { limit: Number.NaN }],
      ["Infinity", { limit: Number.POSITIVE_INFINITY }],
      ["an unsafe integer", { offset: 2 ** 53 }],
      ["a string that looks like a number", { limit: "5" as unknown as number }],
      ["an array", { limit: [1, 2] as unknown as number }],
      ["an object", { limit: { a: 1 } as unknown as number }],
      ["a name that injects another parameter", { "a=1&b": 1 }],
      ["a name with a space", { "limit ": 1 }],
      ["an upper-case name", { Limit: 1 }],
      ["a name starting with an underscore", { __proto__x: 1 }],
      ["an empty name", { "": 1 }],
      ["an over-long name", { [`a${"b".repeat(40)}`]: 1 }],
      ["too many parameters", Object.fromEntries(Array.from({ length: 9 }, (_, i) => [`p${String.fromCharCode(97 + i)}`, i]))],
    ];

    it.each(bad)("refuses %s before the token is read", async (_name, query) => {
      const getAccessToken = vi.fn(async () => TOKEN);
      const fetchImpl = vi.fn();
      const client = createApiClient({
        baseUrl: BASE,
        getAccessToken,
        onUnauthorized: vi.fn(),
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });
      expect((await failure(client.getJson("/api/v1/people", { query }))).kind).toBe("unexpected");
      expect(getAccessToken).not.toHaveBeenCalled();
      expect(fetchImpl).not.toHaveBeenCalled();
    });

    it("cannot be used to smuggle a query through the path", async () => {
      const fetchImpl = vi.fn();
      const client = createApiClient({
        baseUrl: BASE,
        getAccessToken: async () => TOKEN,
        onUnauthorized: vi.fn(),
        fetchImpl: fetchImpl as unknown as typeof fetch,
      });
      expect((await failure(client.getJson("/api/v1/people?limit=5"))).kind).toBe("unexpected");
      expect(fetchImpl).not.toHaveBeenCalled();
    });
  });

  describe("422 field errors", () => {
    function withFields(fields: unknown) {
      return setup({ response: () => json(422, { error: { code: "validation_error", message: "SECRET-MESSAGE", request_id: "req-7", fields } }) });
    }

    it("keeps well-formed locations and types", async () => {
      const { client } = withFields([
        { loc: ["body", "display_name"], type: "string_too_long" },
        { loc: ["body", "interests", 3], type: "value_error" },
        { loc: ["body", "<unknown field>"], type: "extra_forbidden" },
      ]);
      const error = await failure(client.request({ method: "POST", path: "/api/v1/people", body: {} }));
      expect(error.kind).toBe("invalid");
      expect(error.status).toBe(422);
      expect(error.code).toBe("validation_error");
      expect(error.requestId).toBe("req-7");
      expect(error.fields).toEqual([
        { loc: ["body", "display_name"], type: "string_too_long" },
        { loc: ["body", "interests", 3], type: "value_error" },
        { loc: ["body", "<unknown field>"], type: "extra_forbidden" },
      ]);
    });

    it("drops malformed entries instead of repairing or showing them", async () => {
      const { client } = withFields([
        { loc: ["body", "ok"], type: "fine" },
        { loc: ["body", "has spaces and \"quotes\" and a very long invented name that is not a field"], type: "fine" },
        { loc: ["body", "x"], type: "Not A Type!" },
        { loc: [], type: "fine" },
        { loc: ["a", "b", "c", "d", "e", "f", "g"], type: "fine" },
        { loc: ["body", -1], type: "fine" },
        { loc: ["body", 1.5], type: "fine" },
        { loc: ["body", {}], type: "fine" },
        { loc: "body", type: "fine" },
        { loc: ["body", "input"], type: 5 },
        { type: "fine" },
        "text",
        null,
        { loc: ["body", "name"], type: "fine", input: "SECRET-NOTE-TEXT", msg: "SECRET-MESSAGE" },
      ]);
      const error = await failure(client.request({ method: "POST", path: "/api/v1/people", body: {} }));
      expect(error.fields).toEqual([
        { loc: ["body", "ok"], type: "fine" },
        { loc: ["body", "name"], type: "fine" },
      ]);
      const everything = JSON.stringify({ message: error.message, fields: error.fields });
      expect(everything).not.toContain("SECRET");
    });

    it("limits how many entries are kept, and ignores a non-list", async () => {
      const many = Array.from({ length: 80 }, (_, i) => ({ loc: ["body", `f${i}`], type: "fine" }));
      expect((await failure(withFields(many).client.request({ method: "POST", path: "/api/v1/people", body: {} }))).fields).toHaveLength(50);
      expect((await failure(withFields("nope").client.request({ method: "POST", path: "/api/v1/people", body: {} }))).fields).toEqual([]);
      expect((await failure(withFields(undefined).client.request({ method: "POST", path: "/api/v1/people", body: {} }))).fields).toEqual([]);
    });

    it("has no fields on other errors", async () => {
      const { client } = setup({ response: () => json(404, { error: { code: "not_found", request_id: "r-1" } }) });
      expect((await failure(client.getJson("/api/v1/people/11111111-1111-4111-8111-111111111111"))).fields).toEqual([]);
    });
  });

  describe("the #11 protections hold for writes", () => {
    const WRITE = { method: "POST", path: "/api/v1/people/11111111-1111-4111-8111-111111111111/notes", body: { content: "Synthetic note" } } as const;

    function withTokens(tokens: (string | null)[], response: () => Response) {
      let call = 0;
      const getAccessToken = vi.fn(async () => tokens[Math.min(call++, tokens.length - 1)]);
      const fetchImpl = vi.fn(async () => response());
      const onUnauthorized = vi.fn();
      const client = createApiClient({ baseUrl: BASE, getAccessToken, onUnauthorized, fetchImpl: fetchImpl as unknown as typeof fetch });
      return { client, fetchImpl, onUnauthorized };
    }

    it("a 401 for a token that was refreshed meanwhile does not end the session, and is not retried", async () => {
      const { client, fetchImpl, onUnauthorized } = withTokens(["old-token", "new-token"], () => json(401, {}));
      expect((await failure(client.request(WRITE))).kind).toBe("session_changed");
      expect(onUnauthorized).not.toHaveBeenCalled();
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("a 401 for the current token ends the session once, and is not retried", async () => {
      const { client, fetchImpl, onUnauthorized } = withTokens(["same", "same"], () => json(401, {}));
      expect((await failure(client.request(WRITE))).kind).toBe("unauthorized");
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("does not call the API for a write when there is no session", async () => {
      const { client, fetchImpl, onUnauthorized } = withTokens([null], () => json(201, {}));
      expect((await failure(client.request(WRITE))).kind).toBe("unauthorized");
      expect(fetchImpl).not.toHaveBeenCalled();
      expect(onUnauthorized).toHaveBeenCalledTimes(1);
    });

    it.each([
      [503, "unavailable"],
      [500, "server"],
      [404, "not_found"],
      [422, "invalid"],
    ])("a %s on a write is reported once and never retried", async (status, kind) => {
      const { client, fetchImpl, onUnauthorized } = withTokens(["t"], () => json(status, {}));
      expect((await failure(client.request(WRITE))).kind).toBe(kind);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      expect(onUnauthorized).not.toHaveBeenCalled();
    });

    it("a network failure on a write is reported once and never retried", async () => {
      const fetchImpl = vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      });
      const client = createApiClient({ baseUrl: BASE, getAccessToken: async () => TOKEN, onUnauthorized: vi.fn(), fetchImpl: fetchImpl as unknown as typeof fetch });
      expect((await failure(client.request(WRITE))).kind).toBe("network");
      expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it("can cancel a write", async () => {
      const controller = new AbortController();
      const fetchImpl = vi.fn(
        (_url: URL, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      );
      const client = createApiClient({ baseUrl: BASE, getAccessToken: async () => TOKEN, onUnauthorized: vi.fn(), fetchImpl: fetchImpl as unknown as typeof fetch });
      const pending = client.request({ ...WRITE, signal: controller.signal });
      await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
      controller.abort();
      expect((await failure(pending)).kind).toBe("aborted");
    });

    it("sends the note text only in the body: never in the URL, a header or an error", async () => {
      const text = "SECRET-NOTE-TEXT-123";
      const { client, fetchImpl } = withTokens(["t"], () => json(500, { error: { code: "internal_error", message: text } }));
      const error = await failure(client.request({ ...WRITE, body: { content: text } }));
      const [url, init] = fetchImpl.mock.calls[0] as unknown as [URL, RequestInit];
      expect(url.href).not.toContain(text);
      expect(JSON.stringify(init.headers)).not.toContain(text);
      expect(init.body).toContain(text);
      expect(error.message).not.toContain(text);
    });
  });
});
