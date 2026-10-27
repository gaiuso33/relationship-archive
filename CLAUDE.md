
# Engineering Guidelines

## Workflow
- Read the relevant GitHub issue before implementing a task.
- Work on a feature branch; never commit directly to main.
- Keep changes focused and explain important implementation decisions.
- Link pull requests to their corresponding issues.
- Do not merge changes without passing required CI checks.

## Testing
- Add or update automated tests for changed behavior.
- Include regression tests for bug fixes when practical.
- Never delete, skip, or weaken tests merely to make CI pass.
- Run relevant lint, test, and build commands before finishing.

## Security and privacy
- Never commit credentials, secrets, or real personal data.
- Follow least-privilege access principles.
- Keep the archive private by default.
- Require user permission for proactive behavior.
- Treat public profile matches and AI-generated insights as unverified
  until appropriately reviewed.
- Document security-sensitive changes.

## Completion
- Verify the issue's acceptance criteria.
- Update relevant documentation.
- Report commands executed and their actual results.
- Clearly identify failing checks, untested behavior, and limitations.
- Do not claim that tests or deployments passed unless verified.
