# ADR-001: Initial Technology Stack

- **Status:** Proposed
- **Date:** 2026-10-27
- **Decision owners:** Project maintainer

## Context

Relationship Archive needs a maintainable web architecture that supports private user data, a future AI assistant, automated testing, database migrations, and a reliable delivery pipeline.

The project is being developed by a solo engineer who wants to strengthen practical software engineering and DevOps skills.

## Decision

Adopt the following initial stack:

- Frontend: React, TypeScript, and Vite.
- Backend: Python and FastAPI.
- Database: PostgreSQL.
- ORM: SQLAlchemy.
- Schema migrations: Alembic.
- Authentication: Supabase Auth.
- Backend testing: pytest.
- Frontend testing: Vitest and React Testing Library.
- End-to-end testing: Playwright when appropriate.
- CI/CD: GitHub Actions.

Hosting and deployment providers will be selected separately.

## Rationale

- React and TypeScript provide a maintainable frontend with static type checking.
- Python supports the developer's existing experience and future AI integrations.
- FastAPI provides request validation, API documentation, and a clear HTTP API structure.
- PostgreSQL supports relational records and transactions.
- SQLAlchemy and Alembic separate data access from schema evolution.
- Managed authentication avoids unnecessarily building password management and identity infrastructure.
- GitHub Actions supports automated quality checks and repeatable delivery.

## Consequences

### Positive
- Clear separation between frontend, backend, and persistence.
- Strong support for automated testing and database migrations.
- A practical path toward later AI functionality.
- Useful portfolio evidence of engineering and delivery practices.

### Negative
- Multiple runtimes and dependency ecosystems must be maintained.
- Managed authentication introduces an external service dependency.
- Deployment and local development require configuration across components.

## Alternatives considered

- A single full-stack framework: simpler deployment in some cases, but less aligned with the planned Python API and AI integration.
- A fully managed backend platform: less infrastructure to maintain, but potentially less direct practice with API design and backend testing.
- Self-managed authentication: more control, but unnecessary security and maintenance overhead for the initial product.

## Review conditions

Reconsider this decision if the stack materially slows development, creates operational costs that the project cannot sustain, or fails to meet privacy and reliability requirements.