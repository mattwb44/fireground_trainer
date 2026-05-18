from datetime import datetime

from extensions import db


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UserRole(db.Model):
    __tablename__ = "user_roles"

    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id = db.Column(
        db.Integer, db.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="role_links")
    role = db.relationship("Role", back_populates="user_links")


class Department(TimestampMixin, db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    invite_code = db.Column(db.String(32), nullable=False, unique=True, index=True)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    members = db.relationship(
        "User", back_populates="department", foreign_keys="User.department_id"
    )


class User(TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(120), nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_email_verified = db.Column(db.Boolean, nullable=False, default=False)
    last_login_at = db.Column(db.DateTime, nullable=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    department = db.relationship(
        "Department", back_populates="members", foreign_keys=[department_id]
    )
    role_links = db.relationship(
        "UserRole", back_populates="user", cascade="all, delete-orphan"
    )
    created_scenarios = db.relationship(
        "Scenario",
        back_populates="created_by",
        foreign_keys="Scenario.created_by_user_id",
    )
    created_sessions = db.relationship(
        "TrainingSession",
        back_populates="created_by",
        foreign_keys="TrainingSession.created_by_user_id",
    )
    participant_profiles = db.relationship("Participant", back_populates="user")
    magic_tokens = db.relationship(
        "MagicLoginToken", back_populates="user", cascade="all, delete-orphan"
    )
    activation_tokens = db.relationship(
        "AccountActivationToken", back_populates="user", cascade="all, delete-orphan"
    )
    scenario_likes = db.relationship(
        "ScenarioLike", back_populates="user", cascade="all, delete-orphan"
    )
    admin_audit_entries = db.relationship(
        "AdminAuditLog",
        back_populates="actor",
        foreign_keys="AdminAuditLog.actor_user_id",
    )


class Role(TimestampMixin, db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)

    user_links = db.relationship(
        "UserRole", back_populates="role", cascade="all, delete-orphan"
    )


class Scenario(TimestampMixin, db.Model):
    __tablename__ = "scenarios"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False, index=True)
    dispatch_text = db.Column(db.Text, nullable=False)
    base_image_path = db.Column(db.String(255), nullable=False)
    overlay_image_path = db.Column(db.String(255), nullable=True)
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    archived_at = db.Column(db.DateTime, nullable=True)
    is_official = db.Column(db.Boolean, nullable=False, default=False, index=True)
    submitted_for_official_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    like_count = db.Column(db.Integer, nullable=False, default=0, index=True)
    is_public = db.Column(db.Boolean, nullable=False, default=False, index=True)
    training_category = db.Column(db.String(20), nullable=True, index=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    forked_from_scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )

    created_by = db.relationship(
        "User", back_populates="created_scenarios", foreign_keys=[created_by_user_id]
    )
    approved_by = db.relationship(
        "User", foreign_keys=[approved_by_user_id]
    )
    department = db.relationship("Department", foreign_keys=[department_id])
    questions = db.relationship(
        "Question", back_populates="scenario", cascade="all, delete-orphan"
    )
    training_sessions = db.relationship(
        "TrainingSession", back_populates="scenario", cascade="all, delete-orphan"
    )
    submissions = db.relationship("Submission", back_populates="scenario")
    likes = db.relationship(
        "ScenarioLike", back_populates="scenario", cascade="all, delete-orphan"
    )
    tag_links = db.relationship(
        "ScenarioTag", back_populates="scenario", cascade="all, delete-orphan"
    )
    position_links = db.relationship(
        "ScenarioPosition", back_populates="scenario", cascade="all, delete-orphan"
    )


class Tag(TimestampMixin, db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    slug = db.Column(db.String(80), nullable=False, unique=True, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    scenario_links = db.relationship(
        "ScenarioTag", back_populates="tag", cascade="all, delete-orphan"
    )


class ScenarioTag(db.Model):
    __tablename__ = "scenario_tags"

    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id = db.Column(
        db.Integer, db.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    scenario = db.relationship("Scenario", back_populates="tag_links")
    tag = db.relationship("Tag", back_populates="scenario_links")


class ScenarioPosition(db.Model):
    __tablename__ = "scenario_positions"

    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="CASCADE"), primary_key=True
    )
    position = db.Column(db.String(40), primary_key=True)

    scenario = db.relationship("Scenario", back_populates="position_links")


class ScenarioLike(TimestampMixin, db.Model):
    __tablename__ = "scenario_likes"
    __table_args__ = (
        db.UniqueConstraint("scenario_id", "user_id", name="uq_scenario_likes_scenario_user"),
        db.Index("ix_scenario_likes_scenario_liked", "scenario_id", "is_liked"),
        db.Index("ix_scenario_likes_user_updated", "user_id", "updated_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_liked = db.Column(db.Boolean, nullable=False, default=True)

    scenario = db.relationship("Scenario", back_populates="likes")
    user = db.relationship("User", back_populates="scenario_likes")


class Question(TimestampMixin, db.Model):
    __tablename__ = "questions"
    __table_args__ = (
        db.UniqueConstraint("scenario_id", "question_key"),
        db.UniqueConstraint("scenario_id", "sort_order"),
        db.Index("ix_questions_scenario_sort", "scenario_id", "sort_order"),
    )

    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False
    )
    question_key = db.Column(db.String(50), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(40), nullable=False, default="discussion_only")
    instructor_answer = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    scenario = db.relationship("Scenario", back_populates="questions")
    submission_answers = db.relationship("SubmissionAnswer", back_populates="question")
    choices = db.relationship(
        "QuestionChoice", back_populates="question", cascade="all, delete-orphan",
        order_by="QuestionChoice.sort_order",
    )


class QuestionChoice(db.Model):
    __tablename__ = "question_choices"
    __table_args__ = (
        db.Index("ix_question_choices_question", "question_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(
        db.Integer, db.ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    choice_text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    question = db.relationship("Question", back_populates="choices")


class TrainingSession(TimestampMixin, db.Model):
    __tablename__ = "training_sessions"
    __table_args__ = (
        db.Index("ix_training_sessions_scenario_created", "scenario_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False
    )
    created_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    join_code = db.Column(db.String(16), nullable=False, unique=True)
    title = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    starts_at = db.Column(db.DateTime, nullable=True)
    ends_at = db.Column(db.DateTime, nullable=True)
    archived_at = db.Column(db.DateTime, nullable=True)

    scenario = db.relationship("Scenario", back_populates="training_sessions")
    created_by = db.relationship(
        "User", back_populates="created_sessions", foreign_keys=[created_by_user_id]
    )
    participants = db.relationship(
        "Participant", back_populates="training_session", cascade="all, delete-orphan"
    )
    submissions = db.relationship(
        "Submission",
        back_populates="training_session",
        cascade="all, delete-orphan",
        foreign_keys="Submission.training_session_id",
    )
    revealed_question_answers = db.relationship(
        "SessionQuestionReveal",
        back_populates="training_session",
        cascade="all, delete-orphan",
    )


class Participant(db.Model):
    __tablename__ = "participants"
    __table_args__ = (db.Index("ix_participants_session_joined", "training_session_id", "joined_at"),)

    id = db.Column(db.Integer, primary_key=True)
    training_session_id = db.Column(
        db.Integer,
        db.ForeignKey("training_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    display_name = db.Column(db.String(120), nullable=True)
    shift_label = db.Column(db.String(50), nullable=True)
    is_anonymous = db.Column(db.Boolean, nullable=False, default=False)
    joined_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    training_session = db.relationship("TrainingSession", back_populates="participants")
    user = db.relationship("User", back_populates="participant_profiles")
    submissions = db.relationship(
        "Submission", back_populates="participant", cascade="all, delete-orphan"
    )


class Submission(db.Model):
    __tablename__ = "submissions"
    __table_args__ = (
        db.UniqueConstraint("participant_id", "attempt_number"),
        db.CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        db.Index("ix_submissions_session_submitted", "training_session_id", "submitted_at"),
        db.Index("ix_submissions_scenario_submitted", "scenario_id", "submitted_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    participant_id = db.Column(
        db.Integer, db.ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    training_session_id = db.Column(
        db.Integer,
        db.ForeignKey("training_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="submitted")
    notes = db.Column(db.Text, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime, nullable=True)
    approved_by_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    participant = db.relationship("Participant", back_populates="submissions")
    training_session = db.relationship(
        "TrainingSession",
        back_populates="submissions",
        foreign_keys=[training_session_id],
    )
    scenario = db.relationship("Scenario", back_populates="submissions")
    approved_by = db.relationship("User", foreign_keys=[approved_by_user_id])
    answers = db.relationship(
        "SubmissionAnswer", back_populates="submission", cascade="all, delete-orphan"
    )
    audit_logs = db.relationship(
        "SubmissionAuditLog", back_populates="submission", cascade="all, delete-orphan"
    )


class SubmissionAnswer(TimestampMixin, db.Model):
    __tablename__ = "submission_answers"
    __table_args__ = (
        db.UniqueConstraint("submission_id", "question_id"),
        db.Index("ix_submission_answers_question", "question_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer, db.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    question_id = db.Column(
        db.Integer, db.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    )
    answer_text = db.Column(db.Text, nullable=False, default="")
    selected_choice_id = db.Column(
        db.Integer, db.ForeignKey("question_choices.id", ondelete="SET NULL"), nullable=True
    )

    submission = db.relationship("Submission", back_populates="answers")
    question = db.relationship("Question", back_populates="submission_answers")
    session_reveals = db.relationship("SessionQuestionReveal", back_populates="submission_answer")
    selected_choice = db.relationship("QuestionChoice", foreign_keys=[selected_choice_id])


class SessionQuestionReveal(TimestampMixin, db.Model):
    __tablename__ = "session_question_reveals"
    __table_args__ = (
        db.UniqueConstraint(
            "training_session_id",
            "submission_answer_id",
            name="uq_session_question_reveals_session_answer",
        ),
        db.Index(
            "ix_session_question_reveals_session_question",
            "training_session_id",
            "question_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    training_session_id = db.Column(
        db.Integer,
        db.ForeignKey("training_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_id = db.Column(
        db.Integer, db.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    )
    submission_answer_id = db.Column(
        db.Integer,
        db.ForeignKey("submission_answers.id", ondelete="RESTRICT"),
        nullable=False,
    )

    training_session = db.relationship(
        "TrainingSession",
        back_populates="revealed_question_answers",
    )
    question = db.relationship("Question")
    submission_answer = db.relationship(
        "SubmissionAnswer",
        back_populates="session_reveals",
    )


class SubmissionAuditLog(db.Model):
    __tablename__ = "submission_audit_logs"
    __table_args__ = (
        db.Index("ix_submission_audit_logs_submission_created", "submission_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(
        db.Integer, db.ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    actor_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action = db.Column(db.String(40), nullable=False)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    submission = db.relationship("Submission", back_populates="audit_logs")
    actor = db.relationship("User", foreign_keys=[actor_user_id])


class AdminAuditLog(db.Model):
    __tablename__ = "admin_audit_logs"
    __table_args__ = (
        db.Index("ix_admin_audit_logs_created_at", "created_at"),
        db.Index("ix_admin_audit_logs_actor_created", "actor_user_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action = db.Column(db.String(80), nullable=False)
    target_type = db.Column(db.String(40), nullable=False)
    target_id = db.Column(db.Integer, nullable=True)
    target_label = db.Column(db.String(255), nullable=True)
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    actor = db.relationship("User", back_populates="admin_audit_entries", foreign_keys=[actor_user_id])


class MagicLoginToken(db.Model):
    __tablename__ = "magic_login_tokens"
    __table_args__ = (
        db.Index("ix_magic_login_tokens_user_expires", "user_id", "expires_at"),
        db.Index("ix_magic_login_tokens_token_hash", "token_hash"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="magic_tokens")


class AccountActivationToken(db.Model):
    __tablename__ = "account_activation_tokens"
    __table_args__ = (
        db.Index("ix_account_activation_tokens_user_expires", "user_id", "expires_at"),
        db.Index("ix_account_activation_tokens_token_hash", "token_hash"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User", back_populates="activation_tokens")


class DrillAttempt(TimestampMixin, db.Model):
    __tablename__ = "drill_attempts"
    __table_args__ = (
        db.UniqueConstraint("user_id", "scenario_id", "attempt_number"),
        db.Index("ix_drill_attempts_user_scenario", "user_id", "scenario_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scenario_id = db.Column(
        db.Integer, db.ForeignKey("scenarios.id", ondelete="RESTRICT"), nullable=False
    )
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="submitted")
    submitted_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user = db.relationship("User")
    scenario = db.relationship("Scenario")
    answers = db.relationship(
        "DrillAttemptAnswer", back_populates="drill_attempt", cascade="all, delete-orphan"
    )


class DrillAttemptAnswer(db.Model):
    __tablename__ = "drill_attempt_answers"
    __table_args__ = (
        db.UniqueConstraint("drill_attempt_id", "question_id"),
        db.Index("ix_drill_attempt_answers_question", "question_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    drill_attempt_id = db.Column(
        db.Integer, db.ForeignKey("drill_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id = db.Column(
        db.Integer, db.ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    )
    answer_text = db.Column(db.Text, nullable=False, default="")
    selected_choice_id = db.Column(
        db.Integer, db.ForeignKey("question_choices.id", ondelete="SET NULL"), nullable=True
    )

    drill_attempt = db.relationship("DrillAttempt", back_populates="answers")
    question = db.relationship("Question")
    selected_choice = db.relationship("QuestionChoice", foreign_keys=[selected_choice_id])
