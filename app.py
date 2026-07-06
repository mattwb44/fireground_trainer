from pathlib import Path

from flask import Flask
from flask_migrate import stamp as db_stamp, upgrade as db_upgrade
from sqlalchemy import inspect as sqla_inspect

from blueprints.admin import admin_bp
from blueprints.auth import auth_bp
from blueprints.departments import departments_bp
from blueprints.lists import lists_bp
from blueprints.main import main_bp
from blueprints.reports import reports_bp
from blueprints.scenarios import scenarios_bp
from blueprints.sessions import sessions_bp
from extensions import db, migrate

# Re-exported for test compatibility (tests do `app_module.X`)
from helpers import (  # noqa: F401
    ACTIVE_PARTICIPANT_ID_KEY,
    ACTIVE_TRAINING_SESSION_ID_KEY,
    CSRF_SESSION_KEY,
    DEFAULT_QUESTION_TYPE,
    HOST_TRAINING_SESSION_ID_KEY,
    PARTICIPANT_JOIN_MAP_KEY,
    ROLE_INSTRUCTOR,
    ROLE_PARTICIPANT,
    SCENARIO_STATUS_APPROVED,
    SCENARIO_STATUS_ARCHIVED,
    SCENARIO_STATUS_DRAFT,
    SCENARIO_STATUS_SUBMITTED,
    SUBMISSION_STATUS_APPROVED,
    SUBMISSION_STATUS_EXCLUDED,
    SUBMISSION_STATUS_FLAGGED,
    SUBMISSION_STATUS_SUBMITTED,
    build_join_url_for_session,
    default_sqlite_database_url,
    detect_lan_ip,
    normalize_static_asset_path,
    safe_redirect_target,
)
from helpers import build_runtime_config, ensure_seed_data, ensure_seed_scenarios, ensure_seed_tags, log_runtime_configuration_warnings
from models import (  # noqa: F401
    AccountActivationToken,
    AdminAuditLog,
    Department,
    DrillAttempt,
    DrillAttemptAnswer,
    MagicLoginToken,
    Participant,
    Question,
    QuestionChoice,
    ScenarioFlag,
    ScenarioTokenLayout,
    SessionBoardState,
    Role,
    Scenario,
    ScenarioLike,
    ScenarioPosition,
    ScenarioTag,
    UserList,
    UserListScenario,
    SessionQuestionReveal,
    Submission,
    SubmissionAnswer,
    SubmissionAuditLog,
    Tag,
    TrainingSession,
    User,
    UserRole,
)

# For tests that reference app_module.generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash  # noqa: F401

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config.update(build_runtime_config(app.instance_path))
db.init_app(app)
migrate.init_app(app, db)

app.register_blueprint(admin_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(departments_bp)
app.register_blueprint(lists_bp)
app.register_blueprint(main_bp)
app.register_blueprint(reports_bp)
app.register_blueprint(scenarios_bp)
app.register_blueprint(sessions_bp)

with app.app_context():
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    # On an empty database, create_all() builds the full current schema, so
    # replaying migrations would fail on ALTER TABLE for columns that already
    # exist — stamp head instead. Existing databases upgrade as before.
    _is_fresh_db = not sqla_inspect(db.engine).get_table_names()
    db.create_all()
    if _is_fresh_db:
        db_stamp()
    else:
        db_upgrade()
    ensure_seed_data()
    ensure_seed_scenarios()
    ensure_seed_tags()
    log_runtime_configuration_warnings()

if __name__ == "__main__":
    app.run(
        host=app.config["RUN_HOST"],
        port=app.config["RUN_PORT"],
        debug=app.config["RUN_DEBUG"],
    )
