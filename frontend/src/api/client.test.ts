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
});
