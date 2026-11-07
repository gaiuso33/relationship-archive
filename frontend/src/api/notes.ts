import { ApiError, type ApiClient } from "./client";
import { isUuid, personPath } from "./people";
import type { Note } from "./types";

/** The API's default page size for notes. A full page means there may be more. */
export const NOTES_PAGE_SIZE = 20;

export function parseNote(value: unknown): Note {
  if (typeof value === "object" && value !== null) {
    const n = value as Record<string, unknown>;
    if (
      typeof n.id === "string" &&
      isUuid(n.id) &&
      typeof n.person_id === "string" &&
      isUuid(n.person_id) &&
      typeof n.content === "string" &&
      typeof n.created_at === "string" &&
      typeof n.updated_at === "string"
    ) {
      return { id: n.id, person_id: n.person_id, content: n.content, created_at: n.created_at, updated_at: n.updated_at };
    }
  }
  throw new ApiError("unexpected");
}

/** One page of notes about a person, newest first. */
export async function listNotes(
  api: ApiClient,
  personId: string,
  page: { limit: number; offset: number },
  signal?: AbortSignal,
): Promise<Note[]> {
  const body = await api.request({
    method: "GET",
    path: `${personPath(personId)}/notes`,
    query: { limit: page.limit, offset: page.offset },
    signal,
  });
  const items = typeof body === "object" && body !== null ? (body as { items?: unknown }).items : undefined;
  if (!Array.isArray(items)) throw new ApiError("unexpected");
  return items.map(parseNote);
}

/** Adds a note. The text is sent exactly as given, and only in the request body. */
export async function createNote(api: ApiClient, personId: string, content: string, signal?: AbortSignal): Promise<Note> {
  return parseNote(
    await api.request({ method: "POST", path: `${personPath(personId)}/notes`, body: { content }, signal }),
  );
}
