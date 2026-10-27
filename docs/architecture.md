# Relationship Archive — System Architecture

**Status:** Proposed  
**Last updated:** 2026-10-27  
**Related decisions:** ADR-001, ADR-002, ADR-003

## 1. Architectural goals

Relationship Archive is a private, AI-assisted application for maintaining useful personal memories about relationships.

The architecture must:

- Protect personal information through authentication and server-side authorization.
- Support reliable storage and retrieval of people, notes, and important dates.
- Remain straightforward for a solo developer to understand and maintain.
- Support automated testing, database migrations, CI, and controlled deployments.
- Allow future AI features without making the initial application dependent on an AI provider.
- Leave room for future multi-user use without prematurely building a complex distributed system.

## 2. Technology stack

| Layer | Technology | Responsibility |
|---|---|---|
| Frontend | React, TypeScript, Vite | User interface and client-side interaction |
| Backend | Python, FastAPI | REST API, validation, business rules, authorization |
| Database | PostgreSQL | Persistent application data |
| ORM | SQLAlchemy | Database models and queries |
| Migrations | Alembic | Version-controlled schema changes |
| Authentication | Supabase Auth | Managed identity and sign-in |
| Backend tests | pytest | Business logic, API, authorization, and database tests |
| Frontend tests | Vitest and React Testing Library | Component and interaction tests |
| End-to-end tests | Playwright, introduced when useful | Critical user journeys |
| CI/CD | GitHub Actions | Automated quality checks and deployment workflows |
| Hosting | To be decided | Frontend, API, and database hosting |

Exact dependency versions and supported runtime versions will be recorded in the project configuration and updated deliberately.

## 3. System overview

```text
User
  |
  v
React + TypeScript frontend
  |
  | HTTPS requests + access token
  v
FastAPI backend
  |
  +--> Authenticate and authorize request
  |
  +--> Validate input and enforce business rules
  |
  v
SQLAlchemy
  |
  v
PostgreSQL

Supabase Auth
  |
  +--> Sign-in and identity management
  |
  +--> Verified identity tokens consumed by the API

GitHub Actions
  |
  +--> Lint, test, and build
  +--> Deployment workflows added after hosting is selected
```

The frontend is not a trusted security boundary. The backend must verify the user's identity and permissions before returning or changing private data.

## 4. Application structure

The repository will use separate frontend and backend applications.

```text
relationship-archive/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   ├── workflows/
│   └── pull_request_template.md
├── docs/
│   ├── architecture.md
│   └── decisions/
├── frontend/
│   ├── src/
│   ├── public/
│   └── package.json
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── main.py
│   ├── migrations/
│   ├── tests/
│   └── pyproject.toml
├── .editorconfig
├── .gitignore
├── .env.example
├── CLAUDE.md
├── CONTRIBUTING.md
└── README.md
```

This is a target structure, not a requirement to create every directory immediately. Add modules when they serve a real responsibility.

### Backend responsibilities

- `api/`: HTTP routes and request handling.
- `core/`: configuration, authentication, and shared infrastructure.
- `models/`: SQLAlchemy database models.
- `schemas/`: request and response validation.
- `services/`: business logic that should not live inside route handlers.
- `migrations/`: Alembic schema migration history.
- `tests/`: automated backend tests.

Avoid creating unnecessary abstractions for features that do not yet exist.

## 5. Initial domain model

The first data model should support:

### Person
- Unique internal identifier.
- Owner identifier.
- Display name.
- Optional birthday.
- Optional timezone or date-handling metadata where appropriate.
- Optional interests and preferences, each a list of short, user-entered text entries. Count and length limits are defined in the API schema. These are user-provided information; AI-suggested or discovered candidates are stored separately as unconfirmed until the user accepts them (ADR-003).
- Created and updated timestamps.

### Note
- Unique internal identifier.
- Owner identifier.
- Associated person identifier.
- Note content.
- Creation and update timestamps.

### Future entities

Important events, reminders, discovered public profiles, and AI-generated suggestions will be added in later milestones.

Database relationships and authorization rules must prevent a user from accessing records belonging to another user. The backend must not rely on a client-supplied owner identifier as proof of ownership.

## 6. API design

The backend will expose a versioned REST API, initially under `/api/v1`.

Likely initial endpoints:

- `GET /api/v1/health` — basic service health.
- `GET /api/v1/people` — list the authenticated user's people.
- `POST /api/v1/people` — create a person.
- `GET /api/v1/people/{person_id}` — retrieve an authorized person.
- `PATCH /api/v1/people/{person_id}` — update an authorized person.
- `DELETE /api/v1/people/{person_id}` — delete an authorized person.
- `GET /api/v1/people/{person_id}/notes` — list notes for an authorized person.
- `POST /api/v1/people/{person_id}/notes` — add a note.

These endpoints are planned contracts, not implemented functionality.

All inputs and outputs must be validated. Responses must avoid exposing database internals, authentication secrets, or unrelated personal records.

## 7. Authentication and authorization

Supabase Auth will handle user identity and sign-in.

The frontend will obtain an access token and send it to the backend using the standard Bearer authorization header over HTTPS.

The backend must:

1. Validate the token's signature and relevant claims using the supported verification approach.
2. Reject expired, invalid, or otherwise unacceptable tokens.
3. Derive the authenticated user's identity from the verified token.
4. Enforce authorization for every operation involving private records.
5. Never trust a user ID supplied in a request body or query string as the authenticated identity.

Database access should be structured so ownership checks are difficult to omit. Tests must verify both permitted access and cross-user access denial.

Authentication and authorization are separate concerns; both must be implemented and tested.

## 8. Database and migrations

PostgreSQL will be the persistent source of truth.

SQLAlchemy will provide database access, and Alembic will manage schema migrations.

Rules:

- Commit every intentional schema change with its migration.
- Test migrations against a real PostgreSQL test instance in CI where practical.
- Do not rely on automatic table creation as a production migration strategy.
- Use transactions for operations that require atomic changes.
- Use parameterized ORM/database operations rather than constructing unsafe SQL.
- Keep database credentials out of source control.
- Define backup and restoration procedures before production data is introduced.

## 9. Privacy and data lifecycle

Personal records are private by default.

The application will:

- Collect only data needed for the intended functionality.
- Avoid logging personal note contents and access tokens.
- Provide a way to export and delete the user's records.
- Consider deletion across dependent records and backups.
- Use synthetic fixtures in development and tests.
- Require explicit approval before sending personal information to an external AI service.
- Distinguish user-confirmed facts, sourced information, and AI-generated suggestions.

Public profile discovery and AI-based memory extraction are outside the initial MVP.

## 10. Testing strategy

### Backend
- Unit tests for business rules and validation.
- API tests for expected success and error responses.
- Authorization tests for cross-user access.
- Database integration tests using PostgreSQL.
- Migration tests where appropriate.

### Frontend
- Component and interaction tests using Vitest and React Testing Library.
- Tests for loading, error, empty, and successful states.
- Tests for forms and validation.

### End-to-end
Introduce Playwright tests for critical journeys, including signing in, creating a person, adding a note, and viewing the person's information.

Tests must not use real personal information.

## 11. CI/CD and Git workflow

The `main` branch must remain in a deployable state.

- Work through prioritized GitHub Issues.
- Create a feature branch for each issue.
- Open pull requests against `main`.
- Run linting, tests, and builds before merging.
- Use preview deployments for pull requests where supported.
- Deploy production from `main` only after required checks pass.
- Protect deployment credentials using repository or environment secrets.
- Keep deployment and rollback procedures documented.

Production hosting is not selected yet. Deployment configuration will follow that decision rather than assuming a particular provider.

## 12. AI integration strategy

AI will be introduced after the core archive, notes, and birthday functionality works reliably.

Before integration, document:

- Which data may be sent to the AI provider.
- How provider retention and training policies are evaluated.
- How users approve or reject extracted facts.
- How suggestions are distinguished from confirmed facts.
- How AI failures, timeouts, and provider changes are handled.

The core archive must remain usable when AI services are unavailable.

## 13. Initial implementation order

1. Repository standards and development documentation.
2. Frontend and backend scaffold.
3. Configuration and health endpoint.
4. Database connection and first migration.
5. Authentication and authorization foundation.
6. People profile CRUD.
7. Notes.
8. Birthday listing and reminders.
9. Production readiness and deployment.
10. AI features in a later milestone.

## 14. Open decisions

The following remain unresolved until reviewed and documented:

- Hosting provider and deployment topology.
- Exact authentication token verification implementation.
- Database hosting, backup, and recovery plan.
- Notification delivery provider.
- AI provider and personal-data processing boundaries.
- Approved sources and permitted methods for public profile discovery.

Architecture decisions should be revisited when evidence, security requirements, or product needs justify a change.