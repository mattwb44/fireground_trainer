import csv
import hashlib
import hmac
import io
import ipaddress
import os
import random
import re
import secrets
import socket
import time
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from authz import (
    CurrentUser,
    PERM_APPROVE_SCENARIOS,
    PERM_CREATE_SCENARIOS,
    PERM_CREATE_SESSIONS,
    PERM_EXPORT_REPORTS,
    PERM_MANAGE_USERS,
    PERM_REVEAL_INSTRUCTOR_ANSWERS,
    PERM_SELECT_REVIEW_ANSWER,
    PERM_SHARE_REVIEW_ANSWER,
    PERM_SUBMIT_ANSWERS,
    PERM_VIEW_SESSION_SUBMISSIONS,
    PERM_VIEW_REPORTS,
    PERM_VIEW_SCENARIOS,
    ROLE_ADMIN,
    ROLE_INSTRUCTOR,
    ROLE_PARTICIPANT,
    ROLE_TRAINING_CHIEF,
    normalize_roles,
    requires_permission,
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

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///fireground_trainer.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL", "").strip() or None,
    DEMO_BOOTSTRAP_PASSWORD=os.getenv("DEMO_BOOTSTRAP_PASSWORD", "EasyPass123"),
    ENABLE_MAGIC_LINK_DEBUG=os.getenv("ENABLE_MAGIC_LINK_DEBUG", "1") == "1",
    MAGIC_LINK_TTL_MINUTES=int(os.getenv("MAGIC_LINK_TTL_MINUTES", "15")),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
)
db.init_app(app)

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
    SUBMISSION_STATUS_SUBMITTED: "Pending Review",
    SUBMISSION_STATUS_APPROVED: "Approved For Reporting",
    SUBMISSION_STATUS_FLAGGED: "Flagged For Follow-Up",
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


def role_label(role_name: str) -> str:
    return ROLE_LABELS.get(role_name, role_name.replace("_", " ").title())


def ensure_legacy_schema_compatibility() -> None:
    with db.engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())

        if "users" in table_names:
            user_columns = {col["name"] for col in inspector.get_columns("users")}
            if "password_hash" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)"))
            if "is_email_verified" not in user_columns:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN is_email_verified BOOLEAN NOT NULL DEFAULT 0")
                )
            if "last_login_at" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME"))

        if "questions" in table_names:
            question_columns = {col["name"] for col in inspector.get_columns("questions")}
            if "instructor_answer" not in question_columns:
                conn.execute(text("ALTER TABLE questions ADD COLUMN instructor_answer TEXT"))
            if "question_type" not in question_columns:
                conn.execute(
                    text(
                        "ALTER TABLE questions ADD COLUMN question_type VARCHAR(40) "
                        "NOT NULL DEFAULT 'discussion_only'"
                    )
                )

        if "training_sessions" in table_names:
            session_columns = {col["name"] for col in inspector.get_columns("training_sessions")}
            if "revealed_submission_id" not in session_columns:
                conn.execute(
                    text("ALTER TABLE training_sessions ADD COLUMN revealed_submission_id INTEGER")
                )
            if "reveal_mode" not in session_columns:
                conn.execute(text("ALTER TABLE training_sessions ADD COLUMN reveal_mode VARCHAR(20)"))
            if "revealed_at" not in session_columns:
                conn.execute(text("ALTER TABLE training_sessions ADD COLUMN revealed_at DATETIME"))

        if "submissions" in table_names:
            submission_columns = {col["name"] for col in inspector.get_columns("submissions")}
            if "approved_at" not in submission_columns:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN approved_at DATETIME"))
            if "approved_by_user_id" not in submission_columns:
                conn.execute(text("ALTER TABLE submissions ADD COLUMN approved_by_user_id INTEGER"))

        if "scenarios" in table_names:
            scenario_columns = {col["name"] for col in inspector.get_columns("scenarios")}
            if "approved_by_user_id" not in scenario_columns:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN approved_by_user_id INTEGER"))
            if "status" not in scenario_columns:
                conn.execute(
                    text("ALTER TABLE scenarios ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft'")
                )
            if "submitted_at" not in scenario_columns:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN submitted_at DATETIME"))
            if "approved_at" not in scenario_columns:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN approved_at DATETIME"))
            if "archived_at" not in scenario_columns:
                conn.execute(text("ALTER TABLE scenarios ADD COLUMN archived_at DATETIME"))
            if "is_official" not in scenario_columns:
                conn.execute(
                    text("ALTER TABLE scenarios ADD COLUMN is_official BOOLEAN NOT NULL DEFAULT 0")
                )
            if "like_count" not in scenario_columns:
                conn.execute(
                    text("ALTER TABLE scenarios ADD COLUMN like_count INTEGER NOT NULL DEFAULT 0")
                )


def ensure_seed_data() -> None:
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

    default_password = app.config["DEMO_BOOTSTRAP_PASSWORD"]
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


def summarize_scenario_for_catalog(scenario: Scenario, vote_state: dict | None = None) -> dict:
    vote_state = vote_state or {}
    question_count = len([question for question in scenario.questions if question.is_active])
    dispatch_summary = " ".join((scenario.dispatch_text or "").split())
    return {
        "id": scenario.id,
        "title": scenario.title,
        "dispatch_summary": dispatch_summary[:180] + ("..." if len(dispatch_summary) > 180 else ""),
        "question_count": question_count,
        "updated_at": scenario.updated_at,
        "is_official": scenario.is_official,
        "creator_filter_key": scenario_creator_filter_key(scenario),
        "creator_filter_label": scenario_creator_filter_label(scenario),
        "status": scenario.status,
        "like_count": vote_state.get("like_count", scenario.like_count or 0),
        "can_like": vote_state.get("can_like", False),
        "has_liked": vote_state.get("has_liked", False),
        "eligibility_note": vote_state.get(
            "eligibility_note",
            "Complete a signed-in submission for this scenario before liking it.",
        ),
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
            "href": url_for("fireground_training"),
            "is_available": True,
        },
        {
            "key": CATEGORY_MVA,
            "label": CATEGORY_LABELS[CATEGORY_MVA],
            "description": (
                "Coming soon: stabilization, entrapment, highway command, and rescue benchmarks."
            ),
            "count_label": "Placeholder page",
            "href": url_for("mva_training"),
            "is_available": False,
        },
        {
            "key": CATEGORY_EMS,
            "label": CATEGORY_LABELS[CATEGORY_EMS],
            "description": (
                "Coming soon: patient assessment, airway, medical decision making, and team communication."
            ),
            "count_label": "Placeholder page",
            "href": url_for("ems_training"),
            "is_available": False,
        },
    ]


def build_training_category_page(
    category_key: str,
    selected_filter: str,
    db_user: User | None,
) -> dict:
    scenarios = load_category_scenarios(category_key, selected_filter)
    featured_source_scenarios = load_category_scenarios(category_key, CATEGORY_FILTER_ALL)
    vote_state_map = build_scenario_vote_state_map(featured_source_scenarios, db_user)
    filter_options = [
        {"key": key, "label": label}
        for key, label in CATEGORY_FILTER_LABELS.items()
    ]
    is_placeholder = category_key in {CATEGORY_MVA, CATEGORY_EMS}
    category_label = CATEGORY_LABELS[category_key]
    featured_scenarios = [
        summarize_scenario_for_catalog(scenario, vote_state_map.get(scenario.id))
        for scenario in featured_source_scenarios
        if (scenario.like_count or 0) > 0
    ][:3]
    return {
        "key": category_key,
        "label": category_label,
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
        "filter_options": filter_options,
        "featured_scenarios": featured_scenarios,
        "scenarios": [
            summarize_scenario_for_catalog(scenario, vote_state_map.get(scenario.id))
            for scenario in scenarios
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
    training_session: TrainingSession,
    error: str | None = None,
    status_code: int = 200,
):
    form_values = {
        "shift_label": request.form.get("shift_label", "").strip()
        if request.method == "POST"
        else "",
        "custom_shift_label": request.form.get("custom_shift_label", "").strip()
        if request.method == "POST"
        else "",
        "identity_mode": request.form.get("identity_mode", "anonymous")
        if request.method == "POST"
        else "anonymous",
        "display_name": request.form.get("display_name", "").strip()
        if request.method == "POST"
        else "",
    }
    return (
        render_template(
            "session_join_landing.html",
            training_session=training_session,
            error=error,
            form_values=form_values,
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
    join_path = url_for("join_by_code", join_code=training_session.join_code)
    configured_base_url = normalize_public_base_url(app.config.get("PUBLIC_BASE_URL"))
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
    if training_session.status != "active":
        return participant, training_session

    # Keep board locked to this session's scenario once joined.
    session["scenario_id"] = training_session.scenario_id
    return participant, training_session


def load_host_training_session() -> TrainingSession | None:
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
    if training_session is None:
        clear_host_training_session_context()
        return None

    set_host_training_session_context(training_session)
    return training_session


def safe_redirect_target(raw_target: str | None) -> str:
    if not raw_target:
        return url_for("home")

    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc:
        return url_for("home")
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return url_for("home")
    if parsed.path in POST_ONLY_PATHS:
        return url_for("home")
    return raw_target


def get_attempt_key(email: str) -> tuple[str, str]:
    forwarded = request.headers.get("X-Forwarded-For", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (request.remote_addr or "unknown")
    return (ip, email)


def is_login_rate_limited(email: str) -> bool:
    now = time.time()
    key = get_attempt_key(email)
    attempts = [ts for ts in LOGIN_ATTEMPTS.get(key, []) if (now - ts) < LOGIN_WINDOW_SECONDS]
    LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= LOGIN_ATTEMPT_LIMIT


def record_failed_login(email: str) -> None:
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


def validate_csrf_or_abort() -> None:
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


def format_submission_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M UTC")


def format_submission_status(status: str | None) -> str:
    return SUBMISSION_STATUS_LABELS.get(status or SUBMISSION_STATUS_SUBMITTED, "Pending Review")


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
    reveal_by_question_id = {
        reveal.question_id: reveal
        for reveal in training_session.revealed_question_answers
        if reveal.submission_answer is not None
    }

    question_rows: list[dict] = []
    for question in active_questions:
        answer_rows = []
        current_reveal = reveal_by_question_id.get(question.id)
        for submission in latest_submissions:
            answer = next(
                (
                    row
                    for row in submission.answers
                    if row.question_id == question.id
                ),
                None,
            )
            if answer is None:
                continue
            participant = submission.participant
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
                    "is_excluded": submission.status == SUBMISSION_STATUS_EXCLUDED,
                    "is_revealed": (
                        current_reveal is not None
                        and current_reveal.submission_answer_id == answer.id
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
                "answer_rows": answer_rows,
                "revealed_answer_count": 1 if current_reveal is not None else 0,
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
    reveal_by_question_id = {
        reveal.question_id: reveal
        for reveal in training_session.revealed_question_answers
        if reveal.submission_answer is not None
    }
    has_revealed_answers = bool(reveal_by_question_id)

    question_rows = []
    for question in active_questions:
        reveal = reveal_by_question_id.get(question.id)
        answer = reveal.submission_answer if reveal is not None else None
        submission = answer.submission if answer is not None else None
        participant = submission.participant if submission is not None else None
        question_rows.append(
            {
                "question_id": question.id,
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "is_revealed": answer is not None,
                "answer_text": answer.answer_text if answer is not None else "",
                "participant_label": (
                    participant_labels.get(participant.id)
                    if participant is not None
                    else None
                ),
                "shift_label": (
                    participant.shift_label or "Unspecified"
                    if participant is not None
                    else None
                ),
                "attempt_number": submission.attempt_number if submission is not None else None,
                "revealed_at_label": (
                    format_submission_timestamp(reveal.updated_at or reveal.created_at)
                    if reveal is not None
                    else None
                ),
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
    approved_submission_count = 0
    for submission in submissions:
        participant = submission.participant
        saved_answer_count = len(
            [
                answer
                for answer in submission.answers
                if answer.question_id in active_question_ids
            ]
        )
        if submission.status == SUBMISSION_STATUS_APPROVED:
            approved_submission_count += 1
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
                "approved_at_label": format_submission_timestamp(submission.approved_at),
                "approved_by_label": format_user_identity(submission.approved_by),
                "is_excluded": submission.status == SUBMISSION_STATUS_EXCLUDED,
            }
        )

    submitted_participant_ids = {submission.participant_id for submission in submissions}
    return {
        "participant_count": len(participants),
        "submitted_participant_count": len(submitted_participant_ids),
        "submission_count": len(submissions),
        "approved_submission_count": approved_submission_count,
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


def build_host_board_workspace_view_model(training_session: TrainingSession) -> dict:
    join_url, join_url_warning = build_join_url_for_session(training_session)
    dashboard = build_session_dashboard_view_model(training_session)
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
        "question_rows": build_session_question_review_view_model(training_session),
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
    training_session.revealed_submission = None
    training_session.revealed_submission_id = None
    training_session.reveal_mode = None
    training_session.revealed_at = None


def set_revealed_answer_for_question(
    training_session: TrainingSession,
    question: Question,
    submission_answer: SubmissionAnswer | None,
) -> None:
    existing_reveal = next(
        (
            reveal
            for reveal in training_session.revealed_question_answers
            if reveal.question_id == question.id
        ),
        None,
    )

    if submission_answer is None:
        if existing_reveal is not None:
            db.session.delete(existing_reveal)
        return

    if existing_reveal is None:
        db.session.add(
            SessionQuestionReveal(
                training_session_id=training_session.id,
                question_id=question.id,
                submission_answer_id=submission_answer.id,
            )
        )
    else:
        existing_reveal.submission_answer_id = submission_answer.id

    # Legacy whole-submission fields no longer represent the mixed reveal state.
    training_session.revealed_submission = None
    training_session.revealed_submission_id = None
    training_session.reveal_mode = None
    training_session.revealed_at = None


def clear_revealed_answers_for_submission(
    training_session: TrainingSession,
    submission: Submission,
) -> None:
    for reveal in list(training_session.revealed_question_answers):
        answer = reveal.submission_answer
        if answer is not None and answer.submission_id == submission.id:
            db.session.delete(reveal)


def set_revealed_submission(
    training_session: TrainingSession,
    submission: Submission | None,
    reveal_mode: str | None,
) -> None:
    clear_all_revealed_answers(training_session)
    if submission is None:
        return

    active_answers = [
        answer
        for answer in submission.answers
        if answer.question is not None and answer.question.is_active
    ]
    for answer in active_answers:
        set_revealed_answer_for_question(training_session, answer.question, answer)

    training_session.revealed_submission = submission
    training_session.revealed_submission_id = submission.id
    training_session.reveal_mode = reveal_mode
    training_session.revealed_at = datetime.utcnow()


def choose_random_submission(training_session: TrainingSession) -> Submission | None:
    submissions = [
        submission
        for submission in training_session.submissions
        if submission.status != SUBMISSION_STATUS_EXCLUDED
    ]
    if not submissions:
        return None

    current_revealed_id = training_session.revealed_submission_id
    candidates = [submission for submission in submissions if submission.id != current_revealed_id]
    if not candidates:
        candidates = submissions
    return random.choice(candidates)


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
    if action == "approve":
        submission.status = SUBMISSION_STATUS_APPROVED
        submission.approved_at = datetime.utcnow()
        submission.approved_by_user_id = actor.id if actor else None
        return
    if action == "reopen":
        submission.status = SUBMISSION_STATUS_SUBMITTED
        submission.approved_at = None
        submission.approved_by_user_id = None
        return
    if action == "flag":
        submission.status = SUBMISSION_STATUS_FLAGGED
        submission.approved_at = None
        submission.approved_by_user_id = None
        return
    if action == "exclude":
        submission.status = SUBMISSION_STATUS_EXCLUDED
        submission.approved_at = None
        submission.approved_by_user_id = None
        if training_session is not None:
            clear_revealed_answers_for_submission(training_session, submission)
            if training_session.revealed_submission_id == submission.id:
                set_revealed_submission(training_session, None, reveal_mode=None)
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
            error=error,
            success=success,
        ),
        status_code,
    )


@app.before_request
def load_current_user():
    g.current_user = get_current_user()
    g.active_participant, g.active_training_session = load_active_participant_session()


@app.context_processor
def inject_template_context():
    return {
        "current_user": g.current_user,
        "active_participant": g.active_participant,
        "active_training_session": g.active_training_session,
        "site_name": SITE_NAME,
        "training_categories": [
            {"key": CATEGORY_FIREGROUND, "label": CATEGORY_LABELS[CATEGORY_FIREGROUND], "href": url_for("fireground_training")},
            {"key": CATEGORY_MVA, "label": CATEGORY_LABELS[CATEGORY_MVA], "href": url_for("mva_training")},
            {"key": CATEGORY_EMS, "label": CATEGORY_LABELS[CATEGORY_EMS], "href": url_for("ems_training")},
        ],
        "shift_options": SHIFT_OPTIONS,
        "role_label": role_label,
        "permission_keys": PERMISSION_KEYS,
        "question_type_labels": QUESTION_TYPE_LABELS,
        "csrf_token": issue_csrf_token,
    }


@app.get("/create-account")
def create_account_page():
    return render_template("create_account.html")


@app.get("/login")
def login_page():
    next_target = safe_redirect_target(request.args.get("next"))
    if g.current_user.user_id != "guest":
        return redirect(next_target)
    return render_login(error=None, next_target=next_target)


@app.post("/login")
def login_submit():
    validate_csrf_or_abort()
    next_target = safe_redirect_target(request.form.get("next"))
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        return render_login(error="Email and password are required.", next_target=next_target), 400
    if is_login_rate_limited(email):
        return (
            render_login(error="Too many attempts. Wait 10 minutes and try again.", next_target=next_target),
            429,
        )

    user = User.query.filter_by(email=email).first()
    if not user or not user.is_active or not user.password_hash:
        record_failed_login(email)
        return render_login(error="Invalid credentials.", next_target=next_target), 401
    if not check_password_hash(user.password_hash, password):
        record_failed_login(email)
        return render_login(error="Invalid credentials.", next_target=next_target), 401
    if not user_is_staff(user):
        record_failed_login(email)
        return render_login(error="This account cannot access instructor/admin login.", next_target=next_target), 403

    clear_failed_logins(email)
    clear_participant_session_context()
    clear_host_training_session_context()
    user.last_login_at = datetime.utcnow()
    session["user_id"] = user.id
    rotate_csrf_token()
    db.session.commit()
    return redirect(next_target)


@app.post("/logout")
def logout():
    validate_csrf_or_abort()
    session.pop("user_id", None)
    clear_participant_session_context()
    clear_host_training_session_context()
    rotate_csrf_token()
    return redirect(url_for("home"))


@app.get("/magic-link/request")
def request_magic_link_page():
    return render_template("request_magic_link.html", error=None)


@app.post("/magic-link/request")
def request_magic_link_submit():
    validate_csrf_or_abort()
    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template("request_magic_link.html", error="Email is required."), 400

    magic_link = None
    user = User.query.filter_by(email=email, is_active=True).first()
    if user and user_is_staff(user):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        ttl_minutes = app.config["MAGIC_LINK_TTL_MINUTES"]
        expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        db.session.add(
            MagicLoginToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        db.session.commit()
        if app.config["ENABLE_MAGIC_LINK_DEBUG"]:
            magic_link = url_for("consume_magic_link", token=token, _external=True)

    return render_template(
        "magic_link_sent.html",
        requested_email=email,
        magic_link=magic_link,
    )


@app.get("/magic-link/consume")
def consume_magic_link():
    token = request.args.get("token", "")
    if not token:
        abort(400)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.utcnow()
    token_row = (
        MagicLoginToken.query.filter_by(token_hash=token_hash)
        .filter(MagicLoginToken.used_at.is_(None))
        .filter(MagicLoginToken.expires_at >= now)
        .order_by(MagicLoginToken.created_at.desc())
        .first()
    )
    if token_row is None or token_row.user is None or not token_row.user.is_active:
        return render_login(error="Magic link is invalid or expired.", next_target=url_for("board")), 400
    if not user_is_staff(token_row.user):
        return render_login(error="This account cannot access instructor/admin login.", next_target=url_for("board")), 403

    token_row.used_at = now
    token_row.user.last_login_at = now
    clear_host_training_session_context()
    session["user_id"] = token_row.user_id
    rotate_csrf_token()
    db.session.commit()
    return redirect(url_for("board"))


@app.get("/")
def home():
    return render_template(
        "home.html",
        category_cards=build_home_category_cards(),
    )


@app.get("/board")
@requires_permission(PERM_VIEW_SCENARIOS)
def board():
    host_training_session = load_host_training_session()
    if host_training_session is not None:
        session["scenario_id"] = host_training_session.scenario_id
    _scenario_row, scenario = get_current_scenario()
    db_user = get_current_db_user()
    revealed_submission = (
        build_revealed_submission_view_model(g.active_training_session)
        if g.active_training_session is not None
        else None
    )
    return render_template(
        "scenario.html",
        scenario=scenario,
        answers={},
        question_feedback={},
        submitted=False,
        submission_error=None,
        submission_message=None,
        saved_submission=None,
        scenario_vote=build_scenario_vote_summary(_scenario_row, db_user),
        host_workspace=(
            build_host_board_workspace_view_model(host_training_session)
            if host_training_session is not None
            else None
        ),
        revealed_submission=revealed_submission,
    )


@app.get("/training/fireground")
def fireground_training():
    selected_filter = resolve_category_filter(request.args.get("filter"))
    return render_template(
        "training_category.html",
        category=build_training_category_page(
            CATEGORY_FIREGROUND,
            selected_filter,
            get_current_db_user(),
        ),
    )


@app.get("/training/mva")
def mva_training():
    return render_template(
        "training_category.html",
        category=build_training_category_page(
            CATEGORY_MVA,
            CATEGORY_FILTER_ALL,
            get_current_db_user(),
        ),
    )


@app.get("/training/ems")
def ems_training():
    return render_template(
        "training_category.html",
        category=build_training_category_page(
            CATEGORY_EMS,
            CATEGORY_FILTER_ALL,
            get_current_db_user(),
        ),
    )


@app.get("/scenarios")
@requires_permission(PERM_VIEW_SCENARIOS)
def scenario_library():
    db_user = get_current_db_user()
    selected_tab = resolve_library_tab(request.args.get("tab"), g.current_user)
    scenarios = load_library_scenarios(selected_tab, g.current_user, db_user)
    vote_state_map = build_scenario_vote_state_map(scenarios, db_user)
    scenario_summaries = [
        summarize_scenario_for_library(scenario, vote_state_map.get(scenario.id))
        for scenario in scenarios
    ]
    tabs = [
        {"key": tab_key, "label": LIBRARY_TAB_LABELS[tab_key]}
        for tab_key in allowed_library_tabs_for_user(g.current_user)
    ]
    return render_template(
        "scenario_library.html",
        tabs=tabs,
        selected_tab=selected_tab,
        scenarios=scenario_summaries,
        can_create_scenarios=g.current_user.has_permission(PERM_CREATE_SCENARIOS),
        can_manage_official=g.current_user.has_permission(PERM_APPROVE_SCENARIOS),
    )


@app.post("/scenarios/vote")
@requires_permission(PERM_VIEW_SCENARIOS)
def vote_for_scenario():
    validate_csrf_or_abort()
    raw_scenario_id = request.form.get("scenario_id", "").strip()
    vote_action = get_scenario_vote_action(request.form.get("vote_action", "").strip())
    if not raw_scenario_id.isdigit() or vote_action is None:
        abort(400)

    scenario = Scenario.query.filter_by(id=int(raw_scenario_id), is_active=True).first()
    if scenario is None:
        abort(404)

    visible_scenario_ids = {row.id for row in load_visible_scenarios_for_user(g.current_user)}
    if scenario.id not in visible_scenario_ids:
        abort(403)

    next_target = safe_redirect_target(request.form.get("next"))
    db_user = get_current_db_user()
    if db_user is None:
        flash("Sign in with your account and complete this scenario once before liking it.", "warning")
        return redirect(next_target)
    if not user_can_like_scenario(scenario, db_user):
        flash("Only users with a completed signed-in submission for this scenario can like it.", "warning")
        return redirect(next_target)

    try:
        set_scenario_like_vote(scenario, db_user, vote_action)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Your vote could not be saved. Please try again.", "warning")
        return redirect(next_target)

    if vote_action == "like":
        flash("Scenario liked. Popularity totals updated.", "success")
    else:
        flash("Your like was removed.", "success")
    return redirect(next_target)


@app.post("/scenarios/select")
@requires_permission(PERM_VIEW_SCENARIOS)
def select_scenario():
    validate_csrf_or_abort()
    raw_scenario_id = request.form.get("scenario_id", "").strip()
    if not raw_scenario_id.isdigit():
        abort(400)

    scenario_id = int(raw_scenario_id)
    visible_scenarios = load_visible_scenarios_for_user(g.current_user)
    if scenario_id not in {scenario.id for scenario in visible_scenarios}:
        abort(403)

    session["scenario_id"] = scenario_id
    clear_host_training_session_context()
    next_target = safe_redirect_target(request.form.get("next"))
    return redirect(next_target)


@app.get("/scenarios/new")
@requires_permission(PERM_CREATE_SCENARIOS)
def new_scenario_page():
    return render_create_scenario()


@app.post("/scenarios/new")
@requires_permission(PERM_CREATE_SCENARIOS)
def create_scenario():
    validate_csrf_or_abort()
    title = request.form.get("title", "").strip()
    dispatch = request.form.get("dispatch", "").strip()
    base_image_path = request.form.get("base_image_path", "").strip()
    overlay_image_path = request.form.get("overlay_image_path", "").strip() or None
    is_official = request.form.get("is_official") == "on"

    if not title:
        return render_create_scenario(error="Scenario title is required.", status_code=400)
    if not dispatch:
        return render_create_scenario(error="Dispatch text is required.", status_code=400)
    if not base_image_path:
        return render_create_scenario(error="Base image path is required.", status_code=400)

    questions, question_error = parse_create_scenario_questions()
    if question_error:
        return render_create_scenario(error=question_error, status_code=400)

    current_db_user = get_current_db_user()
    scenario = Scenario(
        title=title,
        dispatch_text=dispatch,
        base_image_path=base_image_path,
        overlay_image_path=overlay_image_path,
        created_by_user_id=current_db_user.id if current_db_user else None,
        status=SCENARIO_STATUS_DRAFT,
        is_official=is_official and g.current_user.has_permission(PERM_APPROVE_SCENARIOS),
        is_active=True,
    )
    db.session.add(scenario)
    db.session.flush()

    for index, question in enumerate(questions, start=1):
        db.session.add(
            Question(
                scenario_id=scenario.id,
                question_key=f"q{index}",
                prompt=question["prompt"],
                question_type=question["question_type"],
                instructor_answer=question["instructor_answer"],
                sort_order=index,
                is_active=True,
            )
        )

    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("board"))


@app.get("/sessions/new")
@requires_permission(PERM_CREATE_SESSIONS)
def new_training_session_page():
    scenarios = load_visible_scenarios_for_user(g.current_user)
    if not scenarios:
        return render_create_session(
            scenarios=[],
            selected_scenario_id=None,
            error="No available scenarios to start a session.",
            status_code=400,
        )

    requested_id = request.args.get("scenario_id", "").strip()
    selected_scenario_id = session.get("scenario_id")
    if requested_id.isdigit():
        selected_scenario_id = int(requested_id)
    if not isinstance(selected_scenario_id, int) or not can_use_scenario_for_session(
        selected_scenario_id, g.current_user
    ):
        selected_scenario_id = scenarios[0].id

    return render_create_session(
        scenarios=scenarios,
        selected_scenario_id=selected_scenario_id,
    )


@app.post("/sessions/new")
@requires_permission(PERM_CREATE_SESSIONS)
def create_training_session():
    validate_csrf_or_abort()
    scenarios = load_visible_scenarios_for_user(g.current_user)
    available_ids = {scenario.id for scenario in scenarios if scenario.status != SCENARIO_STATUS_ARCHIVED}
    if not available_ids:
        return render_create_session(
            scenarios=[],
            selected_scenario_id=None,
            error="No available scenarios to start a session.",
            status_code=400,
        )

    raw_scenario_id = request.form.get("scenario_id", "").strip()
    if not raw_scenario_id.isdigit():
        return render_create_session(
            scenarios=scenarios,
            selected_scenario_id=None,
            error="Please select a scenario.",
            status_code=400,
        )
    scenario_id = int(raw_scenario_id)
    if scenario_id not in available_ids:
        return render_create_session(
            scenarios=scenarios,
            selected_scenario_id=scenario_id,
            error="Selected scenario is not available for session creation.",
            status_code=403,
        )

    current_db_user = get_current_db_user()
    title = request.form.get("title", "").strip()
    session_row = TrainingSession(
        scenario_id=scenario_id,
        created_by_user_id=current_db_user.id if current_db_user else None,
        join_code=generate_join_code(),
        title=title or None,
        status="active",
        starts_at=datetime.utcnow(),
    )
    db.session.add(session_row)
    db.session.commit()
    set_host_training_session_context(session_row)
    return redirect(url_for("board", session_id=session_row.id))


@app.get("/sessions/<int:session_id>")
@requires_permission(PERM_CREATE_SESSIONS)
def training_session_detail(session_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)
    set_host_training_session_context(session_row)
    join_url, join_url_warning = build_join_url_for_session(session_row)
    qr_image_url = get_qr_image_url(join_url)
    dashboard = build_session_dashboard_view_model(session_row)
    return render_template(
        "session_detail.html",
        training_session=session_row,
        join_url=join_url,
        join_url_warning=join_url_warning,
        qr_image_url=qr_image_url,
        dashboard=dashboard,
    )


@app.get("/sessions/<int:session_id>/submissions")
@requires_permission(PERM_VIEW_SESSION_SUBMISSIONS)
def training_session_submissions_partial(session_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)
    dashboard = build_session_dashboard_view_model(session_row)
    return render_template(
        "session_submissions_partial.html",
        training_session=session_row,
        dashboard=dashboard,
    )


@app.get("/sessions/<int:session_id>/workspace")
@requires_permission(PERM_VIEW_SESSION_SUBMISSIONS)
def training_session_workspace_partial(session_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)
    return render_template(
        "session_board_workspace_partial.html",
        workspace=build_host_board_workspace_view_model(session_row),
    )


@app.post("/sessions/<int:session_id>/reveal")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def reveal_submission_for_session(session_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    raw_submission_id = request.form.get("submission_id", "").strip()
    if not raw_submission_id.isdigit():
        abort(400)

    submission = Submission.query.filter_by(
        id=int(raw_submission_id),
        training_session_id=session_row.id,
    ).first()
    if submission is None:
        abort(404)
    if submission.status == SUBMISSION_STATUS_EXCLUDED:
        abort(409)

    set_revealed_submission(session_row, submission, reveal_mode="manual")
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("training_session_detail", session_id=session_row.id))


@app.post("/sessions/<int:session_id>/reveal-random")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def reveal_random_submission_for_session(session_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    submission = choose_random_submission(session_row)
    if submission is None:
        next_target = request.form.get("next")
        if next_target:
            return redirect(safe_redirect_target(next_target))
        return redirect(url_for("training_session_detail", session_id=session_row.id))

    set_revealed_submission(session_row, submission, reveal_mode="random")
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("training_session_detail", session_id=session_row.id))


@app.post("/sessions/<int:session_id>/reveal-clear")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def clear_revealed_submission_for_session(session_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    set_revealed_submission(session_row, None, reveal_mode=None)
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("training_session_detail", session_id=session_row.id))


@app.post("/sessions/<int:session_id>/questions/<int:question_id>/reveal")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def reveal_question_answer_for_session(session_id: int, question_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    raw_submission_answer_id = request.form.get("submission_answer_id", "").strip()
    if not raw_submission_answer_id.isdigit():
        abort(400)

    question = Question.query.filter_by(id=question_id, scenario_id=session_row.scenario_id).first()
    if question is None:
        abort(404)

    submission_answer = SubmissionAnswer.query.filter_by(id=int(raw_submission_answer_id)).first()
    if submission_answer is None:
        abort(404)
    if submission_answer.question_id != question.id:
        abort(409)
    if submission_answer.submission.training_session_id != session_row.id:
        abort(409)
    if submission_answer.submission.status == SUBMISSION_STATUS_EXCLUDED:
        abort(409)

    set_revealed_answer_for_question(session_row, question, submission_answer)
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("board", session_id=session_row.id))


@app.post("/sessions/<int:session_id>/questions/<int:question_id>/reveal-clear")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def clear_revealed_question_answer_for_session(session_id: int, question_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    question = Question.query.filter_by(id=question_id, scenario_id=session_row.scenario_id).first()
    if question is None:
        abort(404)

    set_revealed_answer_for_question(session_row, question, None)
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("board", session_id=session_row.id))


@app.post("/sessions/<int:session_id>/revealed-answers/clear")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def clear_all_revealed_answers_for_session(session_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    clear_all_revealed_answers(session_row)
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("board", session_id=session_row.id))


@app.get("/sessions/<int:session_id>/submissions/<int:submission_id>")
@requires_permission(PERM_VIEW_SESSION_SUBMISSIONS)
def training_session_submission_detail(session_id: int, submission_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    submission = Submission.query.filter_by(
        id=submission_id,
        training_session_id=session_row.id,
    ).first()
    if submission is None:
        abort(404)

    return render_template(
        "submission_detail.html",
        training_session=session_row,
        submission=build_submission_detail_view_model(submission),
    )


@app.post("/sessions/<int:session_id>/submissions/<int:submission_id>/review")
@requires_permission(PERM_SELECT_REVIEW_ANSWER)
def review_submission_for_session(session_id: int, submission_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    submission = Submission.query.filter_by(
        id=submission_id,
        training_session_id=session_row.id,
    ).first()
    if submission is None:
        abort(404)

    review_notes = request.form.get("review_notes", "").strip()
    action = request.form.get("review_action", "").strip()
    submission.notes = review_notes[:2000] or None
    actor = get_current_db_user()
    update_submission_review_state(
        submission=submission,
        action=action,
        actor=actor,
        training_session=session_row,
    )
    append_submission_audit_log(
        submission=submission,
        actor=actor,
        action=action,
        notes=submission.notes,
    )
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(
        url_for("training_session_submission_detail", session_id=session_row.id, submission_id=submission.id)
    )


@app.get("/sessions/<int:session_id>/revealed-answer")
def training_session_revealed_answer_partial(session_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)
    if not can_view_revealed_answers_for_session(session_row.id):
        abort(403)

    return render_template(
        "revealed_submission_partial.html",
        training_session=session_row,
        revealed_submission=build_revealed_submission_view_model(session_row),
    )


@app.get("/new")
@requires_permission(PERM_VIEW_SCENARIOS)
def new_scenario():
    visible_scenarios = load_visible_scenarios_for_user(g.current_user)
    if not visible_scenarios:
        abort(404)

    current = session.get("scenario_id")
    visible_ids = [scenario.id for scenario in visible_scenarios]
    if len(visible_ids) == 1:
        session["scenario_id"] = visible_ids[0]
    else:
        choices = [scenario_id for scenario_id in visible_ids if scenario_id != current]
        session["scenario_id"] = random.choice(choices)
    return redirect(url_for("board"))


@app.post("/submit")
@requires_permission(PERM_SUBMIT_ANSWERS)
def submit():
    validate_csrf_or_abort()
    scenario_row, scenario = get_current_scenario()
    db_user = get_current_db_user()
    answers = {
        str(question["id"]): request.form.get(f"q_{question['id']}", "").strip()
        for question in scenario["questions"]
    }
    question_feedback = build_submission_feedback(scenario, answers)
    participant, training_session, submission_error = validate_submission_context(scenario_row)
    saved_submission = None
    submission_message = None

    if submission_error is None and participant is not None and training_session is not None:
        saved_submission, submission_error = persist_submission(
            scenario_row=scenario_row,
            scenario=scenario,
            participant=participant,
            training_session=training_session,
            answers=answers,
        )
        if saved_submission is not None:
            submission_message = (
                f"Submitted and saved as attempt #{saved_submission.attempt_number} for this session."
            )

    return render_template(
        "scenario.html",
        scenario=scenario,
        answers=answers,
        question_feedback=question_feedback,
        submitted=True,
        submission_error=submission_error,
        submission_message=submission_message,
        saved_submission=saved_submission,
        scenario_vote=build_scenario_vote_summary(scenario_row, db_user),
        revealed_submission=(
            build_revealed_submission_view_model(g.active_training_session)
            if g.active_training_session is not None
            else None
        ),
    )


@app.post("/scenario/submit-review")
@requires_permission(PERM_CREATE_SCENARIOS)
def submit_scenario_for_review():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    apply_scenario_transition_or_abort(
        scenario=scenario,
        action="submit_for_review",
        actor=get_current_db_user(),
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("board"))


@app.post("/scenario/approve")
@requires_permission(PERM_APPROVE_SCENARIOS)
def approve_scenario():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    apply_scenario_transition_or_abort(
        scenario=scenario,
        action="approve",
        actor=get_current_db_user(),
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("board"))


@app.post("/scenario/archive")
@requires_permission(PERM_APPROVE_SCENARIOS)
def archive_scenario():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    apply_scenario_transition_or_abort(
        scenario=scenario,
        action="archive",
        actor=get_current_db_user(),
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("board"))


@app.post("/scenario/official")
@requires_permission(PERM_APPROVE_SCENARIOS)
def set_scenario_official_status():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    action = request.form.get("official_action", "").strip()

    if action == "make":
        if scenario.status != SCENARIO_STATUS_APPROVED:
            abort(409)
        scenario.is_official = True
    elif action == "remove":
        scenario.is_official = False
    else:
        abort(400)

    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(safe_redirect_target(request.form.get("next")))


@app.route("/join/<join_code>", methods=["GET", "POST"])
def join_by_code(join_code: str):
    training_session = TrainingSession.query.filter_by(join_code=join_code.upper()).first()
    if training_session is None:
        abort(404)

    existing_participant = get_joined_participant_for_session(training_session.id)
    if existing_participant is not None and request.method == "GET":
        set_active_participant_context(existing_participant)
        return redirect(url_for("board"))

    if request.method == "GET":
        return render_join_page(training_session=training_session)

    validate_csrf_or_abort()
    if training_session.status != "active":
        return render_join_page(
            training_session=training_session,
            error="This session is not active.",
            status_code=409,
        )

    if existing_participant is not None:
        set_active_participant_context(existing_participant)
        return redirect(url_for("board"))

    shift_label = request.form.get("shift_label", "").strip()
    custom_shift_label = request.form.get("custom_shift_label", "").strip()
    if not shift_label:
        return render_join_page(
            training_session=training_session,
            error="Please select a shift.",
            status_code=400,
        )
    if shift_label not in SHIFT_OPTIONS:
        return render_join_page(
            training_session=training_session,
            error="Invalid shift option.",
            status_code=400,
        )
    if shift_label == "Other":
        if not custom_shift_label:
            return render_join_page(
                training_session=training_session,
                error="Enter your shift label when selecting Other.",
                status_code=400,
            )
        shift_value = custom_shift_label[:50]
    else:
        shift_value = shift_label

    identity_mode = request.form.get("identity_mode", "").strip()
    if identity_mode not in {"anonymous", "named"}:
        return render_join_page(
            training_session=training_session,
            error="Please choose anonymous or named join.",
            status_code=400,
        )

    display_name = request.form.get("display_name", "").strip()
    is_anonymous = identity_mode == "anonymous"
    if is_anonymous:
        display_name = None
    elif not display_name:
        return render_join_page(
            training_session=training_session,
            error="Display name is required when joining with name.",
            status_code=400,
        )
    else:
        display_name = display_name[:120]

    current_db_user = get_current_db_user()
    participant = Participant(
        training_session_id=training_session.id,
        user_id=current_db_user.id if current_db_user else None,
        display_name=display_name,
        shift_label=shift_value,
        is_anonymous=is_anonymous,
    )
    db.session.add(participant)
    db.session.commit()

    set_active_participant_context(participant)
    return redirect(url_for("board"))


@app.get("/reports")
@requires_permission(PERM_VIEW_REPORTS)
def reports():
    return render_template(
        "reports.html",
        report_sessions=build_reports_index_view_model(),
    )


@app.get("/reports/sessions/<int:session_id>")
@requires_permission(PERM_VIEW_REPORTS)
def session_report(session_id: int):
    training_session = TrainingSession.query.filter_by(id=session_id).first()
    if training_session is None:
        abort(404)
    return render_template(
        "session_report.html",
        report=build_session_report_view_model(training_session),
    )


@app.get("/reports/sessions/<int:session_id>/export.csv")
@requires_permission(PERM_EXPORT_REPORTS)
def export_session_report_csv(session_id: int):
    training_session = TrainingSession.query.filter_by(id=session_id).first()
    if training_session is None:
        abort(404)

    shift_label = request.args.get("shift", "").strip() or None
    if shift_label is not None:
        available_shift_labels = {
            participant.shift_label or "Unspecified"
            for participant in training_session.participants
        }
        if shift_label not in available_shift_labels:
            abort(404)

    csv_content = build_session_report_csv(training_session, shift_label=shift_label)
    filename = make_report_filename(training_session, shift_label=shift_label)
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@app.get("/admin/users")
@requires_permission(PERM_MANAGE_USERS)
def admin_users():
    return render_admin_users()


@app.post("/admin/users/create")
@requires_permission(PERM_MANAGE_USERS)
def admin_create_user():
    validate_csrf_or_abort()
    email = request.form.get("email", "").strip().lower()
    full_name = request.form.get("full_name", "").strip() or None
    password = request.form.get("password", "")
    is_active = request.form.get("is_active") == "on"

    if not email:
        return render_admin_users(error="Email is required.", status_code=400)
    if len(password) < 8:
        return render_admin_users(error="Password must be at least 8 characters.", status_code=400)
    if User.query.filter_by(email=email).first():
        return render_admin_users(error="A user with that email already exists.", status_code=409)

    all_roles = {role.name: role for role in Role.query.all()}
    selected_role_names = normalize_roles(request.form.getlist("roles"))
    selected_role_names = frozenset(role for role in selected_role_names if role in all_roles)
    if not selected_role_names:
        selected_role_names = frozenset({ROLE_PARTICIPANT})

    user = User(
        email=email,
        full_name=full_name,
        password_hash=generate_password_hash(password),
        is_active=is_active,
        is_email_verified=True,
    )
    db.session.add(user)
    db.session.flush()

    for role_name in selected_role_names:
        db.session.add(UserRole(user_id=user.id, role_id=all_roles[role_name].id))

    db.session.commit()
    return render_admin_users(success=f"Created user {email}.")


@app.post("/admin/users/<int:user_id>/update")
@requires_permission(PERM_MANAGE_USERS)
def admin_update_user(user_id: int):
    validate_csrf_or_abort()
    user = User.query.filter_by(id=user_id).first()
    if user is None:
        return render_admin_users(error="User not found.", status_code=404)

    all_roles = {role.name: role for role in Role.query.all()}
    selected_role_names = normalize_roles(request.form.getlist("roles"))
    selected_role_names = frozenset(role for role in selected_role_names if role in all_roles)
    if not selected_role_names:
        selected_role_names = frozenset({ROLE_PARTICIPANT})

    target_role_ids = {all_roles[role_name].id for role_name in selected_role_names}
    current_role_ids = {link.role_id for link in user.role_links}
    for link in list(user.role_links):
        if link.role_id not in target_role_ids:
            db.session.delete(link)
    for role_id in target_role_ids.difference(current_role_ids):
        db.session.add(UserRole(user_id=user.id, role_id=role_id))

    user.full_name = request.form.get("full_name", "").strip() or None
    user.is_active = request.form.get("is_active") == "on"
    db.session.commit()
    return redirect(url_for("admin_users"))


@app.post("/admin/users/<int:user_id>/reset-password")
@requires_permission(PERM_MANAGE_USERS)
def admin_reset_user_password(user_id: int):
    validate_csrf_or_abort()
    user = User.query.filter_by(id=user_id).first()
    if user is None:
        return render_admin_users(error="User not found.", status_code=404)

    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        return render_admin_users(error="New password must be at least 8 characters.", status_code=400)

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    return redirect(url_for("admin_users"))


@app.get("/offline")
def offline():
    return render_template("offline.html")


@app.errorhandler(400)
def bad_request(_err):
    return "<h1>Bad Request</h1><p>Your request could not be processed.</p>", 400


@app.errorhandler(403)
def forbidden(_err):
    return render_template("forbidden.html"), 403


@app.errorhandler(409)
def conflict(_err):
    return "<h1>Conflict</h1><p>This scenario transition is not valid for its current status.</p>", 409


with app.app_context():
    db.create_all()
    ensure_legacy_schema_compatibility()
    ensure_seed_data()
    ensure_seed_scenarios()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
