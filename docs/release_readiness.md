# Release Readiness

This project is still a local/pilot-style Flask app, but `TODO #40` adds a repeatable release-readiness routine so we can check the current build before sharing it more widely.

## Automated Checks

Run the project smoke checks from the repo root:

```bash
./scripts/run_release_checks.sh
```

That script currently runs:

- `python3 -m unittest tests.test_submission_flow`
- `python3 -m py_compile app.py models.py authz.py`

If either command fails, stop and fix that issue before a pilot/demo session.

## Manual Fireground Smoke Pass

After the automated checks pass, verify the main user journey manually:

1. Sign in as an instructor or chief.
2. Open the Fireground category page and the scenario library.
3. Create or open an active session and confirm the host board loads.
4. Join that session as a participant.
5. Submit one attempt.
6. Review, reveal, and approve/exclude from the host side.
7. Open the report view and confirm only approved submissions appear.

## Security Review Reminders

Before pilot use, verify:

- `SECRET_KEY` is not the development default.
- `RUN_DEBUG=0`
- `ENABLE_MAGIC_LINK_DEBUG=0`
- `ENABLE_ACCOUNT_ACTIVATION_DEBUG=0`
- `SESSION_COOKIE_SECURE=1` when the app is behind HTTPS.
- Demo seed flags are off if you no longer want demo users/scenarios created.

## UX Readiness Reminders

Before sharing the build, check:

- The current firefighter red theme is consistent across the pages you expect people to use.
- Error states are understandable for common failures like expired forms or invalid status changes.
- The host board is readable on the screen size you plan to use for facilitation.
- Participant join/submission guidance is still clear on a phone-sized screen.

## MVP-To-Pilot Ship Checklist

Use this as the final quick gate:

- Automated checks passed.
- Manual Fireground smoke pass completed.
- Admin readiness screen reviewed.
- Required scenario images exist under `static/`.
- Join links/QR codes were tested using the real `PUBLIC_BASE_URL`.
- At least one report export was checked with fresh session data.
- Current environment values were reviewed for pilot-safe settings.
