from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from authz import PERM_MANAGE_USERS, ROLE_PARTICIPANT, normalize_roles, requires_permission
from extensions import db
from helpers import append_admin_audit_log, get_current_db_user, render_admin_users, slugify_tag, validate_csrf_or_abort
from models import Role, Tag, User, UserRole

admin_bp = Blueprint("admin", __name__)


@admin_bp.get("/admin/users")
@requires_permission(PERM_MANAGE_USERS)
def admin_users():
    return render_admin_users()


@admin_bp.post("/admin/users/create")
@requires_permission(PERM_MANAGE_USERS)
def admin_create_user():
    validate_csrf_or_abort()
    email = request.form.get("email", "").strip().lower()
    full_name = request.form.get("full_name", "").strip() or None
    password = request.form.get("password", "")
    is_active = request.form.get("is_active") == "on"
    actor = get_current_db_user()

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

    append_admin_audit_log(
        actor=actor,
        action="create_user",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        details=(
            f"Roles: {', '.join(sorted(selected_role_names))} | "
            f"Active: {'yes' if is_active else 'no'}"
        ),
    )
    db.session.commit()
    return render_admin_users(success=f"Created user {email}.")


@admin_bp.post("/admin/users/<int:user_id>/update")
@requires_permission(PERM_MANAGE_USERS)
def admin_update_user(user_id: int):
    validate_csrf_or_abort()
    user = User.query.filter_by(id=user_id).first()
    if user is None:
        return render_admin_users(error="User not found.", status_code=404)
    actor = get_current_db_user()

    all_roles = {role.name: role for role in Role.query.all()}
    selected_role_names = normalize_roles(request.form.getlist("roles"))
    selected_role_names = frozenset(role for role in selected_role_names if role in all_roles)
    if not selected_role_names:
        selected_role_names = frozenset({ROLE_PARTICIPANT})

    original_roles = sorted(link.role.name for link in user.role_links if link.role)
    original_active = user.is_active
    original_name = user.full_name or ""
    target_role_ids = {all_roles[role_name].id for role_name in selected_role_names}
    current_role_ids = {link.role_id for link in user.role_links}
    for link in list(user.role_links):
        if link.role_id not in target_role_ids:
            db.session.delete(link)
    for role_id in target_role_ids.difference(current_role_ids):
        db.session.add(UserRole(user_id=user.id, role_id=role_id))

    user.full_name = request.form.get("full_name", "").strip() or None
    user.is_active = request.form.get("is_active") == "on"
    append_admin_audit_log(
        actor=actor,
        action="update_user",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        details=(
            f"Name: {original_name or '(blank)'} -> {user.full_name or '(blank)'} | "
            f"Active: {'yes' if original_active else 'no'} -> {'yes' if user.is_active else 'no'} | "
            f"Roles: {', '.join(original_roles) or '(none)'} -> {', '.join(sorted(selected_role_names))}"
        ),
    )
    db.session.commit()
    return redirect(url_for("admin.admin_users"))


@admin_bp.post("/admin/users/<int:user_id>/reset-password")
@requires_permission(PERM_MANAGE_USERS)
def admin_reset_user_password(user_id: int):
    validate_csrf_or_abort()
    user = User.query.filter_by(id=user_id).first()
    if user is None:
        return render_admin_users(error="User not found.", status_code=404)
    actor = get_current_db_user()

    new_password = request.form.get("new_password", "")
    if len(new_password) < 8:
        return render_admin_users(error="New password must be at least 8 characters.", status_code=400)

    user.password_hash = generate_password_hash(new_password)
    append_admin_audit_log(
        actor=actor,
        action="reset_user_password",
        target_type="user",
        target_id=user.id,
        target_label=user.email,
        details="Password reset from admin screen.",
    )
    db.session.commit()
    return redirect(url_for("admin.admin_users"))


# ── Tag management ───────────────────────────────────────────────────────────

@admin_bp.get("/admin/tags")
@requires_permission(PERM_MANAGE_USERS)
def admin_tags():
    tags = Tag.query.order_by(Tag.name).all()
    return render_template("admin_tags.html", tags=tags, error=None)


@admin_bp.post("/admin/tags/create")
@requires_permission(PERM_MANAGE_USERS)
def admin_create_tag():
    validate_csrf_or_abort()
    name = request.form.get("name", "").strip()
    if not name:
        tags = Tag.query.order_by(Tag.name).all()
        return render_template("admin_tags.html", tags=tags, error="Tag name is required."), 400

    slug = slugify_tag(name)
    if not slug:
        tags = Tag.query.order_by(Tag.name).all()
        return render_template("admin_tags.html", tags=tags, error="Tag name produced an empty slug."), 400
    if Tag.query.filter_by(slug=slug).first():
        tags = Tag.query.order_by(Tag.name).all()
        return render_template("admin_tags.html", tags=tags, error=f"A tag with slug '{slug}' already exists."), 409

    db.session.add(Tag(name=name, slug=slug, is_active=True))
    db.session.commit()
    flash(f"Tag '{name}' created.", "success")
    return redirect(url_for("admin.admin_tags"))


@admin_bp.post("/admin/tags/<int:tag_id>/toggle")
@requires_permission(PERM_MANAGE_USERS)
def admin_toggle_tag(tag_id: int):
    validate_csrf_or_abort()
    tag = Tag.query.get_or_404(tag_id)
    tag.is_active = not tag.is_active
    db.session.commit()
    state = "activated" if tag.is_active else "deactivated"
    flash(f"Tag '{tag.name}' {state}.", "success")
    return redirect(url_for("admin.admin_tags"))


@admin_bp.post("/admin/tags/<int:tag_id>/rename")
@requires_permission(PERM_MANAGE_USERS)
def admin_rename_tag(tag_id: int):
    validate_csrf_or_abort()
    tag = Tag.query.get_or_404(tag_id)
    name = request.form.get("name", "").strip()
    if not name:
        flash("Tag name is required.", "warning")
        return redirect(url_for("admin.admin_tags"))
    slug = slugify_tag(name)
    existing = Tag.query.filter_by(slug=slug).first()
    if existing and existing.id != tag.id:
        flash(f"A tag with slug '{slug}' already exists.", "warning")
        return redirect(url_for("admin.admin_tags"))
    tag.name = name
    tag.slug = slug
    db.session.commit()
    flash(f"Tag renamed to '{name}'.", "success")
    return redirect(url_for("admin.admin_tags"))
