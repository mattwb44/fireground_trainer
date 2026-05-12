# Local Deployment Notes

This project is still a Flask app started with `python app.py`, but the runtime now has a cleaner separation between local development defaults and safer pilot settings.

## Recommended Local Pilot Configuration

Start from `.env.example` and change at least these values:

- `SECRET_KEY`: use a unique secret
- `RUN_DEBUG=0`
- `ENABLE_MAGIC_LINK_DEBUG=0`
- `ENABLE_ACCOUNT_ACTIVATION_DEBUG=0`
- `SESSION_COOKIE_SECURE=1` if you are serving over HTTPS

If you want to stop automatic demo content creation:

- `ENABLE_DEMO_SEED_USERS=0`
- `ENABLE_DEMO_SEED_SCENARIOS=0`

## Database Notes

- The default local database path is `instance/fireground_trainer.db`.
- You can override it with `DATABASE_URL`.
- Back up the SQLite file before pulling schema-changing updates you care about.

## Join Links And QR Codes

- If participants are joining from phones on the same LAN, set `PUBLIC_BASE_URL`.
- Example: `PUBLIC_BASE_URL=http://192.168.1.23:5000`
- Without that override, the app tries to detect a LAN address when the request host is loopback, but an explicit value is safer for pilot use.

## Static Scenario Assets

Scenario image paths must resolve to files inside `static/`.

Good examples:

- `images/house1.jpg`
- `images/house2.jpg`
- `images/smoke_detector.jpeg`

The app now blocks:

- absolute paths
- `../` traversal
- missing files

That helps keep scenario records portable between machines.

## Startup Warnings

On boot, the app now logs warnings when it detects obviously development-oriented settings such as:

- default `SECRET_KEY`
- `RUN_DEBUG=1`
- magic-link debug display enabled
- activation-link debug display enabled
- demo seeding enabled

Those warnings are meant to make pilot misconfiguration more visible before people start using the app.

## Admin Readiness View

The admin user-management screen now includes:

- system state summary counts
- recent admin activity
- a pre-pilot checklist driven by current config/data

That screen is meant to be the first quick sanity check before a local pilot session starts.

## Repeatable Release Checks

Before a demo or pilot, run:

```bash
./scripts/run_release_checks.sh
```

That gives you one repeatable check path before you do the manual session smoke pass described in [release_readiness.md](release_readiness.md).
