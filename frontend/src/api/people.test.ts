import { describe, expect, it, vi } from "vitest";

import { FakeApi, TOKEN } from "../test/fakeApi";
import { ApiError, createApiClient, type ApiClient, type JsonRequest } from "./client";
import { createNote, listNotes, NOTES_PAGE_SIZE, parseNote } from "./notes";
import {
  createPerson,
  deletePerson,
  getPerson,
  isUuid,
  listPeople,
  parsePerson,
  personPath,
  searchPeople,
  updatePerson,
} from "./people";

const ID = "00000000-0000-4000-8000-000000000001";
const person = {
  id: ID,
  display_name: "Ada Example",
  birthday: null,
  timezone: "Africa/Lagos",
  interests: [],
  preferences: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};
const note = { id: "00000000-0000-4000-8000-000000000002", person_id: ID, content: "x", created_at: "t", updated_at: "t" };

function stubClient(response: unknown = undefined) {
  const request = vi.fn<(req: JsonRequest) => Promise<unknown>>(async () => response);
  const client: ApiClient = { request, getJson: vi.fn(), getMe: vi.fn() };
  return { client, request };
}

describe("personPath and isUuid", () => {
  it("builds the path only for UUIDs", () => {
    expect(isUuid(ID)).toBe(true);
    expect(personPath(ID)).toBe(`/api/v1/people/${ID}`);
  });
  it.each(["", "abc", "../me", `${ID}/notes`, `${ID}?x=1`, "%2e%2e", `${ID}\n`])("treats %j as not found", (bad) => {
    expect(() => personPath(bad)).toThrowError(expect.objectContaining({ kind: "not_found" }));
  });
});

describe("people helpers", () => {
  it("listPeople: GET /people and parses items", async () => {
    const { client, request } = stubClient({ items: [person] });
    expect(await listPeople(client)).toEqual([person]);
    expect(request).toHaveBeenCalledWith({ method: "GET", path: "/api/v1/people", signal: undefined });
  });

  it("getPerson: never sends a request for a non-UUID id", async () => {
    const { client, request } = stubClient(person);
    await expect(getPerson(client, "../me")).rejects.toMatchObject({ kind: "not_found" });
    expect(request).not.toHaveBeenCalled();
  });

  it("createPerson sends exactly four fields, even if extras are smuggled in", async () => {
    const { client, request } = stubClient(person);
    const input = { display_name: "Ada", birthday: null, interests: ["Chess"], preferences: [], timezone: "UTC", owner_id: "x" };
    await createPerson(client, input as never);
    expect(request.mock.calls[0][0]).toMatchObject({
      method: "POST",
      path: "/api/v1/people",
      body: { display_name: "Ada", birthday: null, interests: ["Chess"], preferences: [] },
    });
    expect(Object.keys(request.mock.calls[0][0].body as object).sort()).toEqual(["birthday", "display_name", "interests", "preferences"]);
  });

  it("updatePerson sends only the fields that were given, never a time zone", async () => {
    const { client, request } = stubClient(person);
    await updatePerson(client, ID, { display_name: "New", timezone: "UTC" } as never);
    expect(request.mock.calls[0][0]).toMatchObject({ method: "PATCH", path: `/api/v1/people/${ID}`, body: { display_name: "New" } });
    expect(request.mock.calls[0][0].body).toEqual({ display_name: "New" });
  });

  it("updatePerson keeps an explicit null birthday (clearing it)", async () => {
    const { client, request } = stubClient(person);
    await updatePerson(client, ID, { birthday: null });
    expect(request.mock.calls[0][0].body).toEqual({ birthday: null });
  });

  it("deletePerson sends DELETE and accepts 204 (undefined)", async () => {
    const { client, request } = stubClient(undefined);
    await expect(deletePerson(client, ID)).resolves.toBeUndefined();
    expect(request.mock.calls[0][0]).toMatchObject({ method: "DELETE", path: `/api/v1/people/${ID}` });
    expect(request.mock.calls[0][0].body).toBeUndefined();
  });

  it.each([
    ["not an object", "nope"],
    ["null", null],
    ["missing name", { ...person, display_name: undefined }],
    ["non-UUID id", { ...person, id: "1" }],
    ["interests not strings", { ...person, interests: [1] }],
    ["birthday a number", { ...person, birthday: 5 }],
  ])("parsePerson rejects %s as an unexpected response", (_n, value) => {
    expect(() => parsePerson(value)).toThrowError(expect.objectContaining({ kind: "unexpected" }));
  });

  it("listPeople rejects a response without items", async () => {
    const { client } = stubClient({ people: [] });
    await expect(listPeople(client)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("searchPeople", () => {
  it("posts the text in the body to the fixed search path, with nothing in the URL", async () => {
    const { client, request } = stubClient({ items: [person] });
    expect(await searchPeople(client, "ada 50%_\\/")).toEqual([person]);
    expect(request.mock.calls[0][0]).toMatchObject({ method: "POST", path: "/api/v1/people/search", body: { query: "ada 50%_\\/" } });
    expect(request.mock.calls[0][0].query).toBeUndefined();
    expect(request.mock.calls[0][0].path).not.toContain("ada");
  });
  it("sends exactly one field and rejects a response without items", async () => {
    const { client, request } = stubClient({ people: [] });
    await expect(searchPeople(client, "x")).rejects.toMatchObject({ kind: "unexpected" });
    expect(request.mock.calls[0][0].body).toEqual({ query: "x" });
  });
  it("shape-checks every result like the list does", async () => {
    const { client } = stubClient({ items: [{ ...person, id: "nope" }] });
    await expect(searchPeople(client, "x")).rejects.toMatchObject({ kind: "unexpected" });
  });
  it("through the real client: bearer token, JSON body, text never in the URL, 422 fields kept", async () => {
    const api = new FakeApi();
    api.addPerson({ display_name: "Sample Friend A" });
    const client = createApiClient({ baseUrl: "https://api.example.test", getAccessToken: async () => TOKEN, onUnauthorized: vi.fn(), fetchImpl: api.fetch });
    expect((await searchPeople(client, "sample")).map((p) => p.display_name)).toEqual(["Sample Friend A"]);
    expect(api.calls[0]).toMatchObject({ method: "POST", path: "/api/v1/people/search", search: "", authorization: `Bearer ${TOKEN}`, contentType: "application/json" });
    await expect(searchPeople(client, "a\u200db")).rejects.toMatchObject({ kind: "invalid", status: 422, fields: [{ loc: ["body", "query"], type: "value_error" }] });
  });
});

describe("notes helpers", () => {
  it("listNotes passes limit and offset as query values, not in the path", async () => {
    const { client, request } = stubClient({ items: [note] });
    expect(await listNotes(client, ID, { limit: NOTES_PAGE_SIZE, offset: 40 })).toEqual([note]);
    expect(request.mock.calls[0][0]).toMatchObject({
      method: "GET",
      path: `/api/v1/people/${ID}/notes`,
      query: { limit: 20, offset: 40 },
    });
  });

  it("createNote sends only {content}, byte for byte", async () => {
    const { client, request } = stubClient(note);
    const content = "  keep   this \n spacing  ";
    await createNote(client, ID, content);
    expect(request.mock.calls[0][0]).toMatchObject({ method: "POST", path: `/api/v1/people/${ID}/notes`, body: { content } });
    expect(request.mock.calls[0][0].body).toEqual({ content });
  });

  it("refuses a bad person id without a request", async () => {
    const { client, request } = stubClient({ items: [] });
    await expect(listNotes(client, "x/../y", { limit: 20, offset: 0 })).rejects.toMatchObject({ kind: "not_found" });
    await expect(createNote(client, "x", "hi")).rejects.toMatchObject({ kind: "not_found" });
    expect(request).not.toHaveBeenCalled();
  });

  it("parseNote rejects malformed notes", () => {
    expect(() => parseNote({ ...note, content: 5 })).toThrowError(expect.objectContaining({ kind: "unexpected" }));
    expect(() => parseNote({ ...note, person_id: "nope" })).toThrowError(expect.objectContaining({ kind: "unexpected" }));
  });
});

describe("helpers through the real API client", () => {
  function realClient(api: FakeApi, onUnauthorized = vi.fn()) {
    return createApiClient({
      baseUrl: "https://api.example.test",
      getAccessToken: async () => TOKEN,
      onUnauthorized,
      fetchImpl: api.fetch,
    });
  }

  it("round-trips a person and a note with JSON bodies, the bearer token and no cookies", async () => {
    const api = new FakeApi();
    const client = realClient(api);
    const created = await createPerson(client, { display_name: "Grace Sample", birthday: "1990-05-04", interests: [], preferences: [] });
    await createNote(client, created.id, "Synthetic note");
    expect(await listNotes(client, created.id, { limit: 20, offset: 0 })).toHaveLength(1);
    expect(api.calls.every((c) => c.authorization === `Bearer ${TOKEN}`)).toBe(true);
    expect(api.calls[0].contentType).toBe("application/json");
    expect(api.calls[2].search).toBe("?limit=20&offset=0");
    await deletePerson(client, created.id);
    await expect(getPerson(client, created.id)).rejects.toMatchObject({ kind: "not_found" });
  });

  it("surfaces 422 field errors from a real create", async () => {
    const api = new FakeApi();
    const client = realClient(api);
    await expect(createPerson(client, { display_name: "  ", birthday: null, interests: [], preferences: [] })).rejects.toMatchObject({
      kind: "invalid",
      status: 422,
      fields: [{ loc: ["body", "display_name"], type: "value_error" }],
    });
  });
});
