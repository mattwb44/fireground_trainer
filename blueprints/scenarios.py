import random

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import IntegrityError

from authz import (
    PERM_APPROVE_SCENARIOS,
    PERM_CREATE_SCENARIOS,
    PERM_SUBMIT_ANSWERS,
    PERM_VIEW_SCENARIOS,
    requires_permission,
)
from extensions import db
from helpers import (
    CATEGORY_EMS,
    CATEGORY_FILTER_ALL,
    CATEGORY_FIREGROUND,
    CATEGORY_MVA,
    LIBRARY_TAB_LABELS,
    LIBRARY_TAB_MINE,
    LIBRARY_TAB_OFFICIAL,
    LIBRARY_TAB_PRACTICE,
    LIBRARY_TAB_SUBMITTED,
    SCENARIO_STATUS_ARCHIVED,
    SCENARIO_STATUS_DRAFT,
    allowed_library_tabs_for_user,
    append_admin_audit_log,
    apply_scenario_transition_or_abort,
    build_host_board_workspace_view_model,
    build_participant_submission_state,
    build_revealed_submission_view_model,
    build_scenario_vote_state_map,
    build_scenario_vote_summary,
    build_submission_feedback,
    build_training_category_page,
    clear_host_training_session_context,
    get_current_db_user,
    get_current_scenario,
    get_posted_scenario_or_abort,
    get_scenario_vote_action,
    load_library_scenarios,
    load_visible_scenarios_for_user,
    normalize_static_asset_path,
    parse_create_scenario_questions,
    persist_submission,
    render_create_scenario,
    resolve_category_filter,
    resolve_library_tab,
    safe_redirect_target,
    scenario_asset_validation_error,
    set_scenario_like_vote,
    summarize_scenario_for_library,
    user_can_like_scenario,
    validate_csrf_or_abort,
    validate_submission_context,
)
from models import Question, Scenario

scenarios_bp = Blueprint("scenarios", __name__)


@scenarios_bp.get("/training/fireground")
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


@scenarios_bp.get("/training/mva")
def mva_training():
    return render_template(
        "training_category.html",
        category=build_training_category_page(
            CATEGORY_MVA,
            CATEGORY_FILTER_ALL,
            get_current_db_user(),
        ),
    )


@scenarios_bp.get("/training/ems")
def ems_training():
    return render_template(
        "training_category.html",
        category=build_training_category_page(
            CATEGORY_EMS,
            CATEGORY_FILTER_ALL,
            get_current_db_user(),
        ),
    )


@scenarios_bp.get("/scenarios")
@requires_permission(PERM_VIEW_SCENARIOS)
def scenario_library():
    from authz import PERM_APPROVE_SCENARIOS, PERM_CREATE_SCENARIOS
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
    empty_state_message = {
        LIBRARY_TAB_OFFICIAL: "No official approved scenarios are available yet.",
        LIBRARY_TAB_PRACTICE: "No practice scenarios match this view yet.",
        LIBRARY_TAB_MINE: "You have not created any active scenarios yet.",
        LIBRARY_TAB_SUBMITTED: "There are no scenarios waiting in submitted review right now.",
    }[selected_tab]
    return render_template(
        "scenario_library.html",
        tabs=tabs,
        selected_tab=selected_tab,
        selected_tab_label=LIBRARY_TAB_LABELS[selected_tab],
        selected_tab_count=len(scenario_summaries),
        scenarios=scenario_summaries,
        empty_state_message=empty_state_message,
        can_create_scenarios=g.current_user.has_permission(PERM_CREATE_SCENARIOS),
        can_manage_official=g.current_user.has_permission(PERM_APPROVE_SCENARIOS),
    )


@scenarios_bp.post("/scenarios/vote")
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


@scenarios_bp.post("/scenarios/select")
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


@scenarios_bp.get("/scenarios/new")
@requires_permission(PERM_CREATE_SCENARIOS)
def new_scenario_page():
    return render_create_scenario()


@scenarios_bp.post("/scenarios/new")
@requires_permission(PERM_CREATE_SCENARIOS)
def create_scenario():
    from authz import PERM_APPROVE_SCENARIOS
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
    asset_error = scenario_asset_validation_error(base_image_path, overlay_image_path)
    if asset_error:
        return render_create_scenario(error=asset_error, status_code=400)

    questions, question_error = parse_create_scenario_questions()
    if question_error:
        return render_create_scenario(error=question_error, status_code=400)

    normalized_base_image_path = normalize_static_asset_path(base_image_path)
    normalized_overlay_image_path = normalize_static_asset_path(overlay_image_path, allow_empty=True)
    current_db_user = get_current_db_user()
    scenario = Scenario(
        title=title,
        dispatch_text=dispatch,
        base_image_path=normalized_base_image_path,
        overlay_image_path=normalized_overlay_image_path,
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

    append_admin_audit_log(
        actor=current_db_user,
        action="create_scenario",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details=f"Created draft scenario with {len(questions)} question(s).",
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("main.board"))


@scenarios_bp.get("/new")
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
    return redirect(url_for("main.board"))


@scenarios_bp.post("/submit")
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

    if participant is None or training_session is None:
        # Solo practice — show feedback but don't save anything.
        submission_error = None
        submission_message = "Practicing solo — answers not saved. Join a live session to submit for real."
    elif submission_error is None:
        saved_submission, submission_error = persist_submission(
            scenario_row=scenario_row,
            scenario=scenario,
            participant=participant,
            training_session=training_session,
            answers=answers,
        )
        if saved_submission is not None:
            submission_message = (
                f"Attempt #{saved_submission.attempt_number} saved for this session. The host board now uses your latest saved attempt."
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
        participant_submission_state=build_participant_submission_state(scenario_row),
        scenario_vote=build_scenario_vote_summary(scenario_row, db_user),
        revealed_submission=(
            build_revealed_submission_view_model(g.active_training_session)
            if g.active_training_session is not None
            else None
        ),
    )


@scenarios_bp.post("/scenario/submit-review")
@requires_permission(PERM_CREATE_SCENARIOS)
def submit_scenario_for_review():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()
    apply_scenario_transition_or_abort(scenario=scenario, action="submit_for_review", actor=actor)
    append_admin_audit_log(
        actor=actor,
        action="submit_scenario_for_review",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details="Scenario moved from Draft to Submitted.",
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("main.board"))


@scenarios_bp.post("/scenario/approve")
@requires_permission(PERM_APPROVE_SCENARIOS)
def approve_scenario():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()
    apply_scenario_transition_or_abort(scenario=scenario, action="approve", actor=actor)
    append_admin_audit_log(
        actor=actor,
        action="approve_scenario",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details="Scenario moved from Submitted to Approved.",
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("main.board"))


@scenarios_bp.post("/scenario/archive")
@requires_permission(PERM_APPROVE_SCENARIOS)
def archive_scenario():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()
    apply_scenario_transition_or_abort(scenario=scenario, action="archive", actor=actor)
    append_admin_audit_log(
        actor=actor,
        action="archive_scenario",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details="Scenario moved into archived status.",
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(url_for("main.board"))


@scenarios_bp.post("/scenario/official")
@requires_permission(PERM_APPROVE_SCENARIOS)
def set_scenario_official_status():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    action = request.form.get("official_action", "").strip()
    actor = get_current_db_user()

    if action == "make":
        if scenario.status != "approved":
            abort(409)
        scenario.is_official = True
    elif action == "remove":
        scenario.is_official = False
    else:
        abort(400)

    append_admin_audit_log(
        actor=actor,
        action="make_scenario_official" if action == "make" else "remove_scenario_official",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details="Official flag enabled." if action == "make" else "Official flag removed.",
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    return redirect(safe_redirect_target(request.form.get("next")))
