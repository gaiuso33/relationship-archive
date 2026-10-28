# ADR-002: Managed Authentication and Server-Side Authorization

- **Status:** Accepted
- **Date:** 2026-10-27
- **Accepted:** 2026-10-28, on the project maintainer's instruction
- **Decision owners:** Project maintainer

## Context

Relationship Archive stores personal notes, birthdays, and relationship information. Access must be restricted to the authorized owner.

The application will use a separate frontend and Python API, so the API cannot assume that requests are trustworthy merely because they originate from the frontend.

## Decision

Use Supabase Auth for managed identity and sign-in.

The FastAPI backend will independently validate incoming access tokens and enforce authorization for every operation involving private records.

The backend will derive the authenticated user identifier from a verified token. It will not trust a user identifier supplied by the client as proof of identity.

Database queries and mutations must be scoped to the authenticated owner where appropriate.

## Security requirements

- Require valid authentication for private API endpoints.
- Verify token signatures and relevant claims using a supported, maintained verification approach.
- Reject invalid or expired tokens.
- Enforce ownership checks for reads, updates, and deletes.
- Prevent users from accessing records belonging to another user by changing an identifier in a URL or request body.
- Keep service-role credentials and other privileged secrets on the server.
- Never commit secrets to source control.
- Use HTTPS in deployed environments.
- Avoid logging access tokens or sensitive note contents.

## Testing requirements

Automated tests must verify:

1. Unauthenticated requests to protected endpoints are rejected.
2. Valid authenticated requests can access their own authorized records.
3. One user cannot retrieve another user's records.
4. One user cannot update or delete another user's records.
5. Invalid or expired tokens are rejected.
6. Ownership is enforced consistently across related resources, including notes.

Tests should use synthetic users and data.

## Alternatives considered

- Building authentication from scratch: rejected for the initial version because it increases security and maintenance responsibilities.
- Relying only on frontend route guards: rejected because frontend controls do not enforce API authorization.
- Trusting a client-supplied owner identifier: rejected because clients can manipulate request data.

## Consequences

Managed authentication reduces identity-management overhead, but the project remains responsible for secure token verification, API authorization, secret handling, and data access controls.

## Review conditions

Review this decision before adding multi-user sharing, delegated access, administrative access to personal records, or any new authentication provider.

## Acceptance review

Accepted on 2026-10-28 on the project maintainer's instruction, after the contents were compared with the authentication and authorization decisions in the project instructions and `docs/architecture.md` section 7. The managed-identity choice, server-side token verification, owner-scoped access and testing requirements match.

Not decided here: the exact token verification implementation. This ADR requires a supported, maintained approach but does not choose one; the choice is made and approved in #10.
