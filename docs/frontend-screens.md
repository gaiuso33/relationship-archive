# People and notes screens

The first screens of the archive (issue #14): a list of people, a form to add or edit one, a
person's page with their notes, and deleting a person. They sit on the sign-in, protected routes
and API client from [frontend-auth.md](frontend-auth.md) and call the API described in
[people-api.md](people-api.md) and [notes-api.md](notes-api.md). The backend was not changed.

**Status.** Everything below is covered by automated tests that use a fake Supabase client and a
fake in-memory API with synthetic data, and was checked by eye in a real browser against mocked API
responses (see "Checked in a browser"). **It has not been run against a real backend or a real
Supabase project**; production hosting and CORS are open under issue #33.

## Routes

All routes except `/sign-in` are inside `RequireAuth`, so a signed-out visitor is sent to sign-in
first and brought back afterwards.

| Route | Screen |
| --- | --- |
| `/` | Redirects to `/people`. The old home page was retired. |
| `/people` | The list of people, with "Add person". |
| `/people/new` | Add a person. |
| `/people/:personId` | A person's details, their notes, and deleting them. |
| `/people/:personId/edit` | Edit a person. |
| anything else | "Page not found". |

A `:personId` that is not a UUID can never exist, so it shows "Not found" without any request. A
person that does not exist and a person that belongs to someone else look the same ("Not found"),
matching the API's 404.

## What each screen does

- **List.** Loading, error (with "Try again"), empty ("You have not added anyone yet.") and list
  states. People appear in the order the API returns them. Birthdays are shown as calendar dates.
- **Search.** A "Search by name" box above the list (`pages/PeopleListPage.tsx`), submitted with
  Enter or the Search button, so nothing is sent while typing. It calls
  `POST /api/v1/people/search` ([people-api.md](people-api.md#searching-by-name)) and searches
  **names only**, which the screen says in its no-results message. The text is trimmed and checked
  first (1 to 120 characters, no control characters; `people/validation.ts`). Searching shows
  "Searching…", then "N people match “text”." in a status region, or "No one matches “text”." with
  the Clear button. **Clear** returns to the full list, empties the box and puts focus back in it.
  Errors use the same fixed messages with "Try again"; a `422` says the text cannot be searched
  for, without a retry. Every submission starts a new request and cancels the one still running,
  so an older answer cannot replace a newer one.
- **Form.** Name, optional birthday, and interests and preferences as add/remove lists. Checks run
  before any request and mirror the API's rules (`people/validation.ts`): name 1–120 characters,
  birthday between 1900-01-01 and today, up to 50 entries of 1–100 characters, no repeats ignoring
  case. Lengths count characters the way the API does (code points), so an emoji counts once. The
  API stays the authority: a `422` is mapped to messages beside the right fields from the error's
  `fields` (location and kind only, never text from the response). Text typed in an entry box but
  never added is reported rather than silently dropped.
- **Edit** sends only the fields that changed, and says "No changes to save." when nothing did.
  Concurrent edits are last-write-wins (the API has no version check); there is no
  unsaved-changes warning when leaving the form.
- **Time zone** is deliberately not on the form and is never sent, so editing a person cannot
  change it.
- **Person page.** Details, then notes, then "Delete person". Moving from one person to another
  starts from a clean screen.
- **Notes.** Add a note (a plain text box with a character count against the 10,000 limit), and
  read notes newest first, 20 at a time with "Load more notes". The next page starts after the
  notes already shown (including ones added during the visit), and a note is never listed twice.
  The API returns no total, so "Load more" is offered whenever the last page was full; if that was
  exactly the end, the next click finds nothing and the button disappears. **Editing and deleting
  notes is not offered** (follow-up issue #70).
- **Delete person.** A second, explicit step names the consequence (their notes are deleted too and
  it cannot be undone). Focus moves to "Cancel" when it opens and returns to the opener on cancel.
  Success returns to the list with "Person deleted.".

One-time messages ("Person added.", "Changes saved.", "Person deleted.") travel as a fixed key in
the navigation state and are looked up in a fixed table; no name or other text from the archive is
put in history. The key is removed once shown.

## Privacy

- **The search text** is kept only in the component's state: not in the URL (the page stays at
  `/people`), not in the history, `localStorage`, `sessionStorage` or cookies, not in the page
  title and not in the console, and it is forgotten when the person leaves the screen. On the
  wire it is only in the body of the `POST`. It is shown back as plain text. The cost is that going
  to a person and back starts from the full list.
- Note text and people's details live only in component state. Nothing is written to
  `localStorage`, `sessionStorage` or cookies by these screens, and an unsent draft is discarded
  when the person leaves the screen or the session ends.
- Nothing from the archive is logged (the lint rule `no-console` is an error, and tests spy on the
  console through success and failure paths).
- Note text is sent only in the body of `POST /people/:id/notes`, never in a URL or header, and is
  kept exactly as typed (no trimming).
- Note text and every other field is rendered as plain text (never as HTML; `dangerouslySetInnerHTML`
  is banned by lint). Notes keep their line breaks with CSS `white-space: pre-wrap`.
- Error messages are fixed text for the kind of failure. Bodies from failed responses are never
  shown, so a response that echoed a note would not display it.

## Accessibility

Every input has a visible label; errors are announced (`role="alert"`) and linked to their input
(`aria-invalid`, `aria-describedby`); loading, success and list changes use `role="status"`. A "Skip
to main content" link comes first, navigation is labelled, and each screen has one `h1` that takes
focus when the screen changes so keyboard and screen reader users start at the top. Everything works
from the keyboard (Enter adds an interest without sending the form); focus outlines are visible;
buttons and links are at least 44px tall; colours follow the light or dark setting of the device.
Layout is a single column on a phone and widens from 40rem. This is hand-written CSS in
`src/styles.css`, with no UI library.

## Client-side files

- `api/people.ts`, `api/notes.ts`: typed helpers. Responses are shape-checked, so a surprising body
  becomes a safe "unexpected response" rather than breaking a screen. Everything goes through the
  API client's checks (see [frontend-auth.md](frontend-auth.md)): allow-listed paths, configured
  origin, plain integer query values, JSON bodies only for `POST` and `PATCH`, the stale-token
  rule for a `401`, and no automatic retries.
- `people/validation.ts`, `notes/validation.ts`, `format.ts`: rules and date formatting. Birthdays
  are built and shown in UTC so a time zone can never move them by a day.
- `pages/`, `components/`, `hooks/`: screens and shared pieces.

## Checked in a browser

The screens were rendered in headless Chromium at 375px (phone) and 1280px (desktop) wide, with a
pre-seeded synthetic session and the API mocked by the test script (no real Supabase or backend), in
these states: sign-in, list, empty, list error, add form with validation errors, person with notes,
delete confirmation, edit form, and dark mode with a keyboard focus ring. The check found no
horizontal scrolling, wrapped long names and unbroken text correctly, kept note line breaks, and
measured every button, input and standalone link at 44px tall or more (it caught two header links
under that and they were fixed). This was a one-off visual check and is not an automated test. It
was not repeated on Safari or Firefox, on real devices, or with a screen reader. On a 375px phone
the "Sign out" button wraps onto its own row under the brand, which is deliberate wrapping rather
than a defect but could be tidied later.

## Known limitations

- The client accepts a few characters the API rejects (for example a zero-width joiner or a
  non-breaking space, which the API's printable-character rule refuses). They come back as a field
  error from the API rather than being caught earlier.
- Search covers names only (owner decision; see the PRD). Whether a name matches regardless of case depends on the database's collation: see [people-api.md](people-api.md#searching-by-name).
- No birthday views, no reminders, no export, and no AI features (later milestones).
- Not tested with a real Supabase project or backend, or in production hosting.

## Tests

`pages/screens.test.tsx` (list, form, edit, detail, delete, notes, pagination), `pages/search.test.tsx` (search states, validation, errors, overlapping searches, privacy of the text), `journey.test.tsx`
(sign in → add a person → add a note → see it, and the privacy checks), `components/*.test.tsx`,
`people/validation.test.ts`, `notes/validation.test.ts`, `format.test.ts`, `api/people.test.ts`
(helpers) and `routes.test.tsx` (routes, sign-out, API failures). `src/test/fakeApi.ts` is a small
in-memory API with synthetic data. Run `npm run test -- --run` in `frontend/`. The date tests are
worth running under other time zones too, for example
`TZ=America/Los_Angeles npm run test -- --run`.
