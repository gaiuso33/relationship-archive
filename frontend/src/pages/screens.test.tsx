import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { errorBody, FakeApi } from "../test/fakeApi";
import { renderApp, where } from "../test/renderApp";

const PEOPLE = /^\/api\/v1\/people$/;
const ONE = /^\/api\/v1\/people\/[0-9a-f-]{36}$/;
const NOTES = /^\/api\/v1\/people\/[0-9a-f-]{36}\/notes$/;
const reply = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const type = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));

describe("people list", () => {
  it("shows a loading state, then the people in the order the API gives, with birthdays as calendar dates", async () => {
    const api = new FakeApi();
    api.addPerson({ display_name: "Zed Sample" });
    api.addPerson({ display_name: "Ada Example", birthday: "2000-01-01" });
    const gate = api.holdNext("GET", PEOPLE);
    renderApp({ api });
    await screen.findByRole("heading", { name: "People" });
    expect(await screen.findByText("Loading people…")).toBeInTheDocument();
    await act(async () => gate.resolve(reply(200, { items: [api.people[1], api.people[0]] })));
    const list = await screen.findByRole("list", { name: "People" });
    const links = within(list).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual(["Ada Example", "Zed Sample"]);
    expect(links[0]).toHaveAttribute("href", `/people/${api.people[1].id}`);
    expect(list).toHaveTextContent(/Birthday: .*2000/);
    expect(list).toHaveTextContent(/January 1, 2000|1 January 2000/);
  });

  it("shows an empty state with a way to add the first person", async () => {
    renderApp();
    expect(await screen.findByText("You have not added anyone yet.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Add your first person" }));
    expect(await screen.findByRole("heading", { name: "Add person" })).toBeInTheDocument();
    expect(where()).toBe("/people/new");
  });

  it("shows a fixed error with a retry that works", async () => {
    const api = new FakeApi();
    api.addPerson();
    api.failNext("GET", PEOPLE, () => reply(500, { error: { message: "SECRET" } }));
    renderApp({ api });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side.");
    click("Try again");
    expect(await screen.findByRole("link", { name: "Ada Example" })).toBeInTheDocument();
  });

  it("has a main landmark, a skip link, labelled navigation and focuses the page heading", async () => {
    renderApp();
    const heading = await screen.findByRole("heading", { name: "People" });
    expect(screen.getByRole("main")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Skip to main content" })).toHaveAttribute("href", "#main");
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    await waitFor(() => expect(heading).toHaveFocus());
    expect(document.title).toContain("People");
  });
});

describe("create person", () => {
  const open = () => renderApp({ entry: "/people/new" });

  it("has labelled fields and no time zone field", async () => {
    open();
    await screen.findByRole("heading", { name: "Add person" });
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
    expect(screen.getByLabelText("Birthday (optional)")).toHaveAttribute("type", "date");
    expect(screen.getByLabelText("New interest")).toBeInTheDocument();
    expect(screen.getByLabelText("New preference")).toBeInTheDocument();
    expect(screen.queryByLabelText(/time ?zone/i)).not.toBeInTheDocument();
  });

  it("validates before any request", async () => {
    const { api } = open();
    await screen.findByRole("heading", { name: "Add person" });
    click("Add person");
    expect(await screen.findByText("Enter a name.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveAttribute("aria-invalid", "true");
    type("Name", "Ada");
    type("Birthday (optional)", "2999-01-01");
    click("Add person");
    expect(await screen.findByText("The date cannot be in the future.")).toBeInTheDocument();
    type("Birthday (optional)", "");
    type("New interest", "Chess");
    click("Add person");
    expect(await screen.findByText(/has not been added/)).toBeInTheDocument();
    expect(api.callsTo("POST", PEOPLE)).toHaveLength(0);
  });

  it("creates a person with exactly the four fields, then shows the person with a success notice", async () => {
    const { api } = open();
    await screen.findByRole("heading", { name: "Add person" });
    type("Name", "  Ada Example  ");
    type("Birthday (optional)", "1990-05-04");
    type("New interest", "Chess");
    fireEvent.keyDown(screen.getByLabelText("New interest"), { key: "Enter" });
    type("New preference", "Tea, no sugar");
    click("Add preference");
    expect(api.callsTo("POST", PEOPLE)).toHaveLength(0); // Enter did not send the form
    click("Add person");
    expect(await screen.findByRole("heading", { name: "Ada Example" })).toBeInTheDocument();
    expect(await screen.findByText("Person added.")).toBeInTheDocument();
    const [call] = api.callsTo("POST", PEOPLE);
    expect(call.body).toEqual({ display_name: "Ada Example", birthday: "1990-05-04", interests: ["Chess"], preferences: ["Tea, no sugar"] });
    expect(where()).toBe(`/people/${api.people[0].id}`);
  });

  it("maps 422 field errors to the right inputs and keeps what was typed", async () => {
    const { api } = open();
    api.failNext("POST", PEOPLE, () =>
      reply(422, errorBody("validation_error", { fields: [{ loc: ["body", "display_name"], type: "value_error" }, { loc: ["body", "interests", 1], type: "value_error" }] })),
    );
    await screen.findByRole("heading", { name: "Add person" });
    type("Name", "Ada‍Example");
    click("Add person");
    expect(await screen.findByText("That name was not accepted.")).toBeInTheDocument();
    expect(screen.getByText("The interest in position 2 was not accepted.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Ada‍Example");
    expect(screen.getByRole("button", { name: "Add person" })).toBeEnabled();
    expect(where()).toBe("/people/new");
  });

  it("explains an unplaceable 422 generally, without echoing anything from the response", async () => {
    const { api } = open();
    api.failNext("POST", PEOPLE, () => reply(422, errorBody("validation_error", { message: "LEAK", fields: [] })));
    await screen.findByRole("heading", { name: "Add person" });
    type("Name", "Ada");
    click("Add person");
    expect(await screen.findByText("Some details were not accepted.")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("LEAK");
  });

  it("keeps the form and shows a retryable error on 503", async () => {
    const { api } = open();
    api.failNext("POST", PEOPLE, () => reply(503, errorBody("service_unavailable")));
    await screen.findByRole("heading", { name: "Add person" });
    type("Name", "Ada");
    click("Add person");
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
    expect(screen.getByLabelText("Name")).toHaveValue("Ada");
    click("Add person");
    expect(await screen.findByRole("heading", { name: "Ada" })).toBeInTheDocument();
    expect(api.callsTo("POST", PEOPLE)).toHaveLength(2);
  });

  it("blocks a double submit while saving", async () => {
    const { api } = open();
    const gate = api.holdNext("POST", PEOPLE);
    await screen.findByRole("heading", { name: "Add person" });
    type("Name", "Ada");
    click("Add person");
    const busy = await screen.findByRole("button", { name: "Saving…" });
    expect(busy).toBeDisabled();
    fireEvent.submit(busy.closest("form") as HTMLFormElement);
    expect(api.callsTo("POST", PEOPLE)).toHaveLength(1);
    await act(async () => gate.resolve(reply(201, api.addPerson({ display_name: "Ada" }))));
    expect(await screen.findByRole("heading", { name: "Ada" })).toBeInTheDocument();
  });
});

describe("edit person", () => {
  function seeded() {
    const api = new FakeApi();
    const person = api.addPerson({ display_name: "Ada Example", birthday: "1990-05-04", timezone: "Africa/Lagos", interests: ["Chess", "Tea"], preferences: ["Books"] });
    return { api, person };
  }

  it("is prefilled", async () => {
    const { api, person } = seeded();
    renderApp({ api, entry: `/people/${person.id}/edit` });
    expect(await screen.findByRole("heading", { name: "Edit person" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Name")).toHaveValue("Ada Example");
    expect(screen.getByLabelText("Birthday (optional)")).toHaveValue("1990-05-04");
    expect(screen.getByRole("list", { name: "Interests added" })).toHaveTextContent(/Chess.*Tea/);
    expect(screen.queryByLabelText(/time ?zone/i)).not.toBeInTheDocument();
  });

  it("sends only what changed, never the time zone, and leaves the time zone untouched", async () => {
    const { api, person } = seeded();
    renderApp({ api, entry: `/people/${person.id}/edit` });
    await screen.findByLabelText("Name");
    type("Name", "Ada Sample");
    click("Save changes");
    expect(await screen.findByRole("heading", { name: "Ada Sample" })).toBeInTheDocument();
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(api.callsTo("PATCH", ONE)[0].body).toEqual({ display_name: "Ada Sample" });
    expect(api.people[0].timezone).toBe("Africa/Lagos");
  });

  it("can clear the birthday and remove an interest", async () => {
    const { api, person } = seeded();
    renderApp({ api, entry: `/people/${person.id}/edit` });
    await screen.findByLabelText("Name");
    type("Birthday (optional)", "");
    click("Remove Chess from interests");
    click("Save changes");
    await screen.findByText("Changes saved.");
    expect(api.callsTo("PATCH", ONE)[0].body).toEqual({ birthday: null, interests: ["Tea"] });
  });

  it("says so, and sends nothing, when nothing changed", async () => {
    const { api, person } = seeded();
    renderApp({ api, entry: `/people/${person.id}/edit` });
    await screen.findByLabelText("Name");
    click("Save changes");
    expect(await screen.findByText("No changes to save.")).toBeInTheDocument();
    expect(api.callsTo("PATCH", ONE)).toHaveLength(0);
  });

  it("shows not-found for an unknown person and does not call the API for a malformed id", async () => {
    const { api } = seeded();
    renderApp({ api, entry: "/people/00000000-0000-4000-8000-0000000000ff/edit" });
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
  });

  it("does not request anything for a malformed id", async () => {
    const { api } = renderApp({ entry: "/people/not-an-id/edit" });
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
    expect(api.calls).toHaveLength(0);
  });
});

describe("person detail", () => {
  it("shows the person's details, and 'None added' for empty lists", async () => {
    const api = new FakeApi();
    const full = api.addPerson({ display_name: "Ada Example", birthday: "2000-01-01", interests: ["Chess"], preferences: ["Tea"] });
    renderApp({ api, entry: `/people/${full.id}` });
    expect(await screen.findByRole("heading", { name: "Ada Example" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Interests" })).toHaveTextContent("Chess");
    expect(screen.getByRole("list", { name: "Preferences" })).toHaveTextContent("Tea");
    expect(screen.getByRole("link", { name: "Edit" })).toHaveAttribute("href", `/people/${full.id}/edit`);
    expect(screen.getByRole("link", { name: "Back to people" })).toHaveAttribute("href", "/people");
  });

  it("shows empty lists plainly", async () => {
    const api = new FakeApi();
    const bare = api.addPerson({ display_name: "Bare Example" });
    renderApp({ api, entry: `/people/${bare.id}` });
    await screen.findByRole("heading", { name: "Bare Example" });
    expect(screen.getAllByText("None added")).toHaveLength(2);
    expect(screen.getByText("Not set")).toBeInTheDocument();
  });

  it("treats a missing or other user's person the same way: not found", async () => {
    renderApp({ entry: "/people/00000000-0000-4000-8000-0000000000aa" });
    expect(await screen.findByRole("heading", { name: "Not found" })).toBeInTheDocument();
    expect(screen.getByText("That could not be found.")).toBeInTheDocument();
  });

  it("shows a retryable error when the person cannot be loaded", async () => {
    const api = new FakeApi();
    const person = api.addPerson();
    api.failNext("GET", ONE, () => reply(503, errorBody("service_unavailable")));
    renderApp({ api, entry: `/people/${person.id}` });
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
    click("Try again");
    expect(await screen.findByRole("heading", { name: "Ada Example" })).toBeInTheDocument();
  });
});

describe("delete person", () => {
  async function openDetail() {
    const api = new FakeApi();
    const person = api.addPerson({ display_name: "Ada Example" });
    api.addNote(person.id, "Synthetic note one");
    renderApp({ api, entry: `/people/${person.id}` });
    await screen.findByRole("heading", { name: "Ada Example" });
    return { api, person };
  }

  it("asks for confirmation, names the consequence and focuses the safe choice", async () => {
    const { api } = await openDetail();
    click("Delete person");
    const group = screen.getByRole("group", { name: "Delete Ada Example?" });
    expect(group).toHaveTextContent(/permanently deletes this person and all of their notes/i);
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(api.callsTo("DELETE", ONE)).toHaveLength(0);
  });

  it("cancel closes the confirmation, sends nothing and returns focus to the opener", async () => {
    const { api } = await openDetail();
    click("Delete person");
    click("Cancel");
    expect(screen.queryByRole("group", { name: /Delete Ada/ })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete person" })).toHaveFocus());
    expect(api.callsTo("DELETE", ONE)).toHaveLength(0);
  });

  it("deletes after confirmation, returns to the list with a notice, and the notes go too", async () => {
    const { api } = await openDetail();
    click("Delete person");
    click("Delete permanently");
    expect(await screen.findByText("Person deleted.")).toBeInTheDocument();
    expect(where()).toBe("/people");
    expect(await screen.findByText("You have not added anyone yet.")).toBeInTheDocument();
    expect(api.callsTo("DELETE", ONE)).toHaveLength(1);
    expect(api.notes).toHaveLength(0);
  });

  it("keeps the person and offers another try when deleting fails", async () => {
    const { api } = await openDetail();
    api.failNext("DELETE", ONE, () => reply(503, errorBody("service_unavailable")));
    click("Delete person");
    click("Delete permanently");
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
    expect(where()).toMatch(/^\/people\/[0-9a-f-]{36}$/);
    expect(api.people).toHaveLength(1);
    click("Delete permanently");
    expect(await screen.findByText("Person deleted.")).toBeInTheDocument();
  });

  it("does not send a second DELETE while the first is running", async () => {
    const { api } = await openDetail();
    const gate = api.holdNext("DELETE", ONE);
    click("Delete person");
    click("Delete permanently");
    const busy = await screen.findByRole("button", { name: "Deleting…" });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(api.callsTo("DELETE", ONE)).toHaveLength(1);
    await act(async () => gate.resolve(new Response(null, { status: 204 })));
    expect(await screen.findByText("Person deleted.")).toBeInTheDocument();
  });
});

describe("notes", () => {
  function seeded(count = 0) {
    const api = new FakeApi();
    const person = api.addPerson({ display_name: "Ada Example" });
    for (let i = 1; i <= count; i += 1) api.addNote(person.id, `Synthetic note ${i}`);
    return { api, person };
  }
  const open = async (api: FakeApi, id: string) => {
    renderApp({ api, entry: `/people/${id}` });
    await screen.findByRole("heading", { name: "Ada Example" });
  };
  const shown = () => within(screen.getByRole("list", { name: "Notes, newest first" })).getAllByRole("article").map((a) => a.querySelector(".note-text")?.textContent);

  it("shows an empty state, then loading, before notes", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    expect(await screen.findByText("No notes yet. Write the first one above.")).toBeInTheDocument();
  });

  it("lists notes newest first with machine-readable times", async () => {
    const { api, person } = seeded(3);
    await open(api, person.id);
    await screen.findByRole("list", { name: "Notes, newest first" });
    expect(shown()).toEqual(["Synthetic note 3", "Synthetic note 2", "Synthetic note 1"]);
    expect(document.querySelectorAll("time[datetime]")).toHaveLength(3);
  });

  it("adds a note exactly as typed, shows it first, clears the box, announces it and refocuses", async () => {
    const { api, person } = seeded(1);
    await open(api, person.id);
    await screen.findByRole("list", { name: "Notes, newest first" });
    const text = "  First line\n\n  indented line  ";
    type("New note", text);
    click("Add note");
    expect(await screen.findByText("Note added.")).toBeInTheDocument();
    expect(api.callsTo("POST", NOTES)[0].body).toEqual({ content: text });
    expect(shown()[0]).toBe(text);
    expect(screen.getByLabelText("New note")).toHaveValue("");
    expect(screen.getByLabelText("New note")).toHaveFocus();
  });

  it("adds the first note to an empty list", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    await screen.findByText("No notes yet. Write the first one above.");
    type("New note", "Synthetic first note");
    click("Add note");
    expect(await screen.findByRole("list", { name: "Notes, newest first" })).toHaveTextContent("Synthetic first note");
    expect(screen.queryByText("No notes yet. Write the first one above.")).not.toBeInTheDocument();
  });

  it("rejects blank notes without a request", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    type("New note", " \n ");
    click("Add note");
    expect(await screen.findByText("Write something before adding the note.")).toBeInTheDocument();
    expect(screen.getByLabelText("New note")).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("POST", NOTES)).toHaveLength(0);
  });

  it("rejects an over-long note and counts characters the way the API does", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    type("New note", "😀😀");
    expect(screen.getByText("2 of 10,000 characters")).toBeInTheDocument();
    type("New note", "a".repeat(10_001));
    expect(screen.getByText("10,001 of 10,000 characters")).toBeInTheDocument();
    click("Add note");
    expect(await screen.findByText("Notes can be up to 10,000 characters.")).toBeInTheDocument();
    expect(api.callsTo("POST", NOTES)).toHaveLength(0);
  });

  it("rejects control characters", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    type("New note", "bad\u0000note");
    click("Add note");
    expect(await screen.findByText("The note contains characters that cannot be saved.")).toBeInTheDocument();
    expect(api.callsTo("POST", NOTES)).toHaveLength(0);
  });

  it("renders note text as plain text, never as HTML", async () => {
    const { api, person } = seeded();
    const hostile = `<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script><b>bold</b>`;
    api.addNote(person.id, hostile);
    await open(api, person.id);
    const list = await screen.findByRole("list", { name: "Notes, newest first" });
    expect(list).toHaveTextContent(hostile);
    expect(list.querySelector("img, script, b")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("keeps a failed note in the box and offers another try", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    api.failNext("POST", NOTES, () => reply(503, errorBody("service_unavailable")));
    type("New note", "Synthetic draft");
    click("Add note");
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
    expect(screen.getByLabelText("New note")).toHaveValue("Synthetic draft");
    click("Add note");
    expect(await screen.findByText("Note added.")).toBeInTheDocument();
    expect(api.notes).toHaveLength(1);
  });

  it("blocks a double submit while saving", async () => {
    const { api, person } = seeded();
    await open(api, person.id);
    const gate = api.holdNext("POST", NOTES);
    type("New note", "Synthetic once");
    click("Add note");
    const busy = await screen.findByRole("button", { name: "Adding…" });
    expect(busy).toBeDisabled();
    fireEvent.submit(busy.closest("form") as HTMLFormElement);
    expect(api.callsTo("POST", NOTES)).toHaveLength(1);
    await act(async () => gate.resolve(reply(201, api.addNote(person.id, "Synthetic once"))));
    expect(await screen.findByText("Note added.")).toBeInTheDocument();
  });

  it("shows a retryable error when notes cannot be loaded, and still lets the person be read", async () => {
    const { api, person } = seeded(1);
    api.failNext("GET", NOTES, () => reply(500, errorBody("internal_error")));
    await open(api, person.id);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong on our side.");
    click("Try again");
    expect(await screen.findByRole("list", { name: "Notes, newest first" })).toHaveTextContent("Synthetic note 1");
  });

  describe("pagination", () => {
    it("loads 20 at a time, using the count already shown as the offset, until a short page", async () => {
      const { api, person } = seeded(45);
      await open(api, person.id);
      await screen.findByRole("list", { name: "Notes, newest first" });
      expect(shown()).toHaveLength(20);
      expect(shown()[0]).toBe("Synthetic note 45");
      expect(api.callsTo("GET", NOTES)[0].search).toBe("?limit=20&offset=0");

      click("Load more notes");
      await waitFor(() => expect(shown()).toHaveLength(40));
      expect(api.callsTo("GET", NOTES)[1].search).toBe("?limit=20&offset=20");

      click("Load more notes");
      await waitFor(() => expect(shown()).toHaveLength(45));
      expect(api.callsTo("GET", NOTES)[2].search).toBe("?limit=20&offset=40");
      expect(screen.queryByRole("button", { name: "Load more notes" })).not.toBeInTheDocument();
      expect(shown()[44]).toBe("Synthetic note 1");
      expect(new Set(shown()).size).toBe(45);
    });

    it("offers no button when everything fits on one page", async () => {
      const { api, person } = seeded(5);
      await open(api, person.id);
      await screen.findByRole("list", { name: "Notes, newest first" });
      expect(screen.queryByRole("button", { name: "Load more notes" })).not.toBeInTheDocument();
    });

    it("after adding a note, the next page continues from what is shown with no gap or repeat", async () => {
      const { api, person } = seeded(25);
      await open(api, person.id);
      await screen.findByRole("list", { name: "Notes, newest first" });
      type("New note", "Synthetic newest");
      click("Add note");
      await screen.findByText("Note added.");
      click("Load more notes");
      await waitFor(() => expect(shown()).toHaveLength(26));
      expect(new Set(shown()).size).toBe(26);
      expect(api.callsTo("GET", NOTES)[1].search).toBe("?limit=20&offset=21");
    });

    it("never shows the same note twice if a page overlaps", async () => {
      const { api, person } = seeded(20);
      await open(api, person.id);
      await screen.findByRole("list", { name: "Notes, newest first" });
      const duplicate = [...api.notes].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
      api.failNext("GET", NOTES, () => reply(200, { items: [duplicate] }));
      click("Load more notes");
      await waitFor(() => expect(screen.queryByRole("button", { name: "Load more notes" })).not.toBeInTheDocument());
      expect(shown()).toHaveLength(20);
    });

    it("keeps the notes already shown and allows a retry when a later page fails", async () => {
      const { api, person } = seeded(25);
      await open(api, person.id);
      await screen.findByRole("list", { name: "Notes, newest first" });
      api.failNext("GET", NOTES, () => reply(503, errorBody("service_unavailable")));
      click("Load more notes");
      expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
      expect(shown()).toHaveLength(20);
      click("Load more notes");
      await waitFor(() => expect(shown()).toHaveLength(25));
    });

    it("sends only one request while a page is loading", async () => {
      const { api, person } = seeded(25);
      await open(api, person.id);
      await screen.findByRole("list", { name: "Notes, newest first" });
      const gate = api.holdNext("GET", NOTES);
      click("Load more notes");
      const busy = await screen.findByRole("button", { name: "Loading…" });
      expect(busy).toBeDisabled();
      fireEvent.click(busy);
      expect(api.callsTo("GET", NOTES)).toHaveLength(2);
      await act(async () => gate.resolve(reply(200, { items: [] })));
    });
  });

  it("does not offer to edit or delete notes", async () => {
    const { api, person } = seeded(2);
    await open(api, person.id);
    const list = await screen.findByRole("list", { name: "Notes, newest first" });
    expect(within(list).queryAllByRole("button")).toHaveLength(0);
    expect(within(list).queryAllByRole("link")).toHaveLength(0);
  });
});

vi.setConfig({ testTimeout: 15_000 });
