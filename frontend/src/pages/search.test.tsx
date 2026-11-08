import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { errorBody, FakeApi } from "../test/fakeApi";
import { renderApp, where } from "../test/renderApp";

const SEARCH = /^\/api\/v1\/people\/search$/;
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const box = () => screen.getByLabelText("Search by name");
const type = (value: string) => fireEvent.change(box(), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Search" }));
const names = () => within(screen.getByRole("list", { name: "People" })).getAllByRole("link").map((l) => l.textContent);

afterEach(() => vi.restoreAllMocks());

function seeded() {
  const api = new FakeApi();
  api.addPerson({ display_name: "Sample Friend B" });
  api.addPerson({ display_name: "Sample Friend A" });
  api.addPerson({ display_name: "Someone Else", interests: ["hiking"], preferences: ["hiking boots"] });
  return api;
}
async function open(api = seeded()) {
  renderApp({ api });
  await screen.findByRole("list", { name: "People" });
  return api;
}

describe("search box", () => {
  it("is a labelled search landmark with a Search button, and no Clear until it is needed", async () => {
    await open();
    expect(screen.getByRole("search")).toBeInTheDocument();
    expect(box()).toHaveAttribute("type", "search");
    expect(box()).toHaveAttribute("autocomplete", "off");
    expect(screen.getByRole("button", { name: "Search" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();
  });

  it("shows Clear as soon as something is typed", async () => {
    await open();
    type("s");
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("does not search while typing: only an explicit submission sends anything", async () => {
    const api = await open();
    type("s");
    type("sa");
    type("sam");
    expect(api.callsTo("POST", SEARCH)).toHaveLength(0);
    fireEvent.submit(screen.getByRole("search"));
    await screen.findByText("2 people match “sam”.");
    expect(api.callsTo("POST", SEARCH)).toHaveLength(1);
  });
});

describe("searching", () => {
  it("posts the trimmed text in the body, shows only matches and announces the count", async () => {
    const api = await open();
    type("  sample  ");
    submit();
    expect(await screen.findByText("2 people match “sample”.")).toBeInTheDocument();
    expect(names()).toEqual(["Sample Friend A", "Sample Friend B"]);
    const [call] = api.callsTo("POST", SEARCH);
    expect(call.body).toEqual({ query: "sample" });
    expect(call.search).toBe("");
    expect(call.authorization).toMatch(/^Bearer /);
  });

  it("says '1 person matches' for a single match", async () => {
    await open();
    type("someone");
    submit();
    expect(await screen.findByText("1 person matches “someone”.")).toBeInTheDocument();
  });

  it("shows a 'Searching…' status while waiting, and replaces the old list", async () => {
    const api = await open();
    const gate = api.holdNext("POST", SEARCH);
    type("sample");
    submit();
    expect(await screen.findByText("Searching…")).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "People" })).not.toBeInTheDocument();
    await act(async () => gate.resolve(reply(200, { items: [api.people[1]] })));
    expect(await screen.findByText("1 person matches “sample”.")).toBeInTheDocument();
  });

  it("shows an ordinary no-results message that says only names are searched, with Clear available", async () => {
    await open();
    type("zzz");
    submit();
    expect(await screen.findByText("No one matches “zzz”.")).toBeInTheDocument();
    expect(screen.getByText(/names only, not notes, interests or preferences/i)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Clear" })).toBeInTheDocument();
  });

  it("does not find a person by an interest, preference or note (names only)", async () => {
    const api = await open();
    const person = api.people[2];
    api.addNote(person.id, "Loves hiking on weekends");
    for (const text of ["hiking", "boots", "weekends"]) {
      type(text);
      submit();
      expect(await screen.findByText(`No one matches “${text}”.`)).toBeInTheDocument();
    }
  });

  it("sends wildcard and injection-style text exactly as typed, and shows an ordinary result", async () => {
    const api = await open();
    for (const text of ["%", "_", "\\", "'; DROP TABLE people;--"]) {
      type(text);
      submit();
      await screen.findByText(`No one matches “${text}”.`);
    }
    expect(api.callsTo("POST", SEARCH).map((c) => (c.body as { query: string }).query)).toEqual(["%", "_", "\\", "'; DROP TABLE people;--"]);
  });

  it("re-runs the same search when Search is pressed again", async () => {
    const api = await open();
    type("sample");
    submit();
    await screen.findByText("2 people match “sample”.");
    submit();
    await waitFor(() => expect(api.callsTo("POST", SEARCH)).toHaveLength(2));
    expect(await screen.findByText("2 people match “sample”.")).toBeInTheDocument();
  });

  it("shows the searched-for text as plain text, never as HTML", async () => {
    await open();
    const hostile = `<img src=x onerror="window.__pwned=1"><b>bold</b>`;
    type(hostile);
    submit();
    const message = await screen.findByText(`No one matches “${hostile}”.`);
    expect(message.querySelector("img, b")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("lets a search result open the person", async () => {
    const api = await open();
    type("someone");
    submit();
    fireEvent.click(await screen.findByRole("link", { name: "Someone Else" }));
    expect(await screen.findByRole("heading", { name: "Someone Else" })).toBeInTheDocument();
    expect(where()).toBe(`/people/${api.people[2].id}`);
  });
});

describe("clearing", () => {
  it("returns to the full list, empties the box and puts focus back in it", async () => {
    const api = await open();
    type("sample");
    submit();
    await screen.findByText("2 people match “sample”.");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(await screen.findByRole("list", { name: "People" })).toBeInTheDocument();
    await waitFor(() => expect(names()).toHaveLength(3));
    expect(screen.queryByText(/match/)).not.toBeInTheDocument();
    expect(box()).toHaveValue("");
    expect(box()).toHaveFocus();
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();
    expect(api.callsTo("GET", /^\/api\/v1\/people$/)).toHaveLength(2);
  });

  it("clearing while a search is still running ignores that search's answer", async () => {
    const api = await open();
    const gate = api.holdNext("POST", SEARCH);
    type("sample");
    submit();
    await screen.findByText("Searching…");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(names()).toHaveLength(3));
    await act(async () => gate.resolve(reply(200, { items: [api.people[0]] })));
    expect(names()).toHaveLength(3);
    expect(screen.queryByText(/match/)).not.toBeInTheDocument();
  });
});

describe("validation and errors", () => {
  it("asks for text, without a request, when the box is empty or blank", async () => {
    const api = await open();
    submit();
    expect(await screen.findByText("Enter part of a name to search for.")).toBeInTheDocument();
    type("   ");
    submit();
    expect(await screen.findByText("Enter part of a name to search for.")).toBeInTheDocument();
    expect(box()).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("POST", SEARCH)).toHaveLength(0);
    type("s");
    expect(screen.queryByText("Enter part of a name to search for.")).not.toBeInTheDocument();
  });

  it("refuses over-long text and control characters before any request", async () => {
    const api = await open();
    type("a".repeat(121));
    submit();
    expect(await screen.findByText("Use 120 characters or fewer.")).toBeInTheDocument();
    type("bad\u0000text");
    submit();
    expect(await screen.findByText("The search contains characters that are not allowed.")).toBeInTheDocument();
    expect(api.callsTo("POST", SEARCH)).toHaveLength(0);
  });

  it("explains a server 422 without a retry button and without echoing anything from the response", async () => {
    const api = await open();
    api.failNext("POST", SEARCH, () => reply(422, errorBody("validation_error", { message: "LEAK", fields: [{ loc: ["body", "query"], type: "value_error" }] })));
    type("a‍b");
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("That text cannot be searched for.");
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("LEAK");
  });

  it("shows a fixed, retryable error when the service is unavailable, and retrying searches again", async () => {
    const api = await open();
    api.failNext("POST", SEARCH, () => reply(503, errorBody("service_unavailable", { request_id: "req-s" })));
    type("sample");
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/temporarily unavailable/i);
    expect(alert).toHaveTextContent("Reference: req-s");
    fireEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("2 people match “sample”.")).toBeInTheDocument();
    expect(api.callsTo("POST", SEARCH)).toHaveLength(2);
  });

  it("a failed search leaves the box and Clear usable, and the full list can be restored", async () => {
    const api = await open();
    api.failNext("POST", SEARCH, () => reply(500, errorBody("internal_error")));
    type("sample");
    submit();
    await screen.findByRole("alert");
    expect(box()).toHaveValue("sample");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(names()).toHaveLength(3));
  });

  it("on 401 ends the session like any other screen", async () => {
    const api = await open();
    api.failNext("POST", SEARCH, () => reply(401, errorBody("unauthorized")));
    type("sample");
    submit();
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("overlapping searches", () => {
  it("the answer to an older search never replaces a newer one", async () => {
    const api = await open();
    const slow = api.holdNext("POST", SEARCH);
    type("sample");
    submit();
    await screen.findByText("Searching…");
    type("someone");
    submit();
    expect(await screen.findByText("1 person matches “someone”.")).toBeInTheDocument();
    await act(async () => slow.resolve(reply(200, { items: [api.people[0], api.people[1]] })));
    expect(screen.getByText("1 person matches “someone”.")).toBeInTheDocument();
    expect(names()).toEqual(["Someone Else"]);
  });

  it("starting a new search cancels the request still in flight", async () => {
    const api = seeded();
    const signals: (AbortSignal | null | undefined)[] = [];
    const real = api.fetch;
    // Installed before rendering, because the app takes `fetch` when it starts.
    api.fetch = ((input: Parameters<typeof fetch>[0], init?: RequestInit) => {
      if (init?.method === "POST") signals.push(init.signal);
      return real(input, init);
    }) as typeof fetch;
    await open(api);
    const gate = api.holdNext("POST", SEARCH);
    type("sample");
    submit();
    await screen.findByText("Searching…");
    type("someone");
    submit();
    await screen.findByText("1 person matches “someone”.");
    expect(signals[0]?.aborted).toBe(true);
    expect(signals[1]?.aborted).toBe(false);
    gate.resolve(reply(200, { items: [] }));
  });
});

describe("privacy of the search text", () => {
  it("never reaches the URL, history, browser storage, cookies, console or page title", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    const api = await open();
    const before = document.title;
    type("SyntheticSecretName");
    submit();
    await screen.findByText("No one matches “SyntheticSecretName”.");
    expect(where()).toBe("/people");
    expect(document.title).toBe(before);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    const carrying = api.calls.filter((c) => JSON.stringify(c).includes("SyntheticSecretName"));
    expect(carrying).toHaveLength(1);
    expect(JSON.stringify({ ...carrying[0], body: null })).not.toContain("SyntheticSecretName");
  });

  it("is forgotten when the person leaves the screen", async () => {
    await open();
    type("sample");
    submit();
    await screen.findByText("2 people match “sample”.");
    fireEvent.click(screen.getByRole("link", { name: "Sample Friend A" }));
    await screen.findByRole("heading", { name: "Sample Friend A" });
    fireEvent.click(screen.getByRole("link", { name: "Back to people" }));
    await screen.findByRole("list", { name: "People" });
    expect(box()).toHaveValue("");
    expect(names()).toHaveLength(3);
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("does not log when a search fails", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    const api = await open();
    api.failNext("POST", SEARCH, () => reply(500, { error: { message: "SyntheticSecretName" } }));
    type("SyntheticSecretName");
    submit();
    await screen.findByRole("alert");
    expect(document.body.textContent).not.toMatch(/went wrong on our side.*SyntheticSecretName/);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });
});
