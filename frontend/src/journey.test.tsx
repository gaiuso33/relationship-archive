import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FakeApi, TOKEN } from "./test/fakeApi";
import { FakeAuth, sessionFor, USER_A } from "./test/fakeAuth";
import { renderApp, where } from "./test/renderApp";

const NOTE_TEXT = "Synthetic private note: prefers tea, allergic to nothing";
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));

afterEach(() => vi.restoreAllMocks());

async function runJourney(api: FakeApi, auth: FakeAuth) {
  renderApp({ api, auth, entry: "/people" });

  // Signed out: sent to sign-in, then back to where they were going.
  await screen.findByRole("heading", { name: "Sign in" });
  auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
  type("Email", "person@example.test");
  type("Password", "synthetic-password");
  click("Sign in");
  await screen.findByText("You have not added anyone yet.");
  expect(where()).toBe("/people");

  // Create a person.
  fireEvent.click(screen.getByRole("link", { name: "Add your first person" }));
  await screen.findByRole("heading", { name: "Add person" });
  type("Name", "Ada Example");
  type("Birthday (optional)", "1990-05-04");
  type("New interest", "Chess");
  click("Add interest");
  click("Add person");

  // View the person, then add a note.
  await screen.findByRole("heading", { name: "Ada Example" });
  expect(await screen.findByText("Person added.")).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "Interests" })).toHaveTextContent("Chess");
  await screen.findByText("No notes yet. Write the first one above.");
  type("New note", NOTE_TEXT);
  click("Add note");
  expect(await screen.findByText("Note added.")).toBeInTheDocument();
  expect(screen.getByRole("list", { name: "Notes, newest first" })).toHaveTextContent(NOTE_TEXT);
}

describe("journey: create person → add note → view person", () => {
  it("works end to end through the screens", async () => {
    const api = new FakeApi();
    await runJourney(api, new FakeAuth());

    // The person and the note are saved, and show again after leaving and coming back.
    expect(api.people).toHaveLength(1);
    expect(api.notes).toEqual([expect.objectContaining({ person_id: api.people[0].id, content: NOTE_TEXT })]);
    fireEvent.click(screen.getByRole("link", { name: "Back to people" }));
    const list = await screen.findByRole("list", { name: "People" });
    fireEvent.click(within(list).getByRole("link", { name: "Ada Example" }));
    await screen.findByRole("heading", { name: "Ada Example" });
    expect(await screen.findByRole("list", { name: "Notes, newest first" })).toHaveTextContent(NOTE_TEXT);
    // The one-time notice does not come back on a later visit.
    expect(screen.queryByText("Person added.")).not.toBeInTheDocument();
  });

  it("sends the bearer token on every API call, and never anything else as identity", async () => {
    const api = new FakeApi();
    await runJourney(api, new FakeAuth());
    expect(api.calls.length).toBeGreaterThan(3);
    for (const call of api.calls) {
      expect(call.authorization).toBe(`Bearer ${TOKEN}`);
      const bodies = JSON.stringify(call.body ?? {});
      expect(bodies).not.toMatch(/owner|user_id|timezone/);
    }
  });
});

describe("privacy of note content", () => {
  it("never writes to the console or browser storage, and keeps note text out of URLs, headers and errors", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    const api = new FakeApi();
    await runJourney(api, new FakeAuth());

    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    expect(document.cookie).toBe("");
    expect(where()).not.toContain("Synthetic");
    expect(document.title).not.toContain("Synthetic");

    const carrying = api.calls.filter((c) => JSON.stringify(c).includes(NOTE_TEXT));
    expect(carrying).toHaveLength(1);
    expect(carrying[0]).toMatchObject({ method: "POST" });
    expect(carrying[0].path).toMatch(/\/notes$/);
    expect(carrying[0].search).toBe("");
    expect(JSON.stringify({ ...carrying[0], body: null })).not.toContain(NOTE_TEXT);
  });

  it("does not show anything from an error response, even one that echoes the note", async () => {
    const api = new FakeApi();
    const person = api.addPerson();
    api.failNext("POST", /\/notes$/, () =>
      new Response(JSON.stringify({ error: { code: "internal_error", message: NOTE_TEXT, request_id: "req-1" }, detail: NOTE_TEXT }), { status: 500 }),
    );
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    renderApp({ api, entry: `/people/${person.id}` });
    await screen.findByRole("heading", { name: "Ada Example" });
    await screen.findByText("No notes yet. Write the first one above.");
    type("New note", NOTE_TEXT);
    click("Add note");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong on our side.");
    expect(alert).not.toHaveTextContent(NOTE_TEXT);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
  });

  it("does not log anything when requests fail on any screen", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((m) => vi.spyOn(console, m));
    const api = new FakeApi();
    const person = api.addPerson();
    api.addNote(person.id, NOTE_TEXT);
    const boom = () => new Response(JSON.stringify({ error: { code: "internal_error", message: NOTE_TEXT } }), { status: 500 });
    api.failNext("GET", /^\/api\/v1\/people$/, boom);
    api.failNext("GET", /^\/api\/v1\/people\/[0-9a-f-]{36}$/, boom);
    api.failNext("GET", /\/notes$/, boom);
    api.failNext("PATCH", /^\/api\/v1\/people\/[0-9a-f-]{36}$/, boom);
    renderApp({ api, entry: "/people" });
    await screen.findAllByText(/went wrong on our side/); // list
    click("Try again");
    fireEvent.click(await screen.findByRole("link", { name: "Ada Example" }));
    await screen.findAllByText(/went wrong on our side/); // person
    click("Try again");
    await screen.findByRole("heading", { name: "Ada Example" });
    await screen.findAllByText(/went wrong on our side/); // notes
    fireEvent.click(screen.getByRole("link", { name: "Edit" }));
    await screen.findByLabelText("Name");
    type("Name", "Ada Changed");
    click("Save changes");
    await screen.findAllByText(/went wrong on our side/); // save
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
  });

  it("does not keep a draft note when the person leaves the screen", async () => {
    const api = new FakeApi();
    const person = api.addPerson();
    renderApp({ api, entry: `/people/${person.id}` });
    await screen.findByRole("heading", { name: "Ada Example" });
    type("New note", NOTE_TEXT);
    fireEvent.click(screen.getByRole("link", { name: "Back to people" }));
    await screen.findByRole("list", { name: "People" });
    fireEvent.click(screen.getByRole("link", { name: "Ada Example" }));
    await screen.findByRole("heading", { name: "Ada Example" });
    expect(screen.getByLabelText("New note")).toHaveValue("");
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("clears the screen when the session ends, so no note text stays on show", async () => {
    const api = new FakeApi();
    const person = api.addPerson();
    api.addNote(person.id, NOTE_TEXT);
    const { auth } = renderApp({ api, entry: `/people/${person.id}` });
    await screen.findByText(NOTE_TEXT);
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await screen.findByRole("heading", { name: "Sign in" });
    expect(auth.session).toBeNull();
    await waitFor(() => expect(document.body.textContent).not.toContain(NOTE_TEXT));
  });
});
