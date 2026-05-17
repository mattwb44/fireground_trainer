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
    CATEGORY_LABELS,
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
    build_public_library_view_model,
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
    persist_drill_attempt,
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
from models import Question, Scenario, ScenarioTag, Tag

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

    visibility = request.form.get("visibility", "private").strip()
    training_category = request.form.get("training_category", "").strip() or None
    raw_tag_ids_for_visibility = [int(v) for v in request.form.getlist("tag_ids") if v.isdigit()]

    if visibility == "public":
        if not training_category:
            return render_create_scenario(error="Choose a training category to publish as public.", status_code=400)
        if not raw_tag_ids_for_visibility:
            return render_create_scenario(error="Select at least one tag to publish as public.", status_code=400)

    normalized_base_image_path = normalize_static_asset_path(base_image_path)
    normalized_overlay_image_path = normalize_static_asset_path(overlay_image_path, allow_empty=True)
    current_db_user = get_current_db_user()

    is_public = visibility == "public"
    dept_id = None
    if visibility == "department" and current_db_user and current_db_user.department_id:
        dept_id = current_db_user.department_id

    scenario = Scenario(
        title=title,
        dispatch_text=dispatch,
        base_image_path=normalized_base_image_path,
        overlay_image_path=normalized_overlay_image_path,
        created_by_user_id=current_db_user.id if current_db_user else None,
        status=SCENARIO_STATUS_DRAFT,
        is_official=is_official and g.current_user.has_permission(PERM_APPROVE_SCENARIOS),
        is_active=True,
        is_public=is_public,
        training_category=training_category,
        department_id=dept_id,
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

    raw_tag_ids = [int(v) for v in request.form.getlist("tag_ids") if v.isdigit()]
    if raw_tag_ids:
        valid_tag_ids = {
            t.id for t in Tag.query.filter(Tag.id.in_(raw_tag_ids), Tag.is_active.is_(True)).all()
        }
        for tag_id in valid_tag_ids:
            db.session.add(ScenarioTag(scenario_id=scenario.id, tag_id=tag_id))

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

    submission_message_level = "success"
    show_instructor_answers = False

    if participant is None or training_session is None:
        submission_error = None
        if db_user is not None:
            drill = persist_drill_attempt(db_user, scenario_row, answers)
            show_instructor_answers = True
            submission_message = (
                f"Drill attempt #{drill.attempt_number} saved."
            )
        else:
            submission_message = "Sign in to save your drill attempts and track your progress."
            submission_message_level = "info"
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
                f"Attempt #{saved_submission.attempt_number} submitted. "
                f"The host board is now using your latest answers."
            )

    return render_template(
        "scenario.html",
        scenario=scenario,
        answers=answers,
        question_feedback=question_feedback,
        submitted=True,
        show_instructor_answers=show_instructor_answers,
        submission_error=submission_error,
        submission_message=submission_message,
        submission_message_level=submission_message_level,
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
        scenario.submitted_for_official_at = None
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


@scenarios_bp.post("/scenario/submit-for-official")
@requires_permission(PERM_CREATE_SCENARIOS)
def submit_scenario_for_official():
    """Instructor/TC submits a published scenario for official designation review."""
    from datetime import datetime
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()

    if scenario.status != "approved":
        abort(409)
    if scenario.is_official:
        abort(409)
    if scenario.submitted_for_official_at is not None:
        abort(409)

    scenario.submitted_for_official_at = datetime.utcnow()
    append_admin_audit_log(
        actor=actor,
        action="submit_scenario_for_official",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details="Submitted for official designation review.",
    )
    db.session.commit()
    flash("Scenario submitted for official review.", "success")
    return redirect(safe_redirect_target(request.form.get("next")))


@scenarios_bp.post("/scenario/official-review")
@requires_permission(PERM_APPROVE_SCENARIOS)
def review_scenario_for_official():
    """TC approves or rejects a scenario's request for official designation."""
    from datetime import datetime
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    action = request.form.get("review_action", "").strip()
    actor = get_current_db_user()

    if scenario.submitted_for_official_at is None:
        abort(409)

    if action == "approve":
        if scenario.status != "approved":
            abort(409)
        scenario.is_official = True
        scenario.submitted_for_official_at = None
        append_admin_audit_log(
            actor=actor,
            action="approve_scenario_official",
            target_type="scenario",
            target_id=scenario.id,
            target_label=scenario.title,
            details="Official designation approved.",
        )
        flash("Scenario marked as official.", "success")
    elif action == "reject":
        scenario.submitted_for_official_at = None
        append_admin_audit_log(
            actor=actor,
            action="reject_scenario_official",
            target_type="scenario",
            target_id=scenario.id,
            target_label=scenario.title,
            details="Official designation rejected.",
        )
        flash("Official designation request rejected.", "info")
    else:
        abort(400)

    db.session.commit()
    return redirect(safe_redirect_target(request.form.get("next")))


@scenarios_bp.get("/library")
def public_scenario_library():
    """Public scenario library — accessible to guests and account holders."""
    db_user = get_current_db_user()
    category = request.args.get("category", "").strip()
    tag_slugs = [s.strip() for s in request.args.getlist("tag") if s.strip()]
    keyword = request.args.get("q", "").strip()[:100]
    library = build_public_library_view_model(db_user, category or None, tag_slugs, keyword)
    return render_template(
        "public_library.html",
        library=library,
        is_authenticated=(db_user is not None),
    )


@scenarios_bp.get("/scenarios/official-queue")
@requires_permission(PERM_APPROVE_SCENARIOS)
def official_review_queue():
    """TC sees all scenarios pending official designation."""
    pending = (
        Scenario.query.filter(
            Scenario.submitted_for_official_at.isnot(None),
            Scenario.is_official.is_(False),
            Scenario.status == "approved",
            Scenario.is_active.is_(True),
        )
        .order_by(Scenario.submitted_for_official_at.asc())
        .all()
    )
    return render_template(
        "official_review_queue.html",
        pending_scenarios=pending,
    )


@scenarios_bp.post("/scenario/visibility")
@requires_permission(PERM_CREATE_SCENARIOS)
def set_scenario_visibility():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()

    # Only the creator (or TC+) may change visibility
    if scenario.created_by_user_id != (actor.id if actor else None):
        if not g.current_user.has_permission(PERM_APPROVE_SCENARIOS):
            abort(403)

    visibility = request.form.get("visibility", "").strip()
    training_category = request.form.get("training_category", "").strip() or None
    raw_tag_ids = [int(v) for v in request.form.getlist("tag_ids") if v.isdigit()]

    if visibility not in {"private", "department", "public"}:
        abort(400)

    if visibility == "public":
        if not training_category:
            flash("Choose a training category to make this scenario public.", "warning")
            return redirect(safe_redirect_target(request.form.get("next")))
        if not raw_tag_ids:
            flash("Select at least one tag to make this scenario public.", "warning")
            return redirect(safe_redirect_target(request.form.get("next")))

    scenario.is_public = visibility == "public"
    scenario.training_category = training_category
    if visibility == "department":
        dept_user = actor or get_current_db_user()
        scenario.department_id = dept_user.department_id if dept_user else None
    elif visibility != "department":
        scenario.department_id = None

    # Sync tags if provided alongside visibility change
    if raw_tag_ids:
        from models import ScenarioTag, Tag
        scenario.tag_links.clear()
        valid_tag_ids = {
            t.id for t in Tag.query.filter(Tag.id.in_(raw_tag_ids), Tag.is_active.is_(True)).all()
        }
        for tag_id in valid_tag_ids:
            db.session.add(ScenarioTag(scenario_id=scenario.id, tag_id=tag_id))

    append_admin_audit_log(
        actor=actor,
        action="set_scenario_visibility",
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details=f"Visibility set to {visibility}.",
    )
    db.session.commit()
    session["scenario_id"] = scenario.id
    flash("Scenario visibility updated.", "success")
    return redirect(safe_redirect_target(request.form.get("next")))
