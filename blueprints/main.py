from flask import Blueprint, Response, g, render_template, session, url_for

from authz import PERM_VIEW_SCENARIOS, requires_permission
from models import ScenarioFlag, ScenarioTokenLayout
from constants import CATEGORY_TOKEN_PALETTES, TOKEN_PALETTE_DEFAULT
from helpers import (
    PERMISSION_KEYS,
    QUESTION_TYPE_LABELS,
    SHIFT_OPTIONS,
    SITE_NAME,
    build_home_stats,
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
    participant_board_template,
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
        home_stats=build_home_stats(),
    )


@main_bp.get("/board")
@requires_permission(PERM_VIEW_SCENARIOS, allow_guest=True)
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
    user_has_flagged = (
        db_user is not None and ScenarioFlag.query.filter_by(
            scenario_id=_scenario_row.id, user_id=db_user.id
        ).first() is not None
    )
    saved_layout = ScenarioTokenLayout.query.filter_by(scenario_id=_scenario_row.id).first()
    initial_token_layout = saved_layout.layout_json if saved_layout else "[]"
    token_palette = CATEGORY_TOKEN_PALETTES.get(
        _scenario_row.training_category, TOKEN_PALETTE_DEFAULT
    )
    return render_template(
        participant_board_template(),
        scenario=scenario,
        answers={},
        question_feedback={},
        submitted=False,
        submission_error=None,
        submission_message=None,
        saved_submission=None,
        participant_submission_state=build_participant_submission_state(_scenario_row),
        scenario_vote=build_scenario_vote_summary(_scenario_row, db_user),
        user_has_flagged=user_has_flagged,
        initial_token_layout=initial_token_layout,
        token_palette=token_palette,
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
