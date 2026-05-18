from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from authz import PERM_SUBMIT_ANSWERS, requires_permission
from extensions import db
from helpers import get_current_db_user, validate_csrf_or_abort
from models import Scenario, UserList, UserListScenario

lists_bp = Blueprint("lists", __name__)


def _get_list_or_abort(list_id: int) -> UserList:
    db_user = get_current_db_user()
    if db_user is None:
        abort(403)
    user_list = UserList.query.filter_by(id=list_id, user_id=db_user.id).first()
    if user_list is None:
        abort(404)
    return user_list


@lists_bp.post("/lists/new")
@requires_permission(PERM_SUBMIT_ANSWERS)
def create_list():
    validate_csrf_or_abort()
    db_user = get_current_db_user()
    if db_user is None:
        abort(403)
    name = request.form.get("list_name", "").strip()[:100]
    scenario_id_raw = request.form.get("scenario_id", "").strip()
    next_url = request.form.get("next", url_for("scenarios.scenario_library"))

    if not name:
        flash("List name cannot be empty.", "warning")
        return redirect(next_url)

    user_list = UserList(user_id=db_user.id, name=name)
    db.session.add(user_list)
    db.session.flush()

    if scenario_id_raw.isdigit():
        scenario = Scenario.query.get(int(scenario_id_raw))
        if scenario:
            db.session.add(UserListScenario(list_id=user_list.id, scenario_id=scenario.id))

    db.session.commit()
    flash(f'List "{name}" created.', "success")
    return redirect(next_url)


@lists_bp.post("/lists/<int:list_id>/add")
@requires_permission(PERM_SUBMIT_ANSWERS)
def add_to_list(list_id: int):
    validate_csrf_or_abort()
    user_list = _get_list_or_abort(list_id)
    scenario_id_raw = request.form.get("scenario_id", "").strip()
    next_url = request.form.get("next", url_for("scenarios.scenario_library"))

    if not scenario_id_raw.isdigit():
        abort(400)
    scenario = Scenario.query.get(int(scenario_id_raw))
    if scenario is None:
        abort(404)

    try:
        db.session.add(UserListScenario(list_id=user_list.id, scenario_id=scenario.id))
        db.session.commit()
        flash(f'Added to "{user_list.name}".', "success")
    except IntegrityError:
        db.session.rollback()

    return redirect(next_url)


@lists_bp.post("/lists/<int:list_id>/remove")
@requires_permission(PERM_SUBMIT_ANSWERS)
def remove_from_list(list_id: int):
    validate_csrf_or_abort()
    user_list = _get_list_or_abort(list_id)
    scenario_id_raw = request.form.get("scenario_id", "").strip()
    next_url = request.form.get("next", url_for("scenarios.scenario_library"))

    if not scenario_id_raw.isdigit():
        abort(400)

    UserListScenario.query.filter_by(
        list_id=user_list.id, scenario_id=int(scenario_id_raw)
    ).delete()
    db.session.commit()
    return redirect(next_url)


@lists_bp.post("/lists/<int:list_id>/delete")
@requires_permission(PERM_SUBMIT_ANSWERS)
def delete_list(list_id: int):
    validate_csrf_or_abort()
    user_list = _get_list_or_abort(list_id)
    name = user_list.name
    db.session.delete(user_list)
    db.session.commit()
    flash(f'List "{name}" deleted.', "success")
    return redirect(url_for("scenarios.scenario_library"))


@lists_bp.get("/lists/<int:list_id>")
@requires_permission(PERM_SUBMIT_ANSWERS)
def view_list(list_id: int):
    user_list = _get_list_or_abort(list_id)
    scenarios = [link.scenario for link in user_list.scenario_links if link.scenario and link.scenario.is_active]
    return render_template(
        "user_list_detail.html",
        user_list=user_list,
        scenarios=scenarios,
    )
