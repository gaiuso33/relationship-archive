/**
 * Checks for the person form. They mirror the API's rules (docs/people-api.md) so mistakes are
 * caught before a request. The API stays the authority: its stricter checks (for example on
 * invisible characters) come back as field errors.
 *
 * Lengths count characters the way the API does (code points), not UTF-16 units, so an emoji
 * counts once.
 */

export const DISPLAY_NAME_MAX = 120;
export const ENTRY_MAX = 100;
export const MAX_ENTRIES = 50;
export const EARLIEST_BIRTHDAY = "1900-01-01";

export function countCharacters(text: string): number {
  return Array.from(text).length;
}

const CONTROL_OR_SEPARATOR = /[\p{Cc}\p{Zl}\p{Zp}]/u;

export type Checked<T> = { ok: true; value: T } | { ok: false; error: string };

export function checkDisplayName(raw: string): Checked<string> {
  const value = raw.trim();
  if (value === "") return { ok: false, error: "Enter a name." };
  if (countCharacters(value) > DISPLAY_NAME_MAX) {
    return { ok: false, error: `Use ${DISPLAY_NAME_MAX} characters or fewer.` };
  }
  if (CONTROL_OR_SEPARATOR.test(value)) return { ok: false, error: "The name contains characters that are not allowed." };
  return { ok: true, value };
}

/** `raw` is the value of a date input (`YYYY-MM-DD`), or empty for "no birthday". */
export function checkBirthday(raw: string, today: string): Checked<string | null> {
  if (raw === "") return { ok: true, value: null };
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
  if (!match) return { ok: false, error: "Enter a valid date." };
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) {
    return { ok: false, error: "Enter a valid date." };
  }
  if (raw < EARLIEST_BIRTHDAY) return { ok: false, error: "The date cannot be before 1 January 1900." };
  if (raw > today) return { ok: false, error: "The date cannot be in the future." };
  return { ok: true, value: raw };
}

/** One interest or preference. `existing` is the list so far, to refuse a repeat (ignoring case). */
export function checkEntry(raw: string, existing: readonly string[]): Checked<string> {
  const value = raw.trim();
  if (value === "") return { ok: false, error: "Enter something to add." };
  if (countCharacters(value) > ENTRY_MAX) return { ok: false, error: `Use ${ENTRY_MAX} characters or fewer.` };
  if (CONTROL_OR_SEPARATOR.test(value)) return { ok: false, error: "That contains characters that are not allowed." };
  if (existing.length >= MAX_ENTRIES) return { ok: false, error: `You can add at most ${MAX_ENTRIES}.` };
  const key = value.toLowerCase();
  if (existing.some((entry) => entry.toLowerCase() === key)) return { ok: false, error: "That is already in the list." };
  return { ok: true, value };
}

/** What can be searched for. The API applies the same rules (docs/people-api.md#searching-by-name). */
export function checkSearchQuery(raw: string): Checked<string> {
  const value = raw.trim();
  if (value === "") return { ok: false, error: "Enter part of a name to search for." };
  if (countCharacters(value) > DISPLAY_NAME_MAX) {
    return { ok: false, error: `Use ${DISPLAY_NAME_MAX} characters or fewer.` };
  }
  if (CONTROL_OR_SEPARATOR.test(value)) return { ok: false, error: "The search contains characters that are not allowed." };
  return { ok: true, value };
}

export interface PersonFormValues {
  displayName: string;
  birthday: string;
  interests: string[];
  interestsDraft: string;
  preferences: string[];
  preferencesDraft: string;
}

export interface PersonFormErrors {
  displayName?: string;
  birthday?: string;
  interests?: string;
  preferences?: string;
}

export interface CheckedPerson {
  display_name: string;
  birthday: string | null;
  interests: string[];
  preferences: string[];
}

const PENDING = "You typed something that has not been added. Press Add, or clear the box.";

export function checkPersonForm(
  values: PersonFormValues,
  today: string,
): { ok: true; value: CheckedPerson } | { ok: false; errors: PersonFormErrors } {
  const errors: PersonFormErrors = {};
  const name = checkDisplayName(values.displayName);
  if (!name.ok) errors.displayName = name.error;
  const birthday = checkBirthday(values.birthday, today);
  if (!birthday.ok) errors.birthday = birthday.error;
  if (values.interests.length > MAX_ENTRIES) errors.interests = `You can add at most ${MAX_ENTRIES}.`;
  else if (values.interestsDraft.trim() !== "") errors.interests = PENDING;
  if (values.preferences.length > MAX_ENTRIES) errors.preferences = `You can add at most ${MAX_ENTRIES}.`;
  else if (values.preferencesDraft.trim() !== "") errors.preferences = PENDING;
  if (!name.ok || !birthday.ok || errors.interests || errors.preferences) return { ok: false, errors };
  return {
    ok: true,
    value: {
      display_name: name.value,
      birthday: birthday.value,
      interests: values.interests,
      preferences: values.preferences,
    },
  };
}
