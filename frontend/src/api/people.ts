import { ApiError, type ApiClient } from "./client";
import type { Person, PersonChanges, PersonInput } from "./types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isUuid(value: string): boolean {
  return UUID.test(value);
}

/** The path for one person. An identifier that is not a UUID can never exist, so it is "not found" without a request. */
export function personPath(personId: string): string {
  if (!isUuid(personId)) throw new ApiError("not_found");
  return `/api/v1/people/${personId}`;
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

/** Checks the shape of a person from the API, so a surprising response cannot crash a screen. */
export function parsePerson(value: unknown): Person {
  if (typeof value === "object" && value !== null) {
    const p = value as Record<string, unknown>;
    if (
      typeof p.id === "string" &&
      isUuid(p.id) &&
      typeof p.display_name === "string" &&
      (p.birthday === null || typeof p.birthday === "string") &&
      (p.timezone === null || typeof p.timezone === "string") &&
      isStringList(p.interests) &&
      isStringList(p.preferences) &&
      typeof p.created_at === "string" &&
      typeof p.updated_at === "string"
    ) {
      return {
        id: p.id,
        display_name: p.display_name,
        birthday: p.birthday,
        timezone: p.timezone,
        interests: p.interests,
        preferences: p.preferences,
        created_at: p.created_at,
        updated_at: p.updated_at,
      };
    }
  }
  throw new ApiError("unexpected");
}

export async function listPeople(api: ApiClient, signal?: AbortSignal): Promise<Person[]> {
  const body = await api.request({ method: "GET", path: "/api/v1/people", signal });
  const items = typeof body === "object" && body !== null ? (body as { items?: unknown }).items : undefined;
  if (!Array.isArray(items)) throw new ApiError("unexpected");
  return items.map(parsePerson);
}

export async function getPerson(api: ApiClient, personId: string, signal?: AbortSignal): Promise<Person> {
  return parsePerson(await api.request({ method: "GET", path: personPath(personId), signal }));
}

export async function createPerson(api: ApiClient, input: PersonInput, signal?: AbortSignal): Promise<Person> {
  // Built field by field, so nothing else (such as `timezone` or an owner) can be sent by accident.
  const body: PersonInput = {
    display_name: input.display_name,
    birthday: input.birthday,
    interests: input.interests,
    preferences: input.preferences,
  };
  return parsePerson(await api.request({ method: "POST", path: "/api/v1/people", body, signal }));
}

export async function updatePerson(
  api: ApiClient,
  personId: string,
  changes: PersonChanges,
  signal?: AbortSignal,
): Promise<Person> {
  const body: PersonChanges = {};
  if (changes.display_name !== undefined) body.display_name = changes.display_name;
  if (changes.birthday !== undefined) body.birthday = changes.birthday;
  if (changes.interests !== undefined) body.interests = changes.interests;
  if (changes.preferences !== undefined) body.preferences = changes.preferences;
  return parsePerson(await api.request({ method: "PATCH", path: personPath(personId), body, signal }));
}

export async function deletePerson(api: ApiClient, personId: string, signal?: AbortSignal): Promise<void> {
  await api.request({ method: "DELETE", path: personPath(personId), signal });
}
