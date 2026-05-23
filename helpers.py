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
from constants import (  # noqa: F401 — re-exported for backward-compat
    ScenarioAction,
    CATEGORY_EMS,
    CATEGORY_FILTER_ALL,
    CATEGORY_FILTER_INSTRUCTOR_MADE,
    CATEGORY_FILTER_LABELS,
    CATEGORY_FILTER_OFFICIAL,
    CATEGORY_FILTER_USER_MADE,
    CATEGORY_FIREGROUND,
    CATEGORY_LABELS,
    CATEGORY_MVA,
    CSRF_SESSION_KEY,
    DEFAULT_QUESTION_TYPE,
    EMAIL_PATTERN,
    LIBRARY_TAB_LABELS,
    LIBRARY_TAB_MINE,
    LIBRARY_TAB_OFFICIAL,
    LIBRARY_TAB_PRACTICE,
    LIBRARY_TAB_SUBMITTED,
    PERMISSION_KEYS,
    POST_ONLY_PATHS,
    CREATE_QUESTION_TYPE_LABELS,
    QUESTION_TYPE_AUTO_CHECKLIST,
    QUESTION_TYPE_CHOICES,
    QUESTION_TYPE_DISCUSSION_ONLY,
    QUESTION_TYPE_KEY_POINT_AUTO,
    QUESTION_TYPE_LABELS,
    QUESTION_TYPE_MULTIPLE_CHOICE,
    QUESTION_TYPE_TRUE_FALSE,
    ROLE_LABELS,
    SCENARIO_ACTIVE_STATUSES,
    SCENARIO_STATUS_APPROVED,
    SCENARIO_STATUS_ARCHIVED,
    SCENARIO_STATUS_DRAFT,
    SCENARIO_STATUS_SUBMITTED,
    SHIFT_OPTIONS,
    SITE_NAME,
    STAFF_ROLES,
    STOP_WORDS,
    SUBMISSION_STATUS_APPROVED,
    SUBMISSION_STATUS_EXCLUDED,
    SUBMISSION_STATUS_FLAGGED,
    SUBMISSION_STATUS_LABELS,
    SUBMISSION_STATUS_SUBMITTED,
    POSITION_FIREFIGHTER,
    POSITION_DRIVER_PUMP_OPERATOR,
    POSITION_CAPTAIN,
    POSITION_BATTALION,
    POSITION_LABELS,
    POSITION_CHOICES,
    CATEGORY_TOKEN_PALETTES,
    TOKEN_PALETTE_DEFAULT,
)
from extensions import db
from models import (
    MagicLoginToken,
    Participant,
    Question,
    QuestionChoice,
    Role,
    Scenario,
    ScenarioLike,
    ScenarioPosition,
    SessionQuestionReveal,
    Submission,
    SubmissionAnswer,
    SubmissionAuditLog,
    TrainingSession,
    User,
    UserRole,
)
from view_models import (  # noqa: F401 — re-exported for backward-compat
    build_home_category_cards,
    build_home_stats,
    get_user_lists,
    get_saved_scenario_ids_for_user,
    build_host_board_workspace_view_model,
    build_host_review_summary,
    build_latest_submission_map,
    build_participant_submission_state,
    build_public_library_view_model,
    build_reports_index_view_model,
    build_revealed_submission_view_model,
    build_scenario_view_model,
    build_scenario_vote_state_map,
    build_scenario_vote_summary,
    build_session_dashboard_view_model,
    build_session_participant_label_map,
    build_session_question_review_view_model,
    build_session_report_view_model,
    build_short_answer_feedback,
    build_checklist_feedback,
    build_submission_detail_view_model,
    build_submission_feedback,
    build_training_category_page,
    extract_reference_phrases,
    extract_reference_terms,
    format_audit_action,
    format_relative_date,
    format_scenario_popularity_label,
    format_submission_status,
    format_submission_timestamp,
    format_user_identity,
    normalize_text_for_match,
    ordered_unique,
    score_question_answer,
    submission_status_tone,
    summarize_review_notes,
    summarize_scenario_for_catalog,
    summarize_scenario_for_library,
    tokenize_for_match,
)
from session_context import (  # noqa: F401 — re-exported for backward-compat
    ACTIVE_PARTICIPANT_ID_KEY,
    ACTIVE_TRAINING_SESSION_ID_KEY,
    HOST_TRAINING_SESSION_ID_KEY,
    PARTICIPANT_JOIN_MAP_KEY,
    clear_host_training_session_context,
    clear_participant_session_context,
    get_current_db_user,
    get_joined_participant_for_session,
    get_signed_in_participant_for_session,
    load_active_participant_session,
    load_host_training_session,
    set_active_participant_context,
    set_host_training_session_context,
)

# ---------------------------------------------------------------------------
# Constants (mutable rate-limiting state that lives here, not in constants.py)
# ---------------------------------------------------------------------------

LOGIN_ATTEMPTS: dict[tuple[str, str], list[float]] = {}
LOGIN_WINDOW_SECONDS = 600
LOGIN_ATTEMPT_LIMIT = 5

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
        seed_status = scenario_seed.get("status", SCENARIO_STATUS_DRAFT)
        scenario = Scenario(
            title=scenario_seed["title"],
            dispatch_text=scenario_seed["dispatch"],
            base_image_path=scenario_seed["image"]["base"],
            overlay_image_path=scenario_seed["image"]["overlay"],
            status=seed_status,
            is_official=True,
            is_active=True,
            is_public=seed_status == SCENARIO_STATUS_APPROVED,
            training_category=CATEGORY_FIREGROUND if seed_status == SCENARIO_STATUS_APPROVED else None,
            submitted_at=datetime.utcnow()
            if seed_status in {SCENARIO_STATUS_SUBMITTED, SCENARIO_STATUS_APPROVED}
            else None,
            approved_at=datetime.utcnow()
            if seed_status == SCENARIO_STATUS_APPROVED
            else None,
            archived_at=datetime.utcnow()
            if seed_status == SCENARIO_STATUS_ARCHIVED
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


_SEED_TAGS = [
    ("Apartment Complex", "apartment-complex"),
    ("Commercial Structure", "commercial-structure"),
    ("Duplex", "duplex"),
    ("Electric Vehicle", "electric-vehicle"),
    ("Extrication", "extrication"),
    ("High-Rise", "high-rise"),
    ("Pediatric", "pediatric"),
    ("Residential Structure", "residential-structure"),
    ("Trauma", "trauma"),
    ("Wildland Interface", "wildland-interface"),
]


def ensure_seed_tags() -> None:
    from models import Tag
    for name, slug in _SEED_TAGS:
        if not Tag.query.filter_by(slug=slug).first():
            db.session.add(Tag(name=name, slug=slug, is_active=True))
    db.session.commit()


def slugify_tag(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-").replace("/", "-"))


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


def load_visible_scenarios_for_user(current_user: CurrentUser, db_user: User | None = None) -> list[Scenario]:
    from sqlalchemy import or_
    query = Scenario.query.filter(Scenario.is_active.is_(True)).order_by(Scenario.id.asc())

    # Staff see everything for workflow management
    if current_user.has_permission(PERM_CREATE_SCENARIOS) or current_user.has_permission(PERM_APPROVE_SCENARIOS):
        return query.all()

    # Guests: public only
    if current_user.user_id == "guest":
        return query.filter(Scenario.is_public.is_(True)).all()

    # Authenticated participant: own + their department's + public
    if db_user is None:
        try:
            db_user = get_current_db_user()
        except RuntimeError:
            pass
    if db_user is None:
        return query.filter(Scenario.is_public.is_(True)).all()

    conditions = [Scenario.is_public.is_(True), Scenario.created_by_user_id == db_user.id]
    if db_user.department_id is not None:
        conditions.append(Scenario.department_id == db_user.department_id)
    return query.filter(or_(*conditions)).all()


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


MINE_STATUS_FILTERS = frozenset({"draft", "submitted", "approved"})


def load_library_scenarios(
    tab: str,
    current_user: CurrentUser,
    db_user: User | None,
    status_filter: str | None = None,
) -> list[Scenario]:
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
        mine_query = query.filter(
            Scenario.created_by_user_id == db_user.id,
            Scenario.status != SCENARIO_STATUS_ARCHIVED,
        )
        if status_filter in MINE_STATUS_FILTERS:
            mine_query = mine_query.filter(Scenario.status == status_filter)
        return mine_query.all()
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


def scenario_category_key_for_scenario(scenario: Scenario) -> str:
    if scenario.training_category in {CATEGORY_FIREGROUND, CATEGORY_MVA, CATEGORY_EMS}:
        return scenario.training_category
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
    if category_key not in {CATEGORY_FIREGROUND, CATEGORY_MVA, CATEGORY_EMS}:
        return []

    scenarios = (
        Scenario.query.filter(
            Scenario.is_active.is_(True),
            Scenario.is_public.is_(True),
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
    scenario: Scenario, action: ScenarioAction, actor: User | None
) -> None:
    now = datetime.utcnow()

    if action == ScenarioAction.SUBMIT_FOR_REVIEW:
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

    if action == ScenarioAction.APPROVE:
        if scenario.status != SCENARIO_STATUS_SUBMITTED:
            abort(409)
        scenario.status = SCENARIO_STATUS_APPROVED
        scenario.approved_at = now
        scenario.approved_by_user_id = actor.id if actor else None
        scenario.archived_at = None
        return

    if action == ScenarioAction.ARCHIVE:
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
        if question_type not in QUESTION_TYPE_CHOICES and question_type != "true_false":
            return [], "One or more question types are invalid."
        instructor_answer = (
            instructor_answers[idx].strip() if idx < len(instructor_answers) else ""
        )
        if question_type == "true_false":
            question_type = QUESTION_TYPE_MULTIPLE_CHOICE
            correct_raw = request.form.get(f"correct_choice_{idx}", "0").strip()
            correct_idx = 0 if correct_raw == "0" else 1
            questions.append({
                "prompt": prompt,
                "question_type": QUESTION_TYPE_MULTIPLE_CHOICE,
                "instructor_answer": instructor_answer,
                "choices": [
                    {"choice_text": "True", "is_correct": correct_idx == 0, "sort_order": 0},
                    {"choice_text": "False", "is_correct": correct_idx == 1, "sort_order": 1},
                ],
            })
            continue
        question_data: dict = {
            "prompt": prompt,
            "question_type": question_type,
            "instructor_answer": instructor_answer,
            "choices": [],
        }
        if question_type == QUESTION_TYPE_MULTIPLE_CHOICE:
            # choices submitted as choice_text_<idx>_<choice_idx> and correct_choice_<idx>
            choice_texts_raw = request.form.getlist(f"choice_text_{idx}")
            correct_idx_raw = request.form.get(f"correct_choice_{idx}", "").strip()
            choice_texts = [c.strip() for c in choice_texts_raw if c.strip()]
            if len(choice_texts) < 2:
                return [], f"Multiple choice question {idx + 1} needs at least 2 choices."
            if len(choice_texts) > 6:
                return [], f"Multiple choice question {idx + 1} can have at most 6 choices."
            try:
                correct_idx = int(correct_idx_raw)
                if correct_idx < 0 or correct_idx >= len(choice_texts):
                    raise ValueError
            except (ValueError, TypeError):
                return [], f"Multiple choice question {idx + 1} must have one correct choice marked."
            question_data["choices"] = [
                {"choice_text": text, "is_correct": (i == correct_idx), "sort_order": i}
                for i, text in enumerate(choice_texts)
            ]
        questions.append(question_data)

    if not questions:
        return [], "At least one question is required."
    if len(questions) > 20:
        return [], "Please keep scenarios to 20 questions or fewer."
    return questions, None


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
    selected_choice_ids: dict[str, int | None] | None = None,
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
        qid = str(question.id)
        db.session.add(DrillAttemptAnswer(
            drill_attempt_id=drill.id,
            question_id=question.id,
            answer_text=answers.get(qid, ""),
            selected_choice_id=(selected_choice_ids or {}).get(qid),
        ))
    return drill


def persist_submission(
    scenario_row: Scenario,
    scenario: dict,
    participant: Participant,
    training_session: TrainingSession,
    answers: dict[str, str],
    selected_choice_ids: dict[str, int | None] | None = None,
) -> Submission:
    """Stages submission and answers in db.session. Caller must commit."""
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
        qid = str(question["id"])
        db.session.add(
            SubmissionAnswer(
                submission_id=submission.id,
                question_id=question["id"],
                answer_text=answers.get(qid, ""),
                selected_choice_id=(selected_choice_ids or {}).get(qid),
            )
        )

    return submission


def fork_scenario_for_department(
    original: Scenario,
    adopting_user: User,
) -> Scenario:
    """Clone a scenario into the adopting user's department as a new draft."""
    forked = Scenario(
        title=original.title,
        dispatch_text=original.dispatch_text,
        base_image_path=original.base_image_path,
        overlay_image_path=original.overlay_image_path,
        created_by_user_id=adopting_user.id,
        status=SCENARIO_STATUS_DRAFT,
        is_official=False,
        is_active=True,
        is_public=False,
        training_category=original.training_category,
        department_id=adopting_user.department_id,
        forked_from_scenario_id=original.id,
    )
    db.session.add(forked)
    db.session.flush()

    for question in sorted(original.questions, key=lambda q: q.sort_order):
        if not question.is_active:
            continue
        new_q = Question(
            scenario_id=forked.id,
            question_key=question.question_key,
            prompt=question.prompt,
            question_type=question.question_type,
            instructor_answer=question.instructor_answer,
            sort_order=question.sort_order,
            is_active=True,
        )
        db.session.add(new_q)
        db.session.flush()

        # Copy MC choices
        for choice in sorted(getattr(question, "choices", []), key=lambda c: c.sort_order):
            db.session.add(QuestionChoice(
                question_id=new_q.id,
                choice_text=choice.choice_text,
                is_correct=choice.is_correct,
                sort_order=choice.sort_order,
            ))

    db.session.commit()
    return forked


def get_drill_completed_scenario_ids_for_user(
    db_user: User | None,
    scenario_ids: list[int] | None = None,
) -> set[int]:
    """DrillAttempt-based completion check (solo drills, not live sessions)."""
    from models import DrillAttempt
    if db_user is None:
        return set()
    query = DrillAttempt.query.filter_by(user_id=db_user.id)
    if scenario_ids:
        query = query.filter(DrillAttempt.scenario_id.in_(scenario_ids))
    return {row.scenario_id for row in query.all()}


def get_all_completed_scenario_ids_for_user(
    db_user: User | None,
    scenario_ids: list[int] | None = None,
) -> set[int]:
    """Combined DrillAttempt + Submission completion check."""
    return (
        get_completed_submission_scenario_ids_for_user(db_user, scenario_ids)
        | get_drill_completed_scenario_ids_for_user(db_user, scenario_ids)
    )


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
    if action == "approve":
        submission.status = SUBMISSION_STATUS_APPROVED
        submission.approved_at = datetime.utcnow()
        submission.approved_by_user_id = actor.id if actor else None
        return
    if action == "flag":
        submission.status = SUBMISSION_STATUS_FLAGGED
        submission.approved_at = None
        submission.approved_by_user_id = None
        return
    if action == "reopen":
        submission.status = SUBMISSION_STATUS_SUBMITTED
        submission.approved_at = None
        submission.approved_by_user_id = None
        return
    if action == "reinstate":
        submission.status = SUBMISSION_STATUS_SUBMITTED
        submission.approved_at = None
        submission.approved_by_user_id = None
        return
    abort(400)


def render_create_scenario(
    error: str | None = None,
    status_code: int = 200,
    prefill: dict | None = None,
    draft_scenario_id: int | None = None,
):
    from models import Tag
    is_post = request.method == "POST"
    pf = prefill or {}
    form_data = {
        "title": request.form.get("title", "").strip() if is_post else pf.get("title", ""),
        "dispatch": request.form.get("dispatch", "").strip() if is_post else pf.get("dispatch", ""),
        "base_image_path": (
            request.form.get("base_image_path", "").strip()
            if is_post
            else pf.get("base_image_path", "images/house1.jpg")
        ),
        "overlay_image_path": (
            request.form.get("overlay_image_path", "").strip()
            if is_post
            else pf.get("overlay_image_path", "")
        ),
        "is_official": (
            request.form.get("is_official") == "on"
            if is_post
            else pf.get("is_official", False)
        ),
        "selected_tag_ids": (
            [int(v) for v in request.form.getlist("tag_ids") if v.isdigit()]
            if is_post
            else pf.get("selected_tag_ids", [])
        ),
        "selected_positions": (
            request.form.getlist("positions") if is_post else pf.get("selected_positions", [])
        ),
        "visibility": (
            request.form.get("visibility", "private") if is_post else pf.get("visibility", "private")
        ),
        "training_category": (
            request.form.get("training_category", "") if is_post else pf.get("training_category", "")
        ),
        "token_layout": pf.get("token_layout", "[]"),
    }
    if is_post:
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
    elif pf.get("question_rows"):
        question_rows = pf["question_rows"]
    else:
        question_rows = [
            {"prompt": "", "question_type": DEFAULT_QUESTION_TYPE, "instructor_answer": ""}
            for _ in range(4)
        ]
    available_tags = Tag.query.filter_by(is_active=True).order_by(Tag.name).all()
    return (
        render_template(
            "scenario_create.html",
            error=error,
            form_data=form_data,
            question_rows=question_rows,
            question_type_labels=CREATE_QUESTION_TYPE_LABELS,
            default_question_type=DEFAULT_QUESTION_TYPE,
            available_tags=available_tags,
            category_labels=CATEGORY_LABELS,
            position_choices=POSITION_CHOICES,
            position_labels=POSITION_LABELS,
            draft_scenario_id=draft_scenario_id,
            token_palette=TOKEN_PALETTE_DEFAULT,
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
