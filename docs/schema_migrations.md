# Schema Migration Strategy

This project started with direct `db.create_all()` bootstrapping plus compatibility patches for older local databases. That was fine early on, but it does not scale well once we start adding more tables, indexes, and data backfills over time.

## Current Approach

The app now applies schema work in this order during startup:

1. `db.create_all()`
2. `ensure_legacy_schema_compatibility()`
3. `apply_schema_migrations()`
4. seed/bootstrap steps

Why this split exists:

- `db.create_all()` remains the quickest way to stand up a fresh local SQLite database.
- `ensure_legacy_schema_compatibility()` protects older development databases created before newer auth/workflow columns existed.
- `apply_schema_migrations()` is the new versioned ledger for ongoing schema changes that should be tracked intentionally.

## Migration Ledger

Managed migrations are recorded in the `schema_migrations` table.

Each migration has:

- a stable string name such as `2026_05_01_account_activation_tokens_schema`
- an idempotent function in `app.py`
- a row in `schema_migrations` once applied

The current managed migrations cover newer schema objects that arrived after the original MVP schema:

- `scenario_likes`
- `session_question_reveals`
- `account_activation_tokens`

## Local Upgrade Steps

When you pull schema changes locally:

1. Back up `instance/fireground_trainer.db` if you care about existing data.
2. Start the app with `python app.py`.
3. Startup will apply any missing managed migrations automatically.
4. Confirm the app still loads and run the local test suite.

Recommended checks:

```bash
python3 -m unittest tests.test_submission_flow
python3 -m py_compile app.py models.py authz.py
```

## How To Add The Next Migration

When a future TODO needs schema changes:

1. Add or update the SQLAlchemy model definition in `models.py`.
2. Add a new migration function in `app.py`.
3. Make the migration idempotent:
   - inspect before creating a table/index
   - avoid assuming a perfectly fresh database
4. Append the migration to `SCHEMA_MIGRATIONS` with a new dated name.
5. Keep `ensure_legacy_schema_compatibility()` focused on older pre-ledger column patching only.
6. Add a regression test when practical.

## Why Not A Bigger Refactor Yet

`Flask-Migrate` is already in the dependency list, but the current app still bootstraps directly at import time. A full app-factory plus Alembic workflow is still a good future move, but it is a larger refactor than we need right now.

This ledger approach gives us:

- explicit migration history
- safer repeatable upgrades for local databases
- a clear place for future schema work
- less reliance on one-off compatibility patches alone
