# Contributing

Relationship Archive stores private information about people, so the workflow below puts correctness, testing and privacy first. This guide covers the process. For local setup, running the app and running the checks, see the [development guide](docs/development.md).

## Workflow

1. **Start from an issue.** Work is tracked in GitHub Issues with acceptance criteria. If none exists, open one using an issue template. Read the issue, its dependencies and its acceptance criteria before you start.
2. **Create a branch from the latest `main`.** Never commit to `main` directly.
3. **Make a focused change.** Implement only what the issue asks for, with tests, and update documentation when behavior, configuration or setup changes.
4. **Run the checks** listed in the [development guide](docs/development.md) and fix any failure.
5. **Open a pull request into `main`** using the pull request template, and link the issue.
6. **Wait for CI and review.** The maintainer merges once the checks pass and the change is approved.

## Branch names

Use `<type>/<issue-number>-<short-description>` in lowercase with hyphens. Leave out the number only when there is no issue, and list every number when a branch covers several issues.

| Type | Use for |
|---|---|
| `feature` | New user-facing capability |
| `fix` | Bug fix |
| `docs` | Documentation |
| `chore` | Maintenance, tooling and configuration |
| `test` | Test infrastructure or coverage |
| `security` | Security and privacy work |
| `refactor` | Refactoring and technical debt |

Examples: `feature/4-application-scaffold`, `chore/3-5-6-engineering-foundation`, `docs/accept-adr-001-003`.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/): `type: short summary`, where the type is `feat`, `fix`, `docs`, `test`, `chore`, `ci` or `refactor`. Write the summary in the imperative (`add health endpoint`). Use the body to explain why, and reference the issue with `Refs #N`.

## Linking issues

- Write `Closes #N` only when the pull request satisfies every acceptance criterion of the issue.
- Write `Refs #N` when it does not, for example when part of the work is a GitHub setting that only the maintainer can change.

## Pull requests

- Keep each pull request small and about one thing. Do not include unrelated changes.
- Fill in every section of the template. Under Verification, list the commands you ran and their actual results. Do not tick a box for a check you did not run.
- Use a draft pull request for work in progress.
- Explain trade-offs and limitations in the reviewer notes.

## Review and merging

The maintainer reviews and merges pull requests. Do not merge your own pull request.

A reviewer checks that:

- the acceptance criteria of the linked issue are met;
- the tests are meaningful and cover the change, including failure cases;
- the security and privacy checklist holds, for example access checks on private data and no personal data in logs;
- documentation is updated and the diff contains nothing unrelated.

Merging requires passing CI. A separate Security workflow adds the "Secret scan" and "Dependency audit" jobs (see [docs/security-checks.md](docs/security-checks.md)); a failing "Dependency audit" is not required to merge but must not be ignored. The two main CI jobs are **Frontend checks** and **Backend checks**, and `main` requires both to pass before a pull request can be merged.

`main` is protected by a repository ruleset: changes must arrive through a pull request, both checks above are required, force pushes and deletion are blocked, and there are no bypass actors. A pull request does not have to be up to date with `main` to merge. The ruleset does not currently require an approving review, because the maintainer is the only reviewer and GitHub does not allow approving your own pull request. A review is still expected.

## Testing

| Check | Where | Command |
|---|---|---|
| Type-check | `frontend/` | `npm run typecheck` |
| Lint | `frontend/` | `npm run lint` |
| Unit and component tests | `frontend/` | `npm run test -- --run` |
| Production build | `frontend/` | `npm run build` |
| Lint | `backend/` | `ruff check .` |
| Apply migrations | `backend/` | `alembic upgrade head` |
| Models and migrations agree | `backend/` | `alembic check` |
| Tests (need PostgreSQL) | `backend/` | `pytest -q` |

- Add or update tests for every behavior change, and add a regression test for every bug fix.
- Never delete, skip or weaken a test just to make CI pass.
- Features that touch private data need authorization tests, including that one user cannot read, change or delete another user's records ([ADR-002](docs/decisions/ADR-002-authentication-and-authorization.md)).
- Use synthetic data only.
- Database tests run against a real PostgreSQL and fail rather than skip when it is missing. Schema changes need an Alembic migration.

## Labels and priority

| Label | Meaning |
|---|---|
| `type: feature` | New user-facing capability |
| `type: bug` | Incorrect or broken behavior |
| `type: chore` | Maintenance and routine work |
| `type: docs` | Documentation |
| `type: security` | Security and privacy work |
| `type: technical-debt` | Refactoring or architectural |
| `type: test` | Test infrastructure or coverage |
| `area: frontend`, `area: backend`, `area: devops`, `area: ai`, `area: privacy` | The part of the system affected |
| `priority: P0` | Critical: immediate attention |
| `priority: P1` | High: next priority |
| `priority: P2` | Normal: planned work |
| `priority: P3` | Low: defer if necessary |
| `status: triage` | Needs evaluation |
| `status: ready` | Defined and ready to implement |
| `status: blocked` | Waiting on a dependency or decision |

Issues list their dependencies. Do not start an issue whose dependencies are unfinished unless the issue says it can start independently.

## Security and privacy

Treat every relationship record as private personal data.

- Never commit credentials, tokens, `.env` files or real personal data, and never paste them into issues, pull requests, logs or screenshots. The "Secret scan" job checks every pull request; how findings are triaged is in [docs/security-checks.md](docs/security-checks.md).
- Do not log access tokens or the contents of notes. The logging and error-handling conventions are in [docs/logging-and-errors.md](docs/logging-and-errors.md).
- If you commit a secret, revoke or rotate it immediately. Deleting it in a later commit is not enough, because it stays in the history. Tell the maintainer.
- Do not report vulnerabilities in public issues or pull requests. Report them privately with [GitHub private vulnerability reporting](https://github.com/gaiuso33/relationship-archive/security/advisories/new).
- Do not send personal data to an external AI provider, or build public profile discovery, until the decision records for those areas are accepted. See [ADR-003](docs/decisions/ADR-003-data-and-privacy.md).

## Architecture and documentation

Accepted architecture decisions live in [docs/decisions](docs/decisions/) and [docs/architecture.md](docs/architecture.md). Do not change an accepted decision as a side effect of another change. Propose it in an issue, and record a new decision when one is made.
