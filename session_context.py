"""
Manages the two request-scoped training contexts stored in the Flask session:

  Participant context  – which Participant record the browser is joined as.
  Host context         – which TrainingSession the instructor is hosting.

All reads and writes of the four participant/host session keys are concentrated
here so that invariants (e.g. scenario_id staying in sync) are enforced in
one place.
"""
from __future__ import annotations

from flask import g, request, session

from authz import PERM_VIEW_SESSION_SUBMISSIONS

# Session key names — re-exported via helpers.py and app.py for test access
PARTICIPANT_JOIN_MAP_KEY = "joined_participants"
ACTIVE_TRAINING_SESSION_ID_KEY = "active_training_session_id"
ACTIVE_PARTICIPANT_ID_KEY = "active_participant_id"
HOST_TRAINING_SESSION_ID_KEY = "host_training_session_id"


# ---------------------------------------------------------------------------
# Current user / DB helpers
# ---------------------------------------------------------------------------

def get_current_db_user():
    from models import User
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    return User.query.filter_by(id=user_id, is_active=True).first()


def get_signed_in_participant_for_session(training_session_id: int, user):
    from models import Participant
    if user is None:
        return None
    return (
        Participant.query.filter_by(
            training_session_id=training_session_id,
            user_id=user.id,
        )
        .order_by(Participant.joined_at.desc(), Participant.id.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Participant context
# ---------------------------------------------------------------------------

def clear_participant_session_context() -> None:
    session.pop(PARTICIPANT_JOIN_MAP_KEY, None)
    session.pop(ACTIVE_TRAINING_SESSION_ID_KEY, None)
    session.pop(ACTIVE_PARTICIPANT_ID_KEY, None)


def get_joined_participant_for_session(training_session_id: int):
    from models import Participant
    joined_map = session.get(PARTICIPANT_JOIN_MAP_KEY, {})
    if not isinstance(joined_map, dict):
        return None
    raw_participant_id = joined_map.get(str(training_session_id))
    if not isinstance(raw_participant_id, int):
        return None
    participant = Participant.query.filter_by(id=raw_participant_id).first()
    if participant is None or participant.training_session_id != training_session_id:
        return None
    return participant


def set_active_participant_context(participant) -> None:
    from models import TrainingSession
    joined_map = session.get(PARTICIPANT_JOIN_MAP_KEY, {})
    if not isinstance(joined_map, dict):
        joined_map = {}
    joined_map[str(participant.training_session_id)] = participant.id
    session[PARTICIPANT_JOIN_MAP_KEY] = joined_map
    session[ACTIVE_TRAINING_SESSION_ID_KEY] = participant.training_session_id
    session[ACTIVE_PARTICIPANT_ID_KEY] = participant.id
    training_session = TrainingSession.query.filter_by(id=participant.training_session_id).first()
    if training_session is not None:
        session["scenario_id"] = training_session.scenario_id


def load_active_participant_session():
    from models import Participant, TrainingSession
    training_session_id = session.get(ACTIVE_TRAINING_SESSION_ID_KEY)
    participant_id = session.get(ACTIVE_PARTICIPANT_ID_KEY)
    if not isinstance(training_session_id, int) or not isinstance(participant_id, int):
        return None, None

    participant = Participant.query.filter_by(id=participant_id).first()
    training_session = TrainingSession.query.filter_by(id=training_session_id).first()
    if (
        participant is None
        or training_session is None
        or participant.training_session_id != training_session.id
    ):
        clear_participant_session_context()
        return None, None

    current_db_user = get_current_db_user()
    if participant.user_id is not None and (
        current_db_user is None or current_db_user.id != participant.user_id
    ):
        clear_participant_session_context()
        return None, None

    if training_session.status != "active":
        return participant, training_session

    session["scenario_id"] = training_session.scenario_id
    return participant, training_session


# ---------------------------------------------------------------------------
# Host context
# ---------------------------------------------------------------------------

def clear_host_training_session_context() -> None:
    session.pop(HOST_TRAINING_SESSION_ID_KEY, None)


def set_host_training_session_context(training_session) -> None:
    session[HOST_TRAINING_SESSION_ID_KEY] = training_session.id
    session["scenario_id"] = training_session.scenario_id


def load_host_training_session():
    from models import TrainingSession
    if not g.current_user.has_permission(PERM_VIEW_SESSION_SUBMISSIONS):
        return None

    requested_session_id = request.args.get("session_id", "").strip()
    resolved_session_id = None
    if requested_session_id.isdigit():
        resolved_session_id = int(requested_session_id)
    else:
        stored_session_id = session.get(HOST_TRAINING_SESSION_ID_KEY)
        if isinstance(stored_session_id, int):
            resolved_session_id = stored_session_id

    if resolved_session_id is None:
        return None

    training_session = TrainingSession.query.filter_by(id=resolved_session_id).first()
    if training_session is None:
        clear_host_training_session_context()
        return None
    # Allow host to view closed sessions (read-only facilitation)
    if training_session.status not in {"active", "closed"}:
        clear_host_training_session_context()
        return None

    set_host_training_session_context(training_session)
    return training_session
