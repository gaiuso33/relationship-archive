# People API

The owner-scoped people endpoints (issue #12). Every person belongs to exactly one user, and
that user is the only one who can see or change them ([ADR-002](decisions/ADR-002-authentication-and-authorization.md)).

All endpoints are under `/api/v1/people`, require `Authorization: Bearer <access token>`
([authentication.md](authentication.md)). Successful responses send `Cache-Control: no-store`;
error responses (401, 404, 422, 503) do not currently send it, and they contain no personal
data. Errors use the standard format in [logging-and-errors.md](logging-and-errors.md).

## Endpoints

| Method and path | Success | Purpose |
|---|---|---|
| `GET /people` | 200 `{"items": [Person, ...]}` | List the caller's people, ordered by name (case-insensitive), then id. |
| `POST /people` | 201 `Person` | Create a person. |
| `POST /people/search` | 200 `{"items": [Person, ...]}` | Search the caller's people by name ([below](#searching-by-name)). |
| `GET /people/{person_id}` | 200 `Person` | Retrieve one of the caller's people. |
| `PATCH /people/{person_id}` | 200 `Person` | Change some fields of one of the caller's people. |
| `DELETE /people/{person_id}` | 204, no body | Delete one of the caller's people, **and all their notes**. |

Other statuses: `401` (missing or bad token), `404 not_found` (no such person *for this user*; not used by search),
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

## Searching by name

`POST /api/v1/people/search` with the body `{"query": "..."}` returns the caller's people whose
**name** contains the text, in the same shape and order as the list (case-insensitive name, then
id). It changes nothing. It is a `POST` so the text, which is part of a person's name, travels in
the body and never in a URL, where hosting and proxy logs, browser history and referrers can
record it. (The application's own logs record neither query strings nor bodies.)

**Names only.** Notes, interests, preferences, birthdays, time zones and identifiers are never
searched. This is the owner's decision of 2026-10-31 recorded in the product requirements
(section 5.2 and journey J7): searching for "hiking" returns nobody whose only "hiking" is in a
note, interest or preference. Tests check this.

**Matching rules**

- The text is trimmed, then must be 1 to 120 characters (code points) with no control characters:
  the same rules as a name, so a blank text, a control character or an invisible separator such
  as a zero-width joiner, a no-break space or a byte-order mark is a `422`.
- The whole phrase is matched as one piece of literal text anywhere in the name. It is not split
  into words, so "Friend Sample" does not match "Sample Friend".
- Case does not matter (see the limits below). Accents, spelling variants and typos do matter:
  there is no accent folding, no Unicode normalisation, no fuzzy matching and no ranking.
- `%`, `_`, `\` and `/` match themselves. The statement is
  `display_name ILIKE '%' || :text || '%' ESCAPE '/'` with the text as a bound parameter in which
  `%`, `_` and `/` are escaped; it is never part of the SQL text. Injection-style text is just
  text that matches nothing (or a name that really contains it).
- There is no pagination, like the list. A search that matches nobody is `200` with
  `{"items": []}`, not an error.

**Case-insensitivity depends on the database's collation.** It is PostgreSQL's `ILIKE`, which
follows the database's locale settings, so this is not universal Unicode case folding. Checked on
PostgreSQL 17.10 (one-off, by hand; CI runs only the `postgres:17` image's default, which is
expected to be `en_US.UTF-8` and which CI now records, see below) against five databases:

| Behaviour | libc `en_US.UTF-8`, libc `C.UTF-8`, builtin `C.UTF-8` | ICU `en-US` | `C` locale |
|---|---|---|---|
| ASCII (`ADA` finds `Ada`) | folds | folds | folds |
| Accented Latin (`élodie` finds `Élodie`), Greek, Cyrillic, Armenian, Vietnamese, full-width Latin | folds | folds | **does not fold** |
| Dotted capital `İ` found by plain `i` | matches | does not match | does not match |
| Greek final sigma `ς` found by `Σ` | does not match | matches | does not match |
| `ß` found by `SS` | never | never | never |
| `ß` in the name found by a capital `ẞ` | matches | matches | does not match |
| `é` as one character vs `e` plus a combining accent | never equal | never equal | never equal |

The last row is the practical one: text typed or pasted in a different Unicode form from the
stored name will not match. Scripts without case (such as Chinese) match exactly.

**Finding out what a database uses.** Run `python -m app.tools.db_collation` against it (it
reads `DATABASE_URL`, changes nothing and prints no connection details), or run this SQL, which
works on PostgreSQL 15, 16 and 17 (checked on 16.15 and 17.10):

```sql
select split_part(version(), ' ', 2) as server,
       pg_encoding_to_char(encoding) as encoding,
       datlocprovider, datcollate, datctype,
       to_jsonb(d)->>'datlocale'    as datlocale,      -- PostgreSQL 17 and later
       to_jsonb(d)->>'daticulocale' as daticulocale,   -- PostgreSQL 15 and 16
       datcollversion
from pg_database d
where datname = current_database();
```

Read it by provider (`datlocprovider`: `c` libc, `i` ICU, `b` builtin). For `c`, the locale is
`datcollate` and `datctype`. **For `i` and `b`, `datcollate` and `datctype` are still filled in
(with the cluster's defaults, for example `C.UTF-8`) but are not what the database uses**: the
locale is in `datlocale` (17+) or `daticulocale` (15 and 16). A query that omits those columns
shows an ICU `en-US` database as `C.UTF-8`. A column can also carry its own collation, so check
`people.display_name` too (the tool does):

```sql
select collation_name from information_schema.columns
where table_name = 'people' and column_name = 'display_name';   -- null: the database default
```

PostgreSQL version differences: `datlocprovider` and `datcollversion` exist from 15, so this SQL
fails on 14 and earlier (there the database locale is always libc, and ICU is per collation);
the ICU locale column was renamed from `daticulocale` to `datlocale` in 17; the `builtin`
provider is new in 17. The production database's collation is not known yet (hosting is open
under #33). Before relying on non-ASCII folding there, follow
[database-collation-checklist.md](database-collation-checklist.md). CI records the value it runs
against in each backend job's summary; it does not require any particular collation.

**Tests** (`backend/tests/test_people_search_api.py`) cover the J7 example, another user's
people (never returned, including with an identical name and at the service layer), names-only,
wildcards and escape characters, injection-style input, validation without echoing the text,
authentication before anything else, and the absence of the text and of any person details from
logs and error bodies. The non-ASCII tests compare the search with the database's own `lower()`
instead of fixed expectations, so they pass on every collation above (the whole file was run
against all five databases).

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

## Deleting a person deletes their notes

Deleting a person permanently deletes every note about them ([notes-api.md](notes-api.md)).
This is done by the database (a foreign key with `ON DELETE CASCADE`), not by application code,
so it cannot be skipped. Notes about the caller's other people, and every other user's notes,
are not affected. There is no soft delete and no undo, so clients should ask for confirmation.

## Storage

Table `people` (migration `0002`): `id`, `owner_id` (indexed, no foreign key because users live
in Supabase), `display_name` (non-blank check), `birthday`, `timezone`, `interests` and
`preferences` (PostgreSQL `text[]`, default empty), `created_at`, `updated_at`. Migration `0003`
adds the unique constraint `uq_people_id_owner_id` on `(id, owner_id)`, which the notes table's
composite foreign key refers to.

## Tests

`backend/tests/test_people_api.py` (two synthetic users, real PostgreSQL, fake signing keys) and
`backend/tests/test_migrations.py`. They enumerate the routes from the OpenAPI schema, so an
unauthenticated route added later fails the suite.
