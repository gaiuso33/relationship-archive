# Logging and error handling

How the backend logs and reports errors without leaking personal data, tokens or internals. It implements the rules in [ADR-002](decisions/ADR-002-authentication-and-authorization.md) and [ADR-003](decisions/ADR-003-data-and-privacy.md) and section 9 of the [architecture](architecture.md). Code: `backend/app/core/logging.py`, `middleware.py` and `errors.py`.

## The rule

**Log what happened, never what the user said.** A log entry says that a request to a route finished with a status, or that an error of some type occurred. It never contains the data the request carried.

Never log or return: access tokens, passwords, API keys, cookies or any header; request or response bodies; query strings or raw paths; note text, names, emails, birthdays or other person details; database parameters or database error text; client IP addresses; connection URLs; exception messages.

## How it is enforced

Three layers, in order of importance:

1. **Allowlisted structured fields (the primary boundary).** Every entry has `timestamp`, `level`, `logger`, `message`, and `request_id` when there is one. The only other fields that can appear are `event`, `method`, `route`, `status`, `duration_ms`, `error_type`, `error_id`, `sqlstate` and `frames`, and each is validated (for example `route` must look like a route template, `sqlstate` must be five characters). Any other `extra` key is dropped, and an invalid value is dropped. So `logger.info("saved", extra={"note": text})` logs `saved` and nothing about the note.
2. **No exception messages, arguments or tracebacks.** Application loggers (`app.*`) never interpolate arguments: `logger.info("saved %s", name)` logs the literal `saved %s`, and a non-text message such as `logger.error(exc)` is replaced by a fixed notice. A static test (`tests/test_logging_conventions.py`) fails the build if application code logs an f-string, `%` or `.format()` message, or passes log arguments. An exception is logged as `error_type`, an `error_id`, an optional `sqlstate`, and `frames`: project-relative `file:line in function` entries (for example `app/api/people.py:42 in create_person`, or `sqlalchemy/engine/base.py:1963 in _execute_context`). There is no message, no source text and no local variables. Paths never reveal the machine: project files are relative to `backend/`, installed packages relative to `site-packages`, the standard library is `<stdlib>/...`, and anything else is `<other>/<file name>`. Only the last 15 frames are kept.
3. **Pattern redaction (supplementary).** The log message text is scrubbed for bearer and basic credentials, JWTs, `Authorization`/`Cookie` values, credential-like `key=value` pairs (`password`, `token`, `secret`, `api_key`, `session` and similar), `user:password@` in URLs, URL query strings and email addresses. Patterns cannot recognise names or note text, so this layer only catches mistakes: do not rely on it.

JSON and text output are built from the same entry, so both have identical protections. JSON is the default.

Library logging is kept quiet. `LOG_LEVEL` applies to application loggers only. **SQLAlchemy engine records are dropped at every level**: the loggers are silenced, and the handler also refuses them if someone raises a logger to DEBUG or creates an engine with `echo=True`, so SQL statements, parameters and result rows cannot reach the logs. uvicorn's access log is disabled, because it prints the raw URL (including the query string) and the client address. Any other third-party record is written only at WARNING or above, and its message is replaced by `third-party log message suppressed`; only the logger name and level remain. The one exception is uvicorn's own start-up and shutdown lines, which are redacted but otherwise kept.

## What every request logs

One line per request, from `app.access`:

```json
{"timestamp":"...","level":"INFO","logger":"app.access","message":"request completed","request_id":"3f1c...","event":"request_completed","method":"GET","route":"/api/v1/people/{person_id}","status":200,"duration_ms":4.12}
```

The route is the **template** taken from the route definition (`/api/v1/people/{person_id}`), never the raw path. A request that matches no route is logged as `unmatched`. There are no headers, no body, no query string, no raw path and no client IP.

## Request ids

The server generates a new `request_id` for every request. A client-supplied `X-Request-ID` is ignored, so a client cannot choose what appears in the logs. The id is returned in the `X-Request-ID` response header and in every error body, and appears on every log entry for that request. Give it to whoever investigates a problem.

## Error responses

Every error has the same shape:

```json
{"error": {"code": "internal_error", "message": "An unexpected error occurred.", "request_id": "3f1c..."}}
```

| Situation | Status | `code` |
|---|---|---|
| Unhandled exception | 500 | `internal_error` |
| Unique or foreign-key violation (SQLSTATE `23505`, `23503`) | 409 | `conflict` |
| Database unavailable (connection errors, shutdown, too many connections) | 503 | `service_unavailable` |
| Any other database error | 500 | `internal_error` |
| Request validation (422) | 422 | `validation_error`, plus `fields` |
| `HTTPException` or routing error (404, 405 and so on) | as raised | from the status; the `detail` is never used. Only `WWW-Authenticate`, `Allow` and `Retry-After` headers are kept |
| `AppError(ErrorCode.X)` | from the table | `x` |

Validation errors list only the **location and type** of each problem, for example `{"loc": ["body", "rating"], "type": "int_parsing"}`. The submitted value, FastAPI's `input`, and the message text are never returned. An unexpected field name chosen by the client is replaced with `<unknown field>`. Limitation: keys of a free-form dictionary field cannot be told apart from declared field names, so avoid dictionary-typed request fields for private data, or review them.

Database errors, from SQLAlchemy or raised directly by the `psycopg` driver, are translated into the responses above. PostgreSQL's own message includes the offending value (for example `Key (email)=(...) already exists`), so it is never logged or returned. The log keeps the error type and the `sqlstate` (for example `23505`), which is enough to tell what kind of failure it was.

## Raising an error on purpose

```python
from app.core.errors import AppError, ErrorCode

raise AppError(ErrorCode.NOT_FOUND)
```

`AppError` takes an `ErrorCode`, not a message. The client text comes from a fixed table in `app/core/errors.py` (`APPROVED`). There is no way to attach a string, so an exception message cannot reach a client by accident. To add a new client-visible error, add an `ErrorCode` and an `APPROVED` entry in a reviewed pull request, with a message that contains no data from the request.

## Adding logging in new code

- Use a logger named under `app` (the static check only recognises variables called `logger` or ending in `_logger`): `logger = logging.getLogger("app.people")`.
- Write a static message and, if useful, an `event` name in snake case: `logger.info("person created", extra={"event": "person_created"})`.
- Do not pass data as arguments, f-string data into the message, or log `request`, `body`, `headers`, `str(exc)` or `repr(model)` objects.
- To log an exception without its message, use `log_error("event_name", exc)` from `app.core.errors`. It logs type and frames and returns the `error_id`.
- To log a new kind of field, add it to the allowlist in `_clean_extras` (`app/core/logging.py`) with a strict validator, and add a test. That is a reviewed change: ask yourself whether the value could ever be personal.

## Testing rules

- Use synthetic data only. `backend/tests/support.py` has distinctive fake tokens, emails, names and note text.
- Every new endpoint that handles private data must be covered by a test that sends those values and asserts they appear in neither the captured logs nor any response (see `assert_nothing_sensitive` in `tests/test_error_handling.py`).
- `tests/test_privacy_real_server.py` starts a real `uvicorn` process, because FastAPI's test client cannot show what uvicorn itself prints. Keep it passing when changing logging.
- Database tests that could produce error text use the real PostgreSQL fixtures: see `tests/test_database_errors.py`.
- The privacy tests run in both output formats (`json` and `text`). Neither may be skipped.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Applies to application loggers only. |
| `LOG_FORMAT` | `json` | `json` (default) or `text` (one readable line per entry). Same protections either way. |

## Known limits

- Pattern redaction is best effort. The allowlist and "no messages" rules are the real protection.
- uvicorn's own start-up and shutdown lines keep their message (after redaction). They contain host, port and process information, not request data.
- When `uvicorn --reload` is used, the supervisor process prints its own three start-up lines in uvicorn's default format before the application loads. They contain no request data.
- If an exception happens after a response has started, the response cannot be replaced. The exception goes to uvicorn's error logger, where its message is dropped and only the type and frames remain.
- Production log shipping, retention and alerting are not part of this issue (see v0.6.0 and #33). Anything that stores logs must treat them as potentially sensitive.
- The static log-call check cannot see a message hidden in a variable or built in a helper, and the route template recovers the `/api/v1` prefix from the raw path (current FastAPI does not keep it on the matched route). Both are covered by tests, but reviewers should still read new log calls.
