# ADR-004: Supabase Access Token Verification

- **Status:** Accepted: 2026-11-01, on merge of PR #64 (merge commit 7c1c578). Item 9 amended by issue #65 (see Status history).
- **Date:** 2026-11-01
- **Decision owners:** Project maintainer
- **Relates to:** [ADR-002](ADR-002-authentication-and-authorization.md), which requires server-side token verification with a supported, maintained approach and leaves the choice to #10.

## Status history

- **Proposed, 2026-11-01.** The project maintainer approved the direction in issue #10; it was to become Accepted when the #10 pull request was merged.
- **Accepted, 2026-11-01,** on merge of PR #64 (merge commit 7c1c578).
- **Item 9 amended, issue #65.** The original item 9 read: "**JWKS handling limits:** 3 second timeout; response size at most 64 KiB; no redirects; at most 20 keys; keys cached for 5 minutes (Supabase says not to cache longer than its own 10 minute edge cache unless the cache can be purged); at most one forced refresh per 30 seconds however many unknown key ids arrive; no new fetch for 5 seconds after a failure; expired keys are never served after a failed refresh." The fixed 3 second total did not leave room for DNS on a slow network (measured: about 5 seconds for DNS alone), so the first real-project check failed closed before any request was sent. Only the time limit changed; every other limit is as before.

## Context

The backend must decide who is calling from the access token the frontend sends. Supabase signs those tokens. Its current guidance is to use asymmetric signing keys, published as a JSON Web Key Set (JWKS) at `https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json`, and to avoid verifying with the legacy shared JWT secret. A shared secret that can verify tokens can also forge them, and it would have to be present on the backend.

## Decision

1. **Verify tokens locally, with the project's public signing keys.** No call to Supabase is made per request.
2. **Asymmetric algorithms only: `ES256` and `RS256`.** The legacy HS256 shared secret is not supported. `none`, HMAC and any other algorithm are rejected before any key is looked up.
3. **Library: PyJWT with the `crypto` extra** (`pyjwt[crypto]`) does the parsing, signature check and claim checks. Fetching and caching of the key set is our own small, tested code (`JwksKeyProvider`), so the limits below are explicit rather than inherited.
4. **Keys come from configuration, never from a token.** The JWKS URL is derived from `SUPABASE_URL`. The `iss`, `jku`, `x5u`, `jwk` and `x5c` header or claim values of an unverified token never choose where keys come from or which key is trusted. The header's `kid` can only select among keys already fetched from our own URL.
5. **Claims checked:** signature; algorithm allowlist; `iss` equal to `<SUPABASE_URL>/auth/v1`; `aud` equal to `SUPABASE_JWT_AUDIENCE` (default `authenticated`); `exp`, `sub`, `iss` and `aud` required; `exp`, `nbf` and `iat` enforced with 30 seconds of clock leeway.
6. **Identity:** `sub` must be a canonical UUID. The result is `AuthenticatedUser(user_id)`, immutable, with no email or other claims.
7. **Stricter than the signature:** the `role` claim must be `authenticated`, and `is_anonymous` must be present and `false`.
   - Rationale: Supabase's claims reference lists `authenticated` as the role of signed-in users and `anon` as the role of the anonymous API key, and its anonymous sign-in guide says anonymous users' tokens carry an `is_anonymous` claim while still using the `authenticated` database role. Anonymous sign-ins have no owner we can attach private records to, and the product does not offer them.
   - This was checked against Supabase's published documentation, not against a live project. Two documentation pages describe anonymous tokens differently (`anon` role versus `is_anonymous`), which is why both are enforced. The maintainer will confirm the real project's tokens with `python -m app.tools.check_token` ([authentication.md](../authentication.md)).
8. **Failure behaviour (never fail open):**
   - A missing, malformed, expired, wrongly signed or otherwise unacceptable token gives `401`, the same body every time, with `WWW-Authenticate: Bearer`.
   - If the keys cannot be obtained (endpoint unreachable, timeout, error status, oversized or malformed response, no keys published, or Supabase not configured) the answer is `503 service_unavailable`.
9. **JWKS handling limits (amended by #65):**
   - **One total wall-clock deadline** covers the whole fetch: DNS, connect, TLS, response headers and body. The default is 10 seconds, configurable from 1 to 30 seconds with `SUPABASE_JWKS_TIMEOUT_SECONDS`, and it cannot be disabled.
   - Connect and read timeouts are fixed at 3 seconds each and are capped by the time left in the total deadline.
   - The DNS lookup runs in a helper thread, at most one in flight per key provider. The operating system's resolver call cannot be cancelled, so a lookup that outlives its caller finishes on its own and its result is discarded. The caller waits only until the deadline.
   - Response size at most 64 KiB; no redirects; the status must be 200; at most 20 keys.
   - Keys are cached for 5 minutes (Supabase says not to cache longer than its own 10 minute edge cache unless the cache can be purged); at most one forced refresh per 30 seconds however many unknown key ids arrive; no new fetch for 5 seconds after a failure; expired keys are never served after a failed refresh.
   - Failures are reported only as one of seven fixed categories (`dns`, `connect`, `tls`, `status`, `deadline`, `size`, `body`), set by our code, including unexpected internal errors, which are categorised by phase and start the same failure backoff. `deadline` means the total deadline was the limit that ran out and takes precedence once it has passed; a single connect or read that stalls for its own 3 seconds while time remains is reported under its phase (`connect`, `tls`, `body`). No exception text, host name, address, URL or response content is logged or shown. The API answer stays the generic `503 service_unavailable`.
10. **Configuration:** `SUPABASE_URL` (the project origin, `https`; plain `http` only for loopback outside production) and optionally `SUPABASE_JWT_AUDIENCE`. In production the application refuses to start if `SUPABASE_URL` is missing or invalid. In development and test a missing value does not stop the application; protected endpoints answer 503.
11. **The backend holds no Supabase secret.** It does not need the service-role key, the legacy JWT secret or the publishable key, and none of them may be added to backend configuration for token verification.
12. **Authentication only.** This decision identifies the caller. Ownership and authorization of people and notes remain the responsibility of each endpoint (#12 onwards), as ADR-002 requires. `GET /api/v1/me` is a permanent endpoint that returns only the caller's `user_id`.

## Alternatives considered

- **Verify with the legacy HS256 shared secret.** Rejected: Supabase recommends against it, and the secret would let the backend (and anyone who obtained it) mint tokens.
- **Ask Supabase to validate each token (`GET /auth/v1/user`).** Rejected for now: it adds a network round trip to every request and sends every token to a third party. It would see revocations sooner than local verification, which is the main trade-off.
- **Use PyJWT's built-in `PyJWKClient`.** Considered. It also supports caching and a refresh cooldown, but it has no response size limit, and distinguishing "unknown key" from "endpoint down" depends on exception types. A small provider of our own makes those limits and the 401/503 split explicit and testable.
- **`python-jose`, `authlib`, the Supabase Python client.** Not chosen: PyJWT is the smallest maintained option that covers the need.

## Consequences

- A user who signs out, or whose account is removed, can keep using an already issued access token until it expires (about an hour by default). This is inherent to local verification.
- The backend needs outbound HTTPS to the Supabase project. Hosting (#33) must allow it.
- A project still on the legacy secret publishes no keys. Every protected request then returns 503 until asymmetric signing keys are enabled.
- A newly rotated key can take a few minutes to be served, bounded by the Supabase edge cache and our 30 second refresh cooldown.
- Revoking a signing key takes effect for us after our cache expires (at most 5 minutes).
- The provider lock is held during a refresh, so for up to the configured deadline (10 seconds by default) other requests that need keys wait behind it. A fetch that fails still ends within the deadline, and the 5 second failure backoff then stops further attempts. Raising the deadline raises this worst case.
- A DNS lookup that times out can keep one background thread alive until the operating system's resolver returns. There is never more than one per provider, because new lookups wait for the outstanding one.

## Review conditions

Review this decision if Supabase changes its signing approach, if another identity provider is added, before adding sharing or delegated access, or if instant session revocation becomes a requirement.

## Sources

- Supabase, [JWTs](https://supabase.com/docs/guides/auth/jwts), [JWT signing keys](https://supabase.com/docs/guides/auth/signing-keys), [JWT claims reference](https://supabase.com/docs/guides/auth/jwt-fields) and [anonymous sign-ins](https://supabase.com/docs/guides/auth/auth-anonymous), read on 2026-10-31.
- PyJWT [changelog](https://pyjwt.readthedocs.io/en/stable/changelog.html).
