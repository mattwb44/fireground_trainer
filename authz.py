from __future__ import annotations

from dataclasses import dataclass, field
from functools import wraps
from typing import Iterable

from flask import abort, g

ROLE_ADMIN = "admin"
ROLE_TRAINING_CHIEF = "training_chief"
ROLE_INSTRUCTOR = "instructor"
ROLE_PARTICIPANT = "participant"

PERM_VIEW_SCENARIOS = "view_scenarios"
PERM_SUBMIT_ANSWERS = "submit_answers"
PERM_REVEAL_INSTRUCTOR_ANSWERS = "reveal_instructor_answers"
PERM_CREATE_SCENARIOS = "create_scenarios"
PERM_CREATE_SESSIONS = "create_sessions"
PERM_APPROVE_SCENARIOS = "approve_scenarios"
PERM_VIEW_SESSION_SUBMISSIONS = "view_session_submissions"
PERM_SELECT_REVIEW_ANSWER = "select_review_answer"
PERM_SHARE_REVIEW_ANSWER = "share_review_answer"
PERM_VIEW_REPORTS = "view_reports"
PERM_EXPORT_REPORTS = "export_reports"
PERM_MANAGE_USERS = "manage_users"
PERM_MANAGE_DEPARTMENT = "manage_department"

ROLE_PERMISSIONS = {
    ROLE_PARTICIPANT: {
        PERM_VIEW_SCENARIOS,
        PERM_SUBMIT_ANSWERS,
        PERM_REVEAL_INSTRUCTOR_ANSWERS,
        PERM_CREATE_SCENARIOS,
    },
    ROLE_INSTRUCTOR: {
        PERM_VIEW_SCENARIOS,
        PERM_SUBMIT_ANSWERS,
        PERM_REVEAL_INSTRUCTOR_ANSWERS,
        PERM_CREATE_SCENARIOS,
        PERM_CREATE_SESSIONS,
        PERM_VIEW_SESSION_SUBMISSIONS,
        PERM_SELECT_REVIEW_ANSWER,
        PERM_SHARE_REVIEW_ANSWER,
    },
    ROLE_TRAINING_CHIEF: {
        PERM_VIEW_SCENARIOS,
        PERM_SUBMIT_ANSWERS,
        PERM_REVEAL_INSTRUCTOR_ANSWERS,
        PERM_CREATE_SCENARIOS,
        PERM_CREATE_SESSIONS,
        PERM_APPROVE_SCENARIOS,
        PERM_VIEW_SESSION_SUBMISSIONS,
        PERM_SELECT_REVIEW_ANSWER,
        PERM_SHARE_REVIEW_ANSWER,
        PERM_VIEW_REPORTS,
        PERM_EXPORT_REPORTS,
        PERM_MANAGE_DEPARTMENT,
    },
    ROLE_ADMIN: set(),
}

ALL_PERMISSIONS = {
    permission for permissions in ROLE_PERMISSIONS.values() for permission in permissions
}
ALL_PERMISSIONS.add(PERM_MANAGE_USERS)


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    display_name: str
    roles: frozenset[str]
    permission_overrides: frozenset[str] = field(default_factory=frozenset)

    @property
    def permissions(self) -> frozenset[str]:
        if ROLE_ADMIN in self.roles:
            return frozenset(ALL_PERMISSIONS)
        collected: set[str] = set(self.permission_overrides)
        for role in self.roles:
            collected.update(ROLE_PERMISSIONS.get(role, set()))
        return frozenset(collected)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


def normalize_roles(raw_roles: Iterable[str]) -> frozenset[str]:
    normalized = {role.strip().lower() for role in raw_roles if role and role.strip()}
    return frozenset(normalized)


def requires_permission(permission: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            from flask import redirect, request, url_for
            current_user = getattr(g, "current_user", None)
            if current_user is None or current_user.user_id == "guest":
                return redirect(url_for("auth.login_page", next=request.full_path.rstrip("?")))
            if not current_user.has_permission(permission):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
