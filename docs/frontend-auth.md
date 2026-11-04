# Frontend authentication

How the browser signs a user in and talks to the API (issue #11). The backend side, which decides
who is calling from the access token, is in [authentication.md](authentication.md) and
[ADR-004](decisions/ADR-004-token-verification.md). The decision to use Supabase Auth is in
[ADR-002](decisions/ADR-002-authentication-and-authorization.md).

**Status.** Everything below is covered by automated tests that use a fake Supabase client and a
fake API. **No sign-in against a real Supabase project has been tested yet**, and production
browser-to-API connectivity (the API origin, CORS allow-list and hosting) is still open under
issue #33.

## What is implemented

- Email and password sign-in and sign-out through Supabase Auth.
- A session that survives a page reload and is refreshed by the Supabase SDK.
- Private routes that send signed-out visitors to `/sign-in`.
- A typed API client that sends the access token to the API, used so far for `GET /api/v1/me`.

Not implemented (separate issues): sign-up, password reset, magic links, OAuth, MFA, account
management and the people and notes screens (#14).

## Configuration

The frontend reads three **public** values at build time. Put them in `frontend/.env.local`
(git ignores it); `frontend/.env.example` lists them with empty placeholders.

| Variable | Public? | Meaning |
|---|---|---|
| `VITE_SUPABASE_URL` | yes | The project origin, `https://<project-ref>.supabase.co`. `http` is accepted only for `localhost`, `127.0.0.1` and `[::1]`. No path. |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | yes, by design | The publishable key (`sb_publishable_...`), or the older `anon` key. |
| `VITE_API_BASE_URL` | yes | The API origin, or `/` to use the page's own origin (development proxy). |
| `DEV_API_PROXY_TARGET` | no | Development only: where the Vite proxy sends `/api` requests. Default `http://127.0.0.1:8000`. It has no `VITE_` prefix, so Vite never puts it in the bundle. |

Everything with a `VITE_` prefix is copied into the JavaScript that every visitor downloads. **Never
put any of these in a `VITE_` variable or anywhere in `frontend/`:** the service-role key, a secret
key (`sb_secret_...`), the legacy JWT secret, a database URL or password, or any backend setting.
The application refuses to start if the publishable key is a secret key or a service-role key, if a
value is missing, or if a Supabase or API URL is not `https` (outside loopback), carries
credentials, or has a path. The error names the variable and the rule, never the value.

A test builds the production bundle and checks that non-`VITE_` values are not in it, and that it
holds no secret-key-shaped value and no service-role token.

## Local development

1. Start the backend ([development.md](development.md)). Without `SUPABASE_URL` it answers 503.
2. Create `frontend/.env.local` with your **non-production** project's URL and publishable key and
   `VITE_API_BASE_URL=/`.
3. `cd frontend && npm run dev`. Vite proxies `/api/...` to the backend, keeping the paths
   unchanged, so the browser sees one origin and the backend needs no CORS configuration.

The proxy is for development only. The backend has no CORS middleware and this issue did not add
any.

## How it works

- **Session start.** `AuthProvider` subscribes to the SDK's `onAuthStateChange` first and then asks
  `getSession()`. If an auth event has already arrived, a late `getSession()` answer is ignored, so
  an old answer cannot undo something newer. The subscription is removed when the provider
  unmounts. The callback only sets state and never calls the SDK, which can deadlock it.
- **State the UI sees.** `loading`, `signedOut` (optionally "ended") or `signedIn` with the user id.
  Tokens are never kept in React state and never rendered.
- **Private routes.** While `loading`, `RequireAuth` shows a status message and neither the private
  screen nor a redirect. When signed out it redirects to `/sign-in?next=<path>`. After sign-in the
  user goes to `next` only if it is a safe in-application path: one leading `/`, not `//`, no
  backslash, no control characters or spaces, nothing that resolves to another origin. Absolute,
  protocol-relative and `javascript:` values go to `/`. The guard is a convenience: **it is not
  security**, and the API checks the token on every request.
- **Sign-in.** An accessible form with client-side checks. Every rejected sign-in shows the same
  message ("We could not sign you in..."), so the form cannot be used to learn which emails have
  accounts. A network or server failure says the service is unavailable instead.
- **Sign-out.** Ends the session on this device (and revokes it at Supabase when reachable). If that
  fails the user stays signed in and is told so.
- **Session refresh and expiry.** The SDK refreshes the access token before it expires. If the
  session ends without the user asking (refresh failure, or sign-out in another tab) the user is
  returned to sign-in with "Your session has ended".

## The API client

`src/api/client.ts` takes the access-token provider as an argument. It sends
`Authorization: Bearer <token>` to the configured API origin only, for `/api/v1/...` paths only,
with no cookies, no caching and no redirects. The token never goes in a URL, a query string, a log
or an error. Requests can be cancelled with an `AbortSignal`.

| Outcome | Behaviour |
|---|---|
| `401` | The session is ended locally, once, and the user goes to sign-in. No retry. |
| No session | The API is not called; treated like a `401`. |
| `503` | "Temporarily unavailable" with a retry button. The user stays signed in. |
| Network failure | A connectivity message. The user stays signed in. |
| `404`, other `4xx`, other `5xx`, odd responses | A fixed message for each. |

Messages are fixed text. Nothing from a response body or an SDK error is shown or logged, except
the API's `request_id` (shown as a reference for support) if it has the expected shape.

## Where the session is stored, and the trade-off

The Supabase SDK keeps the session, **including the access token and the refresh token, in the
browser's `localStorage`**. Any script that runs on the page, for example through a cross-site
scripting (XSS) flaw, can read it. This was an explicit decision to accept the SDK's default for
this project at this stage, instead of writing custom storage or a backend that holds the session.

Mitigations in place or planned:

- No `dangerouslySetInnerHTML` (the lint rule forbids it); user text is always rendered as text.
- No token or session in logs, URLs or React state; `console` calls are forbidden by lint.
- The SDK does not read credentials from the address bar (`detectSessionInUrl` is off, as there is
  no magic-link or OAuth sign-in).
- Few runtime dependencies: React, React Router and the Supabase SDK.
- A strict production Content-Security-Policy and security headers are **not yet in place**; they
  belong with hosting (#33).

## What a sign-out does not do

Signing out ends the session locally and at Supabase, but the backend verifies access tokens
locally ([ADR-004](decisions/ADR-004-token-verification.md)), so an access token issued earlier can
still be accepted until it expires (about an hour by default). This was accepted in ADR-004.

## Tests

`frontend/src` (Vitest and React Testing Library, role-based queries): `config.test.ts`,
`auth/*.test.ts(x)`, `api/client.test.ts`, `routes.test.tsx` and `bundle.test.ts`. They use a fake
Supabase client (`src/test/fakeAuth.ts`), synthetic users and tokens, and no network. Run
`npm run test -- --run` in `frontend/`.
