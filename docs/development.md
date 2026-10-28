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

No real values are needed yet. The only setting is:

| Variable | Default | Meaning |
|---|---|---|
| `APP_ENV` | `development` | One of `development`, `test` or `production`. |

Settings are read from real environment variables first and then from the `.env` file in the repository root. Restart the backend after changing them.

Later issues add settings such as the database (#7) and Supabase authentication (#10, #11). Each one adds placeholders to `.env.example` and documents where to obtain the real value. Real values live only in your local `.env` or the project's approved secret store.

## 3. Backend (FastAPI)

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

Checks, the same as CI's "Backend checks":

```sh
ruff check .
pytest -q
```

## 4. Frontend (React, TypeScript, Vite)

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

## Before opening a pull request

Run the backend checks (section 3) and the frontend checks (section 4). They must all pass. CI runs them on every pull request.

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
