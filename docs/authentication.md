# Authentication (backend)

How the backend decides who is calling. The decision and its rationale are in [ADR-004](decisions/ADR-004-token-verification.md). Authentication here means "which verified user is this?". Whether that user may read or change a record is a separate decision that each endpoint makes (ADR-002).

Code: `backend/app/core/auth.py` (verification), `backend/app/api/deps.py` (the dependency), `backend/app/api/me.py` (the session endpoint).

## How a request is authenticated

1. The frontend sends `Authorization: Bearer <access token>` over HTTPS. Nothing else (query string, body, cookies, other headers such as `X-User-Id`) is ever used to identify the caller.
2. The backend reads exactly one `Authorization` header with the `Bearer` scheme and a token of at most 4096 characters.
3. It reads the token header only to pick a key: the algorithm must be `ES256` or `RS256`, and the `kid` must be one of the project's public keys, fetched from the URL derived from `SUPABASE_URL`.
4. It verifies the signature, issuer, audience, expiry, not-before and required claims, then requires `role` = `authenticated` and `is_anonymous` = `false`, and a canonical UUID `sub`.
5. The route receives `AuthenticatedUser(user_id)` and nothing else.

## Using it in a route

```python
from app.api.deps import CurrentUser

@router.get("/people")
def list_people(user: CurrentUser) -> ...:
    ...  # user.user_id is the verified owner; scope every query to it
```

`GET /api/v1/me` returns `{"user_id": "<uuid>"}` for a valid token. It is the permanent way for the frontend to check a session.

## Responses

| Situation | Status | Body `code` | Notes |
|---|---|---|---|
| No `Authorization` header, wrong scheme, malformed, expired, bad signature, wrong issuer or audience, missing or invalid subject, wrong role, anonymous user | 401 | `unauthorized` | Identical body for every case, plus `WWW-Authenticate: Bearer`. The response never says which check failed. |
| Signing keys cannot be obtained, or `SUPABASE_URL` is not configured | 503 | `service_unavailable` | Never a pass. No `WWW-Authenticate`. |

Both use the standard error body with a `request_id` that matches the `X-Request-ID` header ([logging-and-errors.md](logging-and-errors.md)).

## Logging

A rejection logs one WARNING with a fixed event name such as `auth_rejected_expired` or `auth_rejected_bad_audience`, and nothing else about the request. A key problem logs `auth_keys_unavailable` with the exception type and frames only (no URL or error text). A missing configuration logs `auth_not_configured`. Tokens, the `Authorization` header and claims are never passed to a logger. The access log already records the status (401 or 503) and the route template.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SUPABASE_URL` | none | The project origin, for example `https://<project-ref>.supabase.co`, with no path. Not a secret. Required in production (the application will not start without it). In development and test a missing value leaves protected endpoints answering 503. `http://` is accepted only for localhost outside production. |
| `SUPABASE_JWT_AUDIENCE` | `authenticated` | The `aud` value the backend requires. Leave the default unless your project differs. |

The key fetch has a **total** time limit of 3 seconds (a response that arrives a byte at a time is cut off at that limit), a 64 KiB size cap, and refuses redirects. It connects directly to `SUPABASE_URL`: proxy environment variables such as `HTTPS_PROXY` are not used.

The backend needs **no** Supabase key. Never put the service-role key, the legacy JWT secret or any other privileged value in backend configuration for this purpose, and never expose them to the frontend. The frontend's own settings arrive with #11.

## Checking your real Supabase project

Automated tests use synthetic keys and tokens only. To confirm that your real project matches what the backend expects (signing algorithm, issuer, audience, role, anonymous flag and subject format) run a local check **on your own machine**. It never prints the token or personal claims, and the token never leaves your machine.

1. In the Supabase dashboard, open **Authentication → JWT signing keys** and make sure the project uses asymmetric signing keys (ES256 is recommended). A project still on the legacy secret publishes no keys and the backend will answer 503.
2. Put your **non-production** project URL in your local, git-ignored `.env`: `SUPABASE_URL=https://<project-ref>.supabase.co`.
3. Sign in to your own test user through your own means (for example the Supabase dashboard, or the sign-in screens when #11 exists) and copy an access token. Do not paste it into a chat, an issue, a commit or a shell command line.
4. From `backend/`, with the virtual environment active, run:

   ```sh
   python -m app.tools.check_token
   ```

   The tool prints the keys the project publishes, asks for the token with the input hidden, and prints a short report: the algorithm, whether the `kid` is among the published keys, whether `iss` and `aud` match, the role, the anonymous flag, whether `sub` is a canonical UUID, the token lifetime, and finally "would ACCEPT" or "would REJECT: <reason>". Values that are not short plain text are not shown, and `sub`, the email and other personal claims are never printed.
5. Compare the report with the expectations printed beside each line. Anything marked `NO`, or a REJECT, means the backend and your project disagree. Tell the maintainer the **line** that disagrees (for example "role: authenticated, is_anonymous: <absent>"), not the token.
6. Revoke the session (sign out) afterwards if you pasted the token anywhere you did not intend.

The tool contacts only your project's public key endpoint.
