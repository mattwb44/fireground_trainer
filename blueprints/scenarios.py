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
    SCENARIO_STATUS_SUBMITTED,
    allowed_library_tabs_for_user,
    append_admin_audit_log,
    ScenarioAction,
    apply_scenario_transition_or_abort,
    build_host_board_workspace_view_model,
    build_public_library_view_model,
    can_use_scenario_for_session,
    fork_scenario_for_department,
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
    MINE_STATUS_FILTERS,
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
    get_user_lists,
)
from models import Question, Scenario, ScenarioFlag, ScenarioTag, ScenarioTokenLayout, Tag

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
    raw_status_filter = request.args.get("status", "").strip()
    status_filter = raw_status_filter if raw_status_filter in MINE_STATUS_FILTERS else None
    scenarios = load_library_scenarios(selected_tab, g.current_user, db_user, status_filter=status_filter)
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
        user_lists=get_user_lists(db_user),
        mine_status_filter=status_filter or "",
        is_mine_tab=(selected_tab == LIBRARY_TAB_MINE),
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

    if not can_use_scenario_for_session(scenario.id, g.current_user):
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


ALLOWED_FLAG_REASONS = frozenset({"incorrect", "inappropriate", "other"})


@scenarios_bp.post("/scenarios/flag")
@requires_permission(PERM_VIEW_SCENARIOS)
def flag_scenario():
    validate_csrf_or_abort()
    raw_scenario_id = request.form.get("scenario_id", "").strip()
    if not raw_scenario_id.isdigit():
        abort(400)

    scenario = Scenario.query.filter_by(id=int(raw_scenario_id), is_active=True).first()
    if scenario is None:
        abort(404)

    next_target = safe_redirect_target(request.form.get("next"))
    db_user = get_current_db_user()
    if db_user is None:
        flash("Sign in to report a scenario.", "warning")
        return redirect(next_target)

    raw_reason = request.form.get("reason", "").strip()
    reason = raw_reason if raw_reason in ALLOWED_FLAG_REASONS else None

    existing = ScenarioFlag.query.filter_by(
        scenario_id=scenario.id, user_id=db_user.id
    ).first()
    if existing:
        flash("You have already reported this scenario.", "info")
        return redirect(next_target)

    flag = ScenarioFlag(scenario_id=scenario.id, user_id=db_user.id, reason=reason)
    db.session.add(flag)
    try:
        db.session.commit()
        flash("Scenario reported. Thank you for helping keep the library accurate.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Could not submit report. Please try again.", "warning")
    return redirect(next_target)


@scenarios_bp.post("/scenarios/select")
@requires_permission(PERM_VIEW_SCENARIOS)
def select_scenario():
    validate_csrf_or_abort()
    raw_scenario_id = request.form.get("scenario_id", "").strip()
    if not raw_scenario_id.isdigit():
        abort(400)

    scenario_id = int(raw_scenario_id)
    if not can_use_scenario_for_session(scenario_id, g.current_user):
        abort(403)

    session["scenario_id"] = scenario_id
    clear_host_training_session_context()
    next_target = safe_redirect_target(request.form.get("next"))
    return redirect(next_target)


ALLOWED_IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp"})
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB


@scenarios_bp.post("/scenarios/upload-image")
@requires_permission(PERM_CREATE_SCENARIOS)
def upload_scenario_image():
    import json
    import uuid
    from pathlib import Path
    from flask import current_app
    validate_csrf_or_abort()
    f = request.files.get("image")
    if f is None or not f.filename:
        return json.dumps({"error": "No file received"}), 400, {"Content-Type": "application/json"}

    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return json.dumps({"error": "Only JPG, PNG, GIF, and WebP files are allowed"}), 400, {"Content-Type": "application/json"}

    content = f.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        return json.dumps({"error": "Image must be 8 MB or smaller"}), 413, {"Content-Type": "application/json"}

    static_root = Path(current_app.static_folder or (Path(current_app.root_path) / "static")).resolve()
    upload_dir = static_root / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{ext}"
    (upload_dir / filename).write_bytes(content)

    return json.dumps({"path": f"uploads/{filename}"}), 200, {"Content-Type": "application/json"}


@scenarios_bp.get("/scenarios/new")
@requires_permission(PERM_CREATE_SCENARIOS)
def new_scenario_page():
    return render_create_scenario()


@scenarios_bp.get("/scenarios/<int:scenario_id>/edit")
@requires_permission(PERM_CREATE_SCENARIOS)
def edit_scenario_page(scenario_id: int):
    current_db_user = get_current_db_user()
    scenario = Scenario.query.filter_by(
        id=scenario_id, is_active=True, status=SCENARIO_STATUS_DRAFT
    ).first_or_404()
    if current_db_user is None or scenario.created_by_user_id != current_db_user.id:
        abort(403)

    from models import ScenarioPosition
    tag_ids = [link.tag_id for link in scenario.tag_links]
    positions = [sp.position for sp in scenario.position_links]

    visibility = "private"
    if scenario.is_public:
        visibility = "public"
    elif scenario.department_id:
        visibility = "department"

    question_rows = []
    for q in sorted(scenario.questions, key=lambda x: x.sort_order):
        if not q.is_active:
            continue
        from constants import QUESTION_TYPE_MULTIPLE_CHOICE
        question_rows.append({
            "prompt": q.prompt,
            "question_type": q.question_type,
            "instructor_answer": q.instructor_answer or "",
            "choices": [
                {"choice_text": c.choice_text, "is_correct": c.is_correct}
                for c in sorted(q.choices, key=lambda c: c.sort_order)
            ] if q.question_type == QUESTION_TYPE_MULTIPLE_CHOICE else [],
        })
    if not question_rows:
        question_rows = [
            {"prompt": "", "question_type": "discussion_only", "instructor_answer": ""}
            for _ in range(4)
        ]

    saved_layout = ScenarioTokenLayout.query.filter_by(scenario_id=scenario.id).first()
    prefill = {
        "title": scenario.title,
        "dispatch": scenario.dispatch_text,
        "base_image_path": scenario.base_image_path or "",
        "overlay_image_path": scenario.overlay_image_path or "",
        "is_official": scenario.is_official,
        "selected_tag_ids": tag_ids,
        "selected_positions": positions,
        "visibility": visibility,
        "training_category": scenario.training_category or "",
        "question_rows": question_rows,
        "token_layout": saved_layout.layout_json if saved_layout else "[]",
    }
    return render_create_scenario(prefill=prefill, draft_scenario_id=scenario.id)


def _persist_scenario_data(
    scenario: Scenario,
    title: str,
    dispatch: str,
    base_image_path: str,
    overlay_image_path: str | None,
    questions: list[dict],
    is_official: bool,
    is_public: bool,
    dept_id: int | None,
    training_category: str | None,
    tag_ids: list[int],
    positions: list[str],
):
    """Update an existing Scenario row with new field values and replace its questions."""
    from models import QuestionChoice, ScenarioPosition
    from constants import POSITION_CHOICES

    scenario.title = title
    scenario.dispatch_text = dispatch
    scenario.base_image_path = base_image_path
    scenario.overlay_image_path = overlay_image_path
    scenario.is_official = is_official
    scenario.is_public = is_public
    scenario.department_id = dept_id
    scenario.training_category = training_category

    # Replace questions (delete-and-recreate)
    for q in list(scenario.questions):
        db.session.delete(q)
    db.session.flush()

    for index, question in enumerate(questions, start=1):
        q_obj = Question(
            scenario_id=scenario.id,
            question_key=f"q{index}",
            prompt=question["prompt"],
            question_type=question["question_type"],
            instructor_answer=question["instructor_answer"],
            sort_order=index,
            is_active=True,
        )
        db.session.add(q_obj)
        db.session.flush()
        for choice_data in question.get("choices", []):
            db.session.add(QuestionChoice(
                question_id=q_obj.id,
                choice_text=choice_data["choice_text"],
                is_correct=choice_data["is_correct"],
                sort_order=choice_data["sort_order"],
            ))

    # Replace tags
    for link in list(scenario.tag_links):
        db.session.delete(link)
    db.session.flush()
    if tag_ids:
        valid_tag_ids = {
            t.id for t in Tag.query.filter(Tag.id.in_(tag_ids), Tag.is_active.is_(True)).all()
        }
        for tag_id in valid_tag_ids:
            db.session.add(ScenarioTag(scenario_id=scenario.id, tag_id=tag_id))

    # Replace positions
    for sp in list(scenario.position_links):
        db.session.delete(sp)
    db.session.flush()
    valid_positions = [p for p in positions if p in POSITION_CHOICES]
    for pos in valid_positions:
        db.session.add(ScenarioPosition(scenario_id=scenario.id, position=pos))


@scenarios_bp.post("/scenarios/new")
@requires_permission(PERM_CREATE_SCENARIOS)
def create_scenario():
    from authz import PERM_APPROVE_SCENARIOS
    validate_csrf_or_abort()
    save_mode = request.form.get("save_mode", "finish").strip()
    is_draft_save = save_mode == "draft"
    raw_draft_id = request.form.get("draft_scenario_id", "").strip()
    draft_scenario_id = int(raw_draft_id) if raw_draft_id.isdigit() else None

    title = request.form.get("title", "").strip()
    dispatch = request.form.get("dispatch", "").strip()
    base_image_path = request.form.get("base_image_path", "").strip()
    overlay_image_path = request.form.get("overlay_image_path", "").strip() or None
    is_official = request.form.get("is_official") == "on"

    if not is_draft_save and base_image_path:
        asset_error = scenario_asset_validation_error(base_image_path, overlay_image_path)
        if asset_error:
            return render_create_scenario(error=asset_error, status_code=400, draft_scenario_id=draft_scenario_id)

    questions, question_error = parse_create_scenario_questions()
    if question_error and not is_draft_save:
        return render_create_scenario(error=question_error, status_code=400, draft_scenario_id=draft_scenario_id)

    visibility = request.form.get("visibility", "private").strip()
    training_category = request.form.get("training_category", "").strip() or None
    raw_tag_ids = [int(v) for v in request.form.getlist("tag_ids") if v.isdigit()]
    raw_positions = request.form.getlist("positions")

    if not is_draft_save and visibility == "public":
        if not training_category:
            return render_create_scenario(error="Choose a training category to publish as public.", status_code=400, draft_scenario_id=draft_scenario_id)
        if not raw_tag_ids:
            return render_create_scenario(error="Select at least one tag to publish as public.", status_code=400, draft_scenario_id=draft_scenario_id)

    normalized_base = (normalize_static_asset_path(base_image_path) or "") if base_image_path else ""
    normalized_overlay = normalize_static_asset_path(overlay_image_path, allow_empty=True)
    current_db_user = get_current_db_user()

    is_public = visibility == "public"
    dept_id = None
    if visibility == "department" and current_db_user and current_db_user.department_id:
        dept_id = current_db_user.department_id

    can_approve = g.current_user.has_permission(PERM_APPROVE_SCENARIOS)

    if draft_scenario_id:
        scenario = Scenario.query.filter_by(
            id=draft_scenario_id, status=SCENARIO_STATUS_DRAFT, is_active=True
        ).first()
        if scenario is None or scenario.created_by_user_id != (current_db_user.id if current_db_user else None):
            return render_create_scenario(error="Draft not found or not yours.", status_code=403, draft_scenario_id=None)
        _persist_scenario_data(
            scenario=scenario,
            title=title or scenario.title,
            dispatch=dispatch or scenario.dispatch_text,
            base_image_path=normalized_base,
            overlay_image_path=normalized_overlay,
            questions=questions,
            is_official=is_official and can_approve,
            is_public=is_public,
            dept_id=dept_id,
            training_category=training_category,
            tag_ids=raw_tag_ids,
            positions=raw_positions,
        )
        action_label = "draft_save" if is_draft_save else "update_scenario"
    else:
        scenario = Scenario(
            title=title or "Untitled Draft",
            dispatch_text=dispatch or "",
            base_image_path=normalized_base,
            overlay_image_path=normalized_overlay,
            created_by_user_id=current_db_user.id if current_db_user else None,
            status=SCENARIO_STATUS_DRAFT,
            is_official=is_official and can_approve,
            is_active=True,
            is_public=is_public,
            training_category=training_category,
            department_id=dept_id,
        )
        db.session.add(scenario)
        db.session.flush()

        from models import QuestionChoice, ScenarioPosition
        from constants import POSITION_CHOICES as _PCHK
        for index, question in enumerate(questions, start=1):
            q_obj = Question(
                scenario_id=scenario.id,
                question_key=f"q{index}",
                prompt=question["prompt"],
                question_type=question["question_type"],
                instructor_answer=question["instructor_answer"],
                sort_order=index,
                is_active=True,
            )
            db.session.add(q_obj)
            db.session.flush()
            for choice_data in question.get("choices", []):
                db.session.add(QuestionChoice(
                    question_id=q_obj.id,
                    choice_text=choice_data["choice_text"],
                    is_correct=choice_data["is_correct"],
                    sort_order=choice_data["sort_order"],
                ))

        if raw_tag_ids:
            valid_tag_ids = {
                t.id for t in Tag.query.filter(Tag.id.in_(raw_tag_ids), Tag.is_active.is_(True)).all()
            }
            for tag_id in valid_tag_ids:
                db.session.add(ScenarioTag(scenario_id=scenario.id, tag_id=tag_id))

        valid_pos = [p for p in raw_positions if p in _PCHK]
        for pos in valid_pos:
            db.session.add(ScenarioPosition(scenario_id=scenario.id, position=pos))

        action_label = "create_scenario"

    import json as _json
    raw_token_layout = request.form.get("token_layout", "[]").strip() or "[]"
    try:
        _json.loads(raw_token_layout)
    except (ValueError, TypeError):
        raw_token_layout = "[]"
    layout_obj = ScenarioTokenLayout.query.filter_by(scenario_id=scenario.id).first()
    if layout_obj:
        layout_obj.layout_json = raw_token_layout
    else:
        db.session.add(ScenarioTokenLayout(scenario_id=scenario.id, layout_json=raw_token_layout))

    if not is_draft_save:
        from datetime import datetime as _dt
        scenario.status = SCENARIO_STATUS_SUBMITTED
        scenario.submitted_at = _dt.utcnow()

    append_admin_audit_log(
        actor=current_db_user,
        action=action_label,
        target_type="scenario",
        target_id=scenario.id,
        target_label=scenario.title,
        details=f"{'Draft saved' if is_draft_save else 'Submitted for review'} with {len(questions)} question(s).",
    )
    db.session.commit()

    if is_draft_save:
        flash(f"Draft saved: '{scenario.title}'.", "success")
        return redirect(url_for("scenarios.edit_scenario_page", scenario_id=scenario.id))

    flash(f"'{scenario.title}' submitted for review.", "success")
    session["scenario_id"] = scenario.id
    return redirect(url_for("main.board"))


@scenarios_bp.post("/scenarios/autosave")
@requires_permission(PERM_CREATE_SCENARIOS)
def autosave_scenario():
    """AJAX endpoint for debounced auto-save during scenario creation."""
    import json
    validate_csrf_or_abort()
    current_db_user = get_current_db_user()
    if current_db_user is None:
        return json.dumps({"error": "not authenticated"}), 403, {"Content-Type": "application/json"}

    raw_draft_id = request.form.get("draft_scenario_id", "").strip()
    draft_scenario_id = int(raw_draft_id) if raw_draft_id.isdigit() else None

    title = request.form.get("title", "").strip() or "Untitled Draft"
    dispatch = request.form.get("dispatch", "").strip() or ""
    base_image_path = request.form.get("base_image_path", "").strip()
    overlay_image_path = request.form.get("overlay_image_path", "").strip() or None
    is_official = request.form.get("is_official") == "on"
    visibility = request.form.get("visibility", "private").strip()
    training_category = request.form.get("training_category", "").strip() or None
    raw_tag_ids = [int(v) for v in request.form.getlist("tag_ids") if v.isdigit()]
    raw_positions = request.form.getlist("positions")

    questions, _ = parse_create_scenario_questions()
    normalized_base = (normalize_static_asset_path(base_image_path) or "") if base_image_path else ""
    normalized_overlay = normalize_static_asset_path(overlay_image_path, allow_empty=True)
    is_public = visibility == "public"
    dept_id = None
    if visibility == "department" and current_db_user.department_id:
        dept_id = current_db_user.department_id
    can_approve = g.current_user.has_permission(PERM_APPROVE_SCENARIOS)

    if draft_scenario_id:
        scenario = Scenario.query.filter_by(
            id=draft_scenario_id, status=SCENARIO_STATUS_DRAFT, is_active=True
        ).first()
        if scenario is None or scenario.created_by_user_id != current_db_user.id:
            return json.dumps({"error": "draft not found"}), 403, {"Content-Type": "application/json"}
        _persist_scenario_data(
            scenario=scenario,
            title=title,
            dispatch=dispatch,
            base_image_path=normalized_base,
            overlay_image_path=normalized_overlay,
            questions=questions,
            is_official=is_official and can_approve,
            is_public=is_public,
            dept_id=dept_id,
            training_category=training_category,
            tag_ids=raw_tag_ids,
            positions=raw_positions,
        )
    else:
        from models import QuestionChoice, ScenarioPosition
        from constants import POSITION_CHOICES as _PCHK
        scenario = Scenario(
            title=title,
            dispatch_text=dispatch,
            base_image_path=normalized_base,
            overlay_image_path=normalized_overlay,
            created_by_user_id=current_db_user.id,
            status=SCENARIO_STATUS_DRAFT,
            is_official=is_official and can_approve,
            is_active=True,
            is_public=is_public,
            training_category=training_category,
            department_id=dept_id,
        )
        db.session.add(scenario)
        db.session.flush()
        for index, question in enumerate(questions, start=1):
            q_obj = Question(
                scenario_id=scenario.id,
                question_key=f"q{index}",
                prompt=question["prompt"],
                question_type=question["question_type"],
                instructor_answer=question["instructor_answer"],
                sort_order=index,
                is_active=True,
            )
            db.session.add(q_obj)
            db.session.flush()
            for choice_data in question.get("choices", []):
                db.session.add(QuestionChoice(
                    question_id=q_obj.id,
                    choice_text=choice_data["choice_text"],
                    is_correct=choice_data["is_correct"],
                    sort_order=choice_data["sort_order"],
                ))
        if raw_tag_ids:
            valid_tag_ids = {
                t.id for t in Tag.query.filter(Tag.id.in_(raw_tag_ids), Tag.is_active.is_(True)).all()
            }
            for tag_id in valid_tag_ids:
                db.session.add(ScenarioTag(scenario_id=scenario.id, tag_id=tag_id))
        valid_pos = [p for p in raw_positions if p in _PCHK]
        for pos in valid_pos:
            db.session.add(ScenarioPosition(scenario_id=scenario.id, position=pos))

    import json as _json2
    raw_tl = request.form.get("token_layout", "[]").strip() or "[]"
    try:
        _json2.loads(raw_tl)
    except (ValueError, TypeError):
        raw_tl = "[]"
    tl_obj = ScenarioTokenLayout.query.filter_by(scenario_id=scenario.id).first()
    if tl_obj:
        tl_obj.layout_json = raw_tl
    else:
        db.session.add(ScenarioTokenLayout(scenario_id=scenario.id, layout_json=raw_tl))

    db.session.commit()
    return (
        json.dumps({"scenario_id": scenario.id, "saved": True}),
        200,
        {"Content-Type": "application/json"},
    )


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
    from helpers import QUESTION_TYPE_MULTIPLE_CHOICE
    validate_csrf_or_abort()
    scenario_row, scenario = get_current_scenario()
    db_user = get_current_db_user()
    answers = {}
    selected_choice_ids: dict[str, int | None] = {}
    for question in scenario["questions"]:
        qid = str(question["id"])
        if question.get("question_type") == QUESTION_TYPE_MULTIPLE_CHOICE:
            raw_choice = request.form.get(f"qc_{question['id']}", "").strip()
            choice_id = int(raw_choice) if raw_choice.isdigit() else None
            selected_choice_ids[qid] = choice_id
            # Populate answer_text from the selected choice text for display/scoring
            chosen = next(
                (c for c in question.get("choices", []) if c["id"] == choice_id),
                None,
            )
            answers[qid] = chosen["choice_text"] if chosen else ""
        else:
            answers[qid] = request.form.get(f"q_{question['id']}", "").strip()
            selected_choice_ids[qid] = None
    question_feedback = build_submission_feedback(scenario, answers)
    participant, training_session, submission_error = validate_submission_context(scenario_row)
    saved_submission = None
    submission_message = None

    submission_message_level = "success"
    show_instructor_answers = False

    if participant is None or training_session is None:
        submission_error = None
        if db_user is not None:
            drill = persist_drill_attempt(db_user, scenario_row, answers, selected_choice_ids=selected_choice_ids)
            db.session.commit()
            show_instructor_answers = True
            submission_message = f"Drill attempt #{drill.attempt_number} saved."
        else:
            # Guest: show instructor answers as payoff even without saving
            show_instructor_answers = True
            submission_message_level = "info"
    elif submission_error is None:
        try:
            saved_submission = persist_submission(
                scenario_row=scenario_row,
                scenario=scenario,
                participant=participant,
                training_session=training_session,
                answers=answers,
                selected_choice_ids=selected_choice_ids,
            )
            db.session.commit()
            submission_message = (
                f"Attempt #{saved_submission.attempt_number} submitted. "
                f"The host board is now using your latest answers."
            )
        except IntegrityError:
            db.session.rollback()
            submission_error = "Your answers could not be saved. Please try submitting again."

    user_has_flagged = (
        db_user is not None and ScenarioFlag.query.filter_by(
            scenario_id=scenario_row.id, user_id=db_user.id
        ).first() is not None
    )
    saved_layout = ScenarioTokenLayout.query.filter_by(scenario_id=scenario_row.id).first()
    from constants import CATEGORY_TOKEN_PALETTES, TOKEN_PALETTE_DEFAULT
    token_palette = CATEGORY_TOKEN_PALETTES.get(scenario_row.training_category, TOKEN_PALETTE_DEFAULT)
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
        user_has_flagged=user_has_flagged,
        initial_token_layout=saved_layout.layout_json if saved_layout else "[]",
        token_palette=token_palette,
        revealed_submission=(
            build_revealed_submission_view_model(g.active_training_session)
            if g.active_training_session is not None
            else None
        ),
    )


@scenarios_bp.post("/save-guest-drill")
@requires_permission(PERM_SUBMIT_ANSWERS)
def save_guest_drill():
    """Auto-save a drill attempt from localStorage after a guest creates an account."""
    import json
    validate_csrf_or_abort()
    db_user = get_current_db_user()
    if db_user is None:
        from flask import abort as _abort
        _abort(401)

    raw_scenario_id = request.form.get("scenario_id", "").strip()
    answers_json = request.form.get("answers_json", "").strip()
    if not raw_scenario_id.isdigit() or not answers_json:
        from flask import abort as _abort
        _abort(400)

    scenario_id = int(raw_scenario_id)
    scenario_row = Scenario.query.filter_by(id=scenario_id, is_active=True).first()
    if scenario_row is None:
        from flask import abort as _abort
        _abort(404)

    try:
        raw_answers = json.loads(answers_json)
        if not isinstance(raw_answers, dict):
            from flask import abort as _abort
            _abort(400)
    except (json.JSONDecodeError, ValueError):
        from flask import abort as _abort
        _abort(400)

    # Build answer map from {str(question_id): answer_text}
    answers: dict[str, str] = {}
    selected_choice_ids: dict[str, int | None] = {}
    for key, value in raw_answers.items():
        if key.startswith("_choice_"):
            qid = key[len("_choice_"):]
            selected_choice_ids[qid] = int(value) if str(value).isdigit() else None
        else:
            answers[key] = str(value)[:2000]

    # Avoid duplicate saves
    from models import DrillAttempt as _DrillAttempt
    existing = _DrillAttempt.query.filter_by(
        user_id=db_user.id, scenario_id=scenario_id
    ).first()
    if existing is not None:
        # 409 tells the client to clear localStorage (already saved)
        from flask import jsonify
        return jsonify({"status": "already_saved"}), 409

    try:
        persist_drill_attempt(db_user, scenario_row, answers, selected_choice_ids=selected_choice_ids)
        db.session.commit()
    except Exception:
        db.session.rollback()
        from flask import abort as _abort
        _abort(500)

    from flask import jsonify
    return jsonify({"status": "saved"}), 200


@scenarios_bp.post("/scenario/save-token-layout")
@requires_permission(PERM_CREATE_SCENARIOS)
def save_scenario_token_layout():
    """Save the creator's initial token arrangement for a scenario."""
    import json as _json
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    current_db_user = get_current_db_user()
    if current_db_user is None or scenario.created_by_user_id != current_db_user.id:
        abort(403)

    raw_layout = request.form.get("layout_json", "[]")
    try:
        parsed = _json.loads(raw_layout)
        if not isinstance(parsed, list):
            raise ValueError
        layout_json = _json.dumps(parsed)
    except (ValueError, TypeError):
        layout_json = "[]"

    from models import ScenarioTokenLayout
    layout = ScenarioTokenLayout.query.filter_by(scenario_id=scenario.id).first()
    if layout is None:
        layout = ScenarioTokenLayout(scenario_id=scenario.id, layout_json=layout_json)
        db.session.add(layout)
    else:
        layout.layout_json = layout_json
    db.session.commit()

    next_target = safe_redirect_target(request.form.get("next"))
    flash("Starting token layout saved.", "success")
    return redirect(next_target)


@scenarios_bp.post("/scenario/submit-review")
@requires_permission(PERM_CREATE_SCENARIOS)
def submit_scenario_for_review():
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()
    apply_scenario_transition_or_abort(scenario=scenario, action=ScenarioAction.SUBMIT_FOR_REVIEW, actor=actor)
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
    apply_scenario_transition_or_abort(scenario=scenario, action=ScenarioAction.APPROVE, actor=actor)
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
    apply_scenario_transition_or_abort(scenario=scenario, action=ScenarioAction.ARCHIVE, actor=actor)
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


@scenarios_bp.post("/scenario/adopt")
@requires_permission(PERM_CREATE_SCENARIOS)
def adopt_scenario_for_department():
    """Fork a public scenario into the instructor's department as a new draft."""
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()
    if actor is None:
        abort(403)
    if actor.department_id is None:
        flash("You must be a member of a department to adopt scenarios.", "warning")
        return redirect(safe_redirect_target(request.form.get("next")))

    # Only public/approved scenarios can be adopted
    if not scenario.is_public or scenario.status != "approved":
        abort(409)

    forked = fork_scenario_for_department(original=scenario, adopting_user=actor)
    append_admin_audit_log(
        actor=actor,
        action="adopt_scenario",
        target_type="scenario",
        target_id=forked.id,
        target_label=forked.title,
        details=f"Forked from scenario #{scenario.id}.",
    )
    # Note: fork_scenario_for_department already committed
    flash(f"Scenario adopted as a new draft in your department library.", "success")
    session["scenario_id"] = forked.id
    return redirect(url_for("main.board"))


@scenarios_bp.post("/scenarios/clone")
@requires_permission(PERM_CREATE_SCENARIOS)
def clone_scenario():
    """Clone any approved scenario into the user's personal draft library."""
    validate_csrf_or_abort()
    scenario = get_posted_scenario_or_abort()
    actor = get_current_db_user()
    if actor is None:
        abort(403)
    if scenario.status != "approved":
        abort(409)

    from models import QuestionChoice
    cloned = Scenario(
        title=f"Copy of {scenario.title}"[:200],
        dispatch_text=scenario.dispatch_text,
        base_image_path=scenario.base_image_path,
        overlay_image_path=scenario.overlay_image_path,
        created_by_user_id=actor.id,
        status=SCENARIO_STATUS_DRAFT,
        is_official=False,
        is_active=True,
        is_public=False,
        training_category=scenario.training_category,
        department_id=actor.department_id,
        forked_from_scenario_id=scenario.id,
    )
    db.session.add(cloned)
    db.session.flush()

    for question in sorted(scenario.questions, key=lambda q: q.sort_order):
        if not question.is_active:
            continue
        new_q = Question(
            scenario_id=cloned.id,
            question_key=question.question_key,
            prompt=question.prompt,
            question_type=question.question_type,
            instructor_answer=question.instructor_answer,
            sort_order=question.sort_order,
            is_active=True,
        )
        db.session.add(new_q)
        db.session.flush()
        for choice in sorted(question.choices, key=lambda c: c.sort_order):
            db.session.add(QuestionChoice(
                question_id=new_q.id,
                choice_text=choice.choice_text,
                is_correct=choice.is_correct,
                sort_order=choice.sort_order,
            ))

    append_admin_audit_log(
        actor=actor,
        action="clone_scenario",
        target_type="scenario",
        target_id=cloned.id,
        target_label=cloned.title,
        details=f"Cloned from scenario #{scenario.id}.",
    )
    db.session.commit()
    flash(f"Scenario cloned. Edit your draft: '{cloned.title}'.", "success")
    session["scenario_id"] = cloned.id
    return redirect(url_for("main.board"))


@scenarios_bp.get("/library")
def public_scenario_library():
    """Public scenario library — accessible to guests and account holders."""
    db_user = get_current_db_user()
    category = request.args.get("category", "").strip()
    tag_slugs = [s.strip() for s in request.args.getlist("tag") if s.strip()]
    keyword = request.args.get("q", "").strip()[:100]
    position = request.args.get("position", "").strip()
    library = build_public_library_view_model(db_user, category or None, tag_slugs, keyword, position or None)
    from helpers import get_saved_scenario_ids_for_user
    scenario_ids = [s["id"] for s in library["scenarios"]]
    user_lists = get_user_lists(db_user)
    saved_ids = get_saved_scenario_ids_for_user(db_user, scenario_ids)
    return render_template(
        "public_library.html",
        library=library,
        is_authenticated=(db_user is not None),
        user_lists=user_lists,
        saved_scenario_ids=saved_ids,
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

    # Sync positions
    from constants import POSITION_CHOICES
    from models import ScenarioPosition
    raw_positions = [p for p in request.form.getlist("positions") if p in POSITION_CHOICES]
    scenario.position_links.clear()
    for pos in raw_positions:
        db.session.add(ScenarioPosition(scenario_id=scenario.id, position=pos))

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
