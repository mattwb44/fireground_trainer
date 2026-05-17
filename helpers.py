import csv
import hashlib
import hmac
import io
import ipaddress
import os
import re
import secrets
import socket
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

from flask import abort, current_app, g, redirect, render_template, request, session, url_for
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from authz import (
    PERM_APPROVE_SCENARIOS,
    PERM_CREATE_SCENARIOS,
    PERM_CREATE_SESSIONS,
    PERM_EXPORT_REPORTS,
    PERM_MANAGE_USERS,
    PERM_REVEAL_INSTRUCTOR_ANSWERS,
    PERM_SELECT_REVIEW_ANSWER,
    PERM_SHARE_REVIEW_ANSWER,
    PERM_SUBMIT_ANSWERS,
    PERM_VIEW_REPORTS,
    PERM_VIEW_SCENARIOS,
    PERM_VIEW_SESSION_SUBMISSIONS,
    ROLE_ADMIN,
    ROLE_INSTRUCTOR,
    ROLE_PARTICIPANT,
    ROLE_TRAINING_CHIEF,
    CurrentUser,
    normalize_roles,
)
from extensions import db
from models import (
    MagicLoginToken,
    Participant,
    Question,
    Role,
    Scenario,
    ScenarioLike,
    SessionQuestionReveal,
    Submission,
    SubmissionAnswer,
    SubmissionAuditLog,
    TrainingSession,
    User,
    UserRole,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PERMISSION_KEYS = {
    "view_scenarios": PERM_VIEW_SCENARIOS,
    "submit_answers": PERM_SUBMIT_ANSWERS,
    "reveal_instructor_answers": PERM_REVEAL_INSTRUCTOR_ANSWERS,
    "create_scenarios": PERM_CREATE_SCENARIOS,
    "create_sessions": PERM_CREATE_SESSIONS,
    "approve_scenarios": PERM_APPROVE_SCENARIOS,
    "select_review_answer": PERM_SELECT_REVIEW_ANSWER,
    "share_review_answer": PERM_SHARE_REVIEW_ANSWER,
    "view_reports": PERM_VIEW_REPORTS,
    "export_reports": PERM_EXPORT_REPORTS,
    "manage_users": PERM_MANAGE_USERS,
}

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_TRAINING_CHIEF: "Training Chief",
    ROLE_INSTRUCTOR: "Instructor",
    ROLE_PARTICIPANT: "Participant",
}

STAFF_ROLES = frozenset({ROLE_INSTRUCTOR, ROLE_TRAINING_CHIEF, ROLE_ADMIN})
SCENARIO_STATUS_DRAFT = "draft"
SCENARIO_STATUS_SUBMITTED = "submitted"
SCENARIO_STATUS_APPROVED = "approved"
SCENARIO_STATUS_ARCHIVED = "archived"
SCENARIO_ACTIVE_STATUSES = frozenset(
    {SCENARIO_STATUS_DRAFT, SCENARIO_STATUS_SUBMITTED, SCENARIO_STATUS_APPROVED}
)
QUESTION_TYPE_AUTO_CHECKLIST = "auto_checklist"
QUESTION_TYPE_KEY_POINT_AUTO = "key_point_auto"
QUESTION_TYPE_DISCUSSION_ONLY = "discussion_only"
QUESTION_TYPE_LABELS = {
    QUESTION_TYPE_AUTO_CHECKLIST: "Auto-scored checklist",
    QUESTION_TYPE_KEY_POINT_AUTO: "Short Answer (Participant's Answers Matched and Scored to Creator's Answer)",
    QUESTION_TYPE_DISCUSSION_ONLY: "Discussion-only open-ended (non-graded)",
}
QUESTION_TYPE_CHOICES = frozenset(QUESTION_TYPE_LABELS.keys())
DEFAULT_QUESTION_TYPE = QUESTION_TYPE_DISCUSSION_ONLY
SUBMISSION_STATUS_SUBMITTED = "submitted"
SUBMISSION_STATUS_APPROVED = "approved"
SUBMISSION_STATUS_FLAGGED = "flagged"
SUBMISSION_STATUS_EXCLUDED = "excluded"
SUBMISSION_STATUS_LABELS = {
    SUBMISSION_STATUS_SUBMITTED: "Active",
    SUBMISSION_STATUS_APPROVED: "Active",
    SUBMISSION_STATUS_FLAGGED: "Active",
    SUBMISSION_STATUS_EXCLUDED: "Excluded",
}
SHIFT_OPTIONS = (
    "A Shift",
    "B Shift",
    "C Shift",
    "D Shift",
    "Swing",
    "Other",
)
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "with",
        "your",
        "you",
    }
)
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CSRF_SESSION_KEY = "_csrf_token"
PARTICIPANT_JOIN_MAP_KEY = "joined_participants"
ACTIVE_TRAINING_SESSION_ID_KEY = "active_training_session_id"
ACTIVE_PARTICIPANT_ID_KEY = "active_participant_id"
HOST_TRAINING_SESSION_ID_KEY = "host_training_session_id"
LOGIN_ATTEMPTS: dict[tuple[str, str], list[float]] = {}
LOGIN_WINDOW_SECONDS = 600
LOGIN_ATTEMPT_LIMIT = 5
POST_ONLY_PATHS = frozenset(
    {
        "/submit",
        "/scenario/submit-review",
        "/scenario/approve",
        "/scenario/archive",
        "/scenario/official",
        "/scenarios/new",
        "/scenarios/select",
        "/sessions/new",
        "/scenarios/vote",
        "/logout",
        "/login",
        "/magic-link/request",
    }
)

LIBRARY_TAB_OFFICIAL = "official"
LIBRARY_TAB_PRACTICE = "practice"
LIBRARY_TAB_MINE = "mine"
LIBRARY_TAB_SUBMITTED = "submitted"
LIBRARY_TAB_LABELS = {
    LIBRARY_TAB_OFFICIAL: "Official",
    LIBRARY_TAB_PRACTICE: "Practice",
    LIBRARY_TAB_MINE: "Mine",
    LIBRARY_TAB_SUBMITTED: "Submitted",
}

SITE_NAME = "Blitzfire Training"
CATEGORY_FIREGROUND = "fireground"
CATEGORY_MVA = "mva"
CATEGORY_EMS = "ems"
CATEGORY_LABELS = {
    CATEGORY_FIREGROUND: "Fireground Training",
    CATEGORY_MVA: "Motor Vehicle Accidents",
    CATEGORY_EMS: "Emergency Medical Services",
}
CATEGORY_FILTER_ALL = "all"
CATEGORY_FILTER_OFFICIAL = "official"
CATEGORY_FILTER_INSTRUCTOR_MADE = "instructor_made"
CATEGORY_FILTER_USER_MADE = "user_made"
CATEGORY_FILTER_LABELS = {
    CATEGORY_FILTER_ALL: "All Scenarios",
    CATEGORY_FILTER_OFFICIAL: "Official",
    CATEGORY_FILTER_INSTRUCTOR_MADE: "Instructor Made",
    CATEGORY_FILTER_USER_MADE: "User Made",
}

SCENARIO_SEED_DATA = [
    {
        "title": "Residential Fire - Bravo Side Smoke Showing",
        "dispatch": (
            "0200 hours. Single-story residential. Neighbors report smoke showing. "
            "Wind 10 mph from the west. First-due engine staffed with 3."
        ),
        "status": SCENARIO_STATUS_APPROVED,
        "image": {
            "base": "images/house1.jpg",
            "overlay": None,
        },
        "questions": [
            {
                "question_key": "q1",
                "prompt": (
                    "After performing a 3-sided search by pulling up to and past the house, "
                    "you notice smoke/fire conditions on the Alpha/Bravo/Charlie/Delta side. "
                    "Give your scene size-up and plan of action."
                ),
                "instructor_answer": (
                    "Size-up construction, occupancy, conditions, and life hazard first; "
                    "declare strategy, line selection, and task assignments."
                ),
            },
            {
                "question_key": "q2",
                "prompt": (
                    "You pull an attack line of 200 ft of 1 3/4-inch with a low-pressure smooth bore nozzle. "
                    "What is your PDP to obtain correct pressure at the nozzle?"
                ),
                "instructor_answer": (
                    "Show nozzle pressure target and friction loss assumptions, then compute "
                    "and state your final PDP."
                ),
            },
            {
                "question_key": "q3",
                "prompt": (
                    "Given the area of the home, approximately how much GPM should be needed to extinguish the fire?"
                ),
                "instructor_answer": (
                    "Use your department fire flow method and state the estimate with assumptions."
                ),
            },
            {
                "question_key": "q4",
                "prompt": (
                    "The attack mode turns defensive. You pull a blitz line of 100 ft of 3-inch to protect the exposure "
                    "on the Delta side. What would your PDP be to supply both the blitz and attack line?"
                ),
                "instructor_answer": (
                    "Account for both lines, appliance/elevation loss if present, and provide total PDP."
                ),
            },
        ],
    },
    {
        "title": "Two-Story Residential - Possible Victims Trapped",
        "dispatch": (
            "1730 hours. Two-story residential. Caller reports smoke alarms and someone possibly still inside. "
            "Light smoke from the Alpha side. Engine staffed with 4."
        ),
        "status": SCENARIO_STATUS_APPROVED,
        "image": {
            "base": "images/house2.jpg",
            "overlay": None,
        },
        "questions": [
            {
                "question_key": "q1",
                "prompt": "Give your size-up (construction, occupancy, fire location cues, life hazard) and first 5 minutes plan.",
                "instructor_answer": "Prioritize life hazard, place first line to protect interior access, and define search/vent tasks.",
            },
            {
                "question_key": "q2",
                "prompt": "Where would you place the first ladder and why? Window base vs offset, and what you are setting up for.",
                "instructor_answer": "Choose placement based on egress, rescue potential, and expected interior movement.",
            },
            {
                "question_key": "q3",
                "prompt": "Describe how you would control the flow path while still making progress on search and attack.",
                "instructor_answer": "Coordinate door control and ventilation timing with line advancement and search benchmarks.",
            },
            {
                "question_key": "q4",
                "prompt": "What are your early Mayday warning signs on interior crews, and what triggers RIT deployment in your plan?",
                "instructor_answer": "Define objective triggers (lost/disoriented/trapped/air emergency) and immediate RIT actions.",
            },
        ],
    },
    {
        "title": "Attic Involvement - Wind-Influenced Fire",
        "dispatch": (
            "2315 hours. Single-story residential. Smoke pushing from eaves on the Charlie/Delta corner. "
            "Wind gusts 15-20 mph. First-due engine staffed with 3."
        ),
        "status": SCENARIO_STATUS_APPROVED,
        "image": {
            "base": "images/house3.jpg",
            "overlay": None,
        },
        "questions": [
            {
                "question_key": "q1",
                "prompt": "What indicators suggest attic involvement, and how does that change your initial line placement?",
                "instructor_answer": "Read extension indicators and position for quickest control of attic pathways.",
            },
            {
                "question_key": "q2",
                "prompt": "Walk through ventilation choice/timing (horizontal vs vertical) and how you prevent making things worse.",
                "instructor_answer": "Delay or sequence ventilation to support suppression and avoid wind-driven intensification.",
            },
            {
                "question_key": "q3",
                "prompt": "When do you call for additional resources (truck/second alarm) and what is your reasoning?",
                "instructor_answer": "Call early when extension risk, staffing limits, or rescue complexity exceeds first alarm capacity.",
            },
            {
                "question_key": "q4",
                "prompt": "If a civilian is removed with suspected smoke inhalation, what is your immediate EMS plan (airway, oxygen, CO/cyanide considerations)?",
                "instructor_answer": "Prioritize airway/oxygenation, monitor toxidrome clues, and treat per protocol rapidly.",
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Shared helper functions
# ---------------------------------------------------------------------------

def role_label(role_name: str) -> str:
    return ROLE_LABELS.get(role_name, role_name.replace("_", " ").title())


def ensure_seed_data() -> None:
    from flask import current_app

    # Roles are always seeded — the app cannot function without them.
    role_specs = [
        (ROLE_PARTICIPANT, "Can join training and submit answers."),
        (ROLE_INSTRUCTOR, "Can create sessions/scenarios and review answers."),
        (ROLE_TRAINING_CHIEF, "Can approve scenarios and access reports."),
        (ROLE_ADMIN, "Can manage all permissions and users."),
    ]

    role_by_name: dict[str, Role] = {}
    for role_name, description in role_specs:
        role = Role.query.filter_by(name=role_name).first()
        if role is None:
            role = Role(name=role_name, description=description)
            db.session.add(role)
            db.session.flush()
        role_by_name[role_name] = role

    # Demo staff accounts are optional — skip when the flag is disabled.
    if not current_app.config.get("ENABLE_DEMO_SEED_USERS"):
        db.session.commit()
        return

    default_password = current_app.config["DEMO_BOOTSTRAP_PASSWORD"]
    seeded_users = [
        ("instructor@demo.local", "Sam Instructor", [ROLE_INSTRUCTOR]),
        ("chief@demo.local", "Casey Training Chief", [ROLE_TRAINING_CHIEF]),
        ("admin@demo.local", "Jordan Admin", [ROLE_ADMIN]),
    ]
    for email, full_name, role_names in seeded_users:
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=generate_password_hash(default_password),
                is_active=True,
                is_email_verified=True,
            )
            db.session.add(user)
            db.session.flush()
        else:
            if not user.password_hash:
                user.password_hash = generate_password_hash(default_password)
            if not user.full_name:
                user.full_name = full_name
            if not user.is_email_verified:
                user.is_email_verified = True

        assigned_role_ids = {link.role_id for link in user.role_links}
        for role_name in role_names:
            role = role_by_name[role_name]
            if role.id not in assigned_role_ids:
                db.session.add(UserRole(user_id=user.id, role_id=role.id))

    db.session.commit()


def ensure_seed_scenarios() -> None:
    from flask import current_app

    # Skip entirely when demo scenario seeding is disabled.
    if not current_app.config.get("ENABLE_DEMO_SEED_SCENARIOS"):
        return

    existing_count = Scenario.query.count()
    if existing_count > 0:
        # Mark legacy seeded scenarios as official.
        Scenario.query.filter(
            Scenario.created_by_user_id.is_(None),
            Scenario.status == SCENARIO_STATUS_APPROVED,
        ).update({"is_official": True}, synchronize_session=False)
        db.session.commit()
        return

    for scenario_seed in SCENARIO_SEED_DATA:
        scenario = Scenario(
            title=scenario_seed["title"],
            dispatch_text=scenario_seed["dispatch"],
            base_image_path=scenario_seed["image"]["base"],
            overlay_image_path=scenario_seed["image"]["overlay"],
            status=scenario_seed.get("status", SCENARIO_STATUS_DRAFT),
            is_official=True,
            is_active=True,
            submitted_at=datetime.utcnow()
            if scenario_seed.get("status") in {SCENARIO_STATUS_SUBMITTED, SCENARIO_STATUS_APPROVED}
            else None,
            approved_at=datetime.utcnow()
            if scenario_seed.get("status") == SCENARIO_STATUS_APPROVED
            else None,
            archived_at=datetime.utcnow()
            if scenario_seed.get("status") == SCENARIO_STATUS_ARCHIVED
            else None,
        )
        db.session.add(scenario)
        db.session.flush()

        for idx, question_seed in enumerate(scenario_seed["questions"], start=1):
            db.session.add(
                Question(
                    scenario_id=scenario.id,
                    question_key=question_seed["question_key"],
                    prompt=question_seed["prompt"],
                    question_type=question_seed.get("question_type", DEFAULT_QUESTION_TYPE),
                    instructor_answer=question_seed.get("instructor_answer"),
                    sort_order=idx,
                    is_active=True,
                )
            )

    db.session.commit()


def log_runtime_configuration_warnings() -> None:
    import sys
    from flask import current_app

    warnings = []

    if current_app.config.get("SECRET_KEY") == "dev-secret-change-me":
        warnings.append("SECRET_KEY is set to the insecure default. Set a unique value before any real use.")
    if current_app.config.get("RUN_DEBUG"):
        warnings.append("RUN_DEBUG is enabled. Set RUN_DEBUG=0 before running a real session.")
    if current_app.config.get("ENABLE_MAGIC_LINK_DEBUG"):
        warnings.append("ENABLE_MAGIC_LINK_DEBUG is on — magic link tokens are visible in the UI.")
    if current_app.config.get("ENABLE_ACCOUNT_ACTIVATION_DEBUG"):
        warnings.append("ENABLE_ACCOUNT_ACTIVATION_DEBUG is on — activation links are visible in the UI.")

    if not warnings:
        return

    border = "=" * 62
    print(f"\n{border}", file=sys.stderr)
    print("  Fireground Trainer — startup warnings", file=sys.stderr)
    print(border, file=sys.stderr)
    for msg in warnings:
        print(f"  ⚠  {msg}", file=sys.stderr)
    print(f"{border}\n", file=sys.stderr)


TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_ENV_VALUES


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def build_runtime_config(instance_path: str) -> dict:
    return {
        "SECRET_KEY": os.getenv("SECRET_KEY", "dev-secret-change-me"),
        "SQLALCHEMY_DATABASE_URI": os.getenv(
            "DATABASE_URL", default_sqlite_database_url(instance_path)
        ),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "PUBLIC_BASE_URL": os.getenv("PUBLIC_BASE_URL", "").strip() or None,
        "DEMO_BOOTSTRAP_PASSWORD": os.getenv("DEMO_BOOTSTRAP_PASSWORD", "EasyPass123"),
        "ENABLE_MAGIC_LINK_DEBUG": env_flag("ENABLE_MAGIC_LINK_DEBUG", True),
        "MAGIC_LINK_TTL_MINUTES": env_int("MAGIC_LINK_TTL_MINUTES", 15),
        "ENABLE_ACCOUNT_ACTIVATION_DEBUG": env_flag("ENABLE_ACCOUNT_ACTIVATION_DEBUG", True),
        "ACCOUNT_ACTIVATION_TTL_HOURS": env_int("ACCOUNT_ACTIVATION_TTL_HOURS", 24),
        "ENABLE_DEMO_SEED_USERS": env_flag("ENABLE_DEMO_SEED_USERS", True),
        "ENABLE_DEMO_SEED_SCENARIOS": env_flag("ENABLE_DEMO_SEED_SCENARIOS", True),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": env_flag("SESSION_COOKIE_SECURE", False),
        "RUN_HOST": os.getenv("RUN_HOST", "0.0.0.0").strip() or "0.0.0.0",
        "RUN_PORT": env_int("RUN_PORT", 5000),
        "RUN_DEBUG": env_flag("RUN_DEBUG", False),
    }


def default_sqlite_database_url(instance_path: str) -> str:
    return f"sqlite:///{instance_path}/fireground_trainer.db"


def user_is_staff(user: User) -> bool:
    role_names = {link.role.name for link in user.role_links if link.role}
    return bool(role_names.intersection(STAFF_ROLES))


def get_role_names_for_user(user: User | None) -> frozenset[str]:
    if user is None:
        return frozenset()
    return normalize_roles(link.role.name for link in user.role_links if link.role)


def get_current_user() -> CurrentUser:
    user_id = session.get("user_id")
    if isinstance(user_id, int):
        user = User.query.filter_by(id=user_id, is_active=True).first()
        if user:
            roles = normalize_roles([link.role.name for link in user.role_links if link.role])
            return CurrentUser(
                user_id=str(user.id),
                display_name=user.full_name or user.email,
                roles=roles,
                permission_overrides=frozenset(),
            )

    return CurrentUser(
        user_id="guest",
        display_name="Guest Participant",
        roles=frozenset({ROLE_PARTICIPANT}),
        permission_overrides=frozenset(),
    )


def get_current_db_user() -> User | None:
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return User.query.filter_by(id=user_id, is_active=True).first()


def load_visible_scenarios_for_user(current_user: CurrentUser) -> list[Scenario]:
    query = Scenario.query.order_by(Scenario.id.asc())
    if current_user.has_permission(PERM_CREATE_SCENARIOS) or current_user.has_permission(PERM_APPROVE_SCENARIOS):
        return query.all()
    return query.filter(Scenario.status == SCENARIO_STATUS_APPROVED).all()


def build_scenario_view_model(scenario: Scenario) -> dict:
    return {
        "id": scenario.id,
        "title": scenario.title,
        "dispatch": scenario.dispatch_text,
        "image": {
            "base": scenario.base_image_path,
            "overlay": scenario.overlay_image_path,
        },
        "questions": [
            {
                "id": q.id,
                "question_key": q.question_key,
                "prompt": q.prompt,
                "question_type": q.question_type or DEFAULT_QUESTION_TYPE,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    q.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "instructor_answer": q.instructor_answer or "",
            }
            for q in sorted(scenario.questions, key=lambda item: item.sort_order)
            if q.is_active
        ],
        "workflow_status": scenario.status,
        "is_official": scenario.is_official,
        "submitted_at": scenario.submitted_at,
        "approved_at": scenario.approved_at,
        "archived_at": scenario.archived_at,
        "like_count": scenario.like_count or 0,
        "approved_by": (
            scenario.approved_by.full_name
            if scenario.approved_by and scenario.approved_by.full_name
            else (scenario.approved_by.email if scenario.approved_by else None)
        ),
    }


def get_current_scenario() -> tuple[Scenario, dict]:
    visible_scenarios = load_visible_scenarios_for_user(g.current_user)
    if not visible_scenarios:
        abort(404)

    scenario_by_id = {scenario.id: scenario for scenario in visible_scenarios}
    scenario_id = session.get("scenario_id")
    if not isinstance(scenario_id, int) or scenario_id not in scenario_by_id:
        scenario_id = visible_scenarios[0].id
        session["scenario_id"] = scenario_id

    scenario_row = scenario_by_id[scenario_id]
    return scenario_row, build_scenario_view_model(scenario_row)


def allowed_library_tabs_for_user(current_user: CurrentUser) -> list[str]:
    tabs = [LIBRARY_TAB_OFFICIAL]
    if current_user.has_permission(PERM_CREATE_SCENARIOS) or current_user.has_permission(PERM_APPROVE_SCENARIOS):
        tabs.extend([LIBRARY_TAB_PRACTICE, LIBRARY_TAB_MINE, LIBRARY_TAB_SUBMITTED])
    return tabs


def resolve_library_tab(raw_tab: str | None, current_user: CurrentUser) -> str:
    allowed_tabs = allowed_library_tabs_for_user(current_user)
    if raw_tab in allowed_tabs:
        return raw_tab
    return allowed_tabs[0]


def load_library_scenarios(tab: str, current_user: CurrentUser, db_user: User | None) -> list[Scenario]:
    query = Scenario.query.filter(Scenario.is_active.is_(True)).order_by(Scenario.updated_at.desc(), Scenario.id.desc())
    if tab == LIBRARY_TAB_OFFICIAL:
        return (
            query.filter(
                Scenario.is_official.is_(True),
                Scenario.status == SCENARIO_STATUS_APPROVED,
            ).all()
        )
    if tab == LIBRARY_TAB_PRACTICE:
        return (
            query.filter(
                Scenario.is_official.is_(False),
                Scenario.status != SCENARIO_STATUS_ARCHIVED,
            ).all()
        )
    if tab == LIBRARY_TAB_MINE:
        if db_user is None:
            return []
        return (
            query.filter(
                Scenario.created_by_user_id == db_user.id,
                Scenario.status != SCENARIO_STATUS_ARCHIVED,
            ).all()
        )
    if tab == LIBRARY_TAB_SUBMITTED:
        return query.filter(Scenario.status == SCENARIO_STATUS_SUBMITTED).all()
    return []


def get_completed_submission_scenario_ids_for_user(
    db_user: User | None,
    scenario_ids: list[int] | None = None,
) -> set[int]:
    if db_user is None:
        return set()

    query = (
        db.session.query(Submission.scenario_id)
        .join(Participant, Participant.id == Submission.participant_id)
        .filter(Participant.user_id == db_user.id)
    )
    if scenario_ids:
        query = query.filter(Submission.scenario_id.in_(scenario_ids))
    return {scenario_id for scenario_id, in query.distinct().all()}


def get_liked_scenario_ids_for_user(
    db_user: User | None,
    scenario_ids: list[int] | None = None,
) -> set[int]:
    if db_user is None:
        return set()

    query = ScenarioLike.query.filter_by(user_id=db_user.id, is_liked=True)
    if scenario_ids:
        query = query.filter(ScenarioLike.scenario_id.in_(scenario_ids))
    return {row.scenario_id for row in query.all()}


def build_scenario_vote_state_map(
    scenarios: list[Scenario],
    db_user: User | None,
) -> dict[int, dict]:
    scenario_ids = [scenario.id for scenario in scenarios]
    eligible_ids = get_completed_submission_scenario_ids_for_user(db_user, scenario_ids)
    liked_ids = get_liked_scenario_ids_for_user(db_user, scenario_ids)
    vote_state_map: dict[int, dict] = {}

    for scenario in scenarios:
        can_like = scenario.id in eligible_ids
        has_liked = scenario.id in liked_ids
        if db_user is None:
            eligibility_note = "Sign in with an account, complete this scenario once, then you can like it."
        elif can_like:
            eligibility_note = "You can like or remove your like for this scenario."
        else:
            eligibility_note = "Complete a signed-in submission for this scenario before liking it."

        vote_state_map[scenario.id] = {
            "can_like": can_like,
            "has_liked": has_liked,
            "like_count": scenario.like_count or 0,
            "eligibility_note": eligibility_note,
        }

    return vote_state_map


def summarize_scenario_for_library(scenario: Scenario, vote_state: dict | None = None) -> dict:
    vote_state = vote_state or {}
    return {
        "id": scenario.id,
        "title": scenario.title,
        "status": scenario.status,
        "is_official": scenario.is_official,
        "question_count": len([q for q in scenario.questions if q.is_active]),
        "updated_at": scenario.updated_at,
        "like_count": vote_state.get("like_count", scenario.like_count or 0),
        "can_like": vote_state.get("can_like", False),
        "has_liked": vote_state.get("has_liked", False),
        "eligibility_note": vote_state.get(
            "eligibility_note",
            "Complete a signed-in submission for this scenario before liking it.",
        ),
        "approved_by": (
            scenario.approved_by.full_name
            if scenario.approved_by and scenario.approved_by.full_name
            else (scenario.approved_by.email if scenario.approved_by else None)
        ),
    }


def scenario_category_key_for_scenario(scenario: Scenario) -> str:
    # Categories are landing-page level for now. Existing seeded scenarios are all fireground.
    return CATEGORY_FIREGROUND


def scenario_creator_filter_key(scenario: Scenario) -> str:
    if scenario.is_official or scenario.created_by is None:
        return CATEGORY_FILTER_OFFICIAL

    creator_role_names = get_role_names_for_user(scenario.created_by)
    if creator_role_names.intersection(STAFF_ROLES):
        return CATEGORY_FILTER_INSTRUCTOR_MADE
    return CATEGORY_FILTER_USER_MADE


def scenario_creator_filter_label(scenario: Scenario) -> str:
    return CATEGORY_FILTER_LABELS[scenario_creator_filter_key(scenario)]


def resolve_category_filter(raw_filter: str | None) -> str:
    if raw_filter in CATEGORY_FILTER_LABELS:
        return raw_filter
    return CATEGORY_FILTER_ALL


def load_category_scenarios(category_key: str, selected_filter: str) -> list[Scenario]:
    if category_key != CATEGORY_FIREGROUND:
        return []

    scenarios = (
        Scenario.query.filter(
            Scenario.is_active.is_(True),
            Scenario.status == SCENARIO_STATUS_APPROVED,
        )
        .order_by(
            Scenario.like_count.desc(),
            Scenario.is_official.desc(),
            Scenario.updated_at.desc(),
            Scenario.id.desc(),
        )
        .all()
    )
    category_scenarios = [
        scenario
        for scenario in scenarios
        if scenario_category_key_for_scenario(scenario) == category_key
    ]
    if selected_filter == CATEGORY_FILTER_ALL:
        return category_scenarios
    return [
        scenario
        for scenario in category_scenarios
        if scenario_creator_filter_key(scenario) == selected_filter
    ]


def format_scenario_popularity_label(like_count: int) -> str:
    if like_count >= 3:
        return "Most Liked"
    if like_count >= 1:
        return "Popular"
    return "New To Likes"


def summarize_scenario_for_catalog(scenario: Scenario, vote_state: dict | None = None) -> dict:
    vote_state = vote_state or {}
    question_count = len([question for question in scenario.questions if question.is_active])
    dispatch_summary = " ".join((scenario.dispatch_text or "").split())
    like_count = vote_state.get("like_count", scenario.like_count or 0)
    creator_filter_label = scenario_creator_filter_label(scenario)
    creator = scenario.created_by
    author_name = (
        (creator.full_name or creator.email) if creator is not None else None
    )
    status_label = (scenario.status or "").replace("_", " ").title()
    return {
        "id": scenario.id,
        "title": scenario.title,
        "dispatch_summary": dispatch_summary[:180] + ("..." if len(dispatch_summary) > 180 else ""),
        "question_count": question_count,
        "question_label": f"{question_count} question{'s' if question_count != 1 else ''}",
        "updated_at": scenario.updated_at,
        "updated_at_label": format_relative_date(scenario.updated_at),
        "is_official": scenario.is_official,
        "creator_filter_key": scenario_creator_filter_key(scenario),
        "creator_filter_label": creator_filter_label,
        "author_label": creator_filter_label,
        "author_name": author_name,
        "status": scenario.status,
        "status_label": status_label,
        "like_count": like_count,
        "likes_label": f"{like_count} like{'s' if like_count != 1 else ''}",
        "popularity_label": format_scenario_popularity_label(like_count),
        "can_like": vote_state.get("can_like", False),
        "has_liked": vote_state.get("has_liked", False),
        "eligibility_note": vote_state.get(
            "eligibility_note",
            "Complete a signed-in submission for this scenario before liking it.",
        ),
    }


def normalize_static_asset_path(raw_path: str | None, *, allow_empty: bool = False) -> str | None:
    from pathlib import Path
    normalized = (raw_path or "").strip().replace("\\", "/")
    if not normalized:
        return None if allow_empty else None
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized:
        return None

    candidate_path = Path(normalized)
    if candidate_path.is_absolute() or ".." in candidate_path.parts:
        return None

    static_root = Path(current_app.static_folder or (Path(current_app.root_path) / "static")).resolve()
    resolved_path = (static_root / candidate_path).resolve()
    try:
        resolved_path.relative_to(static_root)
    except ValueError:
        return None

    if not resolved_path.is_file():
        return None

    return candidate_path.as_posix()


def scenario_asset_validation_error(base_image_path: str, overlay_image_path: str | None) -> str | None:
    if normalize_static_asset_path(base_image_path) is None:
        return "Base image path must point to an existing file inside the static folder."
    if overlay_image_path and normalize_static_asset_path(overlay_image_path, allow_empty=True) is None:
        return "Overlay image path must point to an existing file inside the static folder."
    return None


def get_signed_in_participant_for_session(training_session_id: int, user) -> "Participant | None":
    from models import Participant
    if user is None:
        return None
    return (
        Participant.query.filter_by(
            training_session_id=training_session_id,
            user_id=user.id,
        )
        .order_by(Participant.joined_at.desc(), Participant.id.desc())
        .first()
    )


def build_participant_submission_state(scenario_row) -> dict:
    participant = g.active_participant
    training_session = g.active_training_session

    if participant is None or training_session is None:
        return {
            "can_submit": False,
            "status": "not_joined",
            "message": None,
            "session_title": None,
            "join_code": None,
            "shift_label": None,
            "identity_label": None,
            "latest_attempt_number": 0,
            "next_attempt_number": None,
            "guidance": "Join an active session from a host QR code or join link to unlock submissions.",
        }

    latest_attempt_number = max(
        (submission.attempt_number for submission in participant.submissions),
        default=0,
    )
    identity_label = "Anonymous" if participant.is_anonymous else (participant.display_name or "Participant")
    base_state = {
        "session_title": training_session.title or f"Session #{training_session.id}",
        "join_code": training_session.join_code,
        "shift_label": participant.shift_label or "Unspecified",
        "identity_label": identity_label,
        "latest_attempt_number": latest_attempt_number,
        "next_attempt_number": latest_attempt_number + 1,
    }

    if training_session.status != "active":
        return {
            "can_submit": False,
            "status": "inactive_session",
            "message": "This session is no longer active. New submissions are locked.",
            "guidance": "You can still view revealed answers, but only active sessions accept new attempts.",
            **base_state,
        }
    if participant.training_session_id != training_session.id:
        return {
            "can_submit": False,
            "status": "invalid_session",
            "message": "Your participant session is no longer valid. Rejoin the session to continue.",
            "guidance": "Rejoin the live session to restore your participant entry before submitting again.",
            **base_state,
        }
    if scenario_row is not None and training_session.scenario_id != scenario_row.id:
        return {
            "can_submit": False,
            "status": "scenario_mismatch",
            "message": "This board is showing a different scenario than your active session.",
            "guidance": "Switch back to your session scenario before sending another attempt.",
            **base_state,
        }
    return {
        "can_submit": True,
        "status": "ready",
        "message": None,
        "guidance": (
            "You can submit more than once during a live drill. The host workspace reviews your latest saved attempt."
        ),
        **base_state,
    }


def build_home_category_cards() -> list[dict]:
    fireground_count = len(load_category_scenarios(CATEGORY_FIREGROUND, CATEGORY_FILTER_ALL))
    return [
        {
            "key": CATEGORY_FIREGROUND,
            "label": CATEGORY_LABELS[CATEGORY_FIREGROUND],
            "description": (
                "Residential, commercial, and tactical fireground drills built for company-level review."
            ),
            "count_label": f"{fireground_count} live scenario{'s' if fireground_count != 1 else ''}",
            "href": url_for("scenarios.fireground_training"),
            "is_available": True,
        },
        {
            "key": CATEGORY_MVA,
            "label": CATEGORY_LABELS[CATEGORY_MVA],
            "description": (
                "Coming soon: stabilization, entrapment, highway command, and rescue benchmarks."
            ),
            "count_label": "Placeholder page",
            "href": url_for("scenarios.mva_training"),
            "is_available": False,
        },
        {
            "key": CATEGORY_EMS,
            "label": CATEGORY_LABELS[CATEGORY_EMS],
            "description": (
                "Coming soon: patient assessment, airway, medical decision making, and team communication."
            ),
            "count_label": "Placeholder page",
            "href": url_for("scenarios.ems_training"),
            "is_available": False,
        },
    ]


def build_training_category_page(
    category_key: str,
    selected_filter: str,
    db_user: User | None,
) -> dict:
    # Fetch once — filter in Python to avoid a second DB round-trip.
    all_scenarios = load_category_scenarios(category_key, CATEGORY_FILTER_ALL)
    if selected_filter == CATEGORY_FILTER_ALL:
        scenarios = all_scenarios
    else:
        scenarios = [
            s for s in all_scenarios
            if scenario_creator_filter_key(s) == selected_filter
        ]

    vote_state_map = build_scenario_vote_state_map(all_scenarios, db_user)

    # Build filter options with per-filter counts.
    filter_options = []
    for key, label in CATEGORY_FILTER_LABELS.items():
        if key == CATEGORY_FILTER_ALL:
            count = len(all_scenarios)
        else:
            count = sum(1 for s in all_scenarios if scenario_creator_filter_key(s) == key)
        filter_options.append({"key": key, "label": label, "count": count})

    is_placeholder = category_key in {CATEGORY_MVA, CATEGORY_EMS}
    featured_scenarios = [
        summarize_scenario_for_catalog(s, vote_state_map.get(s.id))
        for s in all_scenarios
        if (s.like_count or 0) > 0
    ][:3]

    return {
        "key": category_key,
        "label": CATEGORY_LABELS[category_key],
        "description": {
            CATEGORY_FIREGROUND: (
                "Choose a fireground scenario, filter by who created it, and send the board live when you are ready."
            ),
            CATEGORY_MVA: (
                "This category page is in place so the site structure is ready. Scenario templates will land here next."
            ),
            CATEGORY_EMS: (
                "This category page is in place so the site structure is ready. Scenario templates will land here next."
            ),
        }[category_key],
        "selected_filter": selected_filter,
        "active_filter_label": CATEGORY_FILTER_LABELS[selected_filter],
        "filter_options": filter_options,
        "total_count": len(all_scenarios),
        "result_count": len(scenarios),
        "featured_scenarios": featured_scenarios,
        "scenarios": [
            summarize_scenario_for_catalog(s, vote_state_map.get(s.id))
            for s in scenarios
        ],
        "is_placeholder": is_placeholder,
    }


def build_scenario_vote_summary(scenario: Scenario, db_user: User | None) -> dict:
    return build_scenario_vote_state_map([scenario], db_user).get(
        scenario.id,
        {
            "can_like": False,
            "has_liked": False,
            "like_count": scenario.like_count or 0,
            "eligibility_note": "Complete a signed-in submission for this scenario before liking it.",
        },
    )


def get_scenario_vote_action(raw_action: str | None) -> str | None:
    if raw_action in {"like", "clear"}:
        return raw_action
    return None


def user_can_like_scenario(scenario: Scenario, db_user: User | None) -> bool:
    if db_user is None:
        return False
    completed_scenario_ids = get_completed_submission_scenario_ids_for_user(db_user, [scenario.id])
    return scenario.id in completed_scenario_ids


def refresh_scenario_like_count(scenario: Scenario) -> None:
    scenario.like_count = ScenarioLike.query.filter_by(
        scenario_id=scenario.id,
        is_liked=True,
    ).count()


def set_scenario_like_vote(scenario: Scenario, db_user: User, vote_action: str) -> None:
    existing_vote = ScenarioLike.query.filter_by(
        scenario_id=scenario.id,
        user_id=db_user.id,
    ).first()
    desired_like_state = vote_action == "like"

    if existing_vote is None:
        existing_vote = ScenarioLike(
            scenario_id=scenario.id,
            user_id=db_user.id,
            is_liked=desired_like_state,
        )
        db.session.add(existing_vote)
    else:
        existing_vote.is_liked = desired_like_state

    db.session.flush()
    refresh_scenario_like_count(scenario)


def generate_join_code(length: int = 6) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if not TrainingSession.query.filter_by(join_code=code).first():
            return code


def can_use_scenario_for_session(scenario_id: int, current_user: CurrentUser) -> bool:
    visible_scenarios = load_visible_scenarios_for_user(current_user)
    visible_ids = {scenario.id for scenario in visible_scenarios}
    return scenario_id in visible_ids


def render_create_session(
    scenarios: list[Scenario],
    selected_scenario_id: int | None,
    error: str | None = None,
    status_code: int = 200,
):
    scenario_options = [
        {
            "id": scenario.id,
            "title": scenario.title,
            "status": scenario.status,
        }
        for scenario in scenarios
        if scenario.status != SCENARIO_STATUS_ARCHIVED
    ]
    form_values = {
        "title": request.form.get("title", "").strip() if request.method == "POST" else "",
    }
    return (
        render_template(
            "session_create.html",
            scenarios=scenario_options,
            selected_scenario_id=selected_scenario_id,
            error=error,
            form_values=form_values,
        ),
        status_code,
    )


def render_join_page(
    training_session: "TrainingSession",
    error: str | None = None,
    error_field: str | None = None,
    status_code: int = 200,
):
    current_db_user = get_current_db_user()
    default_display_name = ""
    default_identity_mode = "anonymous"
    if current_db_user is not None:
        default_display_name = (current_db_user.full_name or current_db_user.email.split("@", 1)[0])[:120]
        default_identity_mode = "named"
    form_values = {
        "shift_label": request.form.get("shift_label", "").strip()
        if request.method == "POST"
        else "",
        "custom_shift_label": request.form.get("custom_shift_label", "").strip()
        if request.method == "POST"
        else "",
        "identity_mode": request.form.get("identity_mode", default_identity_mode)
        if request.method == "POST"
        else default_identity_mode,
        "display_name": request.form.get("display_name", "").strip()
        if request.method == "POST"
        else default_display_name,
    }
    return (
        render_template(
            "session_join_landing.html",
            training_session=training_session,
            error=error,
            error_field=error_field,
            form_values=form_values,
            joining_user=current_db_user,
        ),
        status_code,
    )


def get_join_url_for_session(training_session: TrainingSession) -> str:
    join_url, _warning = build_join_url_for_session(training_session)
    return join_url


def normalize_public_base_url(raw_base_url: str | None) -> str | None:
    if not raw_base_url:
        return None
    normalized = raw_base_url.strip()
    if not normalized:
        return None
    if "://" not in normalized:
        normalized = f"http://{normalized}"
    return normalized.rstrip("/")


def host_is_loopback_or_unspecified(hostname: str | None) -> bool:
    if not hostname:
        return False
    lowered = hostname.strip().lower()
    if lowered == "localhost":
        return True
    try:
        host_ip = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return host_ip.is_loopback or host_ip.is_unspecified


def detect_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            detected_ip = sock.getsockname()[0]
    except OSError:
        return None

    if not detected_ip:
        return None
    if host_is_loopback_or_unspecified(detected_ip):
        return None
    return detected_ip


def build_join_url_for_session(training_session: TrainingSession) -> tuple[str, str | None]:
    from flask import current_app

    join_path = url_for("sessions.join_by_code", join_code=training_session.join_code)
    configured_base_url = normalize_public_base_url(current_app.config.get("PUBLIC_BASE_URL"))
    if configured_base_url:
        return f"{configured_base_url}{join_path}", None

    parsed_host_url = urlsplit(request.host_url)
    request_base_url = f"{parsed_host_url.scheme}://{parsed_host_url.netloc}".rstrip("/")
    if not host_is_loopback_or_unspecified(parsed_host_url.hostname):
        return f"{request_base_url}{join_path}", None

    detected_lan_ip = detect_lan_ip()
    if detected_lan_ip:
        default_port = 443 if parsed_host_url.scheme == "https" else 80
        port_segment = (
            f":{parsed_host_url.port}"
            if parsed_host_url.port and parsed_host_url.port != default_port
            else ""
        )
        lan_base_url = f"{parsed_host_url.scheme}://{detected_lan_ip}{port_segment}"
        return (
            f"{lan_base_url}{join_path}",
            (
                "QR code is using your computer's detected LAN address. "
                "If iPhone joins still fail, start the app on `0.0.0.0` or set `PUBLIC_BASE_URL`."
            ),
        )

    return (
        f"{request_base_url}{join_path}",
        (
            "QR code is still pointing at a local-only address. "
            "Set `PUBLIC_BASE_URL` to your computer's LAN URL, for example `http://192.168.1.23:5000`."
        ),
    )


def get_qr_image_url(join_url: str) -> str:
    encoded = quote(join_url, safe="")
    return f"https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={encoded}"


def clear_participant_session_context() -> None:
    session.pop(PARTICIPANT_JOIN_MAP_KEY, None)
    session.pop(ACTIVE_TRAINING_SESSION_ID_KEY, None)
    session.pop(ACTIVE_PARTICIPANT_ID_KEY, None)


def clear_host_training_session_context() -> None:
    session.pop(HOST_TRAINING_SESSION_ID_KEY, None)


def get_joined_participant_for_session(training_session_id: int) -> Participant | None:
    joined_map = session.get(PARTICIPANT_JOIN_MAP_KEY, {})
    if not isinstance(joined_map, dict):
        return None

    raw_participant_id = joined_map.get(str(training_session_id))
    if not isinstance(raw_participant_id, int):
        return None
    participant = Participant.query.filter_by(id=raw_participant_id).first()
    if participant is None or participant.training_session_id != training_session_id:
        return None
    return participant


def set_active_participant_context(participant: Participant) -> None:
    joined_map = session.get(PARTICIPANT_JOIN_MAP_KEY, {})
    if not isinstance(joined_map, dict):
        joined_map = {}
    joined_map[str(participant.training_session_id)] = participant.id
    session[PARTICIPANT_JOIN_MAP_KEY] = joined_map
    session[ACTIVE_TRAINING_SESSION_ID_KEY] = participant.training_session_id
    session[ACTIVE_PARTICIPANT_ID_KEY] = participant.id
    training_session = TrainingSession.query.filter_by(id=participant.training_session_id).first()
    if training_session is not None:
        session["scenario_id"] = training_session.scenario_id


def set_host_training_session_context(training_session: TrainingSession) -> None:
    session[HOST_TRAINING_SESSION_ID_KEY] = training_session.id
    session["scenario_id"] = training_session.scenario_id


def load_active_participant_session() -> tuple[Participant | None, TrainingSession | None]:
    from models import Participant, TrainingSession
    training_session_id = session.get(ACTIVE_TRAINING_SESSION_ID_KEY)
    participant_id = session.get(ACTIVE_PARTICIPANT_ID_KEY)
    if not isinstance(training_session_id, int) or not isinstance(participant_id, int):
        return None, None

    participant = Participant.query.filter_by(id=participant_id).first()
    training_session = TrainingSession.query.filter_by(id=training_session_id).first()
    if (
        participant is None
        or training_session is None
        or participant.training_session_id != training_session.id
    ):
        clear_participant_session_context()
        return None, None
    current_db_user = get_current_db_user()
    if participant.user_id is not None and (
        current_db_user is None or current_db_user.id != participant.user_id
    ):
        clear_participant_session_context()
        return None, None
    if training_session.status != "active":
        return participant, training_session

    session["scenario_id"] = training_session.scenario_id
    return participant, training_session


def load_host_training_session() -> "TrainingSession | None":
    from models import TrainingSession
    from authz import PERM_VIEW_SESSION_SUBMISSIONS
    if not g.current_user.has_permission(PERM_VIEW_SESSION_SUBMISSIONS):
        return None

    requested_session_id = request.args.get("session_id", "").strip()
    resolved_session_id: int | None = None
    if requested_session_id.isdigit():
        resolved_session_id = int(requested_session_id)
    else:
        stored_session_id = session.get(HOST_TRAINING_SESSION_ID_KEY)
        if isinstance(stored_session_id, int):
            resolved_session_id = stored_session_id

    if resolved_session_id is None:
        return None

    training_session = TrainingSession.query.filter_by(id=resolved_session_id).first()
    if training_session is None or training_session.status != "active":
        clear_host_training_session_context()
        return None

    set_host_training_session_context(training_session)
    return training_session


def safe_redirect_target(raw_target: str | None) -> str:
    if not raw_target:
        return url_for("main.home")

    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc:
        return url_for("main.home")
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return url_for("main.home")
    if parsed.path in POST_ONLY_PATHS:
        return url_for("main.home")
    adapter = current_app.url_map.bind("localhost")
    if not adapter.test(parsed.path, method="GET") and not adapter.test(parsed.path, method="HEAD"):
        return url_for("main.home")
    return raw_target


def get_attempt_key(email: str) -> tuple[str, str]:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")
    return (ip, email)


def is_login_rate_limited(email: str) -> bool:
    import time
    now = time.time()
    key = get_attempt_key(email)
    attempts = [ts for ts in LOGIN_ATTEMPTS.get(key, []) if (now - ts) < LOGIN_WINDOW_SECONDS]
    LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= LOGIN_ATTEMPT_LIMIT


def record_failed_login(email: str) -> None:
    import time
    now = time.time()
    key = get_attempt_key(email)
    attempts = [ts for ts in LOGIN_ATTEMPTS.get(key, []) if (now - ts) < LOGIN_WINDOW_SECONDS]
    attempts.append(now)
    LOGIN_ATTEMPTS[key] = attempts


def clear_failed_logins(email: str) -> None:
    LOGIN_ATTEMPTS.pop(get_attempt_key(email), None)


def issue_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def rotate_csrf_token() -> None:
    session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)


def trusted_request_origin() -> bool:
    expected = urlsplit(request.host_url)
    for header_name in ("Origin", "Referer"):
        raw_value = request.headers.get(header_name, "").strip()
        if not raw_value:
            continue
        parsed = urlsplit(raw_value)
        if not parsed.scheme or not parsed.netloc:
            return False
        return parsed.scheme == expected.scheme and parsed.netloc == expected.netloc
    return True


def validate_csrf_or_abort() -> None:
    if not trusted_request_origin():
        abort(400)
    expected = session.get(CSRF_SESSION_KEY)
    provided = request.form.get("csrf_token", "")
    if not expected or not provided or not hmac.compare_digest(expected, provided):
        abort(400)


def get_posted_scenario_or_abort() -> Scenario:
    raw_scenario_id = request.form.get("scenario_id", "").strip()
    if not raw_scenario_id.isdigit():
        abort(400)
    scenario = Scenario.query.filter_by(id=int(raw_scenario_id)).first()
    if scenario is None:
        abort(404)
    return scenario


def apply_scenario_transition_or_abort(
    scenario: Scenario, action: str, actor: User | None
) -> None:
    now = datetime.utcnow()

    if action == "submit_for_review":
        if scenario.status != SCENARIO_STATUS_DRAFT:
            abort(409)
        if scenario.created_by_user_id is None and actor is not None:
            scenario.created_by_user_id = actor.id
        scenario.status = SCENARIO_STATUS_SUBMITTED
        scenario.submitted_at = now
        scenario.approved_at = None
        scenario.approved_by_user_id = None
        scenario.archived_at = None
        return

    if action == "approve":
        if scenario.status != SCENARIO_STATUS_SUBMITTED:
            abort(409)
        scenario.status = SCENARIO_STATUS_APPROVED
        scenario.approved_at = now
        scenario.approved_by_user_id = actor.id if actor else None
        scenario.archived_at = None
        return

    if action == "archive":
        if scenario.status not in SCENARIO_ACTIVE_STATUSES:
            abort(409)
        scenario.status = SCENARIO_STATUS_ARCHIVED
        scenario.archived_at = now
        return

    abort(400)


def parse_create_scenario_questions() -> tuple[list[dict], str | None]:
    prompts = request.form.getlist("question_prompt")
    types = request.form.getlist("question_type")
    instructor_answers = request.form.getlist("instructor_answer")

    max_len = max(len(prompts), len(types), len(instructor_answers))
    questions: list[dict] = []
    for idx in range(max_len):
        prompt = prompts[idx].strip() if idx < len(prompts) else ""
        if not prompt:
            continue
        question_type = types[idx].strip() if idx < len(types) else DEFAULT_QUESTION_TYPE
        if question_type not in QUESTION_TYPE_CHOICES:
            return [], "One or more question types are invalid."
        instructor_answer = (
            instructor_answers[idx].strip() if idx < len(instructor_answers) else ""
        )
        questions.append(
            {
                "prompt": prompt,
                "question_type": question_type,
                "instructor_answer": instructor_answer,
            }
        )

    if not questions:
        return [], "At least one question is required."
    if len(questions) > 20:
        return [], "Please keep scenarios to 20 questions or fewer."
    return questions, None


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def normalize_text_for_match(text_value: str) -> str:
    lowered = text_value.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def tokenize_for_match(text_value: str) -> list[str]:
    normalized = normalize_text_for_match(text_value)
    tokens = normalized.split()
    return [token for token in tokens if len(token) >= 3 and token not in STOP_WORDS]


def extract_reference_phrases(reference: str) -> list[str]:
    if not reference.strip():
        return []
    parts = re.split(r"[\n;]+", reference)
    raw_phrases: list[str] = []
    for part in parts:
        for section in part.split(","):
            cleaned = re.sub(r"^\s*[-*•\d\.\)\(]+\s*", "", section).strip()
            if cleaned:
                raw_phrases.append(cleaned)
    return ordered_unique(raw_phrases)


def extract_reference_terms(reference: str, max_terms: int = 10) -> list[str]:
    terms = ordered_unique(tokenize_for_match(reference))
    return terms[:max_terms]


def build_short_answer_feedback(answer_text: str, reference_text: str) -> dict:
    key_terms = extract_reference_terms(reference_text, max_terms=12)
    if not key_terms:
        return {
            "mode": "auto",
            "score_percent": None,
            "summary": "Auto-score unavailable. Add clearer creator answer key points.",
            "matched_items": [],
            "missing_items": [],
        }

    answer_tokens = set(tokenize_for_match(answer_text))
    matched_terms = [term for term in key_terms if term in answer_tokens]
    missing_terms = [term for term in key_terms if term not in answer_tokens]
    percent = round((len(matched_terms) / len(key_terms)) * 100)
    return {
        "mode": "auto",
        "score_percent": percent,
        "summary": f"Auto score: {len(matched_terms)}/{len(key_terms)} key points matched ({percent}%).",
        "matched_items": matched_terms[:6],
        "missing_items": missing_terms[:6],
    }


def build_checklist_feedback(answer_text: str, reference_text: str) -> dict:
    checklist_items = extract_reference_phrases(reference_text)
    if not checklist_items:
        return build_short_answer_feedback(answer_text, reference_text)

    answer_tokens = set(tokenize_for_match(answer_text))
    matched: list[str] = []
    missing: list[str] = []
    for item in checklist_items:
        item_tokens = tokenize_for_match(item)
        if not item_tokens:
            continue
        required_hits = max(1, int(len(item_tokens) * 0.7))
        actual_hits = sum(1 for token in item_tokens if token in answer_tokens)
        if actual_hits >= required_hits:
            matched.append(item)
        else:
            missing.append(item)

    total = len(matched) + len(missing)
    if total == 0:
        return {
            "mode": "auto",
            "score_percent": None,
            "summary": "Auto-score unavailable. Add checklist key points in creator answer.",
            "matched_items": [],
            "missing_items": [],
        }

    percent = round((len(matched) / total) * 100)
    return {
        "mode": "auto",
        "score_percent": percent,
        "summary": f"Checklist auto score: {len(matched)}/{total} items matched ({percent}%).",
        "matched_items": matched[:6],
        "missing_items": missing[:6],
    }


def score_question_answer(question: dict, answer_text: str) -> dict:
    question_type = question.get("question_type", DEFAULT_QUESTION_TYPE)
    reference_text = question.get("instructor_answer", "")

    if question_type == QUESTION_TYPE_DISCUSSION_ONLY:
        return {
            "mode": "manual",
            "score_percent": None,
            "summary": "No auto score for discussion-only question.",
            "matched_items": [],
            "missing_items": [],
        }
    if question_type == QUESTION_TYPE_AUTO_CHECKLIST:
        return build_checklist_feedback(answer_text, reference_text)
    return build_short_answer_feedback(answer_text, reference_text)


def build_submission_feedback(scenario: dict, answers: dict[str, str]) -> dict[str, dict]:
    feedback: dict[str, dict] = {}
    for question in scenario["questions"]:
        qid = str(question["id"])
        feedback[qid] = score_question_answer(question, answers.get(qid, ""))
    return feedback


def validate_submission_context(
    scenario_row: Scenario,
) -> tuple[Participant | None, TrainingSession | None, str | None]:
    participant = g.active_participant
    training_session = g.active_training_session

    if participant is None or training_session is None:
        return None, None, "Join an active session before submitting answers."
    if training_session.status != "active":
        return participant, training_session, "This session is no longer active."
    if participant.training_session_id != training_session.id:
        clear_participant_session_context()
        return None, None, "Your participant session was invalid. Join the session again."
    if training_session.scenario_id != scenario_row.id:
        return participant, training_session, "This scenario does not match your active session."
    return participant, training_session, None


def get_next_submission_attempt_number(participant_id: int) -> int:
    latest_submission = (
        Submission.query.filter_by(participant_id=participant_id)
        .order_by(Submission.attempt_number.desc())
        .first()
    )
    if latest_submission is None:
        return 1
    return latest_submission.attempt_number + 1


def get_next_drill_attempt_number(user_id: int, scenario_id: int) -> int:
    from models import DrillAttempt
    latest = (
        DrillAttempt.query.filter_by(user_id=user_id, scenario_id=scenario_id)
        .order_by(DrillAttempt.attempt_number.desc())
        .first()
    )
    return 1 if latest is None else latest.attempt_number + 1


def persist_drill_attempt(
    user: User,
    scenario_row: Scenario,
    answers: dict[str, str],
) -> "DrillAttempt":
    from models import DrillAttempt, DrillAttemptAnswer
    attempt_number = get_next_drill_attempt_number(user.id, scenario_row.id)
    drill = DrillAttempt(
        user_id=user.id,
        scenario_id=scenario_row.id,
        attempt_number=attempt_number,
        status="submitted",
    )
    db.session.add(drill)
    db.session.flush()
    for question in scenario_row.questions:
        if not question.is_active:
            continue
        db.session.add(DrillAttemptAnswer(
            drill_attempt_id=drill.id,
            question_id=question.id,
            answer_text=answers.get(str(question.id), ""),
        ))
    db.session.commit()
    return drill


def persist_submission(
    scenario_row: Scenario,
    scenario: dict,
    participant: Participant,
    training_session: TrainingSession,
    answers: dict[str, str],
) -> tuple[Submission | None, str | None]:
    submission = Submission(
        participant_id=participant.id,
        training_session_id=training_session.id,
        scenario_id=scenario_row.id,
        attempt_number=get_next_submission_attempt_number(participant.id),
        status=SUBMISSION_STATUS_SUBMITTED,
    )
    db.session.add(submission)
    db.session.flush()

    for question in scenario["questions"]:
        db.session.add(
            SubmissionAnswer(
                submission_id=submission.id,
                question_id=question["id"],
                answer_text=answers.get(str(question["id"]), ""),
            )
        )

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, "Your answers could not be saved. Please try submitting again."
    return submission, None


def format_relative_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    delta = datetime.utcnow() - value
    days = delta.days
    if days < 0:
        return "just now"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_submission_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M UTC")


def format_submission_status(status: str | None) -> str:
    return SUBMISSION_STATUS_LABELS.get(status or SUBMISSION_STATUS_SUBMITTED, "Pending Review")


def summarize_review_notes(notes: str | None, max_length: int = 120) -> str | None:
    if not notes:
        return None
    compact = " ".join(notes.split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def submission_status_tone(status: str | None) -> str:
    if status == SUBMISSION_STATUS_EXCLUDED:
        return "excluded"
    return "active"


def format_user_identity(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


def format_audit_action(action: str) -> str:
    return action.replace("_", " ").title()


def build_session_participant_label_map(training_session: TrainingSession) -> dict[int, str]:
    participants = sorted(
        training_session.participants,
        key=lambda item: (item.joined_at or datetime.min, item.id),
    )
    anonymous_counter = 0
    labels: dict[int, str] = {}
    for participant in participants:
        if participant.is_anonymous:
            anonymous_counter += 1
            labels[participant.id] = f"Anonymous Person {anonymous_counter}"
        else:
            labels[participant.id] = participant.display_name or "Named Participant"
    return labels


def build_latest_submission_map(training_session: TrainingSession) -> dict[int, Submission]:
    submissions = sorted(
        training_session.submissions,
        key=lambda item: (
            item.submitted_at or datetime.min,
            item.id,
        ),
        reverse=True,
    )
    latest_submission_by_participant: dict[int, Submission] = {}
    for submission in submissions:
        latest_submission_by_participant.setdefault(submission.participant_id, submission)
    return latest_submission_by_participant


def build_session_question_review_view_model(training_session: TrainingSession) -> list[dict]:
    participant_labels = build_session_participant_label_map(training_session)
    latest_submission_by_participant = build_latest_submission_map(training_session)
    latest_submissions = sorted(
        latest_submission_by_participant.values(),
        key=lambda item: (
            item.submitted_at or datetime.min,
            item.id,
        ),
        reverse=True,
    )
    active_questions = [
        question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    ]
    revealed_answer_ids_by_question: dict[int, set[int]] = {}
    for reveal in training_session.revealed_question_answers:
        if reveal.submission_answer is not None:
            revealed_answer_ids_by_question.setdefault(reveal.question_id, set()).add(
                reveal.submission_answer_id
            )

    question_rows: list[dict] = []
    for question in active_questions:
        answer_rows = []
        revealed_ids = revealed_answer_ids_by_question.get(question.id, set())
        status_counts = {"active": 0, "excluded": 0}
        for submission in latest_submissions:
            answer = next(
                (row for row in submission.answers if row.question_id == question.id),
                None,
            )
            if answer is None:
                continue
            participant = submission.participant
            tone = submission_status_tone(submission.status)
            status_counts[tone] += 1
            latest_audit = max(
                submission.audit_logs,
                key=lambda item: (item.created_at or datetime.min, item.id),
                default=None,
            )
            answer_rows.append(
                {
                    "submission_id": submission.id,
                    "submission_answer_id": answer.id,
                    "participant_label": participant_labels.get(
                        participant.id,
                        "Anonymous" if participant.is_anonymous else "Named Participant",
                    ),
                    "shift_label": participant.shift_label or "Unspecified",
                    "attempt_number": submission.attempt_number,
                    "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                    "answer_text": answer.answer_text,
                    "status": submission.status,
                    "status_label": format_submission_status(submission.status),
                    "status_tone": tone,
                    "is_excluded": submission.status == SUBMISSION_STATUS_EXCLUDED,
                    "is_revealed": answer.id in revealed_ids,
                    "review_notes": submission.notes or "",
                    "review_notes_preview": summarize_review_notes(submission.notes),
                    "has_notes": bool(submission.notes),
                }
            )

        question_rows.append(
            {
                "question_id": question.id,
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "instructor_answer": question.instructor_answer,
                "answer_rows": answer_rows,
                "revealed_answer_count": len(revealed_ids),
                "status_counts": status_counts,
            }
        )

    return question_rows


def build_revealed_submission_view_model(training_session: TrainingSession) -> dict | None:
    participant_labels = build_session_participant_label_map(training_session)
    active_questions = [
        question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    ]
    reveals_by_question_id: dict[int, list] = {}
    for reveal in training_session.revealed_question_answers:
        if reveal.submission_answer is not None:
            reveals_by_question_id.setdefault(reveal.question_id, []).append(reveal)
    has_revealed_answers = bool(reveals_by_question_id)

    question_rows = []
    for question in active_questions:
        reveals = reveals_by_question_id.get(question.id, [])
        revealed_answers = []
        for reveal in reveals:
            answer = reveal.submission_answer
            submission = answer.submission
            participant = submission.participant
            revealed_answers.append(
                {
                    "answer_text": answer.answer_text,
                    "participant_label": (
                        participant_labels.get(participant.id) if participant is not None else None
                    ),
                    "shift_label": (
                        participant.shift_label or "Unspecified" if participant is not None else None
                    ),
                    "attempt_number": submission.attempt_number,
                    "revealed_at_label": format_submission_timestamp(
                        reveal.updated_at or reveal.created_at
                    ),
                }
            )
        question_rows.append(
            {
                "question_id": question.id,
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "is_revealed": bool(revealed_answers),
                "revealed_answers": revealed_answers,
            }
        )

    return {
        "has_revealed_answers": has_revealed_answers,
        "question_rows": question_rows,
    }


def build_session_dashboard_view_model(training_session: TrainingSession) -> dict:
    active_question_ids = {
        question.id
        for question in training_session.scenario.questions
        if question.is_active
    }
    participants = sorted(
        training_session.participants,
        key=lambda item: item.joined_at or datetime.min,
    )
    submissions = sorted(
        training_session.submissions,
        key=lambda item: item.submitted_at or datetime.min,
        reverse=True,
    )

    latest_submission_by_participant = build_latest_submission_map(training_session)
    participant_labels = build_session_participant_label_map(training_session)

    participant_rows = []
    for participant in participants:
        latest_submission = latest_submission_by_participant.get(participant.id)
        participant_rows.append(
            {
                "participant_id": participant.id,
                "identity_label": participant_labels.get(
                    participant.id,
                    "Anonymous" if participant.is_anonymous else "Named Participant",
                ),
                "shift_label": participant.shift_label or "Unspecified",
                "joined_at_label": format_submission_timestamp(participant.joined_at),
                "submission_count": len(participant.submissions),
                "latest_attempt_number": latest_submission.attempt_number if latest_submission else None,
                "latest_submitted_at_label": (
                    format_submission_timestamp(latest_submission.submitted_at)
                    if latest_submission
                    else None
                ),
            }
        )

    submission_rows = []
    for submission in submissions:
        participant = submission.participant
        saved_answer_count = len(
            [
                answer
                for answer in submission.answers
                if answer.question_id in active_question_ids
            ]
        )
        submission_rows.append(
            {
                "submission_id": submission.id,
                "participant_id": participant.id,
                "identity_label": participant_labels.get(
                    participant.id,
                    "Anonymous" if participant.is_anonymous else "Named Participant",
                ),
                "shift_label": participant.shift_label or "Unspecified",
                "attempt_number": submission.attempt_number,
                "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                "saved_answer_count": saved_answer_count,
                "expected_answer_count": len(active_question_ids),
                "status": submission.status,
                "status_label": format_submission_status(submission.status),
                "is_excluded": submission.status == SUBMISSION_STATUS_EXCLUDED,
            }
        )

    submitted_participant_ids = {submission.participant_id for submission in submissions}
    return {
        "participant_count": len(participants),
        "submitted_participant_count": len(submitted_participant_ids),
        "submission_count": len(submissions),
        "active_question_count": len(active_question_ids),
        "participant_rows": participant_rows,
        "submission_rows": submission_rows,
        "last_updated_label": format_submission_timestamp(datetime.utcnow()),
    }


def build_submission_detail_view_model(submission: Submission) -> dict:
    ordered_answers = sorted(
        submission.answers,
        key=lambda item: (
            item.question.sort_order if item.question else 0,
            item.question_id,
        ),
    )
    question_rows = []
    for answer in ordered_answers:
        question = answer.question
        question_type = (
            question.question_type if question and question.question_type else DEFAULT_QUESTION_TYPE
        )
        question_view = {
            "question_type": question_type,
            "instructor_answer": question.instructor_answer or "" if question else "",
        }
        question_rows.append(
            {
                "question_id": answer.question_id,
                "prompt": question.prompt if question else f"Question #{answer.question_id}",
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question_type,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "answer_text": answer.answer_text,
                "feedback": score_question_answer(question_view, answer.answer_text),
                "instructor_answer": question.instructor_answer or "" if question else "",
            }
        )

    participant = submission.participant
    participant_labels = build_session_participant_label_map(submission.training_session)
    audit_rows = sorted(submission.audit_logs, key=lambda item: item.created_at, reverse=True)
    return {
        "submission_id": submission.id,
        "attempt_number": submission.attempt_number,
        "submitted_at_label": format_submission_timestamp(submission.submitted_at),
        "status": submission.status,
        "status_label": format_submission_status(submission.status),
        "participant_label": participant_labels.get(
            participant.id,
            "Anonymous" if participant.is_anonymous else "Named Participant",
        ),
        "shift_label": participant.shift_label or "Unspecified",
        "review_notes": submission.notes or "",
        "approved_at_label": format_submission_timestamp(submission.approved_at),
        "approved_by_label": format_user_identity(submission.approved_by),
        "question_rows": question_rows,
        "audit_rows": [
            {
                "action_label": format_audit_action(log.action),
                "created_at_label": format_submission_timestamp(log.created_at),
                "actor_label": format_user_identity(log.actor) or "System",
                "notes": log.notes or "",
            }
            for log in audit_rows
        ],
    }


def build_host_review_summary(question_rows: list[dict]) -> dict:
    return {
        "question_count": len(question_rows),
        "revealed_question_count": sum(
            question["revealed_answer_count"] for question in question_rows
        ),
        "noted_answer_count": sum(
            1
            for question in question_rows
            for answer in question["answer_rows"]
            if answer["has_notes"]
        ),
    }


def build_host_board_workspace_view_model(training_session: "TrainingSession") -> dict:
    join_url, join_url_warning = build_join_url_for_session(training_session)
    dashboard = build_session_dashboard_view_model(training_session)
    question_rows = build_session_question_review_view_model(training_session)
    return {
        "session_id": training_session.id,
        "title": training_session.title or f"Session #{training_session.id}",
        "join_code": training_session.join_code,
        "status": training_session.status,
        "scenario_title": training_session.scenario.title,
        "join_url": join_url,
        "join_url_warning": join_url_warning,
        "qr_image_url": get_qr_image_url(join_url),
        "dashboard": dashboard,
        "question_rows": question_rows,
        "review_summary": build_host_review_summary(question_rows),
        "revealed_answers": build_revealed_submission_view_model(training_session),
    }


def build_reports_index_view_model() -> list[dict]:
    training_sessions = TrainingSession.query.order_by(
        TrainingSession.created_at.desc(),
        TrainingSession.id.desc(),
    ).all()
    rows = []
    for training_session in training_sessions:
        approved_submissions = [
            submission
            for submission in training_session.submissions
            if submission.status == SUBMISSION_STATUS_APPROVED
        ]
        rows.append(
            {
                "session_id": training_session.id,
                "title": training_session.title or f"Session #{training_session.id}",
                "scenario_title": training_session.scenario.title,
                "join_code": training_session.join_code,
                "status": training_session.status,
                "participant_count": len(training_session.participants),
                "approved_submission_count": len(approved_submissions),
                "created_at_label": format_submission_timestamp(training_session.created_at),
            }
        )
    return rows


def build_session_report_view_model(training_session: TrainingSession) -> dict:
    active_questions = [
        question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    ]
    approved_submissions = [
        submission
        for submission in sorted(
            training_session.submissions,
            key=lambda item: item.submitted_at or datetime.min,
        )
        if submission.status == SUBMISSION_STATUS_APPROVED
    ]
    pending_review_count = len(
        [
            submission
            for submission in training_session.submissions
            if submission.status == SUBMISSION_STATUS_SUBMITTED
        ]
    )
    flagged_count = len(
        [
            submission
            for submission in training_session.submissions
            if submission.status == SUBMISSION_STATUS_FLAGGED
        ]
    )
    excluded_count = len(
        [
            submission
            for submission in training_session.submissions
            if submission.status == SUBMISSION_STATUS_EXCLUDED
        ]
    )

    shift_summary_map: dict[str, dict] = {}
    for participant in training_session.participants:
        shift_label = participant.shift_label or "Unspecified"
        shift_row = shift_summary_map.setdefault(
            shift_label,
            {
                "shift_label": shift_label,
                "participant_count": 0,
                "approved_submission_count": 0,
                "latest_submission_at_label": None,
            },
        )
        shift_row["participant_count"] += 1

    for submission in approved_submissions:
        shift_label = submission.participant.shift_label or "Unspecified"
        shift_row = shift_summary_map.setdefault(
            shift_label,
            {
                "shift_label": shift_label,
                "participant_count": 0,
                "approved_submission_count": 0,
                "latest_submission_at_label": None,
            },
        )
        shift_row["approved_submission_count"] += 1
        shift_row["latest_submission_at_label"] = format_submission_timestamp(submission.submitted_at)

    approved_answer_lookup = {
        submission.id: {answer.question_id: answer for answer in submission.answers}
        for submission in approved_submissions
    }

    question_rows = []
    for question in active_questions:
        answer_rows = []
        for submission in approved_submissions:
            answer = approved_answer_lookup[submission.id].get(question.id)
            if answer is None:
                continue
            answer_rows.append(
                {
                    "participant_label": (
                        "Anonymous"
                        if submission.participant.is_anonymous
                        else (submission.participant.display_name or "Named Participant")
                    ),
                    "shift_label": submission.participant.shift_label or "Unspecified",
                    "attempt_number": submission.attempt_number,
                    "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                    "answer_text": answer.answer_text,
                }
            )
        question_rows.append(
            {
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "answer_rows": answer_rows,
            }
        )

    approved_submission_rows = []
    for submission in approved_submissions:
        approved_submission_rows.append(
            {
                "participant_label": (
                    "Anonymous"
                    if submission.participant.is_anonymous
                    else (submission.participant.display_name or "Named Participant")
                ),
                "shift_label": submission.participant.shift_label or "Unspecified",
                "attempt_number": submission.attempt_number,
                "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                "approved_by_label": format_user_identity(submission.approved_by),
                "approved_at_label": format_submission_timestamp(submission.approved_at),
                "review_notes": submission.notes or "",
            }
        )

    return {
        "session_id": training_session.id,
        "title": training_session.title or f"Session #{training_session.id}",
        "scenario_title": training_session.scenario.title,
        "join_code": training_session.join_code,
        "status": training_session.status,
        "created_at_label": format_submission_timestamp(training_session.created_at),
        "participant_count": len(training_session.participants),
        "approved_submission_count": len(approved_submissions),
        "pending_review_count": pending_review_count,
        "flagged_count": flagged_count,
        "excluded_count": excluded_count,
        "shift_rows": sorted(shift_summary_map.values(), key=lambda item: item["shift_label"]),
        "question_rows": question_rows,
        "approved_submission_rows": approved_submission_rows,
    }


def build_session_report_csv(
    training_session: TrainingSession,
    shift_label: str | None = None,
) -> str:
    active_questions = {
        question.id: question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    }
    approved_submissions = [
        submission
        for submission in sorted(
            training_session.submissions,
            key=lambda item: item.submitted_at or datetime.min,
        )
        if submission.status == SUBMISSION_STATUS_APPROVED
        and (shift_label is None or (submission.participant.shift_label or "Unspecified") == shift_label)
    ]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "session_id",
            "session_title",
            "scenario_title",
            "join_code",
            "participant",
            "shift",
            "attempt_number",
            "submitted_at",
            "approved_by",
            "approved_at",
            "question_number",
            "question_type",
            "question_prompt",
            "answer_text",
            "review_notes",
        ]
    )

    for submission in approved_submissions:
        participant_label = (
            "Anonymous"
            if submission.participant.is_anonymous
            else (submission.participant.display_name or "Named Participant")
        )
        answers_by_question_id = {answer.question_id: answer for answer in submission.answers}
        for question_index, question in enumerate(active_questions.values(), start=1):
            answer = answers_by_question_id.get(question.id)
            if answer is None:
                continue
            writer.writerow(
                [
                    training_session.id,
                    training_session.title or f"Session #{training_session.id}",
                    training_session.scenario.title,
                    training_session.join_code,
                    participant_label,
                    submission.participant.shift_label or "Unspecified",
                    submission.attempt_number,
                    format_submission_timestamp(submission.submitted_at) or "",
                    format_user_identity(submission.approved_by) or "",
                    format_submission_timestamp(submission.approved_at) or "",
                    question_index,
                    QUESTION_TYPE_LABELS.get(
                        question.question_type or DEFAULT_QUESTION_TYPE,
                        QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                    ),
                    question.prompt,
                    answer.answer_text,
                    submission.notes or "",
                ]
            )

    return output.getvalue()


def make_report_filename(training_session: TrainingSession, shift_label: str | None = None) -> str:
    base_name = f"session_report_{training_session.id}"
    if not shift_label:
        return f"{base_name}.csv"
    safe_shift = re.sub(r"[^A-Za-z0-9]+", "_", shift_label).strip("_") or "shift"
    return f"{base_name}_{safe_shift}.csv"


def clear_all_revealed_answers(training_session: TrainingSession) -> None:
    for reveal in list(training_session.revealed_question_answers):
        db.session.delete(reveal)


def set_revealed_answer_for_question(
    training_session: TrainingSession,
    question: Question,
    submission_answer: SubmissionAnswer | None,
) -> None:
    if submission_answer is None:
        for reveal in list(training_session.revealed_question_answers):
            if reveal.question_id == question.id:
                db.session.delete(reveal)
        return

    already_revealed = any(
        reveal.submission_answer_id == submission_answer.id
        for reveal in training_session.revealed_question_answers
    )
    if not already_revealed:
        db.session.add(
            SessionQuestionReveal(
                training_session_id=training_session.id,
                question_id=question.id,
                submission_answer_id=submission_answer.id,
            )
        )


def remove_revealed_answer_for_question(
    training_session: TrainingSession,
    submission_answer: SubmissionAnswer,
) -> None:
    for reveal in list(training_session.revealed_question_answers):
        if reveal.submission_answer_id == submission_answer.id:
            db.session.delete(reveal)
            return


def clear_revealed_answers_for_submission(
    training_session: TrainingSession,
    submission: Submission,
) -> None:
    for reveal in list(training_session.revealed_question_answers):
        answer = reveal.submission_answer
        if answer is not None and answer.submission_id == submission.id:
            db.session.delete(reveal)



def can_view_revealed_answers_for_session(training_session_id: int) -> bool:
    if g.current_user.has_permission(PERM_VIEW_SESSION_SUBMISSIONS):
        return True
    return (
        g.active_training_session is not None
        and g.active_participant is not None
        and g.active_training_session.id == training_session_id
    )


def append_submission_audit_log(
    submission: Submission,
    actor: User | None,
    action: str,
    notes: str | None = None,
) -> None:
    db.session.add(
        SubmissionAuditLog(
            submission_id=submission.id,
            actor_user_id=actor.id if actor else None,
            action=action,
            notes=notes or None,
        )
    )


def update_submission_review_state(
    submission: Submission,
    action: str,
    actor: User | None,
    training_session: TrainingSession | None = None,
) -> None:
    if action == "save_notes":
        return
    if action == "exclude":
        submission.status = SUBMISSION_STATUS_EXCLUDED
        submission.approved_at = None
        submission.approved_by_user_id = None
        if training_session is not None:
            clear_revealed_answers_for_submission(training_session, submission)
        return
    if action == "reinstate":
        submission.status = SUBMISSION_STATUS_SUBMITTED
        submission.approved_at = None
        submission.approved_by_user_id = None
        return
    abort(400)


def render_create_scenario(error: str | None = None, status_code: int = 200):
    form_data = {
        "title": request.form.get("title", "").strip() if request.method == "POST" else "",
        "dispatch": request.form.get("dispatch", "").strip() if request.method == "POST" else "",
        "base_image_path": request.form.get("base_image_path", "").strip()
        if request.method == "POST"
        else "images/house1.jpg",
        "overlay_image_path": request.form.get("overlay_image_path", "").strip()
        if request.method == "POST"
        else "",
        "is_official": request.form.get("is_official") == "on"
        if request.method == "POST"
        else False,
    }
    if request.method == "POST":
        prompts = request.form.getlist("question_prompt")
        types = request.form.getlist("question_type")
        instructor_answers = request.form.getlist("instructor_answer")
        row_count = max(len(prompts), len(types), len(instructor_answers), 4)
        question_rows = []
        for idx in range(row_count):
            question_rows.append(
                {
                    "prompt": prompts[idx] if idx < len(prompts) else "",
                    "question_type": types[idx] if idx < len(types) else DEFAULT_QUESTION_TYPE,
                    "instructor_answer": instructor_answers[idx]
                    if idx < len(instructor_answers)
                    else "",
                }
            )
    else:
        question_rows = [
            {"prompt": "", "question_type": DEFAULT_QUESTION_TYPE, "instructor_answer": ""}
            for _ in range(4)
        ]
    return (
        render_template(
            "scenario_create.html",
            error=error,
            form_data=form_data,
            question_rows=question_rows,
            question_type_labels=QUESTION_TYPE_LABELS,
            default_question_type=DEFAULT_QUESTION_TYPE,
        ),
        status_code,
    )


def build_admin_overview() -> dict:
    from models import Scenario, Submission, TrainingSession, User
    total_users = User.query.count()
    active_users = User.query.filter_by(is_active=True).count()
    inactive_users = total_users - active_users
    pending_verification_users = User.query.filter_by(is_email_verified=False).count()
    total_scenarios = Scenario.query.count()
    approved_scenarios = Scenario.query.filter_by(status=SCENARIO_STATUS_APPROVED).count()
    archived_scenarios = Scenario.query.filter_by(status=SCENARIO_STATUS_ARCHIVED).count()
    official_scenarios = Scenario.query.filter_by(is_official=True).count()
    total_sessions = TrainingSession.query.count()
    active_sessions = TrainingSession.query.filter_by(status="active").count()
    archived_sessions = TrainingSession.query.filter_by(status="archived").count()
    total_submissions = Submission.query.count()
    flagged_submissions = Submission.query.filter_by(status=SUBMISSION_STATUS_FLAGGED).count()
    excluded_submissions = Submission.query.filter_by(status=SUBMISSION_STATUS_EXCLUDED).count()

    attention_items = []
    if inactive_users:
        attention_items.append(f"{inactive_users} inactive user account(s) should be reviewed.")
    if pending_verification_users:
        attention_items.append(f"{pending_verification_users} account(s) are still pending verification.")
    if archived_sessions:
        attention_items.append(f"{archived_sessions} archived session(s) remain in the training history.")
    if archived_scenarios:
        attention_items.append(f"{archived_scenarios} archived scenario(s) remain in the library history.")
    if flagged_submissions:
        attention_items.append(f"{flagged_submissions} submission(s) are still flagged for follow-up.")
    if excluded_submissions:
        attention_items.append(f"{excluded_submissions} submission(s) are currently excluded from reports.")

    return {
        "total_users": total_users,
        "active_users": active_users,
        "inactive_users": inactive_users,
        "pending_verification_users": pending_verification_users,
        "total_scenarios": total_scenarios,
        "approved_scenarios": approved_scenarios,
        "archived_scenarios": archived_scenarios,
        "official_scenarios": official_scenarios,
        "total_sessions": total_sessions,
        "active_sessions": active_sessions,
        "archived_sessions": archived_sessions,
        "total_submissions": total_submissions,
        "flagged_submissions": flagged_submissions,
        "excluded_submissions": excluded_submissions,
        "attention_items": attention_items,
    }


def build_pre_pilot_checklist() -> list[dict]:
    checklist = [
        {
            "label": "Secret key changed from default",
            "status": "pass" if current_app.config.get("SECRET_KEY") != "dev-secret-change-me" else "warning",
            "detail": "Use a unique SECRET_KEY before pilot or production use.",
        },
        {
            "label": "Debug mode disabled",
            "status": "pass" if not current_app.config.get("RUN_DEBUG") else "warning",
            "detail": "Set RUN_DEBUG=0 before pilot use.",
        },
        {
            "label": "Magic-link debug hidden",
            "status": "pass" if not current_app.config.get("ENABLE_MAGIC_LINK_DEBUG") else "warning",
            "detail": "Disable ENABLE_MAGIC_LINK_DEBUG when real users are signing in.",
        },
        {
            "label": "Activation-link debug hidden",
            "status": "pass" if not current_app.config.get("ENABLE_ACCOUNT_ACTIVATION_DEBUG") else "warning",
            "detail": "Disable ENABLE_ACCOUNT_ACTIVATION_DEBUG when real users are signing in.",
        },
        {
            "label": "Demo seeding disabled",
            "status": (
                "pass"
                if not current_app.config.get("ENABLE_DEMO_SEED_USERS")
                and not current_app.config.get("ENABLE_DEMO_SEED_SCENARIOS")
                else "warning"
            ),
            "detail": "Disable demo seed env vars once real users and content exist.",
        },
        {
            "label": "Public join URL configured",
            "status": "pass" if current_app.config.get("PUBLIC_BASE_URL") else "warning",
            "detail": "Set PUBLIC_BASE_URL when phones join from the LAN.",
        },
        {
            "label": "Alembic version table present",
            "status": "pass" if inspect(db.engine).has_table("alembic_version") else "warning",
            "detail": "The alembic_version table tracks applied Flask-Migrate schema upgrades.",
        },
    ]
    database_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    checklist.append(
        {
            "label": "Database location documented for backup",
            "status": "pass",
            "detail": f"Current DATABASE_URL: {database_url}",
        }
    )
    return checklist


def build_admin_recent_activity(limit: int = 12) -> list[dict]:
    from models import AdminAuditLog
    rows = (
        AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "action_label": format_audit_action(row.action),
            "target_label": row.target_label or row.target_type.replace("_", " ").title(),
            "details": row.details,
            "actor_label": format_user_identity(row.actor) or "System",
            "created_at_label": format_submission_timestamp(row.created_at),
        }
        for row in rows
    ]


def append_admin_audit_log(
    actor,
    action: str,
    target_type: str,
    target_id: int | None = None,
    target_label: str | None = None,
    details: str | None = None,
) -> None:
    from models import AdminAuditLog
    db.session.add(
        AdminAuditLog(
            actor_user_id=actor.id if actor else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_label=target_label,
            details=details or None,
        )
    )


def render_login(error: str | None, next_target: str):
    return render_template("login.html", error=error, next_target=next_target)


def render_admin_users(error: str | None = None, success: str | None = None, status_code: int = 200):
    users = User.query.order_by(User.email.asc()).all()
    roles = Role.query.order_by(Role.name.asc()).all()
    return (
        render_template(
            "admin_users.html",
            users=users,
            roles=roles,
            overview=build_admin_overview(),
            pre_pilot_checklist=build_pre_pilot_checklist(),
            recent_activity=build_admin_recent_activity(),
            error=error,
            success=success,
        ),
        status_code,
    )


def complete_user_sign_in(user: "User") -> None:
    previous_scenario_id = session.get("scenario_id")
    session.clear()
    if isinstance(previous_scenario_id, int):
        session["scenario_id"] = previous_scenario_id
    session["user_id"] = user.id
    rotate_csrf_token()


def render_create_account(
    error: str | None = None,
    success: str | None = None,
    activation_link: str | None = None,
    status_code: int = 200,
):
    form_data = {
        "full_name": request.form.get("full_name", "").strip() if request.method == "POST" else "",
        "email": request.form.get("email", "").strip().lower() if request.method == "POST" else "",
    }
    return (
        render_template(
            "create_account.html",
            error=error,
            success=success,
            activation_link=activation_link,
            form_data=form_data,
        ),
        status_code,
    )


def email_looks_valid(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(email))


def build_activation_link(token: str) -> str:
    return url_for("auth.activate_account", token=token, _external=True)


def issue_account_activation(user: "User") -> str | None:
    from models import AccountActivationToken
    AccountActivationToken.query.filter_by(user_id=user.id, used_at=None).update(
        {"used_at": datetime.utcnow()},
        synchronize_session=False,
    )
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.utcnow() + timedelta(hours=current_app.config["ACCOUNT_ACTIVATION_TTL_HOURS"])
    db.session.add(
        AccountActivationToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    if current_app.config["ENABLE_ACCOUNT_ACTIVATION_DEBUG"]:
        return build_activation_link(token)
    return None
