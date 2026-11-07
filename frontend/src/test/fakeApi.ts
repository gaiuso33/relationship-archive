import type { Note, Person } from "../api/types";
import { deferred, type Deferred } from "./fakeAuth";

export const TOKEN = "synthetic-access-token";

export interface Call {
  method: string;
  path: string;
  search: string;
  body: unknown;
  authorization: string | null;
  contentType: string | null;
}

interface Override {
  match: (call: Call) => boolean;
  respond: () => Response | Promise<Response>;
  once: boolean;
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export const errorBody = (code: string, extra: Record<string, unknown> = {}) => ({
  error: { code, message: "fixed message", request_id: "req-test", ...extra },
});

/**
 * A small in-memory stand-in for the people and notes API, with synthetic data. It follows the
 * documented contract closely enough for the screens: owner-less records, trimmed names, notes
 * newest first with `limit` and `offset`, 204 on delete, and `{"error": {...}}` failures. Nothing
 * here touches the network.
 */
export class FakeApi {
  people: Person[] = [];
  notes: Note[] = [];
  calls: Call[] = [];
  private overrides: Override[] = [];
  private counter = 0;
  private clock = Date.UTC(2026, 0, 1, 9, 0, 0);

  nextId(): string {
    this.counter += 1;
    return `00000000-0000-4000-8000-${String(this.counter).padStart(12, "0")}`;
  }

  private stamp(): string {
    this.clock += 60_000;
    return new Date(this.clock).toISOString();
  }

  addPerson(fields: Partial<Person> = {}): Person {
    const now = this.stamp();
    const person: Person = {
      id: this.nextId(),
      display_name: "Ada Example",
      birthday: null,
      timezone: null,
      interests: [],
      preferences: [],
      created_at: now,
      updated_at: now,
      ...fields,
    };
    this.people.push(person);
    return person;
  }

  addNote(personId: string, content: string): Note {
    const now = this.stamp();
    const note: Note = { id: this.nextId(), person_id: personId, content, created_at: now, updated_at: now };
    this.notes.push(note);
    return note;
  }

  /** Answer the next matching call with `respond` instead of the normal behaviour. */
  failNext(method: string, pathPattern: RegExp, respond: () => Response): void {
    this.overrides.push({ match: (c) => c.method === method && pathPattern.test(c.path), respond, once: true });
  }

  /** Answer every matching call with `respond`. */
  failAlways(method: string, pathPattern: RegExp, respond: () => Response): void {
    this.overrides.push({ match: (c) => c.method === method && pathPattern.test(c.path), respond, once: false });
  }

  /** Hold the next matching call until the returned gate is resolved with its response. */
  holdNext(method: string, pathPattern: RegExp): Deferred<Response> {
    const gate = deferred<Response>();
    this.overrides.push({ match: (c) => c.method === method && pathPattern.test(c.path), respond: () => gate.promise, once: true });
    return gate;
  }

  callsTo(method: string, pathPattern: RegExp): Call[] {
    return this.calls.filter((c) => c.method === method && pathPattern.test(c.path));
  }

  fetch: typeof fetch = async (input, init) => {
    const url = new URL(String(input instanceof URL ? input.href : input));
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const rawBody = typeof init?.body === "string" ? init.body : undefined;
    const call: Call = {
      method: init?.method ?? "GET",
      path: url.pathname,
      search: url.search,
      body: rawBody === undefined ? undefined : JSON.parse(rawBody),
      authorization: headers.Authorization ?? null,
      contentType: headers["Content-Type"] ?? null,
    };
    this.calls.push(call);

    const index = this.overrides.findIndex((o) => o.match(call));
    if (index >= 0) {
      const override = this.overrides[index];
      if (override.once) this.overrides.splice(index, 1);
      return override.respond();
    }
    if (call.authorization !== `Bearer ${TOKEN}`) return json(401, errorBody("unauthorized"));
    return this.route(call);
  };

  private route(call: Call): Response {
    const segments = call.path.split("/").filter(Boolean); // api, v1, people, :id, notes, :noteId
    if (segments[2] !== "people") return json(404, errorBody("not_found"));
    const [, , , personId, tail] = segments;

    if (personId === undefined) {
      if (call.method === "GET") return json(200, { items: [...this.people].sort(byName) });
      if (call.method === "POST") return this.createPerson(call.body);
      return json(405, errorBody("method_not_allowed"));
    }
    const person = this.people.find((p) => p.id === personId);
    if (!person) return json(404, errorBody("not_found"));

    if (tail === undefined) {
      if (call.method === "GET") return json(200, person);
      if (call.method === "PATCH") return this.updatePerson(person, call.body);
      if (call.method === "DELETE") {
        this.people = this.people.filter((p) => p.id !== personId);
        this.notes = this.notes.filter((n) => n.person_id !== personId);
        return new Response(null, { status: 204 });
      }
    }
    if (tail === "notes" && segments.length === 5) {
      if (call.method === "GET") return this.listNotes(person, call.search);
      if (call.method === "POST") return this.createNote(person, call.body);
    }
    return json(404, errorBody("not_found"));
  }

  private invalid(field: string, type = "value_error"): Response {
    return json(422, errorBody("validation_error", { fields: [{ loc: ["body", field], type }] }));
  }

  private createPerson(body: unknown): Response {
    const b = (body ?? {}) as Record<string, unknown>;
    const name = typeof b.display_name === "string" ? b.display_name.trim() : "";
    if (name === "" || name.length > 120) return this.invalid("display_name");
    if ("timezone" in b || "owner_id" in b) return this.invalid("<unknown field>", "extra_forbidden");
    const person = this.addPerson({
      display_name: name,
      birthday: (b.birthday as string | null | undefined) ?? null,
      interests: (b.interests as string[] | undefined) ?? [],
      preferences: (b.preferences as string[] | undefined) ?? [],
    });
    return json(201, person);
  }

  private updatePerson(person: Person, body: unknown): Response {
    const b = (body ?? {}) as Record<string, unknown>;
    if (Object.keys(b).length === 0) return this.invalid("body");
    if ("timezone" in b || "owner_id" in b) return this.invalid("<unknown field>", "extra_forbidden");
    if ("display_name" in b) {
      const name = typeof b.display_name === "string" ? b.display_name.trim() : "";
      if (name === "" || name.length > 120) return this.invalid("display_name");
      person.display_name = name;
    }
    if ("birthday" in b) person.birthday = b.birthday as string | null;
    if ("interests" in b) person.interests = b.interests as string[];
    if ("preferences" in b) person.preferences = b.preferences as string[];
    person.updated_at = this.stamp();
    return json(200, person);
  }

  private listNotes(person: Person, search: string): Response {
    const params = new URLSearchParams(search);
    const limit = params.has("limit") ? Number(params.get("limit")) : 20;
    const offset = params.has("offset") ? Number(params.get("offset")) : 0;
    if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) {
      return json(422, errorBody("validation_error", { fields: [{ loc: ["query", "limit"], type: "int_parsing" }] }));
    }
    const mine = this.notes
      .filter((n) => n.person_id === person.id)
      .sort((a, b) => (a.created_at === b.created_at ? (a.id < b.id ? 1 : -1) : a.created_at < b.created_at ? 1 : -1));
    return json(200, { items: mine.slice(offset, offset + limit) });
  }

  private createNote(person: Person, body: unknown): Response {
    const content = (body as { content?: unknown } | undefined)?.content;
    if (typeof content !== "string" || content.trim() === "" || content.length > 10_000) return this.invalid("content");
    return json(201, this.addNote(person.id, content));
  }
}

function byName(a: Person, b: Person): number {
  return a.display_name.toLowerCase() < b.display_name.toLowerCase() ? -1 : 1;
}
