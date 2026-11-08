# Design proposal: re-authentication, export tickets and account deletion

**Status: proposed, for the owner's review. Nothing here is implemented or decided.** It supports [ADR-005](decisions/ADR-005-export-deletion-and-audit.md) and issue #16. Owner-decided behaviour is in the product requirements (section 7.2); this document proposes how to build it and lists what still needs a decision. Synthetic data only.

## Re-authentication

**Owner-decided:** the user re-authenticates immediately before a full-archive export and full account deletion. Individual-person export and deletion need no additional re-authentication. **Not decided:** the mechanism and the recency window.

### Findings (what is and is not established)

| Question | Finding | Basis |
|---|---|---|
| Does `iat` prove a fresh sign-in? | **No.** It is when that token was issued. Supabase documents it only as "when the token was issued" and says nothing about refresh. A refreshed token is newly issued, so a token can be seconds old while the sign-in is hours old. | Supabase JWT claims reference |
| Does `amr` carry a time? | Yes. Each entry has a `method` and a numeric `timestamp`. | JWT claims reference |
| Where does the `amr` timestamp come from? | In Supabase Auth's source it is the claim row's `updated_at`, with one row per session and method; adding the same method to the same session again updates that time. | `internal/models/amr.go` and `sessions.go` in supabase/auth (read, not run) |
| Does a refresh change the password entry's time? Does a password sign-in always create a new session with a new `session_id`? | **Not established.** The sources I could read do not show the refresh path or the session creation. This must be checked against real tokens (below). | Gap |
| Does Supabase's `reauthenticate()` help? | **No.** It sends a one-time code, used for secure password change, and returns no token or claim a backend could check. | Supabase reference |
| Does the backend see passwords? | It must not. A backend endpoint that takes the password and calls Supabase for the user would put credentials in our logs' reach. Not proposed. | ADR-003 |

So `amr` is promising but its freshness semantics are unproven. Do not rely on `iat`, or on `amr` alone, until they are checked.

### Proposed mechanism (needs your approval and the check below)

1. For a full export or account deletion the frontend asks the user for their password and signs in again with Supabase (`signInWithPassword`). That is a real, verified sign-in, and it produces a new access token.
2. It sends that token to a new endpoint, `POST /api/v1/reauthentication`. The backend verifies the token as usual, then requires **all** of: the same `sub` as the caller; a `password` entry in `amr` whose `timestamp` is within a short window; an `iat` within the window; and a `session_id` it has never accepted before.
3. If so, the backend records a **single-use marker** (user, `session_id`, expires in a few minutes) and writes `reauthentication.accepted`. The full export or account deletion consumes the marker, so "immediately before" means once, not "within a window forever".
4. A replayed token fails the "never seen this session" test. A stolen older token fails the window. A missing marker means `403` with a fixed message and a `reauthentication.refused` audit record.

The marker is operational state (user, session id, expiry), deleted when used or expired and with the account.

### What I need from you to choose the window

Run this on your own machine, as you did for `check_token`; I would extend that tool to print only fixed facts and never a token or claim value:

1. sign in with email and password and record: is there a `session_id`, which `amr` methods, and the age of the `password` entry;
2. wait past a token refresh (or force one) and record whether the `session_id` stayed the same, whether the `password` timestamp stayed the same, and whether `iat` moved;
3. sign in again and record whether the `session_id` changed.

If the password timestamp survives a refresh and a new sign-in gives a new `session_id`, the mechanism above is sound and a window of 60 to 120 seconds for step 2 and a few minutes for the marker is reasonable. If not, the design needs another way to prove freshness, and I would come back to you. Tests for the backend logic use synthetic tokens and need no Supabase project.

## Export ticket and download flow

Owner-decided: server-generated JSON; single-use tickets bound to the authenticated user and the export type; no email; no reusable secret in a URL; preferred initial expiry two minutes. The JSON field list and the final lifetime are unresolved.

**Table `export_tickets`:** `token_hash` (SHA-256, primary key), `owner_id`, `scope` (`archive` or `person`), `person_id` (null for a full export), `created_at`, `expires_at`, `consumed_at`. The ticket itself (at least 256 random bits) is never stored.

1. **Request.** `POST /api/v1/exports` with a JSON body `{"scope": "archive"}` or `{"scope": "person", "person_id": "..."}` and the normal bearer token. For `archive` the re-authentication marker is consumed first. A `person_id` that is not the caller's is a `404`, the same as everywhere else. The server stores the hash, writes `export.ticket_issued`, and answers `201` with `{"ticket": "...", "expires_at": "..."}`. The ticket is in the response **body**.
2. **Redeem.** `POST /api/v1/exports/download` with a body `{"ticket": "..."}` and the bearer token. In one statement the server marks the ticket consumed **only if** the hash matches, the owner is the caller, the scope matches, it is unconsumed and it has not expired. If no row changed, the answer is the same `404` for every reason (unknown, used, expired, someone else's, wrong type), and `export.ticket_refused` is written. If a row changed, the server builds the JSON **now** from the database (no stored copy), writes `export.downloaded`, and answers `200` with `Content-Type: application/json`, `Content-Disposition: attachment` and `Cache-Control: no-store`.
3. The frontend fetches the response as a file and offers it to the user. A plain link cannot send the bearer token, which is why the ticket is redeemed by a `POST` and not a link.

Properties: the ticket never appears in a URL, header or log; a second use fails; another user's token is useless without their ticket; a leaked ticket alone is useless without that user's bearer token. Expired rows are removed by a cleanup on later requests (no scheduler). The body of both requests is never logged. The export has no pagination; a very large archive is a known limit to measure before release.

## Person deletion summary

`DELETE /api/v1/people/{person_id}` changes from `204 No Content` to `200 OK` with `{"deleted": true, "notes_deleted": 3}`. A person that is not the caller's is still `404`. The API documentation, the client, the confirmation screen and the tests change together, and `person.deleted` is audited with the person identifier only.

## Account deletion

**Owner direction:** a still-valid access token must not be able to create records after the delete. Add a temporary account-deletion state that blocks new authenticated writes while deletion is pending. It is operational state, not an audit record. It is removed after the Supabase identity is deleted, and if that fails only the minimum needed for a safe retry is kept. No personal content and no permanent identity-linked deletion record.

### Why a second sweep is not enough, and one thing that needs your decision

Tokens are verified locally and statelessly (ADR-004), and `owner_id` is a plain UUID with no users table. Even after Supabase deletes the identity, a token issued earlier still verifies until it expires (Supabase's default access-token lifetime is one hour, which the project can change; the real setting must be checked). A write made with it after the blocking state is removed would create a record for a deleted account. So the block cannot end the moment the identity is deleted; it must last until every earlier token has expired.

**Proposal:** after the identity is deleted, keep only `(owner_id, purge_after)` for the token lifetime (a configured maximum, default one hour plus a margin), then remove it. That is a deliberate exception to "removed after successful identity deletion", needed to close the very race you identified. It holds no content and is not an audit record. **Decision needed:** accept this, or accept the residual window. The block lasts only as long as the longest access-token lifetime, so the backend should also reject a token whose lifetime exceeds the configured maximum; that touches ADR-004 and is part of the decision.

### Schema (temporary operational table)

`account_deletion_state(owner_id uuid primary key, state text not null check (state in ('deleting','identity_pending','identity_deleted')), started_at timestamptz not null, attempts integer not null default 0, purge_after timestamptz null)`

One row per owner, only while in progress. No names, notes, email or other content. `purge_after` is set only in `identity_deleted`. `audit_events` is separate and is **deleted** with the account.

### Enforcement

- **One central check.** The authentication dependency (or a wrapper on it used by every unsafe method) looks the owner up in `account_deletion_state` for `POST`, `PATCH` and `DELETE`, and refuses any state with `409` and a fixed code `account_deletion_in_progress`. Reads stay allowed. New exports are refused too. Because it is central, a new route cannot forget it, and a test lists every unsafe route.
- **Closing the race with a lock.** A check followed by an insert is not atomic. Each write transaction first takes `pg_advisory_xact_lock_shared(key)`, where `key` is derived from the owner id, **then** reads the state. Deletion takes `pg_advisory_xact_lock(key)` (exclusive) before it writes the state row. So a write either finishes before deletion starts, or waits and then sees the committed state row and is refused. A write cannot slip in between. Transaction-level advisory locks also work behind a transaction pooler, which matters for #33.

### Sequence and transaction boundaries

| Step | Where | What |
|---|---|---|
| 0 | Request | Explicit confirmation; the re-authentication marker is consumed (refused with `403` if missing) |
| 1 | **Transaction 1** | Take the exclusive lock. Insert or keep the state row as `deleting`. Delete the owner's people (notes follow), export tickets, re-authentication markers and `audit_events`. Commit. Writes are blocked from this commit on |
| 2 | Outside any transaction | Delete the Supabase identity, permanently, with a timeout |
| 3a | **Transaction 3** on success | Set the state to `identity_deleted` with `purge_after`; insert the non-identifying `account_deletions(deleted_at, outcome='completed')`. Commit |
| 3b | **Transaction 3** on failure | Set the state to `identity_pending`, add one to `attempts`. Insert nothing permanent. Commit |
| 4 | Later | Any later request removes state rows whose `purge_after` has passed |

The response always states what was done: the people and notes removed, and whether the identity is deleted, with the retry instruction if not. The owner's rule that deletion is immediate and has no undo stands.

### Retry

The same call, `DELETE /api/v1/account`, is idempotent. If the owner's state is `deleting` or `identity_pending`, step 1 finds nothing left to delete and goes straight to steps 2 and 3. If Supabase answers that the user does not exist, that counts as success (the earlier call did succeed and its reply was lost). A retry requires a fresh re-authentication and confirmation like the first call. If the user never returns, the identity stays and writes stay blocked; the user can sign in later and finish. A scheduled retry would need a scheduler, which does not exist; I do not propose one.

### Failure scenarios

| # | What goes wrong | State afterwards | What the user is told |
|---|---|---|---|
| 1 | Transaction 1 fails | Nothing changed (the state row rolls back too) | "Nothing was deleted." Safe to retry |
| 2 | The process stops after Transaction 1 | `deleting`; archive gone; writes blocked | Retry completes it |
| 3 | The Supabase call fails or times out | `identity_pending`; archive gone | "Your archive was deleted; removing your sign-in failed. Try again." |
| 4 | The Supabase call succeeds but the reply is lost, or Transaction 3 fails | `deleting` or `identity_pending`, identity gone | Retry sees "no such user" and finishes |
| 5 | A write arrives during deletion | Serialized by the lock: finishes before, or is refused with `409` | A refused write is reported honestly |
| 6 | A pre-deletion token is used after the identity is deleted | Refused with `409` until `purge_after` | None needed; it fails safely |
| 7 | Two deletions at once (two tabs) | The exclusive lock serializes; the second finds the state and is idempotent | The same outcome |
| 8 | The re-authentication marker is missing or used | Nothing changed | "Please confirm your password again." |
| 9 | The database is restored from a backup older than the deletion | Archive data and state may come back | A documented backup limitation, pending #33 |
| 10 | The configured token maximum is shorter than a token's real lifetime | A pre-deletion token outlives the block | A residual window; closed only if over-long tokens are rejected (decision above) |

### What stays recorded, and for how long

`account_deletions(deleted_at, outcome)` only: no identifier, kept permanently, because it identifies no one. `account_deletion_state` rows exist only while a deletion is in progress or until `purge_after`. Everything else identity-linked is gone at the end of Transaction 1.

## Decisions needed before implementation

1. Approve or amend the pending-deletion design, including keeping `(owner_id, purge_after)` until earlier tokens expire, and whether to reject tokens with an over-long lifetime.
2. Run the Supabase freshness check above, then choose the recency window.
3. Approve ADR-005, including the server-held deletion key and the exact Supabase request (to be checked against Supabase's current documentation).
4. Confirm the final ticket lifetime and the initial audit event list.
5. Unresolved and left open: audit retention, the JSON field list, reminder-settings scope, backup and provider retention (#33).
