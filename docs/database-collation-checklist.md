# Database collation checklist

Search by name (`POST /api/v1/people/search`, [people-api.md](people-api.md#searching-by-name)) uses
PostgreSQL's `ILIKE`. What counts as the same letter in different cases beyond ASCII follows the
database's locale, so it can differ between databases. This checklist records what a database
uses and shows whether search behaves as documented on it. It is for issue #33 (hosting) and for
any later change of database, PostgreSQL version or base image. It chooses no provider and forces
no collation, and it changes nothing about how search matches.

## What is and is not guaranteed

| Behaviour | Depends on the database collation? |
|---|---|
| The text is matched literally, anywhere in the name; `%`, `_`, `\` and `/` match themselves | No (the application guarantees it) |
| No accent folding, no Unicode normalisation, no fuzzy matching | No. `é` as one character and `e` plus a combining accent are never equal, on any collation |
| Only the caller's own people are searched | No |
| ASCII letters match regardless of case (`ADA` finds `Ada`) | No (every collation tested) |
| Case matching for accented Latin, Greek, Cyrillic, Armenian, Vietnamese, full-width Latin | **Yes.** The `C` locale does not fold them; libc `en_US`/`C.UTF-8`, builtin `C.UTF-8` and ICU do |
| Turkish dotted `İ` found by plain `i` | **Yes** (libc and builtin match, ICU and `C` do not) |
| Greek final sigma `ς` found by capital `Σ` | **Yes** (ICU matches, the others do not) |
| `ß` found by `SS` | No: never matches. `ß` found by a capital `ẞ` is collation-dependent |
| The order of results (case-insensitive name, then id) | **Yes** for the name part (it follows the collation) |

The table in [people-api.md](people-api.md#searching-by-name) shows what was observed on
PostgreSQL 17.10 for five setups. Treat it as a reference point, not as a promise about another
database.

## Where it is recorded today

- **Local development and CI** use the `postgres:17` image with its default locale. The image is
  built on Debian with `en_US.UTF-8` generated and `LANG=en_US.utf8`, so a new cluster is
  expected to be libc `en_US.UTF-8`. The tag can move to a newer Debian release, and with it a
  different glibc, without any change in this repository.
- **CI records the actual value** in the "Backend checks" job summary ("Database collation",
  step "Record database collation"). It is information only: the step never fails because of a
  value. Compare it with the table above when the summary changes.
- **Locally**, from `backend/`: `python -m app.tools.db_collation`.

## Checklist for a production database

Do these once the hosting provider is chosen (#33), on the real production (or an identical
staging) database, and again after any change of provider, PostgreSQL major version, base image
or database restore into a different cluster. Record the answers in the hosting ADR or the
deployment documentation.

1. **Server.** Record the provider's PostgreSQL major and minor version. The report needs
   PostgreSQL 15 or newer; on 14 and earlier the database locale is always libc.
2. **Database settings.** Run `python -m app.tools.db_collation` (or the SQL in
   [people-api.md](people-api.md#searching-by-name)) against the production database and
   record: encoding (should be `UTF8`), locale provider, `datcollate`, `datctype`, `datlocale`
   (17+) or `daticulocale` (15 and 16), and the collation version.
   - For provider `c` the locale is `datcollate`/`datctype`; for `i` and `b` it is `datlocale` or
     `daticulocale`, and `datcollate`/`datctype` are not what the database uses.
3. **Column.** Confirm `people.display_name` has no collation of its own (the tool reports
   `(database default)`). If it does, record it: it overrides the database.
4. **Library version.** Record the ICU or glibc version behind the collation (the collation
   version above). Note that a provider-managed OS or glibc upgrade can change behaviour without
   a migration here, and that the database may warn about a collation version mismatch.
5. **Behaviour check.** With a synthetic user only, create people named `Élodie Martin`,
   `ΣΟΦΙΑ Example`, `Иван Петров`, `İstanbul Test`, `Istanbul Test`, `Straße Test` and a composed
   and a decomposed `Zoé`, then search for `élodie`, `σοφια`, `иван`, `istanbul`, `STRAẞE` and
   each form of `zoé`. Compare with the table in [people-api.md](people-api.md#searching-by-name)
   and note any difference. Delete the synthetic people afterwards.
6. **Automated check.** Run `backend/tests/test_people_search_api.py` against that database
   (`TEST_DATABASE_URL` must name a database ending in `_test`). Its non-ASCII tests compare the
   search with the database's own `lower()`, so they pass on any collation; a failure means the
   search no longer agrees with the database.
7. **Decide.** If the production locale is `C`, or folds differently from what users will
   expect, record the consequence. Changing the collation, adding accent folding or normalising
   Unicode is a separate product decision and is not part of this checklist.
8. **Keep it current.** Keep the CI summary and this record in step: a different recorded value
   after a base-image or version change should prompt step 5 again.

## Out of scope

Choosing a hosting provider, forcing a database or column collation, and any change to search
semantics (accent folding, normalisation, fuzzy matching, ranking).
