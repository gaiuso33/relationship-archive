# Relationship Archive — Product Requirements

**Status:** Draft v0.2 — pending owner review (not approved)  
**Previous version:** Draft v0.1 (2026-10-27), revised in place  
**Last revised:** 2026-10-31  
**Tracking issue:** [#1](https://github.com/gaiuso33/relationship-archive/issues/1)  
**Project phase:** Engineering Foundation

This revision closes the gaps listed in issue #1 without changing the product direction or the privacy principles in section 4. Sections 1 to 8 keep their numbers so existing references to them remain valid. New material is added as subsections and as sections 9 to 11.

## Document conventions

Every requirement, decision and open question carries one of these labels, so settled decisions are not confused with drafts.

| Label | Meaning |
|---|---|
| **Existing decision** | Recorded in an accepted ADR or a committed repository document. Not reopened here. |
| **Owner-decided** | Stated by the project owner for this revision on 2026-10-31. Recorded here; it becomes part of the approved document when the owner approves the whole PRD. |
| **Proposed** | Drafted for owner review. Not approved. |
| **Unresolved** | Needs an owner decision, or depends on another open decision. This document does not choose an answer. |
| **Out of scope** | Not part of the MVP. |

**Terminology.** "MVP" means the first usable release defined in section 5. The owner's decisions call this release "v0.1". It is not the same as the GitHub milestone "v0.1.0 — Engineering Foundation". The mapping between the MVP and the repository milestones is unresolved (section 7.4, U1).

Input from external reviewers is recorded in section 11. A reviewer's suggestion is not a requirement unless this document labels it Owner-decided or Existing decision.

## 1. Product vision

A private, AI-assisted relationship memory application that helps users remember important details about their friends, discover relevant publicly available information, maintain meaningful connections, and act thoughtfully on what they know.

Parts of this vision are roadmap items outside the MVP. See section 5.3.

## 2. Target users

Individual users, each managing their own private relationship archive. One account's data is never visible to another account (Existing decision: ADR-002, ADR-003). Registration is open to anyone (Owner-decided). Sharing an archive between users is not part of the MVP (section 5.3). The architecture should continue to allow a future multi-user product.

## 3. Core capabilities

Each capability shows its release. Bullets marked "Out of scope" are described for the product direction and are not built in the MVP.

### People archive — MVP
- Create and manage individual profiles.
- Store birthdays, interests, preferences, and personal notes. Custom important dates are deferred (section 5.3).
- Search profiles by name (Owner-decided). Organizing profiles beyond search, such as groups or tags, is undefined and deferred (Proposed, section 7.5).

### Public profile discovery — Out of scope
- Search permitted public sources for potentially relevant social profiles.
- Display source links and supporting evidence.
- Require user confirmation before associating discovered profiles with a person.
- Respect platform restrictions and applicable privacy requirements.

### Conversation memory
- Allow users to submit written notes about conversations. **MVP.**
- Use AI to extract candidate facts, interests, goals, and follow-up items. **Out of scope.**
- Allow users to review, correct, and reject extracted information. **Out of scope.**

### Events and reminders
- Track birthdays. **MVP.** Other user-defined important dates are deferred (Owner-decided).
- Display upcoming birthdays, and birthdays by month. **MVP.** The month view is Proposed (section 7.5).
- Support configurable birthday reminders. **MVP.** Delivery is Unresolved (#21). Recurring events are deferred (Owner-decided).

### AI relationship assistant — Out of scope
- Suggest conversation starters based on saved context.
- Recommend gifts using interests, preferences, occasions, and budgets.
- Offer relevant follow-up suggestions.
- Provide optional zodiac-based recommendations as entertainment.

### Proactive assistance
- Require user permission for proactive suggestions, automatically created reminders, and background profile refresh.
- Provide controls to review, disable, and revoke these capabilities.

In the MVP this principle applies to user-enabled birthday reminders (Existing decision: CLAUDE.md). Proactive suggestions and background profile refresh are out of scope.

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

These principles are recorded as accepted decisions in [ADR-002](decisions/ADR-002-authentication-and-authorization.md) and [ADR-003](decisions/ADR-003-data-and-privacy.md). How they apply to export and deletion in the MVP is set out in sections 9 and 10.

## 5. Initial MVP scope

### 5.1 Included capabilities

The first usable release includes the capabilities below. Each maps to at least one journey in section 9.

| # | Capability | Status | Journeys |
|---|---|---|---|
| 1 | Secure authentication: email-and-password registration, sign-in and sign-out; registration open to anyone | Owner-decided (method and open registration); Existing decision (Supabase Auth with server-side token verification, ADR-001, ADR-002) | J1 |
| 2 | Create, view, edit, and delete people profiles | Existing decision (Draft v0.1 section 5; architecture section 6) | J2, J5, J6, J11 |
| 3 | Store interests, preferences, personal notes, and birthdays | Existing decision (Draft v0.1 section 5; architecture section 5) | J3, J4 |
| 4 | Search people by name | Owner-decided | J7 |
| 5 | Display upcoming birthdays | Existing decision (Draft v0.1 section 5); month view Proposed | J8 |
| 6 | Configure basic birthday reminders, off until the user enables them | Existing decision (opt-in: section 3 and CLAUDE.md); delivery Unresolved (#21) | J9 |
| 7 | Export the user's data | Owner-decided | J10 |
| 8 | Delete a person, or delete the account and its data | Owner-decided | J11, J12 |
| 9 | Run automated tests in CI | Existing decision (section 6) | E1 |
| 10 | Deploy through a controlled pipeline | Existing decision (section 6); hosting Unresolved (#33) | E2 |

### 5.2 Classification of search, export, deletion and profile fields

Issue #1 requires each of these to be included or explicitly deferred.

| Item | Classification | Detail |
|---|---|---|
| Interests and preferences on the person profile | **Included** | Existing decision (architecture section 5). |
| Search | **Included**, by person name only | Owner-decided. Note contents, interests and preferences are not searched. |
| Data export | **Included** | Owner-decided. JSON; full archive and one person. See J10. |
| Data deletion | **Included** | Owner-decided. One person, or the whole account. See J11 and J12. |
| Custom important dates and recurring events | **Deferred** | Owner-decided. Birthdays are the only date-related feature in the MVP. |

### 5.3 Out of scope for the MVP

AI features and public profile discovery are deferred until the core archive works, its access controls are established, and the relevant privacy decisions have been reviewed (Existing decision: Draft v0.1 section 5; ADR-003 review conditions; architecture section 12). Neither is built in the MVP.

| Item | Label | Notes |
|---|---|---|
| AI extraction of facts, interests, goals and follow-ups from conversation notes | Out of scope (Owner-decided) | Later milestone. Provider and data boundaries: #23. ADR-003 review is required before enabling. |
| AI-powered suggestions: conversation starters, gift recommendations, follow-up suggestions, zodiac entertainment | Out of scope (Owner-decided) | Same as above. |
| Public profile discovery | Out of scope (Owner-decided) | Later milestone. Sources and policy: #29. ADR-003 review is required before enabling. |
| Sending personal data to any external AI service | Out of scope (Existing decision) | ADR-003 requires documented provider review first. No AI feature is built in the MVP, so none is sent. |
| Proactive assistance other than user-enabled birthday reminders: proactive suggestions, automatically created reminders, background profile refresh | Out of scope (Proposed) | Follows from the three items above. |
| Custom important dates and recurring events | Out of scope (Owner-decided) | See #20. |
| Re-import of exported data | Out of scope (Owner-decided) | Export only. |
| Search over note contents, interests and preferences | Out of scope (Owner-decided) | Names only. See #15. |
| Undo window or soft deletion | Out of scope (Owner-decided) | Deletion is immediate and permanent in the primary database. |
| Sharing an archive or records between users | Out of scope (Existing decision) | ADR-003: private by default until a separate sharing feature is designed and approved. |
| Organizing profiles beyond search, such as groups or tags | Out of scope (Proposed) | Draft v0.1 mentions "organize" without defining it. |

The core archive must remain usable without any AI service (Existing decision: architecture section 12).

## 6. Engineering requirements

- GitHub Issues for tracked work.
- Feature branches and pull requests.
- Automated frontend and backend checks.
- Unit, integration, and selected end-to-end tests.
- Continuous integration through GitHub Actions.
- Preview deployments for pull requests.
- Production deployment after approved changes reach `main`.
- Documented setup, architecture, testing, and deployment procedures.

## 7. Decisions

### 7.1 Accepted decisions (Existing decision)

These ADRs are Accepted in the repository. This PRD links to them and does not reopen them.

| Decision | Record | What it settles |
|---|---|---|
| Technology stack: React, TypeScript, Vite; Python, FastAPI; PostgreSQL; SQLAlchemy; Alembic; Supabase Auth; pytest, Vitest and React Testing Library, Playwright when useful; GitHub Actions | [ADR-001](decisions/ADR-001-technology-stack.md) | Frontend and backend architecture, database and migration strategy, and authentication provider. These were open in Draft v0.1. |
| Managed authentication with server-side authorization | [ADR-002](decisions/ADR-002-authentication-and-authorization.md) | Token verification by the API, ownership enforcement on every private record, required authorization tests. |
| Personal data handling and privacy boundaries | [ADR-003](decisions/ADR-003-data-and-privacy.md) | Private by default, data minimization, provenance, AI and discovery boundaries, export and deletion, synthetic test data. |

### 7.2 Owner decisions recorded in this revision (Owner-decided, 2026-10-31)

**Export**
- JSON format.
- Full-archive export and individual-person export.
- Includes all user-owned data: notes, interests, preferences, birthdays, reminder settings and relevant timestamps.
- Excludes internal database identifiers and authentication identifiers.
- Re-import is out of scope.
- Authentication is required.

**Deletion**
- Individual-person deletion and full account deletion.
- Account deletion includes the user's Supabase Auth identity and all associated primary-database records.
- Immediate hard deletion from the primary database, with no undo window.
- Backup-retention limits are documented as an unresolved operational requirement pending hosting decision #33.
- Explicit confirmation before deletion, and a post-deletion summary of what was removed and any data retained, with an explanation.

**Other**
- ADR-001 to ADR-003 are referenced as accepted; no ADR changes.
- Search covers names only.
- Custom important dates and recurring events are deferred. Birthdays are the only date-related feature.
- Registration is open.
- Authentication uses email and password.
- This PRD stays Draft v0.2 until the owner approves it.

### 7.3 Open decisions tracked elsewhere (Unresolved)

| Decision | Tracked in | Effect on the MVP |
|---|---|---|
| Hosting and deployment topology | [#33](https://github.com/gaiuso33/relationship-archive/issues/33) | Needed for controlled deployment and pull-request previews. Backup and log retention for deletion (J11, J12) depend on it. |
| Reminder delivery approach | [#21](https://github.com/gaiuso33/relationship-archive/issues/21) | Decides how a reminder reaches the user (J9). |
| Date and time-zone rules: birthday storage, 29 February, "today" in the user's time zone | [#18](https://github.com/gaiuso33/relationship-archive/issues/18) | Needed for J8 and J9. |
| Token verification approach | [#10](https://github.com/gaiuso33/relationship-archive/issues/10) | Implements ADR-002. The issue asks for owner approval before use. |
| AI provider and personal-data boundaries | [#23](https://github.com/gaiuso33/relationship-archive/issues/23) | Out of scope for the MVP. |
| Permitted public profile sources and policy | [#29](https://github.com/gaiuso33/relationship-archive/issues/29) | Out of scope for the MVP. |

### 7.4 Unresolved owner decisions for the MVP

This document asks these questions and does not answer them.

- **U1. Milestone mapping.** The repository has milestones v0.4.0 (AI Memory Assistant) and v0.5.0 (Public Profile Discovery) before v0.6.0 (Production Beta), while this PRD puts AI and discovery out of scope for the MVP and includes controlled deployment in it. Which milestone completes the MVP, and are v0.4.0 and v0.5.0 after the MVP?
- **U2. Export details not covered by the owner's decisions.**
  - How the file reaches the user, for example an in-app download. Delivery by email would depend on #21.
  - Whether the user must re-authenticate before exporting or deleting.
  - Whether export and deletion events are logged, without personal content.
  - The exact field list and JSON structure. Without identifiers, relationships such as a note belonging to a person must be expressed by structure. To be proposed in #16 for approval.
- **U3. Deletion details.**
  - Backup retention: how long deleted data persists in backups and how that is documented. Pending #33 (Owner-decided to leave unresolved).
  - Log retention: application logs carry no personal record content or user identifiers by design ([logging-and-errors.md](logging-and-errors.md)), but retention of shipped logs is not decided (#33, v0.6.0).
  - Supabase Auth: what the provider retains after an identity is deleted has not been verified.
  - Partial failure: the order of database and identity deletion, and what the user is told if only one completes.
  - The form of explicit confirmation, for example a dialog or a typed phrase.
  - Whether the post-deletion summary is shown only on screen.
  - Whether any legal or regulatory retention obligation applies. This has not been assessed, and this PRD gives no legal advice.
- **U4. Registration details.** Email verification, password reset, password rules, and abuse protection such as rate limiting for open registration. No issue covers these; [#11](https://github.com/gaiuso33/relationship-archive/issues/11) covers sign-in and sign-out only.
- **U5. Reminder settings.** Which options a user can configure (for example lead time), and whether settings belong to the user or to each person. The answer decides what an individual-person export contains. See #22.

### 7.5 Proposed items awaiting confirmation

- **P1.** Editing and deleting notes (J4). This extends the endpoint list in architecture section 6, and [#13](https://github.com/gaiuso33/relationship-archive/issues/13) already asks for owner confirmation.
- **P2.** A month view of birthdays in the MVP (J8). Draft v0.1 section 3 mentions "by month"; section 5 mentions only "upcoming birthdays".
- **P3.** Deferring "organize profiles" (section 5.3).
- **P4.** Clarification: a birthday reminder repeats each year because a birthday does. The deferral of "recurring events" refers to custom events, not to birthdays.
- **P5.** All acceptance criteria labelled Proposed in section 9.

### 7.6 Backlog and documentation alignment

These artifacts disagree with this draft or with an owner decision. They are not changed by the pull request that revised this document. Resolve them before the owner approves the PRD.

| Artifact | Difference | Needed change |
|---|---|---|
| [#15](https://github.com/gaiuso33/relationship-archive/issues/15) Search | Covers person names, interests, preferences and note text. | Narrow to person names (Owner-decided). |
| [#16](https://github.com/gaiuso33/relationship-archive/issues/16) Export and deletion | Describes export and deletion of all archive records and leaves identity removal undecided. | Add individual-person export and deletion, account deletion including the Supabase identity, the post-deletion summary, hard deletion, and the exclusion of identifiers from export. |
| [#20](https://github.com/gaiuso33/relationship-archive/issues/20) Important events | In milestone v0.3.0 as MVP-era work. | Deferred (Owner-decided). The owner decides where it sits. |
| [#11](https://github.com/gaiuso33/relationship-archive/issues/11) Frontend sign-in | Covers sign-in and sign-out only. | Cover registration with email and password, or add an issue (U4). |
| Milestones | See U1. | Owner decision. |
| [architecture.md](architecture.md) | Section 5 lists reminders under future entities, but reminders are in the MVP. Section 6 lists no search, birthday, export, account-deletion or reminder endpoints. | Update with the issue that implements each. |

## 8. Initial success criteria

A user can securely create a profile, save information about a friend, retrieve it later, and manage an upcoming birthday. The application can be tested locally from documented instructions, and a pull request cannot be merged without the required CI checks passing.

Acceptance criteria for each capability are in section 9.

## 9. MVP user journeys and acceptance criteria

J1 to J12 cover the user-facing capabilities. E1 and E2 cover the two engineering capabilities. Each criterion carries a label (see "Document conventions"). Examples use synthetic data only.

**Synthetic data used in examples**
- User A (`user-a@example.test`) owns Sample Friend A (birthday 12 March; interest "hiking"; preference "prefers tea"; one note, "Synthetic note: suggested a trail walk.") and Sample Friend B (birthday 20 April).
- User B (`user-b@example.test`) owns Sample Friend C.
- No real names, contacts, notes or credentials appear in this document, tests or demos (Existing decision: ADR-003).

### J1. Register, sign in and sign out
*Capability 1.* A visitor registers with an email address and password, signs in, uses the archive, and signs out.
- Anyone can register with email and password. (Owner-decided)
- After sign-out the app shows no private screens until the user signs in again. (Proposed)
- A request to a private endpoint without a valid token is rejected; invalid, malformed and expired tokens are rejected. (Existing decision: ADR-002, tests 1 and 5)
- The user's identity comes only from the verified token, never from a request body or query string. (Existing decision: ADR-002)
- Frontend route guards are a convenience; the backend is the security boundary. (Existing decision: ADR-002)
- Email verification, password reset, password rules and abuse protection. (Unresolved: U4)

### J2. Create a person
*Capability 2.* User A adds "Sample Friend A" with an optional birthday.
- A person with a display name is saved and appears in User A's list. (Proposed)
- A missing or empty display name is rejected with a validation error. (Proposed)
- The birthday is optional. (Existing decision: architecture section 5)
- The person belongs to the authenticated user and does not appear in User B's list. (Existing decision: ADR-002)

### J3. Add interests and preferences
*Capability 3.* User A adds the interest "hiking" and the preference "prefers tea" to Sample Friend A, on creating or editing the person.
- The entries persist and are returned after reload. (Existing decision: architecture section 5)
- Empty, oversized and duplicate entries are rejected or normalised as documented in the API schema. (Existing decision: architecture section 5)
- Entries are user-entered facts. AI-suggested or discovered candidates do not exist in the MVP. (Existing decision: ADR-003; section 5.3)

### J4. Add and manage notes
*Capability 3.* User A adds a note to Sample Friend A.
- The saved note appears on Sample Friend A's profile with its creation timestamp. (Existing decision: architecture sections 5 and 6)
- User B cannot read or add notes on Sample Friend A. (Existing decision: ADR-002, test 6)
- Note contents do not appear in logs. (Existing decision: architecture section 9; logging-and-errors.md)
- User A can edit and delete their own notes. (Proposed: P1)

### J5. View a person
*Capabilities 2 and 3.* User A opens Sample Friend A.
- The profile shows the name, the birthday if set, interests, preferences and notes. For Sample Friend A: 12 March, "hiking", "prefers tea" and the synthetic note. (Proposed)
- If User B requests Sample Friend A's identifier, the record is not returned and its existence is reported only as the documented policy allows. (Existing decision: ADR-002, test 3; #12)
- Loading, error, empty and success states are tested. (Existing decision: architecture section 10)

### J6. Edit a person
*Capability 2.* User A changes Sample Friend A's name, birthday, interests or preferences.
- The changes persist and the updated timestamp changes. (Proposed)
- User B cannot update User A's person. (Existing decision: ADR-002, test 4)

### J7. Search people by name
*Capability 4.* User A types part of a name.
- Searching "Sample" as User A returns Sample Friend A and Sample Friend B, and never Sample Friend C. (Owner-decided scope)
- Searching for text that appears only in a note, interest or preference, such as "hiking", returns no people. (Owner-decided: names only)
- Special characters and injection-style input are handled safely and return a normal response. (Proposed; consistent with architecture section 8)
- No-results and error states are shown. (Proposed)
- Search terms are not written to logs. (Existing decision: logging-and-errors.md excludes query strings and request bodies)
- Matching rules, such as case and partial words, are documented with the implementation. (Unresolved: to be settled in #15)

### J8. See upcoming birthdays
*Capability 5.* User A opens the upcoming-birthdays view.
- With today fixed at 1 March, Sample Friend A (12 March) is listed before Sample Friend B (20 April). (Proposed)
- Only User A's people are listed, and a person without a birthday is not. (Proposed)
- A month view lists the people with birthdays in a chosen month. (Proposed: P2)
- Day boundaries, year boundaries and 29 February follow the rules decided in #18. (Unresolved)

### J9. Configure a reminder
*Capability 6.* User A turns on a birthday reminder.
- No reminder is produced until the user opts in. (Existing decision: section 3, CLAUDE.md; #22)
- The user can enable a birthday reminder with a lead time, for example 7 days before, and see it listed. (Proposed; the available options are Unresolved: U5)
- The user can disable and revoke the reminder, after which none is produced. (Existing decision: section 3; #22)
- Reminder content is not logged. (Existing decision: logging-and-errors.md; #22)
- How a reminder reaches the user. (Unresolved: #21)
- Reminders apply to birthdays only. Custom events are deferred. (Owner-decided)

### J10. Export my archive
*Capability 7.* A signed-in user exports the whole archive, or one person.
- An unauthenticated export request is rejected. (Owner-decided)
- The output is JSON. (Owner-decided)
- A full export contains every person the user owns, with birthdays, interests, preferences, notes, reminder settings and relevant timestamps. (Owner-decided)
- An individual-person export contains only that person's data. (Owner-decided)
- User A's full export contains Sample Friend A and Sample Friend B with their data and nothing about Sample Friend C. A request by User A to export Sample Friend C is refused the same way as in J5. (Proposed example)
- No internal database identifier and no authentication identifier appears in the output. A test checks keys and values. (Owner-decided)
- Re-import is not supported. (Owner-decided)
- The export is served only to the authenticated owner, and its contents are not logged. (Existing decision: ADR-003, ADR-002)
- Delivery, re-authentication, event logging, field list and reminder-settings scope. (Unresolved: U2, U5)

### J11. Delete a person
*Capability 8.* User A deletes Sample Friend A.
- The user must explicitly confirm before deletion. (Owner-decided)
- The person and their dependent records (notes, interests, preferences, reminder settings) are removed immediately and permanently from the primary database, with no undo. (Owner-decided)
- Afterwards User A's list, search and export no longer include Sample Friend A or the note. Sample Friend B and Sample Friend C are unaffected. (Proposed example)
- User B cannot delete User A's person. (Existing decision: ADR-002, test 4)
- A summary states what was removed, for example "1 person, 1 note", and any data retained with an explanation, for example that backups may keep copies for a period that is not yet documented. (Owner-decided; backup period Unresolved: U3)

### J12. Delete my account and data
*Capability 8.* User A deletes their account.
- The user must explicitly confirm before deletion. (Owner-decided)
- All of the user's records in the primary database and the user's Supabase Auth identity are removed immediately and permanently, with no undo. (Owner-decided)
- Afterwards User A cannot sign in with the old credentials, and User B's data is unaffected. (Proposed)
- A summary states what was removed and any data retained, with an explanation. (Owner-decided)
- The privileged credential needed to delete the identity is used on the server only and never reaches the frontend. (Existing decision: ADR-002)
- The product does not claim that every copy is erased at once. (Proposed; backup, log and provider retention Unresolved: U3)
- If only part of the deletion completes, the summary reports what actually happened. (Proposed; order and failure handling Unresolved: U3)

### E1. Automated checks in CI
*Capability 9.*
- Frontend checks and Backend checks run on every pull request to `main`, and a pull request cannot be merged unless both pass. (Existing decision: architecture section 11; CONTRIBUTING.md)
- The application can be set up and tested locally from documented instructions. (Existing decision: section 8; docs/development.md)
- Tests for features that touch private data include cross-user denial, using synthetic users and data. (Existing decision: ADR-002, ADR-003)

### E2. Controlled deployment
*Capability 10.*
- Production deploys from `main` only after the required checks pass. (Existing decision: architecture section 11)
- Deployment and rollback procedures are documented. (Existing decision: architecture section 11)
- Pull-request preview deployments exist where the host supports them. (Existing decision: section 6; architecture section 11)
- Backup and restoration procedures are defined before production data is introduced. (Existing decision: architecture section 8)
- The hosting provider and deployment topology. (Unresolved: #33)

## 10. Reconciliation

### 10.1 Journeys, requirements and decisions

| Journey | Requirement | Acceptance example | Evidence / decision | Status |
|---|---|---|---|---|
| J1 Register, sign in, sign out | Authenticated access to private data; email-and-password; open registration | A request without a valid token is rejected; User A signs in and sees only their archive | ADR-002; owner decision 2026-10-31 | Existing decision; Owner-decided; details Unresolved (U4) |
| J2 Create a person | Display name required; birthday optional | "Sample Friend A" is saved and listed for User A only | Architecture section 5; ADR-002 | Proposed |
| J3 Add interests and preferences | Optional lists of short entries on the person | "hiking" and "prefers tea" persist after reload | Architecture section 5; #12 | Existing decision |
| J4 Add and manage notes | Notes belong to a person and an owner | A note on Sample Friend A is visible to User A and not to User B | Architecture sections 5 and 6; ADR-002 test 6 | Existing decision; note edit and delete Proposed |
| J5 View a person | Shows only the owner's data | User B cannot retrieve Sample Friend A | ADR-002 test 3 | Existing decision; layout Proposed |
| J6 Edit a person | Owner can change profile fields | Changing the name persists; User B cannot update | ADR-002 test 4 | Proposed |
| J7 Search | Search by person name only | "Sample" returns Sample Friend A and B; "hiking" returns none | Owner decision 2026-10-31; #15 to be aligned | Owner-decided (Included) |
| J8 See upcoming birthdays | Ordered by next occurrence; own people only | With today at 1 March, 12 March is listed before 20 April | Draft v0.1 section 5; #18, #19 | Proposed; month view P2; time-zone rules Unresolved (#18) |
| J9 Configure a reminder | Birthday reminders off until enabled; user can revoke | Enabling "7 days before" is saved and listed | Section 3; CLAUDE.md; #22 | Existing decision (opt-in); delivery Unresolved (#21); options Unresolved (U5) |
| J10 Export my archive | JSON; full archive or one person; user-owned data; no internal or authentication identifiers; authenticated | User A's export has Sample Friend A and B and nothing of C, with no identifier keys | Owner decision 2026-10-31; ADR-003 | Owner-decided (Included); details Unresolved (U2, U5) |
| J11 Delete a person | Confirmation; immediate hard delete of the person and dependent records; summary of removed and retained data | Sample Friend A and the note are gone from list, search and export | Owner decision 2026-10-31; ADR-003 | Owner-decided (Included); backup period Unresolved (U3) |
| J12 Delete my account and data | Confirmation; removes all primary-database records and the Supabase Auth identity; summary | User A cannot sign in again; User B is unaffected | Owner decision 2026-10-31; ADR-002, ADR-003 | Owner-decided (Included); retention and failure handling Unresolved (U3) |
| Custom dates and recurring events | Not built in the MVP | No event model, endpoint or screen | Owner decision 2026-10-31 | Out of scope (deferred) |
| AI extraction, AI suggestions, public profile discovery | Not built in the MVP | No such endpoint, screen or call to an external AI service | ADR-003; owner decision; #23, #29 | Out of scope |
| Hosting and reminder delivery | Decisions stay visible | Section 7.3 lists #33 and #21 as open | Issue #1; #33, #21 | Unresolved |

### 10.2 Export and deletion against ADR-003

ADR-003 requires planning "user-accessible export and deletion" and states that "deletion behavior must account for related records, derived information, and documented retention limitations".

| ADR-003 point | Treatment in the MVP | Status |
|---|---|---|
| Plan for user-accessible export and deletion | J10 to J12 | Owner-decided |
| Related records | Deleting a person removes their notes, interests, preferences, birthday and reminder settings. Deleting the account also removes the Supabase Auth identity. | Owner-decided |
| Derived information | None exists in the MVP, because AI-derived and discovered data are out of scope. ADR-003 requires a review before AI processing or discovery is enabled, and export and deletion scope must be revisited then. | Existing decision |
| Documented retention limitations | Backups: pending #33. Logs: no personal record content or user identifiers by design; retention of shipped logs pending #33. Supabase Auth: provider retention not verified. Legal or regulatory obligations: not assessed. | Unresolved (U3) |
| Private by default; data minimization | Export is served only to the authenticated owner and excludes internal and authentication identifiers. | Owner-decided; Existing decision |

## 11. Revision history and review log

### Revision history

| Version | Date | Summary |
|---|---|---|
| Draft v0.1 | 2026-10-27 | Initial draft. |
| Draft v0.2 | 2026-10-31 | Revised in place for issue #1. Added document conventions, release tags, scope classification, the out-of-scope list, accepted and open decisions, journeys with acceptance criteria, and the reconciliation tables. Recorded the owner's decisions of 2026-10-31. Reworded section 2 for open registration, replaced "search and organize" with search by name, removed custom important dates from the MVP, and replaced the old section 7 list with section 7. |

### Review log for issue #1

The external review comment is [here](https://github.com/gaiuso33/relationship-archive/issues/1#issuecomment-6514895776). The owner's reply is [here](https://github.com/gaiuso33/relationship-archive/issues/1#issuecomment-6532773073).

| Input | Disposition |
|---|---|
| Revise Draft v0.1 instead of creating another PRD | Adopted by the owner. |
| Add a reconciliation table | Adopted by the owner. Section 10. |
| Add the remaining MVP journeys | Adopted by the owner. Section 9. |
| Record search, export and deletion as included or deferred | Adopted by the owner. Section 5.2. |
| Move AI extraction and public profile discovery to non-goals | Adopted by the owner. Section 5.3. |
| Link the accepted ADRs and keep hosting and reminder decisions visible | Adopted by the owner. Sections 7.1 and 7.3. |
| Example questions and example acceptance statements in the reviewer's table | Not adopted as written. The reviewer called them examples, not requirements. Requirements here follow the owner's decisions of 2026-10-31. |
| Optional external authoring workflow | Not adopted. The owner keeps the Markdown workflow in this repository. |
