# Notes API

The owner-scoped notes endpoints (issue #13). A note is a piece of written text about one
person, and it belongs to the same user as that person. Notes are the most sensitive content in
the system: only their owner can read or change them ([ADR-002](decisions/ADR-002-authentication-and-authorization.md)).

All endpoints are nested under a person, require `Authorization: Bearer <access token>`
([authentication.md](authentication.md)), and send `Cache-Control: no-store` on successful
responses. Error responses do not send it, and they never contain note text. Errors use the
standard format in [logging-and-errors.md](logging-and-errors.md).

## Endpoints

| Method and path | Success | Purpose |
|---|---|---|
| `GET /api/v1/people/{person_id}/notes` | 200 `{"items": [Note, ...]}` | List notes about one of the caller's people, newest first. |
| `POST /api/v1/people/{person_id}/notes` | 201 `Note` | Add a note. |
| `PATCH /api/v1/people/{person_id}/notes/{note_id}` | 200 `Note` | Edit a note. |
| `DELETE /api/v1/people/{person_id}/notes/{note_id}` | 204, no body | Delete a note. |

There is deliberately no endpoint that reads a single note: the list returns the full text.

Other statuses: `401` (missing or bad token), `404 not_found`, `422 validation_error` (invalid
input, including a malformed identifier or pagination value), `503` (token verification
unavailable).

## Note

| Field | Type | Notes |
|---|---|---|
| `id` | UUID | Server-generated. |
| `person_id` | UUID | The person the note is about, from the URL. |
| `content` | string | See the rules below. |
| `created_at`, `updated_at` | timestamp (UTC) | Server-set. An edit changes `updated_at` only. |

The owner is never part of a request or a response.

**Create** and **edit** both take `{"content": "..."}`, and nothing else: other fields,
including `owner_id` and `person_id`, are rejected with `422`. An edit replaces the whole text,
and `content` is required. (`PATCH` is used because the note is changed in place and `id`,
`person_id` and `created_at` are kept.)

## Content rules

- 1 to 10,000 **characters** (Unicode code points, not bytes).
- Blank text is rejected: empty, or made only of whitespace.
- The text is stored **exactly as sent**. Leading and trailing whitespace, newlines (`\n`, `\r\n`)
  and tabs are kept.
- NUL and other control characters (for example `\x00`, `\x07`, `\x1b`, `\x7f`, `\x0b`, `\x0c`,
  `\x85`) are rejected. So are lone surrogate code points, which cannot be stored.
- Invalid input is always a `422`, never a `500`.

The database enforces the same limits with check constraints (`ck_notes_content_not_blank` and
`ck_notes_content_max_length`), so no code path can store a blank or oversized note.

## Pagination and ordering

`GET .../notes` takes two query parameters:

| Parameter | Default | Range |
|---|---|---|
| `limit` | 20 | 1 to 100 |
| `offset` | 0 | 0 to 2,147,483,647 |

Values outside the range, or not whole numbers, are a `422`. Notes are ordered newest first by
`created_at`, then by `id` descending as a tie-breaker, so pages are deterministic and do not
overlap. The response has no total count: request the next page (`offset = offset + limit`)
until fewer than `limit` items come back. Both filters, the authenticated owner and the
requested person, are always applied. A person with no notes gives `200` and `{"items": []}`.

## Ownership and how other users' records are reported

- The owner is the verified token's `sub`, supplied by the `require_user` dependency. No body
  field, query parameter or header can choose or change it. The person comes only from the URL.
- Every query in `app/services/notes.py` takes the owner and the person as required arguments
  and filters on them in SQL.
  - **Adding** a note is one `INSERT ... SELECT` that produces a row only if the person exists
    and belongs to the caller, so the ownership test and the write cannot be separated.
  - **Editing and deleting** constrain the note id, the person id and the owner in the same
    statement.
  - **Listing** first checks that the person is the caller's, so another user's person is `404`
    while your own person with no notes is `200`.
- The database adds a backstop: a note references its person through the composite foreign key
  `(person_id, owner_id) -> people (id, owner_id)`. A note therefore cannot have a different owner
  from its person, even if application code had a defect. It is not the authorization mechanism:
  the application checks first, so a foreign person is a `404`, never a database error.
- **A person or note that belongs to another user is reported as `404 not_found`, identical to
  one that does not exist** (same status, code and message). So is a note id used under a
  different person, even one of the caller's own. A `403` is never used.

## Deleting

Notes are deleted permanently (hard delete): there is no soft delete and no undo.

Deleting a person deletes all their notes, through the database foreign key (`ON DELETE
CASCADE`). Notes about the caller's other people, and other users' notes, are not affected
([people-api.md](people-api.md)).

## Privacy guarantees

- Note text is never written to logs. The access log has no bodies, and the route is logged as a
  template without identifiers.
- Note text never appears in an error response. A `422` reports only field locations and error
  types, and database errors are translated to fixed messages with no values, even though
  PostgreSQL's own error text can quote the failing row.
- Tests check these with distinctive synthetic note text, in both log formats, including a forced
  database error.
- Successful responses are not cacheable (`Cache-Control: no-store`).
- Notes are not sent to any external service. There is no search, AI extraction or provenance in
  this API; those are later issues.

## Storage

Table `notes` (migration `0003`): `id`, `owner_id` and `person_id` (not null, no foreign key to
users, which live in Supabase), `content` (text), `created_at` and `updated_at` (set with
`clock_timestamp()`, the real time of the write, so notes written in quick succession order
correctly), the composite foreign key above, the two content check constraints, and the index
`ix_notes_owner_id_person_id_created_at`, which serves the list query and the cascade.

Rolling back (`alembic downgrade 0002`) deletes all notes, then removes the unique constraint
that the notes key depends on.

## Known behaviour

FastAPI parses the raw request body before it runs authentication. A body that is not valid JSON
therefore gets `422` even without a token. Valid JSON without a token gets `401` before any
field is validated. Nothing about notes is revealed either way.

## Tests

`backend/tests/test_notes_api.py` (two synthetic users, real PostgreSQL, real token
verification with fake signing keys) and `backend/tests/test_migrations.py`, which also checks
the composite key, the cascade and the content constraints directly in the database. Routes are
enumerated from the OpenAPI schema, so a notes route added later without authentication fails the
suite.
