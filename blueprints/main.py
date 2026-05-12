from flask import Blueprint, Response, g, render_template, session, url_for

from authz import PERM_VIEW_SCENARIOS, requires_permission
from helpers import (
    CATEGORY_EMS,
    CATEGORY_FIREGROUND,
    CATEGORY_LABELS,
    CATEGORY_MVA,
    PERMISSION_KEYS,
    QUESTION_TYPE_LABELS,
    SHIFT_OPTIONS,
    SITE_NAME,
    build_home_category_cards,
    build_host_board_workspace_view_model,
    build_participant_submission_state,
    build_revealed_submission_view_model,
    build_scenario_vote_summary,
    get_current_db_user,
    get_current_scenario,
    get_current_user,
    issue_csrf_token,
    load_active_participant_session,
    load_host_training_session,
    role_label,
)

main_bp = Blueprint("main", __name__)


@main_bp.before_app_request
def load_current_user():
    g.current_user = get_current_user()
    g.active_participant, g.active_training_session = load_active_participant_session()


@main_bp.after_app_request
def apply_security_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    return response


@main_bp.app_context_processor
def inject_template_context():
    return {
        "current_user": g.current_user,
        "active_participant": g.active_participant,
        "active_training_session": g.active_training_session,
        "site_name": SITE_NAME,
        "training_categories": [
            {"key": CATEGORY_FIREGROUND, "label": CATEGORY_LABELS[CATEGORY_FIREGROUND], "href": url_for("scenarios.fireground_training")},
            {"key": CATEGORY_MVA, "label": CATEGORY_LABELS[CATEGORY_MVA], "href": url_for("scenarios.mva_training")},
            {"key": CATEGORY_EMS, "label": CATEGORY_LABELS[CATEGORY_EMS], "href": url_for("scenarios.ems_training")},
        ],
        "shift_options": SHIFT_OPTIONS,
        "role_label": role_label,
        "permission_keys": PERMISSION_KEYS,
        "question_type_labels": QUESTION_TYPE_LABELS,
        "csrf_token": issue_csrf_token,
    }


@main_bp.app_errorhandler(400)
def bad_request(_err):
    return (
        render_template(
            "bad_request.html",
            return_target=url_for("main.home"),
        ),
        400,
    )


@main_bp.app_errorhandler(403)
def forbidden(_err):
    return render_template("forbidden.html"), 403


@main_bp.app_errorhandler(409)
def conflict(_err):
    return (
        render_template(
            "conflict.html",
            return_target=url_for("main.board"),
        ),
        409,
    )


@main_bp.get("/")
def home():
    return render_template(
        "home.html",
        category_cards=build_home_category_cards(),
    )


@main_bp.get("/board")
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
        participant_submission_state=build_participant_submission_state(_scenario_row),
        scenario_vote=build_scenario_vote_summary(_scenario_row, db_user),
        host_workspace=(
            build_host_board_workspace_view_model(host_training_session)
            if host_training_session is not None
            else None
        ),
        revealed_submission=revealed_submission,
    )


@main_bp.get("/offline")
def offline():
    return render_template("offline.html")
