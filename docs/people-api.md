# People API

The owner-scoped people endpoints (issue #12). Every person belongs to exactly one user, and
that user is the only one who can see or change them ([ADR-002](decisions/ADR-002-authentication-and-authorization.md)).

All endpoints are under `/api/v1/people`, require `Authorization: Bearer <access token>`
([authentication.md](authentication.md)), and send `Cache-Control: no-store`. Errors use the
standard format in [logging-and-errors.md](logging-and-errors.md).

## Endpoints

| Method and path | Success | Purpose |
|---|---|---|
| `GET /people` | 200 `{"items": [Person, ...]}` | List the caller's people, ordered by name (case-insensitive), then id. |
| `POST /people` | 201 `Person` | Create a person. |
| `GET /people/{person_id}` | 200 `Person` | Retrieve one of the caller's people. |
| `PATCH /people/{person_id}` | 200 `Person` | Change some fields of one of the caller's people. |
| `DELETE /people/{person_id}` | 204, no body | Delete one of the caller's people. |

Other statuses: `401` (missing or bad token), `404 not_found` (no such person *for this user*),
`422 validation_error` (invalid input, including a malformed `person_id`), `503` (token
verification unavailable). There is no list pagination yet: the archive is a single user's
personal list.

## Person

| Field | Type | Rules |
|---|---|---|
| `id` | UUID | Server-generated. |
| `display_name` | string | Required. Trimmed, 1 to 120 characters, no control characters. |
| `birthday` | date (`YYYY-MM-DD`) or null | Optional. From 1900-01-01 to today (one day of slack for time zones). |
| `timezone` | string or null | Optional IANA name such as `Africa/Lagos`, at most 64 characters. Only the shape is checked, not membership of the time zone database. |
| `interests` | list of strings | Optional, default `[]`. |
| `preferences` | list of strings | Optional, default `[]`. |
| `created_at`, `updated_at` | timestamp (UTC) | Server-set. `updated_at` changes on every update. |

`interests` and `preferences` are user-entered text. Limits: at most 50 entries per list, each
1 to 100 characters after trimming, no control characters. Entries are trimmed, and
case-insensitive duplicates are dropped (the first spelling is kept, order is preserved). An
empty, oversized or too numerous entry set is rejected with `422`. AI-suggested or discovered
values are out of scope and will be stored separately (ADR-003).

`PATCH` is a partial update: only the fields sent change, and at least one must be sent.
`display_name`, `interests` and `preferences` cannot be `null` (send `[]` to clear a list);
`birthday` and `timezone` can be `null` to clear them.

Requests reject unknown fields. The owner is never part of a request or a response.

## Ownership and how other users' records are reported

- The owner is the verified token's `sub`, supplied by the `require_user` dependency. No body
  field, query parameter or header can choose or change it. Sending `owner_id` (or any other
  unknown field) is a `422`, and nothing is stored.
- All data access goes through `app/services/people.py`. Every function takes the owner as a
  required argument, and every statement filters on it in SQL (`update` and `delete` included, so
  the check and the change are one atomic statement). Nothing looks a person up by id alone.
- **Another user's person is reported as `404 not_found`, exactly like a person that does not
  exist** (same status, code and message). This avoids revealing that an identifier belongs to
  someone else, so identifiers cannot be probed. A `403` is never used for this.
- Identifiers are random UUIDs, but they are not treated as secret: ownership, not obscurity,
  is the protection.
- Names, birthdays and entries are never written to logs or error bodies; a `422` reports only
  field locations and error types.

## Storage

Table `people` (migration `0002`): `id`, `owner_id` (indexed, no foreign key because users live
in Supabase), `display_name` (non-blank check), `birthday`, `timezone`, `interests` and
`preferences` (PostgreSQL `text[]`, default empty), `created_at`, `updated_at`.

## Tests

`backend/tests/test_people_api.py` (two synthetic users, real PostgreSQL, fake signing keys) and
`backend/tests/test_migrations.py`. They enumerate the routes from the OpenAPI schema, so an
unauthenticated route added later fails the suite.
