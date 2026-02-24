# Gate A: Schema Scope Freeze (v1)

## How To Use This Document
- Purpose: freeze the exact database scope before designing tables.
- Rule: do not start Gate B (ERD) until all `Open Decisions` are resolved.
- Editing pattern: replace all placeholder text in brackets (example: `[fill in]`).

---

## Goal
Persist core training data so scenarios, sessions, participants, and submitted answers survive server restarts and can be reviewed later.

---

## In Scope (v1 Schema)
- Persist scenarios and questions.
- Persist training sessions.
- Persist participants (named or anonymous).
- Persist submissions and per-question answers.
- Persist basic users and roles (table-backed).

---

## Out of Scope (Deferred)
- Full authentication implementation details.
- Reporting engine and analytics materialization.
- CSV/PDF export implementation.
- Instructor approval workflow UI.
- Advanced audit dashboards.

---

## Core User Flows
- Instructor creates a scenario and questions; system stores scenario and question rows.
- Instructor creates a training session from a scenario; system stores session row.
- Participant joins a session (name or anonymous); system stores participant row.
- Participant submits answers; system stores submission and submission_answer rows.
- Instructor reviews submissions for a session; system reads submissions joined to answers.

---

## Entity Candidate List
- `users`: people with platform access.
- `roles`: access role definitions.
- `user_roles`: mapping between users and roles.
- `scenarios`: scenario metadata.
- `questions`: question prompts linked to scenarios.
- `training_sessions`: runnable instances of scenario delivery.
- `participants`: people in a specific session.
- `submissions`: one response package from a participant.
- `submission_answers`: per-question response rows.

---

## Open Decisions (Must Resolve in Gate A)
1. Decision: Can a participant submit more than once per session/scenario?
   - Options: `single submission` or `multiple submissions`
   - Chosen: `multiple submissions`
   - Rationale: Preserve attempt history and support repeat reps in training.

2. Decision: Are participants allowed to be anonymous?
   - Options: `yes` or `no`
   - Chosen: `yes`
   - Rationale: Matches planned join flow and lowers participation friction.

3. Decision: Can questions be edited after submissions exist?
   - Options: `locked after first submission` or `editable with versioning`
   - Chosen: `locked after first submission`
   - Rationale: Protect answer integrity in v1 without adding version complexity.

4. Decision: Do we need scenario versioning now?
   - Options: `yes now` or `defer`
   - Chosen: `defer`
   - Rationale: Keep initial schema lean and add versioning in a later migration.

5. Decision: How should roles be represented in v1?
   - Options: `table-backed roles` or `enum-only`
   - Chosen: `table-backed roles`
   - Rationale: More extensible for future permissions and admin workflows.

---

## Acceptance Checklist (Gate A Exit Criteria)
- [x] Goal is written and aligned to v1 outcomes.
- [x] In Scope list is explicit and complete.
- [x] Out of Scope list is explicit.
- [x] Core User Flows cover submission lifecycle.
- [x] Entity Candidate List is stable.
- [x] All Open Decisions have chosen options and rationale.
- [x] Team agrees Gate A is frozen.
