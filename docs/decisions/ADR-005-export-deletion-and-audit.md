# ADR-005: Export Delivery, Audit Records and Account Deletion

- **Status:** **Proposed.** Not accepted. It becomes Accepted only when the project owner approves it on the pull request that adds it. Nothing in it is implemented.
- **Date:** 2026-11-09
- **Decision owners:** Project maintainer
- **Relates to:** [ADR-002](ADR-002-authentication-and-authorization.md) (server-side authorization; privileged credentials stay on the server), [ADR-003](ADR-003-data-and-privacy.md) (export, deletion, no personal content in logs; **not edited**), [ADR-004](ADR-004-token-verification.md) (local token verification), the product requirements (sections 7.2 and 7.4, J10 to J12) and issue #16.

## Context

Issue #16 adds export and deletion. The owner has decided what the product does (product requirements section 7.2, "Owner-decided"). Three things need a design decision of their own, and ADR-003 is accepted and is not changed here: where audit records live, how an export reaches the user, and how the backend may delete a Supabase identity. A fourth, how the backend knows the user has just re-authenticated, is recorded below as an open question with a recommendation.

## Decisions proposed

### 1. Audit records live in the primary database, not in logs

- A table `audit_events` in PostgreSQL, written by the backend only: `id`, `owner_id`, `occurred_at`, `event_type`, `outcome`, and an optional `person_id`. `event_type` and `outcome` are fixed vocabularies enforced by check constraints. **There is no free-text column**, so a name, note, interest, preference or search text cannot be stored by accident.
- Application logs stay as they are (no user identifiers, no personal content: [logging-and-errors.md](../logging-and-errors.md)). Audit records are a separate, owner-scoped store, readable only through the owner's own authorized access and never returned to another user.
- **Initial event types** (backend-observable only; sign-in failures that never reach the backend remain in Supabase's logs):
  - `reauthentication.accepted`, `reauthentication.refused` (a protected action found no recent re-authentication);
  - `export.ticket_issued`, `export.downloaded`, `export.ticket_refused` (expired, already used, wrong user or wrong type), each with the export type and, for an individual export, `person_id`;
  - `person.deleted` (with `person_id`);
  - `account.deletion_started`, `account.deletion_refused`;
  - `write.refused_deletion_pending` (a write blocked by the pending-deletion state).
- **Deliberately not recorded:** rejected or invalid tokens (no verified user, and the volume is chosen by whoever sends them; these stay as counted, privacy-safe log events), cross-user access attempts (reported as 404 exactly like a missing record; recording them is a separate decision), and attempted email addresses.
- **Deletion:** when an account is deleted, every `audit_events` row for that owner is deleted in the same transaction as the archive. What remains is a separate table `account_deletions(deleted_at, outcome)` with **no identifier**.
- **Retention is Unresolved.** No period is chosen here. Until the owner decides, rows are kept for the life of the account (they are deleted with it), and the documentation must say so.

### 2. Export delivery uses a single-use ticket sent in a request body

The ticket and download flow is in [account-deletion-and-export-design.md](../account-deletion-and-export-design.md#export-ticket-and-download-flow). In summary: the client asks for an export (a full export first requires recent re-authentication); the server returns an unguessable ticket in the response **body**; the client redeems it with a second `POST` whose **body** carries the ticket, with its normal bearer token; the server generates the JSON at that moment. The ticket is bound to the user and the export type, expires (the owner's preferred initial value is two minutes), and works once. Only its hash is stored. Nothing is sent by email, and no ticket or token appears in a URL, so none can reach an access log, the browser history or a referrer.

### 3. Identity deletion uses one narrow, server-held credential

- Deleting the Supabase identity requires a privileged key (a Supabase secret or service-role key). Per ADR-002 it stays on the server: an environment variable read only by the backend, never in the frontend bundle, in `VITE_*` variables, in logs or in test fixtures.
- It is used by **one** small module that makes **one** call (delete the user, permanently, not a soft delete). The exact request must be checked against Supabase's current documentation before implementation. It has a timeout and a fixed error category, and the key is never part of an error message.
- Without the key configured, account deletion answers 503 and deletes nothing, so a missing secret can never half-delete an account. Tests use a fake client.
- **Risk to accept:** the key can administer every user in the project. Supabase does not offer a narrower key for this single call. The mitigations are server-only storage, rotation, and where it is kept in production, which depends on the hosting decision (#33). A separate service holding the key is possible but adds infrastructure and is not proposed.

### 4. Open question: verifying "re-authenticated immediately before"

Findings and a recommendation are in the design document ([Re-authentication](../account-deletion-and-export-design.md#re-authentication)). In short, `iat` and `amr` alone are not proof, and Supabase's `reauthenticate()` produces nothing a backend can verify. This ADR does **not** choose the mechanism or the recency window; it needs the owner's check of real Supabase tokens first.

## Alternatives considered

- **Audit in logs only.** Rejected: the logs deliberately carry no user identifiers, and they are not owner-scoped or deletable per account.
- **Free-text audit messages.** Rejected: they invite personal content.
- **A download link that carries the ticket in the URL.** Rejected by the owner's rule against credentials in query strings or anything an access log could record. A consumed or expired ticket is harmless, but a ticket in a log before it is used is not.
- **Storing the generated export until download.** Rejected: it would create a second copy of the archive at rest. The file is generated when redeemed.
- **Deleting the Supabase identity first.** Rejected: if the database step then failed, the data would be left without an owner able to sign in and delete it.

## Consequences

- New tables: `audit_events`, `export_tickets`, `account_deletions`, plus temporary operational tables for re-authentication markers and `account_deletion_state` (see the design document). Each has a migration, owner-isolation tests and deletion tests.
- A new server secret for account deletion, absent in development and test.
- The delete-person call changes from `204` to `200` with a small summary (owner-decided).
- Every write route is affected by the pending-deletion check (design document), so it is enforced centrally, not per route.
- Audit retention, the JSON field list, the final ticket lifetime and backup and provider retention stay open.

## Review this decision before

Adding sharing or delegated access, administrative access to records, any new authentication provider or method (the re-authentication check assumes email and password), moving away from local token verification, or adding a feature that sends personal data to an external service.
