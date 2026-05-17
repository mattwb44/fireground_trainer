from datetime import datetime

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for

from authz import (
    PERM_CREATE_SESSIONS,
    PERM_SELECT_REVIEW_ANSWER,
    PERM_SHARE_REVIEW_ANSWER,
    PERM_VIEW_SESSION_SUBMISSIONS,
    requires_permission,
)
from extensions import db
from helpers import (
    SCENARIO_STATUS_ARCHIVED,
    SHIFT_OPTIONS,
    SUBMISSION_STATUS_EXCLUDED,
    append_admin_audit_log,
    append_submission_audit_log,
    build_host_board_workspace_view_model,
    build_join_url_for_session,
    build_revealed_submission_view_model,
    build_session_dashboard_view_model,
    build_submission_detail_view_model,
    can_use_scenario_for_session,
    can_view_revealed_answers_for_session,
    clear_all_revealed_answers,
    get_current_db_user,
    get_joined_participant_for_session,
    get_qr_image_url,
    get_signed_in_participant_for_session,
    generate_join_code,
    load_visible_scenarios_for_user,
    render_create_session,
    render_join_page,
    safe_redirect_target,
    set_active_participant_context,
    set_host_training_session_context,
    remove_revealed_answer_for_question,
    set_revealed_answer_for_question,
    update_submission_review_state,
    validate_csrf_or_abort,
)
from models import Participant, Question, Submission, SubmissionAnswer, TrainingSession

sessions_bp = Blueprint("sessions", __name__)


@sessions_bp.get("/sessions/new")
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


@sessions_bp.post("/sessions/new")
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
    db.session.flush()
    append_admin_audit_log(
        actor=current_db_user,
        action="create_training_session",
        target_type="training_session",
        target_id=session_row.id,
        target_label=title or f"Session #{session_row.id}",
        details=f"Scenario #{scenario_id} | Join code {session_row.join_code}",
    )
    db.session.commit()
    set_host_training_session_context(session_row)
    return redirect(url_for("main.board", session_id=session_row.id))


@sessions_bp.get("/sessions/<int:session_id>")
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


@sessions_bp.get("/sessions/<int:session_id>/submissions")
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


@sessions_bp.get("/sessions/<int:session_id>/workspace")
@requires_permission(PERM_VIEW_SESSION_SUBMISSIONS)
def training_session_workspace_partial(session_id: int):
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)
    return render_template(
        "session_board_workspace_partial.html",
        workspace=build_host_board_workspace_view_model(session_row),
    )



@sessions_bp.post("/sessions/<int:session_id>/questions/<int:question_id>/reveal")
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
    return redirect(url_for("main.board", session_id=session_row.id))


@sessions_bp.post("/sessions/<int:session_id>/questions/<int:question_id>/reveal-clear")
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
    return redirect(url_for("main.board", session_id=session_row.id))


@sessions_bp.post("/sessions/<int:session_id>/questions/<int:question_id>/reveals/<int:submission_answer_id>/remove")
@requires_permission(PERM_SHARE_REVIEW_ANSWER)
def remove_revealed_question_answer_for_session(session_id: int, question_id: int, submission_answer_id: int):
    validate_csrf_or_abort()
    session_row = TrainingSession.query.filter_by(id=session_id).first()
    if session_row is None:
        abort(404)

    question = Question.query.filter_by(id=question_id, scenario_id=session_row.scenario_id).first()
    if question is None:
        abort(404)

    submission_answer = SubmissionAnswer.query.filter_by(id=submission_answer_id).first()
    if submission_answer is None:
        abort(404)
    if submission_answer.question_id != question.id:
        abort(409)
    if submission_answer.submission.training_session_id != session_row.id:
        abort(409)

    remove_revealed_answer_for_question(session_row, submission_answer)
    db.session.commit()
    next_target = request.form.get("next")
    if next_target:
        return redirect(safe_redirect_target(next_target))
    return redirect(url_for("main.board", session_id=session_row.id))


@sessions_bp.post("/sessions/<int:session_id>/revealed-answers/clear")
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
    return redirect(url_for("main.board", session_id=session_row.id))


@sessions_bp.get("/sessions/<int:session_id>/submissions/<int:submission_id>")
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


@sessions_bp.post("/sessions/<int:session_id>/submissions/<int:submission_id>/review")
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

    if "review_notes" in request.form:
        raw_notes = request.form.get("review_notes", "").strip()
        submission.notes = raw_notes[:2000] or None
    action = request.form.get("review_action", "").strip()
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
        url_for("sessions.training_session_submission_detail", session_id=session_row.id, submission_id=submission.id)
    )


@sessions_bp.get("/sessions/<int:session_id>/revealed-answer")
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


@sessions_bp.route("/join/<join_code>", methods=["GET", "POST"])
def join_by_code(join_code: str):
    training_session = TrainingSession.query.filter_by(join_code=join_code.upper()).first()
    if training_session is None:
        abort(404)

    current_db_user = get_current_db_user()
    existing_participant = get_joined_participant_for_session(training_session.id)
    if existing_participant is None:
        existing_participant = get_signed_in_participant_for_session(training_session.id, current_db_user)
    if (
        existing_participant is not None
        and request.method == "GET"
        and training_session.status == "active"
    ):
        set_active_participant_context(existing_participant)
        return redirect(url_for("main.board"))

    if request.method == "GET":
        return render_join_page(training_session=training_session)

    validate_csrf_or_abort()
    if training_session.status != "active":
        return render_join_page(
            training_session=training_session,
            error="This session is not active.",
            error_field="session",
            status_code=409,
        )

    if existing_participant is not None:
        set_active_participant_context(existing_participant)
        flash("Rejoined your existing session entry.", "success")
        return redirect(url_for("main.board"))

    shift_label = request.form.get("shift_label", "").strip()
    custom_shift_label = request.form.get("custom_shift_label", "").strip()
    if not shift_label:
        return render_join_page(
            training_session=training_session,
            error="Please select a shift.",
            error_field="shift_label",
            status_code=400,
        )
    if shift_label not in SHIFT_OPTIONS:
        return render_join_page(
            training_session=training_session,
            error="Invalid shift option.",
            error_field="shift_label",
            status_code=400,
        )
    if shift_label == "Other":
        if not custom_shift_label:
            return render_join_page(
                training_session=training_session,
                error="Enter your shift label when selecting Other.",
                error_field="custom_shift_label",
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
            error_field="identity_mode",
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
            error_field="display_name",
            status_code=400,
        )
    else:
        display_name = display_name[:120]

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
    return redirect(url_for("main.board"))
