"""View model builders and display formatters for Fireground Trainer templates.

All functions here build plain dicts (or format strings) for template consumption.
They have no side effects and do not commit to the database.

helpers.py re-exports every public symbol for backward-compat with blueprints
that still import from there.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from flask import g, url_for

from constants import (
    CATEGORY_EMS,
    CATEGORY_FIREGROUND,
    CATEGORY_FILTER_ALL,
    CATEGORY_FILTER_LABELS,
    CATEGORY_LABELS,
    CATEGORY_MVA,
    DEFAULT_QUESTION_TYPE,
    POSITION_CHOICES,
    POSITION_LABELS,
    QUESTION_TYPE_DISCUSSION_ONLY,
    QUESTION_TYPE_AUTO_CHECKLIST,
    QUESTION_TYPE_LABELS,
    QUESTION_TYPE_MULTIPLE_CHOICE,
    STOP_WORDS,
    SUBMISSION_STATUS_APPROVED,
    SUBMISSION_STATUS_EXCLUDED,
    SUBMISSION_STATUS_FLAGGED,
    SUBMISSION_STATUS_LABELS,
    SUBMISSION_STATUS_SUBMITTED,
)

if TYPE_CHECKING:
    from models import Scenario, Submission, TrainingSession, User


# ---------------------------------------------------------------------------
# NLP / scoring internals
# ---------------------------------------------------------------------------

def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def normalize_text_for_match(text_value: str) -> str:
    lowered = text_value.lower()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def tokenize_for_match(text_value: str) -> list[str]:
    normalized = normalize_text_for_match(text_value)
    tokens = normalized.split()
    return [token for token in tokens if len(token) >= 3 and token not in STOP_WORDS]


def extract_reference_phrases(reference: str) -> list[str]:
    if not reference.strip():
        return []
    parts = re.split(r"[\n;]+", reference)
    raw_phrases: list[str] = []
    for part in parts:
        for section in part.split(","):
            cleaned = re.sub(r"^\s*[-*•\d\.\)\(]+\s*", "", section).strip()
            if cleaned:
                raw_phrases.append(cleaned)
    return ordered_unique(raw_phrases)


def extract_reference_terms(reference: str, max_terms: int = 10) -> list[str]:
    terms = ordered_unique(tokenize_for_match(reference))
    return terms[:max_terms]


# ---------------------------------------------------------------------------
# Submission scoring / feedback
# ---------------------------------------------------------------------------

def build_short_answer_feedback(answer_text: str, reference_text: str) -> dict:
    key_terms = extract_reference_terms(reference_text, max_terms=12)
    if not key_terms:
        return {
            "mode": "auto",
            "score_percent": None,
            "summary": "Auto-score unavailable. Add clearer creator answer key points.",
            "matched_items": [],
            "missing_items": [],
        }
    answer_tokens = set(tokenize_for_match(answer_text))
    matched_terms = [term for term in key_terms if term in answer_tokens]
    missing_terms = [term for term in key_terms if term not in answer_tokens]
    percent = round((len(matched_terms) / len(key_terms)) * 100)
    return {
        "mode": "auto",
        "score_percent": percent,
        "summary": f"Auto score: {len(matched_terms)}/{len(key_terms)} key points matched ({percent}%).",
        "matched_items": matched_terms[:6],
        "missing_items": missing_terms[:6],
    }


def build_checklist_feedback(answer_text: str, reference_text: str) -> dict:
    checklist_items = extract_reference_phrases(reference_text)
    if not checklist_items:
        return build_short_answer_feedback(answer_text, reference_text)
    answer_tokens = set(tokenize_for_match(answer_text))
    matched: list[str] = []
    missing: list[str] = []
    for item in checklist_items:
        item_tokens = tokenize_for_match(item)
        if not item_tokens:
            continue
        required_hits = max(1, int(len(item_tokens) * 0.7))
        actual_hits = sum(1 for token in item_tokens if token in answer_tokens)
        if actual_hits >= required_hits:
            matched.append(item)
        else:
            missing.append(item)
    total = len(matched) + len(missing)
    if total == 0:
        return {
            "mode": "auto",
            "score_percent": None,
            "summary": "Auto-score unavailable. Add checklist key points in creator answer.",
            "matched_items": [],
            "missing_items": [],
        }
    percent = round((len(matched) / total) * 100)
    return {
        "mode": "auto",
        "score_percent": percent,
        "summary": f"Checklist auto score: {len(matched)}/{total} items matched ({percent}%).",
        "matched_items": matched[:6],
        "missing_items": missing[:6],
    }


def score_question_answer(question: dict, answer_text: str) -> dict:
    question_type = question.get("question_type", DEFAULT_QUESTION_TYPE)
    reference_text = question.get("instructor_answer", "")
    if question_type == QUESTION_TYPE_DISCUSSION_ONLY:
        return {
            "mode": "manual",
            "score_percent": None,
            "summary": "No auto score for discussion-only question.",
            "matched_items": [],
            "missing_items": [],
        }
    if question_type == QUESTION_TYPE_AUTO_CHECKLIST:
        return build_checklist_feedback(answer_text, reference_text)
    return build_short_answer_feedback(answer_text, reference_text)


def build_submission_feedback(scenario: dict, answers: dict[str, str]) -> dict[str, dict]:
    feedback: dict[str, dict] = {}
    for question in scenario["questions"]:
        qid = str(question["id"])
        feedback[qid] = score_question_answer(question, answers.get(qid, ""))
    return feedback


# ---------------------------------------------------------------------------
# Display formatters
# ---------------------------------------------------------------------------

def format_relative_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    delta = datetime.utcnow() - value
    days = delta.days
    if days < 0:
        return "just now"
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    if days < 365:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    years = days // 365
    return f"{years} year{'s' if years != 1 else ''} ago"


def format_submission_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M UTC")


def format_submission_status(status: str | None) -> str:
    return SUBMISSION_STATUS_LABELS.get(status or SUBMISSION_STATUS_SUBMITTED, "Pending Review")


def submission_status_tone(status: str | None) -> str:
    if status == SUBMISSION_STATUS_EXCLUDED:
        return "excluded"
    return "active"


def summarize_review_notes(notes: str | None, max_length: int = 120) -> str | None:
    if not notes:
        return None
    compact = " ".join(notes.split())
    if len(compact) <= max_length:
        return compact
    return f"{compact[: max_length - 3].rstrip()}..."


def format_user_identity(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


def format_audit_action(action: str) -> str:
    return action.replace("_", " ").title()


def format_scenario_popularity_label(like_count: int) -> str:
    if like_count >= 3:
        return "Most Liked"
    if like_count >= 1:
        return "Popular"
    return "New To Likes"


# ---------------------------------------------------------------------------
# Scenario view models
# ---------------------------------------------------------------------------

def _get_forked_from_author(scenario: Scenario) -> str | None:
    if scenario.forked_from_scenario_id is None:
        return None
    from models import Scenario as _Scenario
    original = _Scenario.query.filter_by(id=scenario.forked_from_scenario_id).first()
    if original is None:
        return None
    creator = original.created_by
    if creator is None:
        return None
    return creator.full_name or creator.email


def build_scenario_view_model(scenario: Scenario) -> dict:
    return {
        "id": scenario.id,
        "title": scenario.title,
        "dispatch": scenario.dispatch_text,
        "image": {
            "base": scenario.base_image_path,
            "overlay": scenario.overlay_image_path,
        },
        "questions": [
            {
                "id": q.id,
                "question_key": q.question_key,
                "prompt": q.prompt,
                "question_type": q.question_type or DEFAULT_QUESTION_TYPE,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    q.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "instructor_answer": q.instructor_answer or "",
                "choices": [
                    {
                        "id": c.id,
                        "choice_text": c.choice_text,
                        "is_correct": c.is_correct,
                        "sort_order": c.sort_order,
                    }
                    for c in sorted(getattr(q, "choices", []), key=lambda c: c.sort_order)
                ] if (q.question_type or DEFAULT_QUESTION_TYPE) == QUESTION_TYPE_MULTIPLE_CHOICE else [],
            }
            for q in sorted(scenario.questions, key=lambda item: item.sort_order)
            if q.is_active
        ],
        "workflow_status": scenario.status,
        "is_official": scenario.is_official,
        "submitted_for_official_at": getattr(scenario, "submitted_for_official_at", None),
        "forked_from_scenario_id": scenario.forked_from_scenario_id,
        "forked_from_author": _get_forked_from_author(scenario),
        "submitted_at": scenario.submitted_at,
        "approved_at": scenario.approved_at,
        "archived_at": scenario.archived_at,
        "like_count": scenario.like_count or 0,
        "approved_by": (
            scenario.approved_by.full_name
            if scenario.approved_by and scenario.approved_by.full_name
            else (scenario.approved_by.email if scenario.approved_by else None)
        ),
    }


def build_scenario_vote_state_map(scenarios: list[Scenario], db_user: User | None) -> dict[int, dict]:
    from helpers import get_completed_submission_scenario_ids_for_user, get_liked_scenario_ids_for_user
    scenario_ids = [scenario.id for scenario in scenarios]
    eligible_ids = get_completed_submission_scenario_ids_for_user(db_user, scenario_ids)
    liked_ids = get_liked_scenario_ids_for_user(db_user, scenario_ids)
    vote_state_map: dict[int, dict] = {}
    for scenario in scenarios:
        can_like = scenario.id in eligible_ids
        has_liked = scenario.id in liked_ids
        if db_user is None:
            eligibility_note = "Sign in with an account, complete this scenario once, then you can like it."
        elif can_like:
            eligibility_note = "You can like or remove your like for this scenario."
        else:
            eligibility_note = "Complete a signed-in submission for this scenario before liking it."
        vote_state_map[scenario.id] = {
            "can_like": can_like,
            "has_liked": has_liked,
            "like_count": scenario.like_count or 0,
            "eligibility_note": eligibility_note,
        }
    return vote_state_map


def summarize_scenario_for_library(scenario: Scenario, vote_state: dict | None = None) -> dict:
    vote_state = vote_state or {}
    return {
        "id": scenario.id,
        "title": scenario.title,
        "status": scenario.status,
        "is_official": scenario.is_official,
        "question_count": len([q for q in scenario.questions if q.is_active]),
        "updated_at": scenario.updated_at,
        "like_count": vote_state.get("like_count", scenario.like_count or 0),
        "can_like": vote_state.get("can_like", False),
        "has_liked": vote_state.get("has_liked", False),
        "eligibility_note": vote_state.get(
            "eligibility_note",
            "Complete a signed-in submission for this scenario before liking it.",
        ),
        "approved_by": (
            scenario.approved_by.full_name
            if scenario.approved_by and scenario.approved_by.full_name
            else (scenario.approved_by.email if scenario.approved_by else None)
        ),
        "tags": [link.tag.name for link in scenario.tag_links if link.tag.is_active],
        "is_public": scenario.is_public,
        "submitted_for_official_at": getattr(scenario, "submitted_for_official_at", None),
        "visibility_label": (
            "Public" if scenario.is_public
            else ("Department" if scenario.department_id else "Private")
        ),
    }


def summarize_scenario_for_catalog(scenario: Scenario, vote_state: dict | None = None) -> dict:
    from helpers import scenario_creator_filter_key, scenario_creator_filter_label
    vote_state = vote_state or {}
    question_count = len([question for question in scenario.questions if question.is_active])
    dispatch_summary = " ".join((scenario.dispatch_text or "").split())
    like_count = vote_state.get("like_count", scenario.like_count or 0)
    creator_filter_label = scenario_creator_filter_label(scenario)
    creator = scenario.created_by
    author_name = (creator.full_name or creator.email) if creator is not None else None
    status_label = (scenario.status or "").replace("_", " ").title()
    return {
        "id": scenario.id,
        "title": scenario.title,
        "dispatch_summary": dispatch_summary[:180] + ("..." if len(dispatch_summary) > 180 else ""),
        "question_count": question_count,
        "question_label": f"{question_count} question{'s' if question_count != 1 else ''}",
        "updated_at": scenario.updated_at,
        "updated_at_label": format_relative_date(scenario.updated_at),
        "is_official": scenario.is_official,
        "creator_filter_key": scenario_creator_filter_key(scenario),
        "creator_filter_label": creator_filter_label,
        "author_label": creator_filter_label,
        "author_name": author_name,
        "status": scenario.status,
        "status_label": status_label,
        "like_count": like_count,
        "likes_label": f"{like_count} like{'s' if like_count != 1 else ''}",
        "popularity_label": format_scenario_popularity_label(like_count),
        "can_like": vote_state.get("can_like", False),
        "has_liked": vote_state.get("has_liked", False),
        "eligibility_note": vote_state.get(
            "eligibility_note",
            "Complete a signed-in submission for this scenario before liking it.",
        ),
    }


def build_scenario_vote_summary(scenario: Scenario, db_user: User | None) -> dict:
    return build_scenario_vote_state_map([scenario], db_user).get(
        scenario.id,
        {
            "can_like": False,
            "has_liked": False,
            "like_count": scenario.like_count or 0,
            "eligibility_note": "Complete a signed-in submission for this scenario before liking it.",
        },
    )


def build_participant_submission_state(scenario_row: Scenario | None) -> dict:
    participant = g.active_participant
    training_session = g.active_training_session

    if participant is None or training_session is None:
        return {
            "can_submit": False,
            "status": "not_joined",
            "message": None,
            "session_title": None,
            "join_code": None,
            "shift_label": None,
            "identity_label": None,
            "latest_attempt_number": 0,
            "next_attempt_number": None,
            "guidance": "Join an active session from a host QR code or join link to unlock submissions.",
        }

    latest_attempt_number = max(
        (submission.attempt_number for submission in participant.submissions),
        default=0,
    )
    identity_label = "Anonymous" if participant.is_anonymous else (participant.display_name or "Participant")
    base_state = {
        "session_title": training_session.title or f"Session #{training_session.id}",
        "join_code": training_session.join_code,
        "shift_label": participant.shift_label or "Unspecified",
        "identity_label": identity_label,
        "latest_attempt_number": latest_attempt_number,
        "next_attempt_number": latest_attempt_number + 1,
    }

    if training_session.status != "active":
        return {
            "can_submit": False,
            "status": "inactive_session",
            "message": "This session is no longer active. New submissions are locked.",
            "guidance": "You can still view revealed answers, but only active sessions accept new attempts.",
            **base_state,
        }
    if participant.training_session_id != training_session.id:
        return {
            "can_submit": False,
            "status": "invalid_session",
            "message": "Your participant session is no longer valid. Rejoin the session to continue.",
            "guidance": "Rejoin the live session to restore your participant entry before submitting again.",
            **base_state,
        }
    if scenario_row is not None and training_session.scenario_id != scenario_row.id:
        return {
            "can_submit": False,
            "status": "scenario_mismatch",
            "message": "This board is showing a different scenario than your active session.",
            "guidance": "Switch back to your session scenario before sending another attempt.",
            **base_state,
        }
    return {
        "can_submit": True,
        "status": "ready",
        "message": None,
        "guidance": (
            "You can submit more than once during a live drill. The host workspace reviews your latest saved attempt."
        ),
        **base_state,
    }


def get_user_lists(db_user) -> list[dict]:
    """Return a user's named lists with scenario counts for template use."""
    if db_user is None:
        return []
    from models import UserList
    lists = UserList.query.filter_by(user_id=db_user.id).order_by(UserList.created_at.asc()).all()
    return [{"id": ul.id, "name": ul.name, "count": len(ul.scenario_links)} for ul in lists]


def get_saved_scenario_ids_for_user(db_user, scenario_ids: list[int]) -> set[int]:
    """Return set of scenario IDs the user has saved to any list."""
    if db_user is None or not scenario_ids:
        return set()
    from models import UserListScenario, UserList
    rows = (
        UserListScenario.query
        .join(UserList, UserListScenario.list_id == UserList.id)
        .filter(
            UserList.user_id == db_user.id,
            UserListScenario.scenario_id.in_(scenario_ids),
        )
        .with_entities(UserListScenario.scenario_id)
        .all()
    )
    return {r[0] for r in rows}


def build_home_stats() -> dict:
    from models import Department, Scenario, TrainingSession
    from constants import SCENARIO_STATUS_APPROVED
    return {
        "scenario_count": Scenario.query.filter_by(status=SCENARIO_STATUS_APPROVED, is_public=True).count(),
        "session_count": TrainingSession.query.count(),
        "department_count": Department.query.count(),
    }


def build_home_category_cards() -> list[dict]:
    from helpers import load_category_scenarios
    fireground_count = len(load_category_scenarios(CATEGORY_FIREGROUND, CATEGORY_FILTER_ALL))
    return [
        {
            "key": CATEGORY_FIREGROUND,
            "label": CATEGORY_LABELS[CATEGORY_FIREGROUND],
            "description": (
                "Residential, commercial, and tactical fireground drills built for company-level review."
            ),
            "count_label": f"{fireground_count} live scenario{'s' if fireground_count != 1 else ''}",
            "href": url_for("scenarios.fireground_training"),
            "is_available": True,
        },
        {
            "key": CATEGORY_MVA,
            "label": CATEGORY_LABELS[CATEGORY_MVA],
            "description": (
                "Coming soon: stabilization, entrapment, highway command, and rescue benchmarks."
            ),
            "count_label": "Placeholder page",
            "href": url_for("scenarios.mva_training"),
            "is_available": False,
        },
        {
            "key": CATEGORY_EMS,
            "label": CATEGORY_LABELS[CATEGORY_EMS],
            "description": (
                "Coming soon: patient assessment, airway, medical decision making, and team communication."
            ),
            "count_label": "Placeholder page",
            "href": url_for("scenarios.ems_training"),
            "is_available": False,
        },
    ]


def build_training_category_page(
    category_key: str,
    selected_filter: str,
    db_user: User | None,
) -> dict:
    from helpers import load_category_scenarios, scenario_creator_filter_key
    all_scenarios = load_category_scenarios(category_key, CATEGORY_FILTER_ALL)
    if selected_filter == CATEGORY_FILTER_ALL:
        scenarios = all_scenarios
    else:
        scenarios = [s for s in all_scenarios if scenario_creator_filter_key(s) == selected_filter]

    vote_state_map = build_scenario_vote_state_map(all_scenarios, db_user)

    filter_options = []
    for key, label in CATEGORY_FILTER_LABELS.items():
        if key == CATEGORY_FILTER_ALL:
            count = len(all_scenarios)
        else:
            count = sum(1 for s in all_scenarios if scenario_creator_filter_key(s) == key)
        filter_options.append({"key": key, "label": label, "count": count})

    is_placeholder = category_key in {CATEGORY_MVA, CATEGORY_EMS}
    featured_scenarios = [
        summarize_scenario_for_catalog(s, vote_state_map.get(s.id))
        for s in all_scenarios
        if (s.like_count or 0) > 0
    ][:3]

    return {
        "key": category_key,
        "label": CATEGORY_LABELS[category_key],
        "description": {
            CATEGORY_FIREGROUND: (
                "Choose a fireground scenario, filter by who created it, and send the board live when you are ready."
            ),
            CATEGORY_MVA: (
                "This category page is in place so the site structure is ready. Scenario templates will land here next."
            ),
            CATEGORY_EMS: (
                "This category page is in place so the site structure is ready. Scenario templates will land here next."
            ),
        }[category_key],
        "selected_filter": selected_filter,
        "active_filter_label": CATEGORY_FILTER_LABELS[selected_filter],
        "filter_options": filter_options,
        "total_count": len(all_scenarios),
        "result_count": len(scenarios),
        "featured_scenarios": featured_scenarios,
        "scenarios": [summarize_scenario_for_catalog(s, vote_state_map.get(s.id)) for s in scenarios],
        "is_placeholder": is_placeholder,
    }


def build_public_library_view_model(
    db_user: User | None,
    category_filter: str | None,
    tag_slugs: list[str],
    keyword: str,
    position_filter: str | None = None,
) -> dict:
    from helpers import get_all_completed_scenario_ids_for_user
    from models import Scenario as _Scenario, Tag

    query = (
        _Scenario.query.filter(
            _Scenario.is_active.is_(True),
            _Scenario.is_public.is_(True),
        )
        .order_by(
            _Scenario.is_official.desc(),
            _Scenario.like_count.desc(),
            _Scenario.updated_at.desc(),
            _Scenario.id.desc(),
        )
    )
    from constants import SCENARIO_STATUS_APPROVED
    all_scenarios = query.filter(_Scenario.status == SCENARIO_STATUS_APPROVED).all()
    scenario_ids = [s.id for s in all_scenarios]

    completed_ids = get_all_completed_scenario_ids_for_user(db_user, scenario_ids)

    linked_tag_ids = {
        link.tag_id
        for s in all_scenarios
        for link in s.tag_links
        if link.tag and link.tag.is_active
    }
    available_tags = Tag.query.filter(
        Tag.is_active.is_(True),
        Tag.id.in_(linked_tag_ids),
    ).order_by(Tag.name.asc()).all() if linked_tag_ids else []

    available_categories = sorted({
        s.training_category for s in all_scenarios if s.training_category
    })

    selected_tag_ids: set[int] = set()
    if tag_slugs:
        selected_tags = Tag.query.filter(Tag.slug.in_(tag_slugs), Tag.is_active.is_(True)).all()
        selected_tag_ids = {t.id for t in selected_tags}

    filtered = all_scenarios
    if category_filter and category_filter in {CATEGORY_FIREGROUND, CATEGORY_MVA, CATEGORY_EMS}:
        filtered = [s for s in filtered if s.training_category == category_filter]
    if selected_tag_ids:
        filtered = [
            s for s in filtered
            if selected_tag_ids.issubset({link.tag_id for link in s.tag_links})
        ]
    if keyword:
        kw = keyword.strip().lower()
        filtered = [
            s for s in filtered
            if kw in s.title.lower() or kw in (s.dispatch_text or "").lower()
        ]
    if position_filter and position_filter in POSITION_CHOICES:
        filtered = [
            s for s in filtered
            if not s.position_links or any(p.position == position_filter for p in s.position_links)
        ]

    scenario_summaries = []
    for s in filtered:
        tags = [link.tag.name for link in s.tag_links if link.tag and link.tag.is_active]
        positions = [POSITION_LABELS.get(p.position, p.position) for p in s.position_links]
        creator = s.created_by
        scenario_summaries.append({
            "id": s.id,
            "title": s.title,
            "dispatch_summary": " ".join((s.dispatch_text or "").split())[:180],
            "is_official": s.is_official,
            "training_category": s.training_category,
            "category_label": CATEGORY_LABELS.get(s.training_category or "", s.training_category or ""),
            "tags": tags,
            "positions": positions,
            "like_count": s.like_count or 0,
            "question_count": len([q for q in s.questions if q.is_active]),
            "is_completed": s.id in completed_ids,
            "author_name": (creator.full_name or creator.email) if creator else None,
        })

    return {
        "scenarios": scenario_summaries,
        "total_count": len(all_scenarios),
        "result_count": len(filtered),
        "available_categories": available_categories,
        "category_labels": CATEGORY_LABELS,
        "available_tags": [{"id": t.id, "name": t.name, "slug": t.slug} for t in available_tags],
        "selected_category": category_filter or "",
        "selected_tag_slugs": tag_slugs,
        "keyword": keyword,
        "position_choices": POSITION_CHOICES,
        "position_labels": POSITION_LABELS,
        "selected_position": position_filter or "",
    }


# ---------------------------------------------------------------------------
# Session view models
# ---------------------------------------------------------------------------

def build_session_participant_label_map(training_session: TrainingSession) -> dict[int, str]:
    participants = sorted(
        training_session.participants,
        key=lambda item: (item.joined_at or datetime.min, item.id),
    )
    anonymous_counter = 0
    labels: dict[int, str] = {}
    for participant in participants:
        if participant.is_anonymous:
            anonymous_counter += 1
            labels[participant.id] = f"Anonymous Person {anonymous_counter}"
        else:
            labels[participant.id] = participant.display_name or "Named Participant"
    return labels


def build_latest_submission_map(training_session: TrainingSession) -> dict[int, Submission]:
    submissions = sorted(
        training_session.submissions,
        key=lambda item: (item.submitted_at or datetime.min, item.id),
        reverse=True,
    )
    latest_submission_by_participant: dict[int, Submission] = {}
    for submission in submissions:
        latest_submission_by_participant.setdefault(submission.participant_id, submission)
    return latest_submission_by_participant


def build_session_question_review_view_model(training_session: TrainingSession) -> list[dict]:
    participant_labels = build_session_participant_label_map(training_session)
    latest_submission_by_participant = build_latest_submission_map(training_session)
    latest_submissions = sorted(
        latest_submission_by_participant.values(),
        key=lambda item: (item.submitted_at or datetime.min, item.id),
        reverse=True,
    )
    active_questions = [
        question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    ]
    revealed_answer_ids_by_question: dict[int, set[int]] = {}
    for reveal in training_session.revealed_question_answers:
        if reveal.submission_answer is not None:
            revealed_answer_ids_by_question.setdefault(reveal.question_id, set()).add(
                reveal.submission_answer_id
            )

    question_rows: list[dict] = []
    for question in active_questions:
        answer_rows = []
        revealed_ids = revealed_answer_ids_by_question.get(question.id, set())
        status_counts = {"active": 0, "excluded": 0}
        for submission in latest_submissions:
            answer = next(
                (row for row in submission.answers if row.question_id == question.id),
                None,
            )
            if answer is None:
                continue
            participant = submission.participant
            tone = submission_status_tone(submission.status)
            status_counts[tone] += 1
            selected_choice = getattr(answer, "selected_choice", None)
            answer_rows.append(
                {
                    "submission_id": submission.id,
                    "submission_answer_id": answer.id,
                    "participant_label": participant_labels.get(
                        participant.id,
                        "Anonymous" if participant.is_anonymous else "Named Participant",
                    ),
                    "shift_label": participant.shift_label or "Unspecified",
                    "attempt_number": submission.attempt_number,
                    "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                    "answer_text": answer.answer_text,
                    "selected_choice_text": selected_choice.choice_text if selected_choice else None,
                    "selected_choice_is_correct": selected_choice.is_correct if selected_choice else None,
                    "status": submission.status,
                    "status_label": format_submission_status(submission.status),
                    "status_tone": tone,
                    "is_excluded": submission.status == SUBMISSION_STATUS_EXCLUDED,
                    "is_revealed": answer.id in revealed_ids,
                    "review_notes": submission.notes or "",
                    "review_notes_preview": summarize_review_notes(submission.notes),
                    "has_notes": bool(submission.notes),
                }
            )
        question_rows.append(
            {
                "question_id": question.id,
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "instructor_answer": question.instructor_answer,
                "answer_rows": answer_rows,
                "revealed_answer_count": len(revealed_ids),
                "status_counts": status_counts,
            }
        )

    return question_rows


def build_revealed_submission_view_model(training_session: TrainingSession) -> dict | None:
    participant_labels = build_session_participant_label_map(training_session)
    active_questions = [
        question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    ]
    reveals_by_question_id: dict[int, list] = {}
    for reveal in training_session.revealed_question_answers:
        if reveal.submission_answer is not None:
            reveals_by_question_id.setdefault(reveal.question_id, []).append(reveal)
    has_revealed_answers = bool(reveals_by_question_id)

    question_rows = []
    for question in active_questions:
        reveals = reveals_by_question_id.get(question.id, [])
        revealed_answers = []
        for reveal in reveals:
            answer = reveal.submission_answer
            submission = answer.submission
            participant = submission.participant
            revealed_answers.append(
                {
                    "answer_text": answer.answer_text,
                    "participant_label": (
                        participant_labels.get(participant.id) if participant is not None else None
                    ),
                    "shift_label": (
                        participant.shift_label or "Unspecified" if participant is not None else None
                    ),
                    "attempt_number": submission.attempt_number,
                    "revealed_at_label": format_submission_timestamp(
                        reveal.updated_at or reveal.created_at
                    ),
                }
            )
        question_rows.append(
            {
                "question_id": question.id,
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "is_revealed": bool(revealed_answers),
                "revealed_answers": revealed_answers,
            }
        )

    return {
        "has_revealed_answers": has_revealed_answers,
        "question_rows": question_rows,
    }


def build_session_dashboard_view_model(training_session: TrainingSession) -> dict:
    active_question_ids = {
        question.id
        for question in training_session.scenario.questions
        if question.is_active
    }
    participants = sorted(
        training_session.participants,
        key=lambda item: item.joined_at or datetime.min,
    )
    submissions = sorted(
        training_session.submissions,
        key=lambda item: item.submitted_at or datetime.min,
        reverse=True,
    )
    latest_submission_by_participant = build_latest_submission_map(training_session)
    participant_labels = build_session_participant_label_map(training_session)

    participant_rows = []
    for participant in participants:
        latest_submission = latest_submission_by_participant.get(participant.id)
        participant_rows.append(
            {
                "participant_id": participant.id,
                "identity_label": participant_labels.get(
                    participant.id,
                    "Anonymous" if participant.is_anonymous else "Named Participant",
                ),
                "shift_label": participant.shift_label or "Unspecified",
                "joined_at_label": format_submission_timestamp(participant.joined_at),
                "submission_count": len(participant.submissions),
                "latest_attempt_number": latest_submission.attempt_number if latest_submission else None,
                "latest_submitted_at_label": (
                    format_submission_timestamp(latest_submission.submitted_at)
                    if latest_submission
                    else None
                ),
            }
        )

    submission_rows = []
    for submission in submissions:
        participant = submission.participant
        saved_answer_count = len(
            [answer for answer in submission.answers if answer.question_id in active_question_ids]
        )
        submission_rows.append(
            {
                "submission_id": submission.id,
                "participant_id": participant.id,
                "identity_label": participant_labels.get(
                    participant.id,
                    "Anonymous" if participant.is_anonymous else "Named Participant",
                ),
                "shift_label": participant.shift_label or "Unspecified",
                "attempt_number": submission.attempt_number,
                "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                "saved_answer_count": saved_answer_count,
                "expected_answer_count": len(active_question_ids),
                "status": submission.status,
                "status_label": format_submission_status(submission.status),
                "is_excluded": submission.status == SUBMISSION_STATUS_EXCLUDED,
            }
        )

    submitted_participant_ids = {submission.participant_id for submission in submissions}
    return {
        "participant_count": len(participants),
        "submitted_participant_count": len(submitted_participant_ids),
        "submission_count": len(submissions),
        "active_question_count": len(active_question_ids),
        "participant_rows": participant_rows,
        "submission_rows": submission_rows,
        "last_updated_label": format_submission_timestamp(datetime.utcnow()),
    }


def build_submission_detail_view_model(submission: Submission) -> dict:
    ordered_answers = sorted(
        submission.answers,
        key=lambda item: (
            item.question.sort_order if item.question else 0,
            item.question_id,
        ),
    )
    question_rows = []
    for answer in ordered_answers:
        question = answer.question
        question_type = (
            question.question_type if question and question.question_type else DEFAULT_QUESTION_TYPE
        )
        question_view = {
            "question_type": question_type,
            "instructor_answer": question.instructor_answer or "" if question else "",
        }
        question_rows.append(
            {
                "question_id": answer.question_id,
                "prompt": question.prompt if question else f"Question #{answer.question_id}",
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question_type,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "answer_text": answer.answer_text,
                "feedback": score_question_answer(question_view, answer.answer_text),
                "instructor_answer": question.instructor_answer or "" if question else "",
            }
        )

    participant = submission.participant
    participant_labels = build_session_participant_label_map(submission.training_session)
    audit_rows = sorted(submission.audit_logs, key=lambda item: item.created_at, reverse=True)
    return {
        "submission_id": submission.id,
        "attempt_number": submission.attempt_number,
        "submitted_at_label": format_submission_timestamp(submission.submitted_at),
        "status": submission.status,
        "status_label": format_submission_status(submission.status),
        "participant_label": participant_labels.get(
            participant.id,
            "Anonymous" if participant.is_anonymous else "Named Participant",
        ),
        "shift_label": participant.shift_label or "Unspecified",
        "review_notes": submission.notes or "",
        "approved_at_label": format_submission_timestamp(submission.approved_at),
        "approved_by_label": format_user_identity(submission.approved_by),
        "question_rows": question_rows,
        "audit_rows": [
            {
                "action_label": format_audit_action(log.action),
                "created_at_label": format_submission_timestamp(log.created_at),
                "actor_label": format_user_identity(log.actor) or "System",
                "notes": log.notes or "",
            }
            for log in audit_rows
        ],
    }


def build_host_review_summary(question_rows: list[dict]) -> dict:
    return {
        "question_count": len(question_rows),
        "revealed_question_count": sum(
            question["revealed_answer_count"] for question in question_rows
        ),
        "noted_answer_count": sum(
            1
            for question in question_rows
            for answer in question["answer_rows"]
            if answer["has_notes"]
        ),
    }


def build_host_board_workspace_view_model(training_session: TrainingSession) -> dict:
    from helpers import build_join_url_for_session, get_qr_image_url
    join_url, join_url_warning = build_join_url_for_session(training_session)
    dashboard = build_session_dashboard_view_model(training_session)
    question_rows = build_session_question_review_view_model(training_session)
    return {
        "session_id": training_session.id,
        "title": training_session.title or f"Session #{training_session.id}",
        "join_code": training_session.join_code,
        "status": training_session.status,
        "scenario_title": training_session.scenario.title,
        "join_url": join_url,
        "join_url_warning": join_url_warning,
        "qr_image_url": get_qr_image_url(join_url),
        "dashboard": dashboard,
        "question_rows": question_rows,
        "review_summary": build_host_review_summary(question_rows),
        "revealed_answers": build_revealed_submission_view_model(training_session),
    }


# ---------------------------------------------------------------------------
# Report view models
# ---------------------------------------------------------------------------

def build_reports_index_view_model() -> list[dict]:
    from models import TrainingSession as _TrainingSession
    training_sessions = _TrainingSession.query.order_by(
        _TrainingSession.created_at.desc(),
        _TrainingSession.id.desc(),
    ).all()
    rows = []
    for training_session in training_sessions:
        approved_submissions = [
            submission
            for submission in training_session.submissions
            if submission.status == SUBMISSION_STATUS_APPROVED
        ]
        rows.append(
            {
                "session_id": training_session.id,
                "title": training_session.title or f"Session #{training_session.id}",
                "scenario_title": training_session.scenario.title,
                "join_code": training_session.join_code,
                "status": training_session.status,
                "participant_count": len(training_session.participants),
                "approved_submission_count": len(approved_submissions),
                "created_at_label": format_submission_timestamp(training_session.created_at),
            }
        )
    return rows


def build_session_report_view_model(training_session: TrainingSession) -> dict:
    active_questions = [
        question
        for question in sorted(training_session.scenario.questions, key=lambda item: item.sort_order)
        if question.is_active
    ]
    approved_submissions = [
        submission
        for submission in sorted(
            training_session.submissions,
            key=lambda item: item.submitted_at or datetime.min,
        )
        if submission.status == SUBMISSION_STATUS_APPROVED
    ]
    pending_review_count = len([
        s for s in training_session.submissions if s.status == SUBMISSION_STATUS_SUBMITTED
    ])
    flagged_count = len([
        s for s in training_session.submissions if s.status == SUBMISSION_STATUS_FLAGGED
    ])
    excluded_count = len([
        s for s in training_session.submissions if s.status == SUBMISSION_STATUS_EXCLUDED
    ])

    shift_summary_map: dict[str, dict] = {}
    for participant in training_session.participants:
        shift_label = participant.shift_label or "Unspecified"
        shift_row = shift_summary_map.setdefault(
            shift_label,
            {
                "shift_label": shift_label,
                "participant_count": 0,
                "approved_submission_count": 0,
                "latest_submission_at_label": None,
            },
        )
        shift_row["participant_count"] += 1

    for submission in approved_submissions:
        shift_label = submission.participant.shift_label or "Unspecified"
        shift_row = shift_summary_map.setdefault(
            shift_label,
            {
                "shift_label": shift_label,
                "participant_count": 0,
                "approved_submission_count": 0,
                "latest_submission_at_label": None,
            },
        )
        shift_row["approved_submission_count"] += 1
        shift_row["latest_submission_at_label"] = format_submission_timestamp(submission.submitted_at)

    approved_answer_lookup = {
        submission.id: {answer.question_id: answer for answer in submission.answers}
        for submission in approved_submissions
    }

    question_rows = []
    for question in active_questions:
        answer_rows = []
        for submission in approved_submissions:
            answer = approved_answer_lookup[submission.id].get(question.id)
            if answer is None:
                continue
            answer_rows.append(
                {
                    "participant_label": (
                        "Anonymous"
                        if submission.participant.is_anonymous
                        else (submission.participant.display_name or "Named Participant")
                    ),
                    "shift_label": submission.participant.shift_label or "Unspecified",
                    "attempt_number": submission.attempt_number,
                    "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                    "answer_text": answer.answer_text,
                }
            )
        question_rows.append(
            {
                "prompt": question.prompt,
                "question_type_label": QUESTION_TYPE_LABELS.get(
                    question.question_type or DEFAULT_QUESTION_TYPE,
                    QUESTION_TYPE_LABELS[DEFAULT_QUESTION_TYPE],
                ),
                "answer_rows": answer_rows,
            }
        )

    approved_submission_rows = []
    for submission in approved_submissions:
        approved_submission_rows.append(
            {
                "participant_label": (
                    "Anonymous"
                    if submission.participant.is_anonymous
                    else (submission.participant.display_name or "Named Participant")
                ),
                "shift_label": submission.participant.shift_label or "Unspecified",
                "attempt_number": submission.attempt_number,
                "submitted_at_label": format_submission_timestamp(submission.submitted_at),
                "approved_by_label": format_user_identity(submission.approved_by),
                "approved_at_label": format_submission_timestamp(submission.approved_at),
                "review_notes": submission.notes or "",
            }
        )

    return {
        "session_id": training_session.id,
        "title": training_session.title or f"Session #{training_session.id}",
        "scenario_title": training_session.scenario.title,
        "join_code": training_session.join_code,
        "status": training_session.status,
        "created_at_label": format_submission_timestamp(training_session.created_at),
        "participant_count": len(training_session.participants),
        "approved_submission_count": len(approved_submissions),
        "pending_review_count": pending_review_count,
        "flagged_count": flagged_count,
        "excluded_count": excluded_count,
        "shift_rows": sorted(shift_summary_map.values(), key=lambda item: item["shift_label"]),
        "question_rows": question_rows,
        "approved_submission_rows": approved_submission_rows,
    }
