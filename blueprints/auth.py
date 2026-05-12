import hashlib
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from authz import ROLE_PARTICIPANT, requires_permission, PERM_VIEW_SCENARIOS
from extensions import db
from helpers import (
    complete_user_sign_in,
    email_looks_valid,
    is_login_rate_limited,
    issue_account_activation,
    record_failed_login,
    clear_failed_logins,
    render_create_account,
    render_login,
    rotate_csrf_token,
    safe_redirect_target,
    user_is_staff,
    validate_csrf_or_abort,
)
from models import AccountActivationToken, MagicLoginToken, Role, User, UserRole

auth_bp = Blueprint("auth", __name__)


@auth_bp.get("/create-account")
def create_account_page():
    if g.current_user.user_id != "guest":
        return redirect(url_for("main.board"))
    return render_create_account()


@auth_bp.post("/create-account")
def create_account_submit():
    validate_csrf_or_abort()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not full_name:
        return render_create_account(error="Full name is required.", status_code=400)
    if not email:
        return render_create_account(error="Email is required.", status_code=400)
    if not email_looks_valid(email):
        return render_create_account(error="Enter a valid email address.", status_code=400)
    if len(password) < 8:
        return render_create_account(error="Password must be at least 8 characters.", status_code=400)
    if password != confirm_password:
        return render_create_account(error="Passwords do not match.", status_code=400)
    if User.query.filter_by(email=email).first():
        return render_create_account(
            error="An account with that email already exists. Sign in or use a different email.",
            status_code=409,
        )

    participant_role = Role.query.filter_by(name=ROLE_PARTICIPANT).first()
    if participant_role is None:
        abort(500)

    user = User(
        email=email,
        full_name=full_name[:120],
        password_hash=generate_password_hash(password),
        is_active=True,
        is_email_verified=False,
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(UserRole(user_id=user.id, role_id=participant_role.id))
    activation_link = issue_account_activation(user)
    db.session.commit()
    return render_create_account(
        success="Account created. Activate it from the verification link before signing in.",
        activation_link=activation_link,
        status_code=201,
    )


@auth_bp.get("/login")
def login_page():
    next_target = safe_redirect_target(request.args.get("next"))
    if g.current_user.user_id != "guest":
        return redirect(next_target)
    return render_login(error=None, next_target=next_target)


@auth_bp.post("/login")
def login_submit():
    validate_csrf_or_abort()
    next_target = safe_redirect_target(request.form.get("next"))
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password:
        return render_login(error="Email and password are required.", next_target=next_target), 400
    if is_login_rate_limited(email):
        return (
            render_login(error="Too many attempts. Wait 10 minutes and try again.", next_target=next_target),
            429,
        )

    user = User.query.filter_by(email=email).first()
    if not user or not user.password_hash:
        record_failed_login(email)
        return render_login(error="Invalid credentials.", next_target=next_target), 401
    if not check_password_hash(user.password_hash, password):
        record_failed_login(email)
        return render_login(error="Invalid credentials.", next_target=next_target), 401
    if not user.is_active:
        record_failed_login(email)
        return render_login(error="This account is inactive. Contact an administrator.", next_target=next_target), 403
    if not user.is_email_verified:
        record_failed_login(email)
        return render_login(
            error="Activate your account from the verification link before signing in.",
            next_target=next_target,
        ), 403

    clear_failed_logins(email)
    user.last_login_at = datetime.utcnow()
    complete_user_sign_in(user)
    db.session.commit()
    return redirect(next_target)


@auth_bp.post("/logout")
def logout():
    validate_csrf_or_abort()
    previous_scenario_id = session.get("scenario_id")
    session.clear()
    if isinstance(previous_scenario_id, int):
        session["scenario_id"] = previous_scenario_id
    rotate_csrf_token()
    return redirect(url_for("main.home"))


@auth_bp.get("/magic-link/request")
def request_magic_link_page():
    return render_template("request_magic_link.html", error=None)


@auth_bp.post("/magic-link/request")
def request_magic_link_submit():
    from flask import current_app
    validate_csrf_or_abort()
    email = request.form.get("email", "").strip().lower()
    if not email:
        return render_template("request_magic_link.html", error="Email is required."), 400

    magic_link = None
    user = User.query.filter_by(email=email, is_active=True).first()
    if user and user_is_staff(user):
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        ttl_minutes = current_app.config["MAGIC_LINK_TTL_MINUTES"]
        expires_at = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        db.session.add(
            MagicLoginToken(
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        db.session.commit()
        if current_app.config["ENABLE_MAGIC_LINK_DEBUG"]:
            magic_link = url_for("auth.consume_magic_link", token=token, _external=True)

    return render_template(
        "magic_link_sent.html",
        requested_email=email,
        magic_link=magic_link,
    )


@auth_bp.get("/magic-link/consume")
def consume_magic_link():
    token = request.args.get("token", "")
    if not token:
        abort(400)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.utcnow()
    token_row = (
        MagicLoginToken.query.filter_by(token_hash=token_hash)
        .filter(MagicLoginToken.used_at.is_(None))
        .filter(MagicLoginToken.expires_at >= now)
        .order_by(MagicLoginToken.created_at.desc())
        .first()
    )
    if token_row is None or token_row.user is None or not token_row.user.is_active:
        return render_login(error="Magic link is invalid or expired.", next_target=url_for("main.board")), 400
    if not user_is_staff(token_row.user):
        return render_login(error="This account cannot access instructor/admin login.", next_target=url_for("main.board")), 403

    token_row.used_at = now
    token_row.user.last_login_at = now
    complete_user_sign_in(token_row.user)
    db.session.commit()
    return redirect(url_for("main.board"))


@auth_bp.get("/activate-account")
def activate_account():
    token = request.args.get("token", "")
    if not token:
        abort(400)

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.utcnow()
    token_row = (
        AccountActivationToken.query.filter_by(token_hash=token_hash)
        .filter(AccountActivationToken.used_at.is_(None))
        .filter(AccountActivationToken.expires_at >= now)
        .order_by(AccountActivationToken.created_at.desc())
        .first()
    )
    if token_row is None or token_row.user is None:
        flash("That activation link is invalid or expired.", "warning")
        return redirect(url_for("auth.create_account_page"))
    if not token_row.user.is_active:
        token_row.used_at = now
        db.session.commit()
        flash("That account is inactive. Contact an administrator.", "warning")
        return redirect(url_for("auth.create_account_page"))

    token_row.used_at = now
    token_row.user.is_email_verified = True
    token_row.user.last_login_at = now
    complete_user_sign_in(token_row.user)
    db.session.commit()
    flash("Account activated. You are now signed in.", "success")
    return redirect(url_for("main.board"))
