import hashlib
import hmac
import os
import random
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import quote, urlsplit

from flask import Flask, abort, g, redirect, render_template, request, session, url_for
from sqlalchemy import inspect, text
from werkzeug.security import check_password_hash, generate_password_hash

from authz import (
    CurrentUser,
    PERM_APPROVE_SCENARIOS,
    PERM_CREATE_SCENARIOS,
    PERM_CREATE_SESSIONS,
    PERM_EXPORT_REPORTS,
    PERM_MANAGE_USERS,
    PERM_REVEAL_INSTRUCTOR_ANSWERS,
    PERM_SUBMIT_ANSWERS,
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
from models import MagicLoginToken, Question, Role, Scenario, TrainingSession, User, UserRole

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-secret-change-me"),
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///fireground_trainer.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
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


def summarize_scenario_for_library(scenario: Scenario) -> dict:
    return {
        "id": scenario.id,
        "title": scenario.title,
        "status": scenario.status,
        "is_official": scenario.is_official,
        "question_count": len([q for q in scenario.questions if q.is_active]),
        "updated_at": scenario.updated_at,
        "approved_by": (
            scenario.approved_by.full_name
            if scenario.approved_by and scenario.approved_by.full_name
            else (scenario.approved_by.email if scenario.approved_by else None)
        ),
    }


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


def get_join_url_for_session(training_session: TrainingSession) -> str:
    return url_for("join_by_code", join_code=training_session.join_code, _external=True)


def get_qr_image_url(join_url: str) -> str:
    encoded = quote(join_url, safe="")
    return f"https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={encoded}"


def safe_redirect_target(raw_target: str | None) -> str:
    if not raw_target:
        return url_for("index")

    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc:
        return url_for("index")
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return url_for("index")
    if parsed.path in POST_ONLY_PATHS:
        return url_for("index")
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


@app.context_processor
def inject_template_context():
    return {
        "current_user": g.current_user,
        "role_label": role_label,
        "permission_keys": PERMISSION_KEYS,
        "question_type_labels": QUESTION_TYPE_LABELS,
        "csrf_token": issue_csrf_token,
    }


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
    user.last_login_at = datetime.utcnow()
    session["user_id"] = user.id
    rotate_csrf_token()
    db.session.commit()
    return redirect(next_target)


@app.post("/logout")
def logout():
    validate_csrf_or_abort()
    session.pop("user_id", None)
    rotate_csrf_token()
    return redirect(url_for("index"))


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
        return render_login(error="Magic link is invalid or expired.", next_target=url_for("index")), 400
    if not user_is_staff(token_row.user):
        return render_login(error="This account cannot access instructor/admin login.", next_target=url_for("index")), 403

    token_row.used_at = now
    token_row.user.last_login_at = now
    session["user_id"] = token_row.user_id
    rotate_csrf_token()
    db.session.commit()
    return redirect(url_for("index"))


@app.get("/")
@requires_permission(PERM_VIEW_SCENARIOS)
def index():
    _scenario_row, scenario = get_current_scenario()
    return render_template(
        "scenario.html",
        scenario=scenario,
        answers={},
        question_feedback={},
        submitted=False,
    )


@app.get("/scenarios")
@requires_permission(PERM_VIEW_SCENARIOS)
def scenario_library():
    db_user = get_current_db_user()
    selected_tab = resolve_library_tab(request.args.get("tab"), g.current_user)
    scenarios = load_library_scenarios(selected_tab, g.current_user, db_user)
    scenario_summaries = [summarize_scenario_for_library(scenario) for scenario in scenarios]
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
    return redirect(url_for("index"))


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
    return redirect(url_for("training_session_detail", session_id=session_row.id))


@app.get("/sessions/<int:session_id>")
@requires_permission(PERM_CREATE_SESSIONS)
def training_session_detail(session_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)
    join_url = get_join_url_for_session(session_row)
    qr_image_url = get_qr_image_url(join_url)
    return render_template(
        "session_detail.html",
        training_session=session_row,
        join_url=join_url,
        qr_image_url=qr_image_url,
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
    return redirect(url_for("index"))


@app.post("/submit")
@requires_permission(PERM_SUBMIT_ANSWERS)
def submit():
    validate_csrf_or_abort()
    _scenario_row, scenario = get_current_scenario()
    answers = {
        str(question["id"]): request.form.get(f"q_{question['id']}", "").strip()
        for question in scenario["questions"]
    }
    question_feedback = build_submission_feedback(scenario, answers)
    return render_template(
        "scenario.html",
        scenario=scenario,
        answers=answers,
        question_feedback=question_feedback,
        submitted=True,
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
    return redirect(url_for("index"))


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
    return redirect(url_for("index"))


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
    return redirect(url_for("index"))


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


@app.get("/join/<join_code>")
def join_by_code(join_code: str):
    training_session = TrainingSession.query.filter_by(join_code=join_code.upper()).first()
    if training_session is None:
        abort(404)
    return render_template("session_join_landing.html", training_session=training_session)


@app.get("/reports")
@requires_permission(PERM_VIEW_REPORTS)
def reports():
    return (
        "<h1>Reports</h1>"
        "<p>Placeholder for TODO #19/#20. Access is currently limited to Training Chief and Admin.</p>"
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
