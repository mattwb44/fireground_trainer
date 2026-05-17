import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from authz import PERM_MANAGE_DEPARTMENT, PERM_MANAGE_USERS, requires_permission
from extensions import db
from helpers import get_current_db_user, validate_csrf_or_abort
from models import Department, User

departments_bp = Blueprint("departments", __name__)


def _generate_invite_code() -> str:
    return secrets.token_urlsafe(12)


# ── Admin: list and create departments ──────────────────────────────────────

@departments_bp.get("/admin/departments")
@requires_permission(PERM_MANAGE_USERS)
def admin_departments():
    departments = Department.query.order_by(Department.created_at.desc()).all()
    return render_template("admin_departments.html", departments=departments, error=None)


@departments_bp.post("/admin/departments/create")
@requires_permission(PERM_MANAGE_USERS)
def admin_create_department():
    validate_csrf_or_abort()
    name = request.form.get("name", "").strip()

    if not name:
        departments = Department.query.order_by(Department.created_at.desc()).all()
        return render_template("admin_departments.html", departments=departments, error="Department name is required."), 400

    actor = get_current_db_user()
    dept = Department(
        name=name,
        invite_code=_generate_invite_code(),
        created_by_user_id=actor.id if actor else None,
    )
    db.session.add(dept)
    db.session.commit()
    flash(f"Department '{dept.name}' created.", "success")
    return redirect(url_for("departments.admin_departments"))


# ── TC: department settings ──────────────────────────────────────────────────

@departments_bp.get("/department/settings")
@requires_permission(PERM_MANAGE_DEPARTMENT)
def department_settings():
    db_user = get_current_db_user()
    if db_user is None or db_user.department_id is None:
        flash("You are not assigned to a department.", "warning")
        return redirect(url_for("main.board"))
    dept = Department.query.get_or_404(db_user.department_id)
    return render_template("department_settings.html", dept=dept, error=None)


@departments_bp.post("/department/settings/regenerate-code")
@requires_permission(PERM_MANAGE_DEPARTMENT)
def department_regenerate_code():
    validate_csrf_or_abort()
    db_user = get_current_db_user()
    if db_user is None or db_user.department_id is None:
        abort(403)
    dept = Department.query.get_or_404(db_user.department_id)
    dept.invite_code = _generate_invite_code()
    db.session.commit()
    flash("Invite code regenerated. The old code no longer works.", "success")
    return redirect(url_for("departments.department_settings"))


# ── User: join a department ──────────────────────────────────────────────────

@departments_bp.get("/department/join")
def department_join_page():
    if g.current_user.user_id == "guest":
        return redirect(url_for("auth.login_page", next=url_for("departments.department_join_page")))
    db_user = get_current_db_user()
    already_in = db_user and db_user.department_id is not None
    return render_template("department_join.html", error=None, already_in=already_in, dept=db_user.department if already_in else None)


@departments_bp.post("/department/join")
def department_join_submit():
    validate_csrf_or_abort()
    if g.current_user.user_id == "guest":
        abort(401)
    invite_code = request.form.get("invite_code", "").strip()
    if not invite_code:
        return render_template("department_join.html", error="Enter an invite code.", already_in=False, dept=None), 400

    dept = Department.query.filter_by(invite_code=invite_code).first()
    if dept is None:
        return render_template("department_join.html", error="That invite code is not valid. Check with your Training Chief.", already_in=False, dept=None), 400

    db_user = get_current_db_user()
    if db_user is None:
        abort(500)
    db_user.department_id = dept.id
    db.session.commit()
    flash(f"You joined {dept.name}.", "success")
    return redirect(url_for("main.board"))
