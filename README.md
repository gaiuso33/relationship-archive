# Relationship Archive

A private-by-default, AI-assisted relationship memory application. It helps you remember meaningful details about the people in your life, keep notes, and remember birthdays, while keeping you in control of your information.

The project is at the engineering foundation stage (milestone v0.1.0). The application currently consists of a minimal scaffold: a React frontend and a FastAPI backend with a health endpoint. See `docs/product-requirements.md` for the product scope and `docs/architecture.md` and `docs/decisions/` for the design.

## Stack

- Frontend: React, TypeScript, Vite (`frontend/`)
- Backend: Python, FastAPI (`backend/`)
- Planned: PostgreSQL, SQLAlchemy, Alembic and Supabase Auth, introduced by later issues

## Running the checks

These are the same commands CI runs. Full setup, environment and troubleshooting instructions are tracked in issue #6.

Prerequisites: Node.js 22.12 or later and Python 3.12.

Backend:

```sh
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest -q
```

Frontend:

```sh
cd frontend
npm ci
npm run lint
npm run test -- --run
npm run build
```

## Running locally

```sh
# Backend: http://localhost:8000/api/v1/health
cd backend && uvicorn app.main:app --reload

# Frontend: the URL is printed by Vite
cd frontend && npm run dev
```

## Configuration

Copy `.env.example` to `.env` for local settings. `.env` is ignored by git. Never commit credentials, tokens or real personal data; tests and demos use synthetic data only.
