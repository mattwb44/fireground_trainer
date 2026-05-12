# Fireground Trainer

Fireground Trainer is a web-based training application designed to help firefighters practice fireground size-up, tactics, and decision-making using realistic residential scenarios.

The goal of this project is to create an interactive, visual way to think through fireground operations — not just answer questions, but **place resources, visualize conditions, and justify decisions**.

---

## Current Features (MVP)

- Scenario-based training with dispatch information
- RBAC foundation with roles: participant, instructor, training chief, admin
- Staff authentication with password login
- One-time magic-link login flow (dev-mode link display)
- Participant self-registration with in-app activation link (dev-mode verification)
- CSRF protection on state-changing forms
- Managed schema migration ledger for newer tables/indexes
- Admin user-management screen (create users, assign roles, reset passwords)
- Scenario Library tabs: Official, Practice, Mine, Submitted
- Scenario creation UI with per-question type selection for MVP question modes
- Auto-feedback on submit for checklist/short-answer question types (creator answer key-point matching)
- Session creation flow with unique join code and QR share page
- Residential structure images used as a training board
- Draggable, image-based tokens for:
  - Fire
  - Smoke
  - Wind direction
  - Ladders
  - Attack teams
  - RIT teams
- Tokens can be:
  - Dragged and repositioned
  - Rotated using a rotation handle
  - Deleted using an explicit delete button
- Tokens only show controls when selected (to prevent accidental actions)
- Scenario questions focused on:
  - Scene size-up
  - Hose line selection and PDP
  - GPM calculations
  - Offensive vs defensive transitions
  - Fireground tactics
- Progressive Web App (PWA) setup for future mobile/tablet use

---

## Tech Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, Vanilla JavaScript
- **Architecture:** Server-rendered templates with client-side interaction
- **Platform:** Desktop and mobile browser compatible
- **Version Control:** Git / GitHub

---

## Local Auth Bootstrap

If `ENABLE_DEMO_SEED_USERS=1`, first app start seeds demo staff users with the password from `DEMO_BOOTSTRAP_PASSWORD` (default: `EasyPass123`):

- `instructor@demo.local`
- `chief@demo.local`
- `admin@demo.local`

If `ENABLE_DEMO_SEED_SCENARIOS=1`, first app start also seeds the default Fireground scenarios.

---

## Local Setup

1. Create and activate a virtual environment.
2. Install dependencies with `pip install -r requirements.txt`.
3. Copy `.env.example` into your preferred local env setup.
4. Start the app with `python app.py`.

The default local SQLite database now lives under `instance/fireground_trainer.db` unless you override `DATABASE_URL`.

---

## Environment Variables

Core:

- `SECRET_KEY`: Flask session secret. Change this before pilot or production use.
- `DATABASE_URL`: SQLAlchemy connection string. Defaults to a local SQLite file in `instance/`.
- `PUBLIC_BASE_URL`: Optional override for join links and QR codes. Useful when phones join from the same LAN.

Demo/bootstrap behavior:

- `DEMO_BOOTSTRAP_PASSWORD`: Password used for seeded demo staff accounts.
- `ENABLE_DEMO_SEED_USERS`: `1` by default. Set to `0` to stop auto-seeding demo users.
- `ENABLE_DEMO_SEED_SCENARIOS`: `1` by default. Set to `0` to stop auto-seeding demo scenarios.

Local-only debug helpers:

- `ENABLE_MAGIC_LINK_DEBUG`: `1` by default. Shows magic links in-app for local development.
- `MAGIC_LINK_TTL_MINUTES`: Magic-link lifetime. Defaults to `15`.
- `ENABLE_ACCOUNT_ACTIVATION_DEBUG`: `1` by default. Shows activation links in-app for local development.
- `ACCOUNT_ACTIVATION_TTL_HOURS`: Activation-link lifetime. Defaults to `24`.

Session/runtime:

- `SESSION_COOKIE_SECURE`: `0` by default for local HTTP. Set to `1` behind HTTPS.
- `RUN_HOST`: Dev server bind host. Defaults to `0.0.0.0`.
- `RUN_PORT`: Dev server port. Defaults to `5000`.
- `RUN_DEBUG`: Dev server debug flag. Defaults to `1`.

---

## Static Asset Assumptions

Scenario image paths must point to real files inside `static/`.

Examples:

- `images/house1.jpg`
- `images/smoke_detector.jpeg`

The app now rejects scenario asset paths that:

- point outside `static/`
- use absolute paths
- reference missing files

That keeps scenario records portable across machines instead of depending on one developer's local filesystem layout.

---

## Local Schema Upgrades

Schema setup now has two layers:

- `db.create_all()` still bootstraps a brand-new local database.
- `apply_schema_migrations()` records versioned follow-up schema changes in the `schema_migrations` table.
- `ensure_legacy_schema_compatibility()` is still kept for older local databases created before the migration ledger existed.

Practical local upgrade flow after pulling changes:

1. Back up your local SQLite file if you care about the data.
2. Start the app normally with `python app.py`.
3. Let startup apply any pending managed migrations automatically.
4. If you want the longer explanation or future authoring guidance, see [docs/schema_migrations.md](docs/schema_migrations.md).

---

## Local Pilot Notes

For a safer pilot-style local run, use these settings at minimum:

- change `SECRET_KEY`
- set `RUN_DEBUG=0`
- set `ENABLE_MAGIC_LINK_DEBUG=0`
- set `ENABLE_ACCOUNT_ACTIVATION_DEBUG=0`
- set `ENABLE_DEMO_SEED_USERS=0` once you no longer want demo staff accounts added
- set `ENABLE_DEMO_SEED_SCENARIOS=0` once your real content is established
- set `PUBLIC_BASE_URL` if participant phones are joining from the LAN

Additional deployment notes live in [docs/deployment_local.md](docs/deployment_local.md).

For the release/pilot verification flow, see [docs/release_readiness.md](docs/release_readiness.md).

---

## Project Status

This project is currently in **early MVP development**.

Right now, the focus is on:
- Visual fireground interaction
- Core training workflow
- Usable UI for firefighters

Future iterations will expand realism and persistence.

---

## Planned Features

- Multiple fire/smoke placement zones per structure
- Wind direction effects and flow path visualization
- Alpha/Bravo/Charlie/Delta side labeling
- Scenario randomization
- Saving and reviewing token layouts
- Paramedic/EMS decision-making scenarios
- Multi-story and commercial structures
- Street View–style local structure images
- Instructor review / discussion mode

---

## Why This Project Exists

Fireground training often lives on whiteboards, paper diagrams, or mental reps.  
This project aims to bridge the gap between **tactical knowledge and visual decision-making**, using tools that feel natural and interactive.

The long-term vision is a lightweight, realistic training aid that complements hands-on drills and tabletop discussions.

---

## Disclaimer

This application is for **training and discussion purposes only**.  
It is not intended to replace department SOPs, formal training, or incident command judgment.

---

## Author

Built by a firefighter/paramedic exploring how software can support better training, decision-making, and preparation on the fireground.
