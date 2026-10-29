# Security checks

Automated security checks that run in GitHub Actions, how to read their results, and how findings are triaged. The checks live in [`.github/workflows/security.yml`](../.github/workflows/security.yml), separate from the main CI workflow, so the required checks "Frontend checks" and "Backend checks" are unchanged.

For the workflow around them, see [CONTRIBUTING.md](../CONTRIBUTING.md). For the security and privacy rules these checks support, see [ADR-002](decisions/ADR-002-authentication-and-authorization.md) and [ADR-003](decisions/ADR-003-data-and-privacy.md).

## What runs

| Check (job name) | Tool | Looks for | When |
|---|---|---|---|
| **Secret scan** | [Gitleaks](https://github.com/gitleaks/gitleaks) | Committed credentials, tokens and keys | Pull requests (the commits the PR adds), pushes to `main` and a weekly schedule (whole history), manual runs |
| **Dependency audit** | `npm audit`, [pip-audit](https://github.com/pypa/pip-audit) | Known vulnerabilities in the frontend and backend dependencies | Pull requests, pushes to `main`, a weekly schedule, manual runs |

These checks complement, and do not replace, GitHub's own secret scanning and push protection, which are repository settings. CodeQL code scanning is not part of this work.

Both jobs run with `permissions: contents: read`, use no repository secrets, and never upload scan output as an artifact.

## What fails and what warns

| Finding | Result |
|---|---|
| Any Gitleaks finding | **Fails** "Secret scan" |
| npm advisory of high or critical severity | **Fails** "Dependency audit" |
| npm advisory of low or moderate severity | **Warning** annotation: the job stays green, the full report is in the log |
| Any pip-audit finding (pip-audit has no severity filter) | **Fails** "Dependency audit", unless an unexpired [exception](#python-vulnerability-exceptions) covers it |
| Scanner or advisory service unavailable | **Fails**, so a pass always means a real scan ran. Re-run the job |
| Malformed or expired pip-audit exception | **Fails** |

"Secret scan" is intended to become a required check after it has been validated on `main` (a repository ruleset change made by the owner). "Dependency audit" is not required, so a newly published advisory cannot block an unrelated pull request. It still shows red on the pull request and on the weekly run, and a failing weekly run is reported by GitHub's usual workflow failure notification. Do not ignore a red "Dependency audit".

## How the secret scan works

- Gitleaks is pinned by version and by the SHA-256 of the release archive, and the job refuses to run if the checksum does not match.
- Before scanning, the job writes a fresh synthetic canary (`RA_CANARY_` followed by 20 characters) into a temporary directory and requires Gitleaks to detect it, exit with status 1 and redact it. This proves the scanner, its configuration and its redaction work on every run. The canary exists only on the runner and is never committed.
- Findings are redacted (`--redact`): the log shows the file, line, rule and commit, never the secret. No report file is written.
- Configuration: [`.gitleaks.toml`](../.gitleaks.toml). It keeps every default rule and adds the canary rule and a short allowlist for the documented placeholder passwords (local development database, CI service container, test fixtures).

Run it locally if you have Gitleaks installed (`gitleaks git --config .gitleaks.toml --redact --no-banner --verbose .`). CI is the reference.

### Upgrading Gitleaks

The pinned version and checksum are in `security.yml` (`GITLEAKS_VERSION`, `GITLEAKS_SHA256`). Dependabot cannot update them, so upgrade by hand in a pull request:

1. Pick the release on the Gitleaks releases page.
2. Download `gitleaks_<version>_checksums.txt` from the same release and copy the SHA-256 listed for `gitleaks_<version>_linux_x64.tar.gz`.
3. Update both values and check that "Secret scan" passes.

The current pin was obtained from a CI run that computed the archive hash and compared it with the project's published checksums file (they matched). That is a trust-on-first-use pin plus a cross-check, not a signature verification.

## How the dependency audit works

- **Frontend:** `npm audit` reads the committed `frontend/package-lock.json`. Dependencies are not installed, so no install scripts run in this job. Advisories come from the GitHub Advisory Database through the npm registry.
- **Backend:** the job installs the backend (`pip install -e "./backend[dev]"`) into one virtual environment and runs a pinned pip-audit from another, auditing exactly what CI installed. Advisories come from the PyPI JSON API (the PyPA advisory database). The pip-audit version is pinned in [`.github/security/requirements-audit.txt`](../.github/security/requirements-audit.txt), which Dependabot keeps current.
- The backend has no lockfile yet, only version ranges, so a backend audit reflects whatever versions resolved on that day. Evaluating a lockfile is tracked in #46.
- Both audits send only package names and versions to those public services, never repository contents. No paid service is used.

Run the checks locally:

```sh
# Frontend (from frontend/)
npm audit --audit-level=high

# Backend (from backend/, with the virtual environment active)
pip install pip-audit
pip-audit --skip-editable
```

### Python vulnerability exceptions

pip-audit fails on any known vulnerability. If a finding cannot be fixed immediately and is shown not to be exploitable here, add an entry to [`.github/security/pip-audit-exceptions.json`](../.github/security/pip-audit-exceptions.json) in a pull request:

```json
{
  "exceptions": [
    {
      "id": "PYSEC-0000-0000",
      "reason": "Why this vulnerability does not apply to how we use the package",
      "owner": "Person or team responsible for fixing or reviewing it",
      "expires": "2026-12-31"
    }
  ]
}
```

All four fields are required. `expires` must be in the future and no more than 180 days away. An expired entry fails the job, so an exception cannot quietly become permanent. Renewing one needs a new pull request with a fresh rationale. The checker is [`run_pip_audit.py`](../.github/security/run_pip_audit.py), with unit tests that run in the job.

## Triage

1. **Look at the job log first.** A secret finding names the rule, file, line and commit. Gitleaks never prints the value.
2. **Real secret.** Treat it as compromised. **Revoke or rotate it immediately**, because deleting it in a later commit leaves it in the history. Then remove it from the code, move the value to an environment variable or approved secret store, and tell the maintainer. Never allowlist a real secret.
3. **False positive.** Prefer changing the code or test so it no longer looks like a secret. If that is not reasonable, add a narrow entry to `[[allowlists]]` in `.gitleaks.toml` with a comment saying why it is safe, and explain it in the pull request. The owner reviews every allowlist change.
4. **Vulnerable dependency.** Upgrade to the fixed version (a Dependabot pull request often already exists). If that is not possible, use a Python exception as above, or for npm record the reason in the pull request and the issue tracker. Do not weaken the check.
5. **Scanner problem** (the canary self-test fails, a checksum mismatch, an unavailable service). Do not bypass it. Re-run once, and if it persists open an issue labelled `type: security` and `area: devops`.

Report vulnerabilities in the application itself privately, not in issues: see [CONTRIBUTING.md](../CONTRIBUTING.md#security-and-privacy).

## Automated dependency updates (Dependabot)

[`.github/dependabot.yml`](../.github/dependabot.yml) opens weekly (Monday) update pull requests for the frontend npm packages, the backend Python packages, the pinned audit tooling, GitHub Actions and the Compose file.

Decision and trade-offs:

- **Minor and patch version updates are grouped** per ecosystem to limit pull request noise. Major versions arrive individually, and **security updates are never grouped**, so they stay visible and reviewable on their own.
- At most 5 open pull requests per main ecosystem.
- **PostgreSQL major versions are ignored.** Changing the database major version is an architecture decision, so only minor and patch updates of the Compose image are proposed. The PostgreSQL service in the CI workflow is not covered by Dependabot, so keep it in step with `compose.yaml` by hand.
- Dependabot pull requests run all the same checks and are reviewed and merged by the maintainer like any other.
- Backend dependencies are version ranges, so Dependabot mostly proposes widening a range. A lockfile (#46) would make its updates more precise.

## Repository settings that support these checks

These are changed by the repository owner in GitHub, not by code in this repository. They can be verified with the GitHub CLI:

| Setting | Verify with |
|---|---|
| Secret scanning and push protection enabled | `gh api repos/gaiuso33/relationship-archive --jq .security_and_analysis` |
| Dependabot alerts and security updates enabled | the same command (`dependabot_security_updates`), and `gh api repos/gaiuso33/relationship-archive/dependabot/alerts` must not answer "disabled" |
| Default workflow permissions are read-only, and Actions cannot approve pull requests | `gh api repos/gaiuso33/relationship-archive/actions/permissions/workflow` |
| "Secret scan" is a required check on `main` | the `branch protection` ruleset, Require status checks |
