# Development guide

How to set up, run, test and build Relationship Archive locally. The commands are the ones CI runs, so a green local run means a green pull request. They assume a macOS or Linux shell and were verified on Linux.

For the workflow (issues, branches, pull requests, review), see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Prerequisites

| Tool | Version | Where the version is defined |
|---|---|---|
| Git | any recent version | |
| Node.js | 24 LTS | `frontend/.nvmrc`, `engines` in `frontend/package.json` and CI all use it |
| npm | the one bundled with Node.js | |
| Python | 3.12 | `requires-python` in `backend/pyproject.toml`, and CI |
| PostgreSQL | 17 | `compose.yaml` and the CI service container. Run it with Docker Compose, or install it natively |
| Docker with Compose | any recent version | Only for the Compose route to PostgreSQL |

If you use [nvm](https://github.com/nvm-sh/nvm) or a compatible tool, run `nvm use` inside `frontend/` to pick up the Node version from `.nvmrc`.

## 1. Get the code

```sh
git clone https://github.com/gaiuso33/relationship-archive.git
cd relationship-archive
```

## 2. Configure environment variables

```sh
cp .env.example .env
```

`.env` is ignored by git. Never commit it, and never put credentials, tokens or real personal data in `.env.example` or anywhere else in the repository.

The defaults in `.env.example` work with the local database described in [section 3](#3-database-postgresql). The settings are:

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | One of `development`, `test` or `production`. |
| `DATABASE_URL` | none | Connection URL of the application database. It must start with `postgresql+psycopg://`. It contains a password, so treat it as a secret. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. Applies to application loggers only. |
| `LOG_FORMAT` | `json` | `json` or `text`. `text` is easier to read while developing, with the same privacy protections. See [logging-and-errors.md](logging-and-errors.md). |
| `TEST_DATABASE_URL` | none | Connection URL of the dedicated test database. Its name must end in `_test`. Only the automated tests use it. |

Settings are read from real environment variables first and then from the `.env` file in the repository root. Restart the backend after changing them.

Later issues add settings such as Supabase authentication (#10, #11). Each one adds placeholders to `.env.example` and documents where to obtain the real value. Real values live only in your local `.env` or the project's approved secret store.

## 3. Database (PostgreSQL)

The backend uses PostgreSQL 17 through SQLAlchemy (synchronous, psycopg 3) and Alembic migrations. Database tests run against a real PostgreSQL, never SQLite or mocks, and they fail rather than skip when no test database is available.

### Start PostgreSQL with Docker Compose

From the repository root:

```sh
docker compose up -d --wait
```

This starts PostgreSQL 17 on `127.0.0.1:5432` with two databases: `relationship_archive` (development) and `relationship_archive_test` (tests). The password in `compose.yaml` and `.env.example` is a **development-only placeholder** for your own machine. The port is bound to `127.0.0.1` so other machines cannot reach it. Never reuse that password, or this Compose file, in production or in any shared environment.

Useful commands:

```sh
docker compose stop              # stop, keep the data
docker compose down -v           # stop and delete all local database data, to start over
docker compose logs postgres     # read the server log
```

The test database is created only when the data volume is first created. If you started PostgreSQL before this setup existed, run `docker compose down -v` once and start it again.

### Or use a native PostgreSQL 17

Install PostgreSQL 17, create a role and two UTF8 databases (the test database name must end in `_test`), then set `DATABASE_URL` and `TEST_DATABASE_URL` in `.env` to match. The databases must use the `UTF8` encoding: a `SQL_ASCII` database makes the driver return bytes and the connection fails.

### Apply and check migrations

From `backend/`, with the virtual environment active (section 4) and PostgreSQL running:

```sh
alembic upgrade head     # apply all migrations to DATABASE_URL
alembic current          # show the applied revision
alembic check            # fail if the models need a migration that does not exist
```

To create a migration after changing models, run `alembic revision --autogenerate -m "short description" --rev-id 0002` (use the next sequential number), then review the generated file before committing it. Never edit a migration that has been merged: add a new one. The application never creates tables itself (no `create_all`); schema changes happen only through migrations.

### How the code uses the database

- `app/db/session.py` creates one engine per process from `DATABASE_URL` and provides `get_session`, a FastAPI dependency that gives each request its own session and closes it afterwards.
- Sessions do not commit automatically. Code that changes data calls `session.commit()` explicitly; uncommitted work is rolled back.
- Errors never include SQL parameters, and settings hide the connection URL, so credentials and note contents stay out of logs.
- The session time zone is not set yet. Time zone handling is decided in #18, and connection pooling for production in #33.

## 4. Backend (FastAPI)

Set up, from the repository root:

```sh
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run the API (with the virtual environment active), then open <http://localhost:8000/api/v1/health>:

```sh
uvicorn app.main:app --reload
```

The application writes its own privacy-safe logs and replaces uvicorn's access log, so no extra flags are needed. Never add `--log-config` or a custom access log: they could print query strings and client addresses. Conventions: [logging-and-errors.md](logging-and-errors.md).

Checks, the same as CI's "Backend checks" (PostgreSQL must be running, and `DATABASE_URL` and `TEST_DATABASE_URL` set, normally through `.env`):

```sh
ruff check .
alembic upgrade head
alembic check
pytest -q
```

Each test runs inside a transaction that is rolled back afterwards, so tests do not affect each other or leave data behind. The migration tests reset the Alembic bookkeeping in the test database, which is why that database must never be the development one.

## 5. Frontend (React, TypeScript, Vite)

Set up, from the repository root:

```sh
cd frontend
npm ci
```

Run the development server. The URL is printed in the terminal, by default <http://localhost:5173>:

```sh
npm run dev
```

Checks and build, the same as CI's "Frontend checks":

```sh
npm run typecheck
npm run lint
npm run test -- --run
npm run build
```

`npm run build` also type-checks. The separate `typecheck` step exists so a type error is reported on its own.

## Security checks

CI also runs a secret scan and a dependency audit (the "Security" workflow). The commands to run the audits locally, and how findings are triaged, are in [security-checks.md](security-checks.md). Never commit credentials: the secret scan fails the pull request if you do.

## Before opening a pull request

Run the backend checks (section 4) and the frontend checks (section 5). They must all pass. CI runs them on every pull request.

## Test data

Tests, demos and screenshots use synthetic data only. Never use real people's information or real credentials.

## Troubleshooting

**`npm ci` fails with `npm error code EUSAGE`, or says it can only install with an existing `package-lock.json`.**
You are not in `frontend/`, or the lockfile is missing. Run `cd frontend`, and make sure `frontend/package-lock.json` is present and committed. Use `npm ci` rather than `npm install` so the lockfile is respected.

**npm prints `EBADENGINE Unsupported engine` with `required: { node: '>=24.0.0' }`.**
Your Node.js is older than the supported version. Install Node.js 24 LTS, or run `nvm use` in `frontend/`. Check with `node -v`.

**`pip install` says `requires a different Python: 3.11.x not in '>=3.12'`.**
The virtual environment was created with an older Python. Install Python 3.12, delete `backend/.venv`, and recreate it with `python3.12 -m venv .venv`.

**`pytest` fails with `ImportError while importing test module`, `ModuleNotFoundError` or `command not found: ruff`.**
The dependencies are not installed in the active environment. From `backend/`, run `source .venv/bin/activate` and then `pip install -e ".[dev]"`.

**Database tests fail with `TEST_DATABASE_URL is not set` or `Cannot connect to the test database`.**
This is intentional: database tests never skip. Start PostgreSQL (section 3), and make sure `.env` exists and `TEST_DATABASE_URL` is set. Port 5432 being used by another PostgreSQL is a common cause: stop it, or change the host port in `compose.yaml` and the URLs in `.env`.

**Database tests fail with `must point at a dedicated database whose name ends in '_test'`.**
`TEST_DATABASE_URL` points at the wrong database. The tests refuse to run anywhere except a `_test` database.

**`alembic upgrade head` fails with `DATABASE_URL is not set`.**
Copy `.env.example` to `.env` in the repository root, or export `DATABASE_URL`.

**`uvicorn` fails with `address already in use`.**
Something is already using port 8000. Stop it, or choose another port: `uvicorn app.main:app --reload --port 8001`.

**`npm run dev` prints `Port 5173 is in use, trying another one...`.**
This is not an error. Vite moves to the next free port and prints the new URL. To choose one yourself, run `npm run dev -- --port 5174`.

**Changing `.env` has no effect.**
Restart the backend. A real environment variable with the same name takes priority over `.env`, so check `echo $APP_ENV`. The file must be named `.env` and sit in the repository root, not in `backend/`.

**CI fails at "Set up Node.js" with `Some specified paths were not resolved, unable to cache dependencies`.**
`frontend/package-lock.json` is missing from the commit. Run `npm install` in `frontend/` and commit the lockfile.

**CI fails at "Set up Python" with `No file ... matched to [backend/pyproject.toml ...]`.**
`backend/pyproject.toml` is missing from the commit, or the dependency path in `.github/workflows/ci.yml` does not match it.
