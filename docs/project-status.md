# Relationship Archive — Project Status

**Last reviewed:** 2026-10-27  
**Current milestone:** v0.1.0 — Engineering Foundation  
**Current phase:** Planning completed; implementation preparation

## Product

Relationship Archive is a private-by-default, AI-assisted relationship memory application.

The initial scope is authentication, people profiles, written notes, birthdays, and basic reminders.

AI memory assistance and public profile discovery are deferred until the core archive is working and the necessary privacy decisions have been reviewed.

## Engineering decisions

The planned stack is React, TypeScript, Vite, Python, FastAPI, PostgreSQL, SQLAlchemy, Alembic, Supabase Auth, and GitHub Actions.

Frontend and backend tests are planned. Deployment hosting has not yet been finalized.

See `docs/architecture.md` and `docs/decisions/` for the detailed design.

## Project management

- GitHub repository: https://github.com/gaiuso33/relationship-archive
- Work is tracked through prioritized GitHub Issues.
- Development uses feature branches and pull requests targeting `main`.
- CI checks must pass before merging.
- PR preview and production deployment workflows will be configured as hosting decisions are finalized.

## Completed planning work

The product requirements, architecture documentation, architecture decision records, GitHub workflow files, six issues, milestones, and project board have been created by the project owner.

## Current implementation status

The implementation state has not yet been independently verified by the assistant. Before coding, inspect the actual repository and existing issues. Do not assume the application scaffold, tests, CI pipeline, or deployment have been implemented.

## Next action

Inspect the repository and all six existing issues. Recommend the highest-priority actionable issue for the engineering foundation milestone. Avoid recreating existing documentation or implementing multiple issues in one pass.