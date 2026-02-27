# Gate D: Roles and Permissions (v1)

This document defines application-level authorization behavior for TODO item 2.

## Roles
- `admin`
- `training_chief`
- `instructor`
- `participant`

Users can hold multiple roles at the same time. Effective access is the union of all assigned role permissions.

## Core Permissions
- `view_scenarios`
- `submit_answers`
- `reveal_instructor_answers`
- `create_scenarios`
- `create_sessions`
- `approve_scenarios`
- `view_session_submissions`
- `select_review_answer`
- `share_review_answer`
- `view_reports`
- `export_reports`
- `manage_users`

## Role Matrix (v1)
- `participant`: `view_scenarios`, `submit_answers`, `reveal_instructor_answers`
- `instructor`: participant permissions + `create_scenarios`, `create_sessions`, `view_session_submissions`, `select_review_answer`, `share_review_answer`
- `training_chief`: instructor permissions + `approve_scenarios`, `view_reports`, `export_reports`
- `admin`: all permissions

## Delegated Approvals
A trusted non-chief approver can be supported by granting a direct permission override (`approve_scenarios`) to that user while they retain `instructor` as their role.

## Admin Bypass Clarification
"Admin bypass all checks" means whether admin can ignore both:
1. Permission checks (RBAC)
2. Workflow/state rules (business rules)

v1 policy:
- Admin bypasses permission checks.
- Admin does **not** automatically bypass workflow/state integrity rules. If we later want emergency override actions, those should be explicit operations with audit logs.
