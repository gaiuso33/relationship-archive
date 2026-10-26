# Relationship Archive — Product Requirements

**Status:** Draft v0.1  
**Project phase:** Engineering Foundation

## 1. Product vision

A private, AI-assisted relationship memory application that helps users remember important details about their friends, discover relevant publicly available information, maintain meaningful connections, and act thoughtfully on what they know.

## 2. Target users

Initially, an individual user managing their own relationship archive. The architecture should allow for a future multi-user product.

## 3. Core capabilities

### People archive
- Create and manage individual profiles.
- Store birthdays, interests, preferences, important dates, and personal notes.
- Search and organize profiles.

### Public profile discovery
- Search permitted public sources for potentially relevant social profiles.
- Display source links and supporting evidence.
- Require user confirmation before associating discovered profiles with a person.
- Respect platform restrictions and applicable privacy requirements.

### Conversation memory
- Allow users to submit written notes about conversations.
- Use AI to extract candidate facts, interests, goals, and follow-up items.
- Allow users to review, correct, and reject extracted information.

### Events and reminders
- Track birthdays and other user-defined important dates.
- Display upcoming events and birthdays by month.
- Support configurable reminders and recurring events.

### AI relationship assistant
- Suggest conversation starters based on saved context.
- Recommend gifts using interests, preferences, occasions, and budgets.
- Offer relevant follow-up suggestions.
- Provide optional zodiac-based recommendations as entertainment.

### Proactive assistance
- Require user permission for proactive suggestions, automatically created reminders, and background profile refresh.
- Provide controls to review, disable, and revoke these capabilities.

## 4. Privacy and security principles

- Private by default.
- Authenticate users and enforce server-side access controls.
- Minimize collected personal information.
- Separate user-provided facts, sourced information, and AI-generated suggestions.
- Preserve source attribution and uncertainty for discovered information.
- Never treat a possible public-profile match as verified without appropriate confirmation.
- Protect credentials and secrets.
- Provide appropriate data export and deletion controls.
- Review external AI providers' data handling before sending personal information.

## 5. Initial MVP scope

The first usable release should include:
1. Secure authentication.
2. Create, view, edit, and delete people profiles.
3. Store personal notes and birthdays.
4. Display upcoming birthdays.
5. Configure basic reminders.
6. Run automated tests in CI.
7. Deploy the application through a controlled pipeline.

AI conversation memory and public profile discovery follow after the core archive and its access controls are established.

## 6. Engineering requirements

- GitHub Issues for tracked work.
- Feature branches and pull requests.
- Automated frontend and backend checks.
- Unit, integration, and selected end-to-end tests.
- Continuous integration through GitHub Actions.
- Preview deployments for pull requests.
- Production deployment after approved changes reach `main`.
- Documented setup, architecture, testing, and deployment procedures.

## 7. Open decisions

- Frontend and backend architecture.
- Database and migration strategy.
- Authentication provider.
- AI provider and data-processing boundaries.
- Notification delivery mechanism.
- Public-information discovery sources.
- Hosting and deployment providers.

## 8. Initial success criteria

A user can securely create a profile, save information about a friend, retrieve it later, and manage an upcoming birthday. The application can be tested locally from documented instructions, and a pull request cannot be merged without the required CI checks passing.