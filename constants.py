"""Domain constants for Fireground Trainer.

Import from here in new code. helpers.py re-exports everything for backward
compatibility with existing blueprint and test imports.
"""
from __future__ import annotations

import re
from enum import Enum

from authz import (
    PERM_APPROVE_SCENARIOS,
    PERM_CREATE_SCENARIOS,
    PERM_CREATE_SESSIONS,
    PERM_EXPORT_REPORTS,
    PERM_MANAGE_USERS,
    PERM_REVEAL_INSTRUCTOR_ANSWERS,
    PERM_SELECT_REVIEW_ANSWER,
    PERM_SHARE_REVIEW_ANSWER,
    PERM_SUBMIT_ANSWERS,
    PERM_VIEW_REPORTS,
    PERM_VIEW_SCENARIOS,
    ROLE_ADMIN,
    ROLE_INSTRUCTOR,
    ROLE_PARTICIPANT,
    ROLE_TRAINING_CHIEF,
)

# ---------------------------------------------------------------------------
# Role / permission display
# ---------------------------------------------------------------------------

PERMISSION_KEYS = {
    "view_scenarios": PERM_VIEW_SCENARIOS,
    "submit_answers": PERM_SUBMIT_ANSWERS,
    "reveal_instructor_answers": PERM_REVEAL_INSTRUCTOR_ANSWERS,
    "create_scenarios": PERM_CREATE_SCENARIOS,
    "create_sessions": PERM_CREATE_SESSIONS,
    "approve_scenarios": PERM_APPROVE_SCENARIOS,
    "select_review_answer": PERM_SELECT_REVIEW_ANSWER,
    "share_review_answer": PERM_SHARE_REVIEW_ANSWER,
    "view_reports": PERM_VIEW_REPORTS,
    "export_reports": PERM_EXPORT_REPORTS,
    "manage_users": PERM_MANAGE_USERS,
}

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_TRAINING_CHIEF: "Training Chief",
    ROLE_INSTRUCTOR: "Instructor",
    ROLE_PARTICIPANT: "Participant",
}

STAFF_ROLES = frozenset({ROLE_INSTRUCTOR, ROLE_TRAINING_CHIEF, ROLE_ADMIN})

# ---------------------------------------------------------------------------
# Scenario lifecycle
# ---------------------------------------------------------------------------

SCENARIO_STATUS_DRAFT = "draft"
SCENARIO_STATUS_SUBMITTED = "submitted"
SCENARIO_STATUS_APPROVED = "approved"
SCENARIO_STATUS_ARCHIVED = "archived"
SCENARIO_ACTIVE_STATUSES = frozenset(
    {SCENARIO_STATUS_DRAFT, SCENARIO_STATUS_SUBMITTED, SCENARIO_STATUS_APPROVED}
)


class ScenarioAction(str, Enum):
    SUBMIT_FOR_REVIEW = "submit_for_review"
    APPROVE = "approve"
    ARCHIVE = "archive"

# ---------------------------------------------------------------------------
# Question types
# ---------------------------------------------------------------------------

QUESTION_TYPE_AUTO_CHECKLIST = "auto_checklist"
QUESTION_TYPE_KEY_POINT_AUTO = "key_point_auto"
QUESTION_TYPE_DISCUSSION_ONLY = "discussion_only"
QUESTION_TYPE_MULTIPLE_CHOICE = "multiple_choice"
QUESTION_TYPE_TRUE_FALSE = "true_false"  # UI-only; stored as multiple_choice
QUESTION_TYPE_LABELS = {
    QUESTION_TYPE_AUTO_CHECKLIST: "Auto-scored checklist",
    QUESTION_TYPE_KEY_POINT_AUTO: "Short Answer (Participant's Answers Matched and Scored to Creator's Answer)",
    QUESTION_TYPE_DISCUSSION_ONLY: "Discussion-only open-ended (non-graded)",
    QUESTION_TYPE_MULTIPLE_CHOICE: "Multiple Choice",
}
QUESTION_TYPE_CHOICES = frozenset(QUESTION_TYPE_LABELS.keys())
DEFAULT_QUESTION_TYPE = QUESTION_TYPE_DISCUSSION_ONLY

# Simplified labels shown in the scenario create form
CREATE_QUESTION_TYPE_LABELS = {
    QUESTION_TYPE_DISCUSSION_ONLY: "Open-Ended",
    QUESTION_TYPE_MULTIPLE_CHOICE: "Multiple Choice",
    QUESTION_TYPE_TRUE_FALSE: "True / False",
}

# ---------------------------------------------------------------------------
# Submission lifecycle
# ---------------------------------------------------------------------------

SUBMISSION_STATUS_SUBMITTED = "submitted"
SUBMISSION_STATUS_APPROVED = "approved"
SUBMISSION_STATUS_FLAGGED = "flagged"
SUBMISSION_STATUS_EXCLUDED = "excluded"
SUBMISSION_STATUS_LABELS = {
    SUBMISSION_STATUS_SUBMITTED: "Active",
    SUBMISSION_STATUS_APPROVED: "Active",
    SUBMISSION_STATUS_FLAGGED: "Active",
    SUBMISSION_STATUS_EXCLUDED: "Excluded",
}

# ---------------------------------------------------------------------------
# Shift options
# ---------------------------------------------------------------------------

SHIFT_OPTIONS = (
    "A Shift",
    "B Shift",
    "C Shift",
    "D Shift",
    "Swing",
    "Other",
)

# ---------------------------------------------------------------------------
# Scenario library tabs
# ---------------------------------------------------------------------------

LIBRARY_TAB_OFFICIAL = "official"
LIBRARY_TAB_PRACTICE = "practice"
LIBRARY_TAB_MINE = "mine"
LIBRARY_TAB_SUBMITTED = "submitted"
LIBRARY_TAB_LABELS = {
    LIBRARY_TAB_OFFICIAL: "Official",
    LIBRARY_TAB_PRACTICE: "Practice",
    LIBRARY_TAB_MINE: "Mine",
    LIBRARY_TAB_SUBMITTED: "Submitted",
}

# ---------------------------------------------------------------------------
# Training categories
# ---------------------------------------------------------------------------

SITE_NAME = "Blitzfire Training"
CATEGORY_FIREGROUND = "fireground"
CATEGORY_MVA = "mva"
CATEGORY_EMS = "ems"
CATEGORY_LABELS = {
    CATEGORY_FIREGROUND: "Fireground Training",
    CATEGORY_MVA: "Motor Vehicle Accidents",
    CATEGORY_EMS: "Emergency Medical Services",
}
CATEGORY_FILTER_ALL = "all"
CATEGORY_FILTER_OFFICIAL = "official"
CATEGORY_FILTER_INSTRUCTOR_MADE = "instructor_made"
CATEGORY_FILTER_USER_MADE = "user_made"
CATEGORY_FILTER_LABELS = {
    CATEGORY_FILTER_ALL: "All Scenarios",
    CATEGORY_FILTER_OFFICIAL: "Official",
    CATEGORY_FILTER_INSTRUCTOR_MADE: "Instructor Made",
    CATEGORY_FILTER_USER_MADE: "User Made",
}

# ---------------------------------------------------------------------------
# Category-specific token palettes
# ---------------------------------------------------------------------------

TOKEN_PALETTE_FIREGROUND = [
    {"type": "fire", "label": "Fire", "src": "tokens/fire.png"},
    {"type": "smoke", "label": "Smoke", "src": "tokens/smoke.png"},
    {"type": "wind", "label": "Wind", "src": "tokens/wind_arrow.png"},
    {"type": "ladder", "label": "Ladder", "src": "tokens/ladder.png"},
    {"type": "attack", "label": "Attack", "src": "tokens/fireman.png"},
    {"type": "rit", "label": "RIT", "src": "tokens/RIT.png"},
]

TOKEN_PALETTE_MVA = [
    {"type": "fire", "label": "Fire", "src": "tokens/fire.png"},
    {"type": "smoke", "label": "Smoke", "src": "tokens/smoke.png"},
    {"type": "wind", "label": "Wind", "src": "tokens/wind_arrow.png"},
    {"type": "attack", "label": "Crew", "src": "tokens/fireman.png"},
    {"type": "rit", "label": "RIT", "src": "tokens/RIT.png"},
]

TOKEN_PALETTE_EMS = [
    {"type": "attack", "label": "EMS", "src": "tokens/fireman.png"},
    {"type": "rit", "label": "RIT", "src": "tokens/RIT.png"},
]

TOKEN_PALETTE_DEFAULT = TOKEN_PALETTE_FIREGROUND

CATEGORY_TOKEN_PALETTES = {
    CATEGORY_FIREGROUND: TOKEN_PALETTE_FIREGROUND,
    CATEGORY_MVA: TOKEN_PALETTE_MVA,
    CATEGORY_EMS: TOKEN_PALETTE_EMS,
}

# ---------------------------------------------------------------------------
# GFD position target tags
# ---------------------------------------------------------------------------

POSITION_FIREFIGHTER = "firefighter"
POSITION_DRIVER_PUMP_OPERATOR = "driver_pump_operator"
POSITION_CAPTAIN = "captain"
POSITION_BATTALION = "battalion"

POSITION_LABELS = {
    POSITION_FIREFIGHTER: "Firefighter",
    POSITION_DRIVER_PUMP_OPERATOR: "Driver/Pump Operator",
    POSITION_CAPTAIN: "Captain",
    POSITION_BATTALION: "Battalion",
}

POSITION_CHOICES = (
    POSITION_FIREFIGHTER,
    POSITION_DRIVER_PUMP_OPERATOR,
    POSITION_CAPTAIN,
    POSITION_BATTALION,
)

# ---------------------------------------------------------------------------
# Auth / session utilities
# ---------------------------------------------------------------------------

CSRF_SESSION_KEY = "_csrf_token"

POST_ONLY_PATHS = frozenset(
    {
        "/submit",
        "/scenario/submit-review",
        "/scenario/approve",
        "/scenario/archive",
        "/scenario/official",
        "/scenarios/new",
        "/scenarios/select",
        "/scenarios/vote",
        "/scenarios/flag",
        "/scenarios/clone",
        "/scenarios/upload-image",
        "/scenarios/autosave",
        "/scenario/save-token-layout",
        "/sessions/new",
        "/logout",
        "/login",
        "/magic-link/request",
    }
)

# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "with",
        "your",
        "you",
    }
)

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
