# Gate C: Physical Schema (v1)

This document captures the concrete table-level design implemented in `models.py`.
It defines fields, keys, constraints, and indexes for migration planning.

## Tables

### users
- `id` (PK)
- `email` (required, unique, indexed)
- `full_name` (nullable)
- `is_active` (required, default true)
- `created_at` (required)
- `updated_at` (required)

### roles
- `id` (PK)
- `name` (required, unique)
- `description` (nullable)
- `created_at` (required)
- `updated_at` (required)

### user_roles
- `user_id` (PK part, FK -> users.id, cascade delete)
- `role_id` (PK part, FK -> roles.id, cascade delete)
- `assigned_at` (required)

### scenarios
- `id` (PK)
- `title` (required, indexed)
- `dispatch_text` (required)
- `base_image_path` (required)
- `overlay_image_path` (nullable)
- `created_by_user_id` (nullable FK -> users.id, set null on delete)
- `is_active` (required, default true)
- `created_at` (required)
- `updated_at` (required)

### questions
- `id` (PK)
- `scenario_id` (required FK -> scenarios.id, cascade delete)
- `question_key` (required)
- `prompt` (required)
- `sort_order` (required)
- `is_active` (required, default true)
- `created_at` (required)
- `updated_at` (required)
- Unique constraints:
  - `(scenario_id, question_key)`
  - `(scenario_id, sort_order)`
- Indexes:
  - `(scenario_id, sort_order)`

### training_sessions
- `id` (PK)
- `scenario_id` (required FK -> scenarios.id, restrict delete)
- `created_by_user_id` (nullable FK -> users.id, set null on delete)
- `join_code` (required, unique)
- `title` (nullable)
- `status` (required, indexed, default `active`)
- `starts_at` (nullable)
- `ends_at` (nullable)
- `archived_at` (nullable)
- `created_at` (required)
- `updated_at` (required)
- Indexes:
  - `(scenario_id, created_at)`

### participants
- `id` (PK)
- `training_session_id` (required FK -> training_sessions.id, cascade delete)
- `user_id` (nullable FK -> users.id, set null on delete)
- `display_name` (nullable)
- `shift_label` (nullable)
- `is_anonymous` (required, default false)
- `joined_at` (required)
- Indexes:
  - `(training_session_id, joined_at)`

### submissions
- `id` (PK)
- `participant_id` (required FK -> participants.id, cascade delete)
- `training_session_id` (required FK -> training_sessions.id, cascade delete)
- `scenario_id` (required FK -> scenarios.id, restrict delete)
- `attempt_number` (required, default 1, check > 0)
- `status` (required, default `submitted`)
- `notes` (nullable)
- `submitted_at` (required)
- Unique constraints:
  - `(participant_id, attempt_number)`
- Indexes:
  - `(training_session_id, submitted_at)`
  - `(scenario_id, submitted_at)`

### submission_answers
- `id` (PK)
- `submission_id` (required FK -> submissions.id, cascade delete)
- `question_id` (required FK -> questions.id, restrict delete)
- `answer_text` (required, default empty string)
- `created_at` (required)
- `updated_at` (required)
- Unique constraints:
  - `(submission_id, question_id)`
- Indexes:
  - `(question_id)`

## Relationship Summary
- `users <-> roles` is many-to-many through `user_roles`.
- `scenarios -> questions` is one-to-many.
- `scenarios -> training_sessions` is one-to-many.
- `training_sessions -> participants` is one-to-many.
- `participants -> submissions` is one-to-many.
- `submissions -> submission_answers` is one-to-many.

## Notes
- Multiple submissions per participant are enabled via `(participant_id, attempt_number)`.
- Question editing/versioning policy is application-level in v1; schema versioning is deferred.
