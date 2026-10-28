# ADR-003: Personal Data Handling and Privacy Boundaries

- **Status:** Accepted
- **Date:** 2026-10-27
- **Accepted:** 2026-10-28, on the project maintainer's instruction
- **Decision owners:** Project maintainer

## Context

Relationship Archive is designed to help users remember personal details and maintain relationships. It may eventually process notes using AI and assist with discovering publicly available social profiles.

These capabilities introduce risks involving data exposure, incorrect inferences, external data sharing, and mistaken identity.

## Decision

The application will follow these principles:

### Private by default
Personal records are accessible only to their authorized owner unless a separately designed and explicitly approved sharing feature is introduced.

### Data minimization
Collect only information necessary for the intended features. Avoid collecting sensitive details without a clear user benefit and appropriate safeguards.

### Provenance and confidence
Distinguish between:

- Information entered or explicitly confirmed by the user.
- Information obtained from an identifiable source.
- AI-generated suggestions or candidate facts.

Store source and review metadata where appropriate. AI-generated suggestions must not silently become confirmed facts.

### AI boundaries
AI functionality will be introduced after the core archive works.

Before sending personal data to an external AI provider, document the provider's retention, training, security, and data-processing practices. Limit each request to the information needed for the task.

Where practical, allow users to review and approve extracted facts before saving them as confirmed information.

### Public profile discovery
Future discovery functionality must use permitted public sources and respect their access restrictions and terms.

Potential profile matches must show their source and uncertainty. A match must not be treated as verified until the user confirms it.

The application must not access private accounts without authorization, use leaked data, bypass access controls, or conduct covert monitoring.

### User control
Plan for user-accessible export and deletion. Deletion behavior must account for related records, derived information, and documented retention limitations.

### Development safety
Use synthetic data in tests, demos, and development fixtures. Do not commit personal records, credentials, access tokens, or production data.

## Consequences

These principles add design and implementation work, but reduce the risk of exposing private information or presenting unsupported AI inferences as facts.

They also create a foundation for evaluating future AI providers and public data sources consistently.

## Review conditions

Review this decision before enabling AI processing, public profile discovery, data sharing, background refreshes, or any feature that sends personal information to an external service.

## Acceptance review

Accepted on 2026-10-28 on the project maintainer's instruction, after the contents were compared with the privacy and security principles in the project instructions and `docs/architecture.md` section 9. Private-by-default storage, provenance, AI boundaries, public-profile discovery limits, user control and synthetic test data match.

Not decided here: the AI provider and its permitted data boundaries (#23) and the approved discovery sources (#29). The review conditions above still apply before either is enabled.
