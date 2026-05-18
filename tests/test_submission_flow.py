import importlib
import os
import re
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import inspect, text


_tmp_dir = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir.name) / 'submission_flow.sqlite'}"
app_module = importlib.import_module("app")
helpers_module = importlib.import_module("helpers")

app = app_module.app
db = app_module.db
CSRF_SESSION_KEY = app_module.CSRF_SESSION_KEY
Scenario = app_module.Scenario
TrainingSession = app_module.TrainingSession
Participant = app_module.Participant
ScenarioLike = app_module.ScenarioLike
AccountActivationToken = app_module.AccountActivationToken
Submission = app_module.Submission
SubmissionAnswer = app_module.SubmissionAnswer
SubmissionAuditLog = app_module.SubmissionAuditLog
AdminAuditLog = app_module.AdminAuditLog
User = app_module.User
Role = app_module.Role
UserRole = app_module.UserRole
DrillAttemptAnswer = app_module.DrillAttemptAnswer
DrillAttempt = app_module.DrillAttempt


class SubmissionFlowTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_csrf_token()

        with app.app_context():
            AccountActivationToken.query.delete()
            AdminAuditLog.query.delete()
            ScenarioLike.query.delete()
            SubmissionAnswer.query.delete()
            SubmissionAuditLog.query.delete()
            Submission.query.delete()
            Participant.query.delete()
            TrainingSession.query.delete()
            DrillAttemptAnswer.query.delete()
            DrillAttempt.query.delete()
            Scenario.query.update({"like_count": 0}, synchronize_session=False)
            db.session.commit()

    def _set_csrf_token(self, token: str = "test-csrf-token") -> None:
        with self.client.session_transaction() as flask_session:
            flask_session[CSRF_SESSION_KEY] = token
        self.csrf_token = token

    def _create_training_session(self) -> TrainingSession:
        with app.app_context():
            scenario = (
                Scenario.query.filter(Scenario.is_active.is_(True))
                .order_by(Scenario.id.asc())
                .first()
            )
            join_code = f"SMOKE{TrainingSession.query.count() + 1}"
            training_session = TrainingSession(
                scenario_id=scenario.id,
                join_code=join_code,
                title="Smoke Test Session",
                status="active",
            )
            db.session.add(training_session)
            db.session.commit()
            db.session.refresh(training_session)
            return training_session

    def _login_as_instructor(self) -> None:
        self._login_as_user("instructor@demo.local")

    def _login_as_user(self, email: str) -> None:
        with app.app_context():
            user = User.query.filter_by(email=email).first()
        with self.client.session_transaction() as flask_session:
            flask_session["user_id"] = user.id
            flask_session[CSRF_SESSION_KEY] = self.csrf_token

    def _build_instructor_client(self):
        return self._build_staff_client("instructor@demo.local", "instructor-csrf-token")

    def _build_chief_client(self):
        return self._build_staff_client("chief@demo.local", "chief-csrf-token")

    def _build_staff_client(self, email: str, csrf_token: str):
        staff_client = app.test_client()
        with app.app_context():
            user = User.query.filter_by(email=email).first()
        with staff_client.session_transaction() as flask_session:
            flask_session["user_id"] = user.id
            flask_session[CSRF_SESSION_KEY] = csrf_token
        return staff_client, csrf_token

    def _build_participant_client(self, csrf_token: str):
        participant_client = app.test_client()
        with participant_client.session_transaction() as flask_session:
            flask_session[CSRF_SESSION_KEY] = csrf_token
        return participant_client, csrf_token

    def _create_verified_participant_user(self, email: str, full_name: str) -> User:
        with app.app_context():
            existing_user = User.query.filter_by(email=email).first()
            if existing_user is not None:
                return existing_user
            participant_role = Role.query.filter_by(name=app_module.ROLE_PARTICIPANT).first()
            user = User(
                email=email,
                full_name=full_name,
                password_hash=app_module.generate_password_hash("Participant123"),
                is_active=True,
                is_email_verified=True,
            )
            db.session.add(user)
            db.session.flush()
            db.session.add(UserRole(user_id=user.id, role_id=participant_role.id))
            db.session.commit()
            db.session.refresh(user)
            return user

    def _extract_activation_path(self, response_text: str) -> str:
        match = re.search(r'href="(http://localhost/activate-account\?token=[^"]+)"', response_text)
        self.assertIsNotNone(match)
        return match.group(1).replace("http://localhost", "")

    def _active_questions_for_scenario(self, scenario_id: int) -> list[dict]:
        with app.app_context():
            scenario = Scenario.query.filter_by(id=scenario_id).first()
            return [
                {"id": question.id, "prompt": question.prompt}
                for question in sorted(scenario.questions, key=lambda item: item.sort_order)
                if question.is_active
            ]

    def _submit_answers_for_session(self, client, csrf_token: str, training_session: TrainingSession, label: str):
        questions = self._active_questions_for_scenario(training_session.scenario_id)
        payload = {"csrf_token": csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"{label} answer {index}"
        return client.post("/submit", data=payload)

    def test_join_url_uses_public_base_url_override(self):
        original_public_base_url = app.config.get("PUBLIC_BASE_URL")
        app.config["PUBLIC_BASE_URL"] = "http://192.168.1.23:5000"
        try:
            with app.test_request_context("/", base_url="http://127.0.0.1:5000"):
                training_session = TrainingSession(join_code="OVERRIDE")
                join_url, join_url_warning = app_module.build_join_url_for_session(training_session)
        finally:
            app.config["PUBLIC_BASE_URL"] = original_public_base_url

        self.assertEqual(join_url, "http://192.168.1.23:5000/join/OVERRIDE")
        self.assertIsNone(join_url_warning)

    def test_join_url_rewrites_loopback_to_detected_lan_address(self):
        original_public_base_url = app.config.get("PUBLIC_BASE_URL")
        original_detect_lan_ip = helpers_module.detect_lan_ip
        app.config["PUBLIC_BASE_URL"] = None
        helpers_module.detect_lan_ip = lambda: "192.168.1.50"
        try:
            with app.test_request_context("/", base_url="http://127.0.0.1:5000"):
                training_session = TrainingSession(join_code="LANJOIN")
                join_url, join_url_warning = app_module.build_join_url_for_session(training_session)
        finally:
            app.config["PUBLIC_BASE_URL"] = original_public_base_url
            helpers_module.detect_lan_ip = original_detect_lan_ip

        self.assertEqual(join_url, "http://192.168.1.50:5000/join/LANJOIN")
        self.assertIn("detected LAN address", join_url_warning)

    def test_homepage_renders_correctly(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Blitzfire Training", response.data)
        self.assertIn(b"Fireground Training", response.data)
        self.assertIn(b"Browse Scenarios", response.data)
        self.assertIn(b"Join Session", response.data)
        self.assertIn(b"How it works", response.data)
        self.assertIn(b"Public scenarios", response.data)

    def test_fireground_category_page_shows_filters_and_scenarios(self):
        with app.app_context():
            scenario = (
                Scenario.query.filter(
                    Scenario.is_active.is_(True),
                    Scenario.status == app_module.SCENARIO_STATUS_APPROVED,
                )
                .order_by(Scenario.id.asc())
                .first()
            )

        response = self.client.get("/training/fireground")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Official", response.data)
        self.assertIn(b"Instructor Made", response.data)
        self.assertIn(b"User Made", response.data)
        self.assertIn(scenario.title.encode("utf-8"), response.data)
        self.assertIn(b"live scenarios", response.data)
        self.assertIn(b"Likes come from completed signed-in submissions", response.data)

    def test_scenario_library_shows_consistent_browsing_metadata(self):
        self._login_as_instructor()

        response = self.client.get("/scenarios?tab=official")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Popularity uses likes from completed signed-in submissions", response.data)
        self.assertIn(b"Official", response.data)
        self.assertIn(b"Question", response.data)
        self.assertIn(b"like", response.data)

    def test_create_account_registers_pending_participant_and_shows_activation_link(self):
        response = self.client.post(
            "/create-account",
            data={
                "csrf_token": self.csrf_token,
                "full_name": "Taylor Recruit",
                "email": "taylor@example.com",
                "password": "Recruit123",
                "confirm_password": "Recruit123",
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertIn(b"Account created.", response.data)
        self.assertIn(b"Activate this account now", response.data)

        with app.app_context():
            user = User.query.filter_by(email="taylor@example.com").first()
            self.assertIsNotNone(user)
            self.assertFalse(user.is_email_verified)
            self.assertTrue(user.is_active)
            self.assertEqual(AccountActivationToken.query.filter_by(user_id=user.id).count(), 1)
            role_names = {link.role.name for link in user.role_links}
            self.assertEqual(role_names, {app_module.ROLE_PARTICIPANT})

    def test_unverified_account_cannot_sign_in_until_activation(self):
        create_response = self.client.post(
            "/create-account",
            data={
                "csrf_token": self.csrf_token,
                "full_name": "Morgan Recruit",
                "email": "morgan@example.com",
                "password": "Recruit123",
                "confirm_password": "Recruit123",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        self._set_csrf_token("login-after-register")

        login_response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token,
                "email": "morgan@example.com",
                "password": "Recruit123",
                "next": "/board",
            },
        )

        self.assertEqual(login_response.status_code, 403)
        self.assertIn(b"Activate your account from the verification link before signing in.", login_response.data)

    def test_activation_verifies_account_signs_user_in_and_improves_join_copy(self):
        create_response = self.client.post(
            "/create-account",
            data={
                "csrf_token": self.csrf_token,
                "full_name": "Jamie Probationary",
                "email": "jamie@example.com",
                "password": "Recruit123",
                "confirm_password": "Recruit123",
            },
        )
        self.assertEqual(create_response.status_code, 201)
        activation_path = self._extract_activation_path(create_response.get_data(as_text=True))

        activate_response = self.client.get(activation_path, follow_redirects=True)

        self.assertEqual(activate_response.status_code, 200)
        self.assertIn(b"Account activated. You are now signed in.", activate_response.data)

        with app.app_context():
            user = User.query.filter_by(email="jamie@example.com").first()
            token_row = AccountActivationToken.query.filter_by(user_id=user.id).first()
            self.assertTrue(user.is_email_verified)
            self.assertIsNotNone(token_row.used_at)

        training_session = self._create_training_session()
        join_response = self.client.get(f"/join/{training_session.join_code}")
        self.assertEqual(join_response.status_code, 200)
        self.assertIn(b"Signed in as Jamie Probationary", join_response.data)

    def test_alembic_version_table_present_and_at_head(self):
        with app.app_context():
            inspector = inspect(db.engine)
            self.assertIn("alembic_version", inspector.get_table_names())
            rows = db.session.execute(
                text("SELECT version_num FROM alembic_version")
            ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], "d9e0f1a2b3c4")

    def test_schema_migrations_table_removed_by_initial_migration(self):
        with app.app_context():
            inspector = inspect(db.engine)
            self.assertNotIn("schema_migrations", inspector.get_table_names())

    def test_default_sqlite_database_url_uses_instance_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_url = app_module.default_sqlite_database_url(temp_dir)

        self.assertTrue(database_url.startswith("sqlite:///"))
        self.assertIn("fireground_trainer.db", database_url)

    def test_normalize_static_asset_path_only_allows_existing_files_under_static(self):
        with app.app_context():
            valid_path = app_module.normalize_static_asset_path("images/house1.jpg")
            missing_path = app_module.normalize_static_asset_path("images/does_not_exist.jpg")
            escaped_path = app_module.normalize_static_asset_path("../secrets.txt")
            absolute_path = app_module.normalize_static_asset_path("/tmp/house1.jpg")

        self.assertEqual(valid_path, "images/house1.jpg")
        self.assertIsNone(missing_path)
        self.assertIsNone(escaped_path)
        self.assertIsNone(absolute_path)

    def test_homepage_response_includes_release_security_headers(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(response.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertEqual(
            response.headers.get("Permissions-Policy"),
            "camera=(), microphone=(), geolocation=()",
        )

    def test_login_rejects_cross_site_origin_even_with_valid_csrf_token(self):
        response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token,
                "email": "instructor@demo.local",
                "password": "EasyPass123",
                "next": "/board",
            },
            headers={"Origin": "http://evil.example"},
        )

        self.assertEqual(response.status_code, 400)

    def test_bad_request_error_handler_uses_themed_template(self):
        response = self.client.post(
            "/login",
            data={
                "email": "instructor@demo.local",
                "password": "EasyPass123",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Bad request", response.data)
        self.assertIn(b"Return to a safe page", response.data)

    def test_safe_redirect_target_rejects_dynamic_post_only_route(self):
        with app.test_request_context("/"):
            target = app_module.safe_redirect_target("/sessions/1/reveal")

        self.assertEqual(target, "/")

    def test_conflict_error_handler_uses_themed_template(self):
        chief_client, csrf_token = self._build_chief_client()
        with app.app_context():
            scenario = (
                Scenario.query.filter(
                    Scenario.is_active.is_(True),
                    Scenario.status == app_module.SCENARIO_STATUS_APPROVED,
                )
                .order_by(Scenario.id.asc())
                .first()
            )

        response = chief_client.post(
            "/scenario/approve",
            data={
                "csrf_token": csrf_token,
                "scenario_id": str(scenario.id),
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn(b"Action no longer fits the current state", response.data)
        self.assertIn(b"Return to the training board", response.data)

    def test_stale_signed_in_participant_context_is_cleared_for_different_user(self):
        training_session = self._create_training_session()
        owner = self._create_verified_participant_user("owner@example.com", "Owner User")
        different_user = self._create_verified_participant_user("other@example.com", "Other User")

        with app.app_context():
            participant = Participant(
                training_session_id=training_session.id,
                user_id=owner.id,
                display_name="Owner User",
                shift_label="A Shift",
                is_anonymous=False,
            )
            db.session.add(participant)
            db.session.commit()
            participant_id = participant.id

        with self.client.session_transaction() as flask_session:
            flask_session["user_id"] = different_user.id
            flask_session[app_module.ACTIVE_TRAINING_SESSION_ID_KEY] = training_session.id
            flask_session[app_module.ACTIVE_PARTICIPANT_ID_KEY] = participant_id
            flask_session[app_module.PARTICIPANT_JOIN_MAP_KEY] = {str(training_session.id): participant_id}
            flask_session[CSRF_SESSION_KEY] = self.csrf_token

        response = self.client.get("/board")

        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as flask_session:
            self.assertNotIn(app_module.ACTIVE_TRAINING_SESSION_ID_KEY, flask_session)
            self.assertNotIn(app_module.ACTIVE_PARTICIPANT_ID_KEY, flask_session)
            self.assertNotIn(app_module.PARTICIPANT_JOIN_MAP_KEY, flask_session)

    def test_board_clears_stale_host_workspace_for_inactive_session(self):
        self._login_as_instructor()
        training_session = self._create_training_session()
        with app.app_context():
            stored_session = TrainingSession.query.filter_by(id=training_session.id).first()
            stored_session.status = "archived"
            db.session.commit()

        with self.client.session_transaction() as flask_session:
            flask_session[app_module.HOST_TRAINING_SESSION_ID_KEY] = training_session.id
            flask_session[CSRF_SESSION_KEY] = self.csrf_token

        response = self.client.get("/board")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Live Session", response.data)
        with self.client.session_transaction() as flask_session:
            self.assertNotIn(app_module.HOST_TRAINING_SESSION_ID_KEY, flask_session)

    def test_signed_in_participant_rejoin_reuses_existing_participant_record(self):
        training_session = self._create_training_session()
        participant_user = self._create_verified_participant_user("rejoin@example.com", "Rejoin User")

        first_client, first_csrf = self._build_participant_client("rejoin-first-csrf")
        with first_client.session_transaction() as flask_session:
            flask_session["user_id"] = participant_user.id
            flask_session[CSRF_SESSION_KEY] = first_csrf

        join_response = first_client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": first_csrf,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Rejoin User",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        second_client, second_csrf = self._build_participant_client("rejoin-second-csrf")
        with second_client.session_transaction() as flask_session:
            flask_session["user_id"] = participant_user.id
            flask_session[CSRF_SESSION_KEY] = second_csrf

        second_join_response = second_client.get(
            f"/join/{training_session.join_code}",
            follow_redirects=False,
        )
        self.assertEqual(second_join_response.status_code, 302)
        self.assertEqual(second_join_response.headers["Location"], "/board")

        submit_response = self._submit_answers_for_session(
            second_client,
            second_csrf,
            training_session,
            "Rejoin user",
        )
        self.assertEqual(submit_response.status_code, 200)

        with app.app_context():
            participants = Participant.query.filter_by(training_session_id=training_session.id).all()
            submissions = Submission.query.filter_by(training_session_id=training_session.id).all()
            self.assertEqual(len(participants), 1)
            self.assertEqual(participants[0].user_id, participant_user.id)
            self.assertEqual(len(submissions), 1)
            self.assertEqual(submissions[0].participant_id, participants[0].id)

    def test_join_route_switches_active_participant_between_multiple_sessions(self):
        first_session = self._create_training_session()
        second_session = self._create_training_session()

        self.assertEqual(
            self.client.post(
                f"/join/{first_session.join_code}",
                data={
                    "csrf_token": self.csrf_token,
                    "shift_label": "A Shift",
                    "identity_mode": "anonymous",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        with self.client.session_transaction() as flask_session:
            self.assertEqual(
                flask_session[app_module.ACTIVE_TRAINING_SESSION_ID_KEY],
                first_session.id,
            )

        self.assertEqual(
            self.client.post(
                f"/join/{second_session.join_code}",
                data={
                    "csrf_token": self.csrf_token,
                    "shift_label": "B Shift",
                    "identity_mode": "anonymous",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        with self.client.session_transaction() as flask_session:
            self.assertEqual(
                flask_session[app_module.ACTIVE_TRAINING_SESSION_ID_KEY],
                second_session.id,
            )

        return_response = self.client.get(
            f"/join/{first_session.join_code}",
            follow_redirects=False,
        )
        self.assertEqual(return_response.status_code, 302)
        self.assertEqual(return_response.headers["Location"], "/board")
        with self.client.session_transaction() as flask_session:
            self.assertEqual(
                flask_session[app_module.ACTIVE_TRAINING_SESSION_ID_KEY],
                first_session.id,
            )

    def test_join_page_does_not_auto_redirect_back_into_inactive_session(self):
        training_session = self._create_training_session()

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "C Shift",
                "identity_mode": "anonymous",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        with app.app_context():
            stored_session = TrainingSession.query.filter_by(id=training_session.id).first()
            stored_session.status = "archived"
            db.session.commit()

        archived_response = self.client.get(f"/join/{training_session.join_code}")
        self.assertEqual(archived_response.status_code, 200)
        self.assertIn(b"Join Session", archived_response.data)
        self.assertIn(b"Archived", archived_response.data)

    def test_inactive_session_board_locks_submissions_and_stops_reveal_refresh(self):
        training_session = self._create_training_session()

        self.assertEqual(
            self.client.post(
                f"/join/{training_session.join_code}",
                data={
                    "csrf_token": self.csrf_token,
                    "shift_label": "D Shift",
                    "identity_mode": "anonymous",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )

        with app.app_context():
            stored_session = TrainingSession.query.filter_by(id=training_session.id).first()
            stored_session.status = "archived"
            db.session.commit()

        board_response = self.client.get("/board")

        self.assertEqual(board_response.status_code, 200)
        self.assertIn(b"This session is no longer active. New submissions are locked.", board_response.data)
        self.assertIn(b'data-refresh-enabled="false"', board_response.data)

    def test_guest_cannot_access_staff_review_or_report_routes(self):
        training_session = self._create_training_session()

        review_get_response = self.client.get(f"/sessions/{training_session.id}")
        reports_response = self.client.get("/reports")

        self.assertEqual(review_get_response.status_code, 403)
        self.assertEqual(reports_response.status_code, 403)

    def test_signed_in_participant_cannot_access_staff_review_or_admin_routes(self):
        participant_user = self._create_verified_participant_user("viewer@example.com", "Viewer Participant")
        self._login_as_user(participant_user.email)
        training_session = self._create_training_session()

        dashboard_response = self.client.get(f"/sessions/{training_session.id}")
        reports_response = self.client.get("/reports")
        users_response = self.client.get("/admin/users")

        self.assertEqual(dashboard_response.status_code, 403)
        self.assertEqual(reports_response.status_code, 403)
        self.assertEqual(users_response.status_code, 403)

    def test_admin_users_page_shows_overview_checklist_and_recent_activity(self):
        admin_client, admin_csrf = self._build_staff_client("admin@demo.local", "admin-overview-csrf")

        response = admin_client.get("/admin/users")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Pre-Pilot Checklist", response.data)
        self.assertIn(b"Attention Needed", response.data)
        self.assertIn(b"Recent Admin Activity", response.data)
        self.assertIn(b"Secret key changed from default", response.data)
        self.assertIn(b"Debug mode disabled", response.data)

    def test_admin_user_actions_write_admin_audit_logs(self):
        admin_client, admin_csrf = self._build_staff_client("admin@demo.local", "admin-audit-csrf")

        create_response = admin_client.post(
            "/admin/users/create",
            data={
                "csrf_token": admin_csrf,
                "email": "ops@example.com",
                "full_name": "Ops User",
                "password": "OpsPassword123",
                "roles": [app_module.ROLE_PARTICIPANT],
                "is_active": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(create_response.status_code, 200)

        with app.app_context():
            created_user = User.query.filter_by(email="ops@example.com").first()
            self.assertIsNotNone(created_user)
            created_user_id = created_user.id

        update_response = admin_client.post(
            f"/admin/users/{created_user_id}/update",
            data={
                "csrf_token": admin_csrf,
                "full_name": "Ops User Updated",
                "roles": [app_module.ROLE_PARTICIPANT, app_module.ROLE_INSTRUCTOR],
                "is_active": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(update_response.status_code, 302)

        reset_response = admin_client.post(
            f"/admin/users/{created_user_id}/reset-password",
            data={
                "csrf_token": admin_csrf,
                "new_password": "EvenBetterPass123",
            },
            follow_redirects=False,
        )
        self.assertEqual(reset_response.status_code, 302)

        with app.app_context():
            audit_actions = [
                row.action
                for row in AdminAuditLog.query.order_by(AdminAuditLog.created_at.asc(), AdminAuditLog.id.asc()).all()
            ]

        self.assertIn("create_user", audit_actions)
        self.assertIn("update_user", audit_actions)
        self.assertIn("reset_user_password", audit_actions)

    def test_scenario_moderation_writes_admin_audit_logs(self):
        chief_client, chief_csrf = self._build_chief_client()
        with app.app_context():
            scenario = Scenario(
                title="Audit Scenario",
                dispatch_text="Audit dispatch",
                base_image_path="images/house1.jpg",
                overlay_image_path=None,
                status=app_module.SCENARIO_STATUS_APPROVED,
                is_official=False,
                is_active=True,
            )
            db.session.add(scenario)
            db.session.commit()
            db.session.refresh(scenario)
            scenario_id = scenario.id

        official_response = chief_client.post(
            "/scenario/official",
            data={
                "csrf_token": chief_csrf,
                "scenario_id": str(scenario_id),
                "official_action": "make",
                "next": "/scenarios?tab=official",
            },
            follow_redirects=False,
        )
        self.assertEqual(official_response.status_code, 302)

        archive_response = chief_client.post(
            "/scenario/archive",
            data={
                "csrf_token": chief_csrf,
                "scenario_id": str(scenario_id),
            },
            follow_redirects=False,
        )
        self.assertEqual(archive_response.status_code, 302)

        with app.app_context():
            audit_actions = [
                row.action
                for row in AdminAuditLog.query.filter_by(target_id=scenario_id)
                .order_by(AdminAuditLog.created_at.asc(), AdminAuditLog.id.asc())
                .all()
            ]

        self.assertIn("make_scenario_official", audit_actions)
        self.assertIn("archive_scenario", audit_actions)

    def test_session_creation_redirects_to_board_host_workspace(self):
        self._login_as_instructor()
        with app.app_context():
            scenario = (
                Scenario.query.filter(
                    Scenario.is_active.is_(True),
                    Scenario.status == app_module.SCENARIO_STATUS_APPROVED,
                )
                .order_by(Scenario.id.asc())
                .first()
            )

        response = self.client.post(
            "/sessions/new",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(scenario.id),
                "title": "Host Workspace Test",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/board?session_id=", response.headers["Location"])

        board_response = self.client.get(response.headers["Location"])
        self.assertEqual(board_response.status_code, 200)
        self.assertIn(b"Live Session", board_response.data)
        self.assertIn(b"Question Review", board_response.data)
        self.assertIn(b"Join Code", board_response.data)

    def test_scenario_like_requires_completed_signed_in_submission(self):
        self._login_as_instructor()
        with app.app_context():
            scenario = (
                Scenario.query.filter(
                    Scenario.is_active.is_(True),
                    Scenario.status == app_module.SCENARIO_STATUS_APPROVED,
                )
                .order_by(Scenario.id.asc())
                .first()
            )

        response = self.client.post(
            "/scenarios/vote",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(scenario.id),
                "vote_action": "like",
                "next": "/training/fireground",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Only users with a completed signed-in submission for this scenario can like it.",
            response.data,
        )
        with app.app_context():
            refreshed_scenario = Scenario.query.filter_by(id=scenario.id).first()
            self.assertEqual(refreshed_scenario.like_count, 0)
            self.assertEqual(ScenarioLike.query.count(), 0)

    def test_completed_submission_unlocks_scenario_like_and_vote_changes(self):
        self._login_as_instructor()
        training_session = self._create_training_session()

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Eligible Liker",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        submit_response = self._submit_answers_for_session(
            self.client,
            self.csrf_token,
            training_session,
            "Like unlock",
        )
        self.assertEqual(submit_response.status_code, 200)
        self.assertIn(b"Attempt #1 submitted.", submit_response.data)

        like_response = self.client.post(
            "/scenarios/vote",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "vote_action": "like",
                "next": "/training/fireground",
            },
            follow_redirects=False,
        )
        self.assertEqual(like_response.status_code, 302)

        with app.app_context():
            saved_vote = ScenarioLike.query.filter_by(scenario_id=training_session.scenario_id).first()
            refreshed_scenario = Scenario.query.filter_by(id=training_session.scenario_id).first()
            self.assertIsNotNone(saved_vote)
            self.assertTrue(saved_vote.is_liked)
            self.assertEqual(refreshed_scenario.like_count, 1)

        clear_response = self.client.post(
            "/scenarios/vote",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "vote_action": "clear",
                "next": "/training/fireground",
            },
            follow_redirects=False,
        )
        self.assertEqual(clear_response.status_code, 302)

        with app.app_context():
            saved_vote = ScenarioLike.query.filter_by(scenario_id=training_session.scenario_id).first()
            refreshed_scenario = Scenario.query.filter_by(id=training_session.scenario_id).first()
            self.assertIsNotNone(saved_vote)
            self.assertFalse(saved_vote.is_liked)
            self.assertEqual(refreshed_scenario.like_count, 0)

        second_like_response = self.client.post(
            "/scenarios/vote",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "vote_action": "like",
                "next": "/training/fireground",
            },
            follow_redirects=False,
        )
        self.assertEqual(second_like_response.status_code, 302)

        with app.app_context():
            saved_vote = ScenarioLike.query.filter_by(scenario_id=training_session.scenario_id).first()
            refreshed_scenario = Scenario.query.filter_by(id=training_session.scenario_id).first()
            self.assertTrue(saved_vote.is_liked)
            self.assertEqual(refreshed_scenario.like_count, 1)

    def test_fireground_category_surfaces_most_liked_scenarios(self):
        with app.app_context():
            scenario = (
                Scenario.query.filter(
                    Scenario.is_active.is_(True),
                    Scenario.status == app_module.SCENARIO_STATUS_APPROVED,
                )
                .order_by(Scenario.id.asc())
                .first()
            )
            scenario_title = scenario.title
            scenario.like_count = 2
            db.session.commit()

        response = self.client.get("/training/fireground")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Most Liked Right Now", response.data)
        self.assertIn(scenario_title.encode("utf-8"), response.data)
        self.assertIn(b"2 likes", response.data)

    def test_submit_requires_joined_participant_context(self):
        with app.app_context():
            scenario = (
                Scenario.query.filter(Scenario.is_active.is_(True))
                .order_by(Scenario.id.asc())
                .first()
            )
            questions = self._active_questions_for_scenario(scenario.id)

        payload = {"csrf_token": self.csrf_token}
        for question in questions:
            payload[f"q_{question['id']}"] = "Fallback answer"

        response = self.client.post("/submit", data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Practice Submit", response.data)
        with app.app_context():
            self.assertEqual(Submission.query.count(), 0)
            self.assertEqual(SubmissionAnswer.query.count(), 0)

    def test_create_scenario_rejects_invalid_base_image_path(self):
        self._login_as_instructor()

        response = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf_token,
                "title": "Bad Asset Scenario",
                "dispatch": "Test dispatch",
                "base_image_path": "../outside.jpg",
                "overlay_image_path": "",
                "question_prompt": ["What do you see?"],
                "question_type": [app_module.DEFAULT_QUESTION_TYPE],
                "instructor_answer": [""],
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            b"Base image path must point to an existing file inside the static folder.",
            response.data,
        )

    def test_join_validation_highlights_missing_named_display_name(self):
        training_session = self._create_training_session()

        response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Display name is required when joining with name.", response.data)
        self.assertIn(b"Host sees:", response.data)
        self.assertIn(b"Enter the name the host should see.", response.data)

    def test_submit_persists_submission_answers_and_attempt_numbers(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Smoke Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        first_payload = {"csrf_token": self.csrf_token}
        second_payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            first_payload[f"q_{question['id']}"] = f"Attempt one answer {index}"
            second_payload[f"q_{question['id']}"] = f"Attempt two answer {index}"

        first_response = self.client.post("/submit", data=first_payload)
        second_response = self.client.post("/submit", data=second_payload)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertIn(b"Attempt #1 submitted.", first_response.data)
        self.assertIn(b"Attempt #2 submitted.", second_response.data)

        with app.app_context():
            participant = Participant.query.filter_by(training_session_id=training_session.id).first()
            submissions = (
                Submission.query.filter_by(participant_id=participant.id)
                .order_by(Submission.attempt_number.asc())
                .all()
            )

            self.assertEqual(len(submissions), 2)
            self.assertEqual(submissions[0].attempt_number, 1)
            self.assertEqual(submissions[1].attempt_number, 2)
            self.assertTrue(
                all(
                    submission.training_session_id == training_session.id
                    and submission.scenario_id == training_session.scenario_id
                    for submission in submissions
                )
            )

            saved_answers = (
                SubmissionAnswer.query.join(Submission)
                .filter(Submission.participant_id == participant.id)
                .order_by(Submission.attempt_number.asc(), SubmissionAnswer.question_id.asc())
                .all()
            )

            self.assertEqual(len(saved_answers), len(questions) * 2)
            first_attempt_answers = [
                answer for answer in saved_answers if answer.submission.attempt_number == 1
            ]
            second_attempt_answers = [
                answer for answer in saved_answers if answer.submission.attempt_number == 2
            ]
            self.assertEqual(
                [answer.answer_text for answer in first_attempt_answers],
                [f"Attempt one answer {index}" for index in range(1, len(questions) + 1)],
            )
            self.assertEqual(
                [answer.answer_text for answer in second_attempt_answers],
                [f"Attempt two answer {index}" for index in range(1, len(questions) + 1)],
            )

    def test_session_dashboard_shows_live_submission_data(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "B Shift",
                "identity_mode": "anonymous",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"Dashboard answer {index}"
        submit_response = self.client.post("/submit", data=payload)
        self.assertEqual(submit_response.status_code, 200)

        self._login_as_instructor()

        detail_response = self.client.get(f"/sessions/{training_session.id}")
        partial_response = self.client.get(f"/sessions/{training_session.id}/submissions")

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(partial_response.status_code, 200)
        self.assertIn(b"Live Session Dashboard", detail_response.data)
        self.assertIn(b"Participants Joined", detail_response.data)
        self.assertIn(b"Participants Submitted", partial_response.data)
        self.assertIn(b"Anonymous", partial_response.data)
        self.assertIn(b"Attempt #1", partial_response.data)
        expected_saved_answers = f"Saved answers: {len(questions)}/{len(questions)} active questions".encode()
        self.assertIn(expected_saved_answers, partial_response.data)
        self.assertIn(b"View Answers", partial_response.data)

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()

        detail_response = self.client.get(
            f"/sessions/{training_session.id}/submissions/{submission.id}"
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn(b"Submission Detail", detail_response.data)
        self.assertIn(b"Dashboard answer 1", detail_response.data)

    def test_host_can_reveal_per_question_answers_from_different_participants(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "C Shift",
                "identity_mode": "named",
                "display_name": "Reveal Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Reveal alpha").status_code,
            200,
        )

        second_client, second_csrf = self._build_participant_client("reveal-second-csrf")
        second_join_response = second_client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": second_csrf,
                "shift_label": "D Shift",
                "identity_mode": "named",
                "display_name": "Reveal Backup",
            },
            follow_redirects=False,
        )
        self.assertEqual(second_join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(second_client, second_csrf, training_session, "Reveal bravo").status_code,
            200,
        )

        with app.app_context():
            first_submission = (
                Submission.query.filter_by(training_session_id=training_session.id)
                .join(Participant)
                .filter(Participant.display_name == "Reveal Tester")
                .first()
            )
            second_submission = (
                Submission.query.filter_by(training_session_id=training_session.id)
                .join(Participant)
                .filter(Participant.display_name == "Reveal Backup")
                .first()
            )
            first_answer = next(
                answer for answer in first_submission.answers if answer.question_id == questions[0]["id"]
            )
            second_answer = next(
                answer for answer in second_submission.answers if answer.question_id == questions[1]["id"]
            )

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        board_response = instructor_client.get(f"/board?session_id={training_session.id}")
        self.assertEqual(board_response.status_code, 200)
        self.assertIn(b"Question Review", board_response.data)

        reveal_first_response = instructor_client.post(
            f"/sessions/{training_session.id}/questions/{questions[0]['id']}/reveal",
            data={
                "csrf_token": instructor_csrf_token,
                "submission_answer_id": str(first_answer.id),
                "next": f"/board?session_id={training_session.id}",
            },
            follow_redirects=False,
        )
        self.assertEqual(reveal_first_response.status_code, 302)

        reveal_second_response = instructor_client.post(
            f"/sessions/{training_session.id}/questions/{questions[1]['id']}/reveal",
            data={
                "csrf_token": instructor_csrf_token,
                "submission_answer_id": str(second_answer.id),
                "next": f"/board?session_id={training_session.id}",
            },
            follow_redirects=False,
        )
        self.assertEqual(reveal_second_response.status_code, 302)

        participant_board_response = self.client.get("/board")
        revealed_partial_response = self.client.get(
            f"/sessions/{training_session.id}/revealed-answer"
        )

        self.assertEqual(participant_board_response.status_code, 200)
        self.assertEqual(revealed_partial_response.status_code, 200)
        self.assertIn(b"answer revealed", participant_board_response.data)
        self.assertIn(b"Reveal Tester", participant_board_response.data)  # own identity in session strip
        self.assertIn(b"Reveal alpha answer 1", participant_board_response.data)
        self.assertIn(b"Reveal bravo answer 2", participant_board_response.data)
        self.assertIn(b"answer revealed", revealed_partial_response.data)

        clear_response = instructor_client.post(
            f"/sessions/{training_session.id}/revealed-answers/clear",
            data={
                "csrf_token": instructor_csrf_token,
                "next": f"/board?session_id={training_session.id}",
            },
            follow_redirects=False,
        )
        self.assertEqual(clear_response.status_code, 302)

        cleared_partial_response = self.client.get(
            f"/sessions/{training_session.id}/revealed-answer"
        )
        self.assertIn(b"No answers have been revealed for this session yet.", cleared_partial_response.data)

    def test_reveal_route_rejects_cross_session_answer(self):
        first_session = self._create_training_session()
        second_session = self._create_training_session()
        questions = self._active_questions_for_scenario(first_session.scenario_id)

        first_client, first_csrf = self._build_participant_client("cross-session-first")
        self.assertEqual(
            first_client.post(
                f"/join/{first_session.join_code}",
                data={
                    "csrf_token": first_csrf,
                    "shift_label": "A Shift",
                    "identity_mode": "named",
                    "display_name": "First Session User",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        self.assertEqual(
            self._submit_answers_for_session(first_client, first_csrf, first_session, "First session").status_code,
            200,
        )

        second_client, second_csrf = self._build_participant_client("cross-session-second")
        self.assertEqual(
            second_client.post(
                f"/join/{second_session.join_code}",
                data={
                    "csrf_token": second_csrf,
                    "shift_label": "B Shift",
                    "identity_mode": "named",
                    "display_name": "Second Session User",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        self.assertEqual(
            self._submit_answers_for_session(second_client, second_csrf, second_session, "Second session").status_code,
            200,
        )

        with app.app_context():
            second_submission = Submission.query.filter_by(training_session_id=second_session.id).first()
            second_answer = next(
                answer for answer in second_submission.answers if answer.question_id == questions[0]["id"]
            )

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        reveal_response = instructor_client.post(
            f"/sessions/{first_session.id}/questions/{questions[0]['id']}/reveal",
            data={
                "csrf_token": instructor_csrf_token,
                "submission_answer_id": str(second_answer.id),
                "next": f"/board?session_id={first_session.id}",
            },
            follow_redirects=False,
        )

        self.assertEqual(reveal_response.status_code, 409)

    def test_reveal_route_rejects_answer_for_wrong_question(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "C Shift",
                "identity_mode": "named",
                "display_name": "Mismatch Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Mismatch").status_code,
            200,
        )

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()
            wrong_answer = next(
                answer for answer in submission.answers if answer.question_id == questions[1]["id"]
            )

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        reveal_response = instructor_client.post(
            f"/sessions/{training_session.id}/questions/{questions[0]['id']}/reveal",
            data={
                "csrf_token": instructor_csrf_token,
                "submission_answer_id": str(wrong_answer.id),
                "next": f"/board?session_id={training_session.id}",
            },
            follow_redirects=False,
        )

        self.assertEqual(reveal_response.status_code, 409)

    def test_board_workspace_uses_stable_anonymous_labels(self):
        training_session = self._create_training_session()

        first_client, first_csrf = self._build_participant_client("anon-first-csrf")
        second_client, second_csrf = self._build_participant_client("anon-second-csrf")

        self.assertEqual(
            first_client.post(
                f"/join/{training_session.join_code}",
                data={
                    "csrf_token": first_csrf,
                    "shift_label": "A Shift",
                    "identity_mode": "anonymous",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        self.assertEqual(
            second_client.post(
                f"/join/{training_session.join_code}",
                data={
                    "csrf_token": second_csrf,
                    "shift_label": "B Shift",
                    "identity_mode": "anonymous",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )

        self.assertEqual(
            self._submit_answers_for_session(first_client, first_csrf, training_session, "Anon first").status_code,
            200,
        )
        self.assertEqual(
            self._submit_answers_for_session(second_client, second_csrf, training_session, "Anon second").status_code,
            200,
        )

        instructor_client, _instructor_csrf = self._build_instructor_client()
        board_response = instructor_client.get(f"/board?session_id={training_session.id}")

        self.assertEqual(board_response.status_code, 200)
        self.assertIn(b"Anonymous Person 1", board_response.data)
        self.assertIn(b"Anonymous Person 2", board_response.data)

    def test_instructor_can_save_review_notes_for_submission(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "D Shift",
                "identity_mode": "named",
                "display_name": "Notes Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"Notes answer {index}"
        self.assertEqual(self.client.post("/submit", data=payload).status_code, 200)

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        save_response = instructor_client.post(
            f"/sessions/{training_session.id}/submissions/{submission.id}/review",
            data={
                "csrf_token": instructor_csrf_token,
                "review_notes": "Good situational awareness shown here.",
                "review_action": "save_notes",
            },
            follow_redirects=False,
        )
        self.assertEqual(save_response.status_code, 302)

        detail_response = instructor_client.get(
            f"/sessions/{training_session.id}/submissions/{submission.id}"
        )
        self.assertIn(b"Good situational awareness shown here.", detail_response.data)

    def test_board_review_surfaces_saved_notes_without_leaving_workspace(self):
        training_session = self._create_training_session()

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "B Shift",
                "identity_mode": "named",
                "display_name": "Board Notes Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Board notes").status_code,
            200,
        )

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        save_notes_response = instructor_client.post(
            f"/sessions/{training_session.id}/submissions/{submission.id}/review",
            data={
                "csrf_token": instructor_csrf_token,
                "review_notes": "Host note from the board workspace.",
                "review_action": "save_notes",
                "next": f"/board?session_id={training_session.id}",
            },
            follow_redirects=False,
        )
        self.assertEqual(save_notes_response.status_code, 302)

        board_response = instructor_client.get(f"/board?session_id={training_session.id}")
        self.assertEqual(board_response.status_code, 200)
        self.assertIn(b"Latest Notes", board_response.data)
        self.assertIn(b"Host note from the board workspace.", board_response.data)
        self.assertIn(b"Save Notes", board_response.data)

    def test_board_workspace_exposes_filters_and_question_jump_links(self):
        training_session = self._create_training_session()

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "C Shift",
                "identity_mode": "named",
                "display_name": "Filter Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Filter board").status_code,
            200,
        )

        instructor_client, _instructor_csrf_token = self._build_instructor_client()
        board_response = instructor_client.get(f"/board?session_id={training_session.id}")

        self.assertEqual(board_response.status_code, 200)
        self.assertIn(b"Show Only", board_response.data)
        self.assertIn(b"Live Reveals", board_response.data)
        self.assertIn(b"Jump To Question", board_response.data)
        self.assertIn(b"Expand All Questions", board_response.data)
        self.assertIn(b"Q1", board_response.data)
        self.assertIn(b'data-review-filter="revealed"', board_response.data)
        self.assertIn(b'data-question-group="true"', board_response.data)

    def test_excluding_submission_clears_reveal_and_writes_audit_log(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Exclude Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"Exclude answer {index}"
        submit_response = self.client.post("/submit", data=payload)
        self.assertEqual(submit_response.status_code, 200)

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()
            first_answer = next(
                answer for answer in submission.answers if answer.question_id == questions[0]["id"]
            )

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        reveal_response = instructor_client.post(
            f"/sessions/{training_session.id}/questions/{questions[0]['id']}/reveal",
            data={
                "csrf_token": instructor_csrf_token,
                "submission_answer_id": str(first_answer.id),
                "next": f"/board?session_id={training_session.id}",
            },
            follow_redirects=False,
        )
        self.assertEqual(reveal_response.status_code, 302)

        exclude_response = instructor_client.post(
            f"/sessions/{training_session.id}/submissions/{submission.id}/review",
            data={
                "csrf_token": instructor_csrf_token,
                "review_notes": "Excluded due to off-topic response.",
                "review_action": "exclude",
            },
            follow_redirects=False,
        )
        self.assertEqual(exclude_response.status_code, 302)

        revealed_partial_response = self.client.get(
            f"/sessions/{training_session.id}/revealed-answer"
        )
        dashboard_response = instructor_client.get(f"/sessions/{training_session.id}/submissions")

        self.assertIn(b"No answers have been revealed for this session yet.", revealed_partial_response.data)
        self.assertIn(b"Excluded", dashboard_response.data)

        with app.app_context():
            refreshed_session = TrainingSession.query.filter_by(id=training_session.id).first()
            refreshed_submission = Submission.query.filter_by(id=submission.id).first()
            audit_actions = [
                row.action for row in SubmissionAuditLog.query.filter_by(submission_id=submission.id).all()
            ]

            self.assertEqual(refreshed_submission.status, app_module.SUBMISSION_STATUS_EXCLUDED)
            self.assertIn("exclude", audit_actions)

    def test_invalid_review_transition_does_not_change_submission(self):
        training_session = self._create_training_session()

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Transition Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Transition").status_code,
            200,
        )

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()
            original_status = submission.status
            original_notes = submission.notes

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        invalid_response = instructor_client.post(
            f"/sessions/{training_session.id}/submissions/{submission.id}/review",
            data={
                "csrf_token": instructor_csrf_token,
                "review_notes": "This should not stick.",
                "review_action": "not-a-real-action",
            },
            follow_redirects=False,
        )

        self.assertEqual(invalid_response.status_code, 400)
        with app.app_context():
            refreshed_submission = Submission.query.filter_by(id=submission.id).first()
            self.assertEqual(refreshed_submission.status, original_status)
            self.assertEqual(refreshed_submission.notes, original_notes)

    def test_exclude_clears_reveal_and_reinstate_restores_active_state(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Reinstate Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)
        self.assertEqual(
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Reinstate").status_code,
            200,
        )

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()
            first_answer = next(
                answer for answer in submission.answers if answer.question_id == questions[0]["id"]
            )

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        chief_client, _chief_csrf = self._build_chief_client()

        self.assertEqual(
            instructor_client.post(
                f"/sessions/{training_session.id}/questions/{questions[0]['id']}/reveal",
                data={
                    "csrf_token": instructor_csrf_token,
                    "submission_answer_id": str(first_answer.id),
                    "next": f"/board?session_id={training_session.id}",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        self.assertEqual(
            instructor_client.post(
                f"/sessions/{training_session.id}/submissions/{submission.id}/review",
                data={
                    "csrf_token": instructor_csrf_token,
                    "review_notes": "Exclude for now.",
                    "review_action": "exclude",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        self.assertEqual(
            instructor_client.post(
                f"/sessions/{training_session.id}/submissions/{submission.id}/review",
                data={
                    "csrf_token": instructor_csrf_token,
                    "review_notes": "Reinstated for another look.",
                    "review_action": "reinstate",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )

        revealed_partial_response = self.client.get(
            f"/sessions/{training_session.id}/revealed-answer"
        )
        self.assertIn(b"No answers have been revealed for this session yet.", revealed_partial_response.data)

        with app.app_context():
            refreshed = Submission.query.get(submission.id)
            self.assertEqual(refreshed.status, app_module.SUBMISSION_STATUS_SUBMITTED)

    def test_participant_board_and_reveal_panel_show_attempt_guidance(self):
        training_session = self._create_training_session()

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Guidance Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        board_before_submit = self.client.get("/board")
        self.assertEqual(board_before_submit.status_code, 200)
        self.assertIn(b"Guidance Tester", board_before_submit.data)
        self.assertIn(b"Submitting as #1", board_before_submit.data)
        self.assertIn(b"participant-question-", board_before_submit.data)
        self.assertIn(b"A Shift", board_before_submit.data)
        self.assertIn(
            b"The host board is now using your latest answers.",
            self._submit_answers_for_session(self.client, self.csrf_token, training_session, "Participant guidance").data,
        )

        revealed_partial_response = self.client.get(
            f"/sessions/{training_session.id}/revealed-answer"
        )
        self.assertEqual(revealed_partial_response.status_code, 200)
        self.assertIn(b"Keep this panel open during the session.", revealed_partial_response.data)

    def test_session_report_includes_only_approved_non_excluded_submissions(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        first_join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "A Shift",
                "identity_mode": "named",
                "display_name": "Report Approved",
            },
            follow_redirects=False,
        )
        self.assertEqual(first_join_response.status_code, 302)

        first_payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            first_payload[f"q_{question['id']}"] = f"Approved report answer {index}"
        self.assertEqual(self.client.post("/submit", data=first_payload).status_code, 200)

        second_client, second_csrf = self._build_participant_client("participant-two-csrf")
        second_join_response = second_client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": second_csrf,
                "shift_label": "B Shift",
                "identity_mode": "named",
                "display_name": "Report Excluded",
            },
            follow_redirects=False,
        )
        self.assertEqual(second_join_response.status_code, 302)

        second_payload = {"csrf_token": second_csrf}
        for index, question in enumerate(questions, start=1):
            second_payload[f"q_{question['id']}"] = f"Excluded report answer {index}"
        self.assertEqual(second_client.post("/submit", data=second_payload).status_code, 200)

        with app.app_context():
            approved_submission = (
                Submission.query.filter_by(training_session_id=training_session.id)
                .join(Participant)
                .filter(Participant.display_name == "Report Approved")
                .first()
            )
            excluded_submission = (
                Submission.query.filter_by(training_session_id=training_session.id)
                .join(Participant)
                .filter(Participant.display_name == "Report Excluded")
                .first()
            )

        chief_client, chief_csrf = self._build_staff_client("chief@demo.local", "chief-csrf-token")
        self.assertEqual(
            chief_client.post(
                f"/sessions/{training_session.id}/submissions/{approved_submission.id}/review",
                data={
                    "csrf_token": chief_csrf,
                    "review_notes": "Approved for after-action review.",
                    "review_action": "approve",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )
        self.assertEqual(
            chief_client.post(
                f"/sessions/{training_session.id}/submissions/{excluded_submission.id}/review",
                data={
                    "csrf_token": chief_csrf,
                    "review_notes": "Excluded from reporting.",
                    "review_action": "exclude",
                },
                follow_redirects=False,
            ).status_code,
            302,
        )

        reports_response = chief_client.get("/reports")
        session_report_response = chief_client.get(f"/reports/sessions/{training_session.id}")

        self.assertEqual(reports_response.status_code, 200)
        self.assertEqual(session_report_response.status_code, 200)
        self.assertIn(b"Open Report", reports_response.data)
        self.assertIn(b"Approved report answer 1", session_report_response.data)
        self.assertIn(b"Approved For Reporting", session_report_response.data)
        self.assertIn(b"Excluded", session_report_response.data)
        self.assertNotIn(b"Excluded report answer 1", session_report_response.data)

        export_response = chief_client.get(f"/reports/sessions/{training_session.id}/export.csv")
        self.assertEqual(export_response.status_code, 200)
        self.assertIn("text/csv", export_response.headers.get("Content-Type", ""))
        self.assertIn(
            f'session_report_{training_session.id}.csv',
            export_response.headers.get("Content-Disposition", ""),
        )
        self.assertIn(b"Approved report answer 1", export_response.data)
        self.assertIn(b"Approved for after-action review.", export_response.data)
        self.assertNotIn(b"Excluded report answer 1", export_response.data)

        shift_export_response = chief_client.get(
            f"/reports/sessions/{training_session.id}/export.csv?shift=A+Shift"
        )
        self.assertEqual(shift_export_response.status_code, 200)
        self.assertIn(
            f'session_report_{training_session.id}_A_Shift.csv',
            shift_export_response.headers.get("Content-Disposition", ""),
        )
        self.assertIn(b"Approved report answer 1", shift_export_response.data)
        self.assertNotIn(b"Excluded report answer 1", shift_export_response.data)
        self.assertNotIn(b",B Shift,", shift_export_response.data)


    def test_logged_in_user_solo_submit_saves_drill_attempt(self):
        from models import DrillAttempt, DrillAttemptAnswer
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        self._login_as_instructor()
        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"Drill answer {index}"

        response = self.client.post("/submit", data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Drill attempt #1 saved.", response.data)

        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            drill = DrillAttempt.query.filter_by(
                user_id=user.id, scenario_id=training_session.scenario_id
            ).first()
            self.assertIsNotNone(drill)
            self.assertEqual(drill.attempt_number, 1)
            self.assertEqual(drill.status, "submitted")
            self.assertEqual(len(drill.answers), len(questions))

    def test_drill_attempt_number_increments_on_retry(self):
        from models import DrillAttempt
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        self._login_as_instructor()
        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"First attempt {index}"

        self.client.post("/submit", data=payload)

        payload2 = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload2[f"q_{question['id']}"] = f"Second attempt {index}"

        response2 = self.client.post("/submit", data=payload2)
        self.assertIn(b"Drill attempt #2 saved.", response2.data)

        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            attempts = DrillAttempt.query.filter_by(
                user_id=user.id, scenario_id=training_session.scenario_id
            ).order_by(DrillAttempt.attempt_number).all()
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0].attempt_number, 1)
            self.assertEqual(attempts[1].attempt_number, 2)

    def test_drill_submit_shows_instructor_answers(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        self._login_as_instructor()
        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id

        with app.app_context():
            from models import Question
            q_with_answer = Question.query.filter(
                Question.scenario_id == training_session.scenario_id,
                Question.instructor_answer.isnot(None),
                Question.is_active.is_(True),
            ).first()

        if q_with_answer is None:
            self.skipTest("No questions with instructor answers in seed data")

        payload = {"csrf_token": self.csrf_token}
        for question in questions:
            payload[f"q_{question['id']}"] = "test answer"

        response = self.client.post("/submit", data=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Instructor Answer", response.data)

    # ── Issue #8: Multiple choice question type ───────────────────────────────

    def test_create_scenario_with_multiple_choice_question(self):
        self._login_as_instructor()
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf_token,
                "title": "MC Test Scenario",
                "dispatch": "Units respond to a fire.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["What is the best action?"],
                "question_type": ["multiple_choice"],
                "instructor_answer": [""],
                "choice_text_0": ["Attack from unburned side", "Defensive mode", "Call for backup"],
                "correct_choice_0": "0",
                "visibility": "private",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            from models import QuestionChoice
            scenario = Scenario.query.filter_by(title="MC Test Scenario").first()
            self.assertIsNotNone(scenario)
            q = scenario.questions[0]
            self.assertEqual(q.question_type, "multiple_choice")
            choices = QuestionChoice.query.filter_by(question_id=q.id).all()
            self.assertEqual(len(choices), 3)
            correct = [c for c in choices if c.is_correct]
            self.assertEqual(len(correct), 1)
            self.assertEqual(correct[0].choice_text, "Attack from unburned side")

    def test_create_mc_question_requires_at_least_2_choices(self):
        self._login_as_instructor()
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf_token,
                "title": "MC Bad Choices",
                "dispatch": "Dispatch.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["Question?"],
                "question_type": ["multiple_choice"],
                "instructor_answer": [""],
                "choice_text_0": ["Only one choice"],
                "correct_choice_0": "0",
                "visibility": "private",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 400)

    def test_mc_drill_submit_saves_selected_choice(self):
        """Submitting a MC answer as a solo drill persists selected_choice_id."""
        self._login_as_instructor()
        # Create a MC scenario
        with app.app_context():
            from models import QuestionChoice
            scenario = Scenario.query.filter_by(title="MC Test Scenario").first()
            if scenario is None:
                self.skipTest("MC Test Scenario not found — run create test first")
            q = scenario.questions[0]
            correct_choice = next(c for c in q.choices if c.is_correct)
            scenario_id = scenario.id
            question_id = q.id
            choice_id = correct_choice.id

        with self.client.session_transaction() as s:
            s["scenario_id"] = scenario_id

        resp = self.client.post(
            "/submit",
            data={
                "csrf_token": self.csrf_token,
                f"qc_{question_id}": str(choice_id),
            },
        )
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            from models import DrillAttemptAnswer
            attempt = DrillAttempt.query.filter_by(scenario_id=scenario_id).first()
            self.assertIsNotNone(attempt)
            answer = DrillAttemptAnswer.query.filter_by(
                drill_attempt_id=attempt.id, question_id=question_id
            ).first()
            self.assertIsNotNone(answer)
            self.assertEqual(answer.selected_choice_id, choice_id)

    def test_guest_solo_submit_shows_sign_in_prompt(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"Guest answer {index}"

        response = self.client.post("/submit", data=payload)
        self.assertEqual(response.status_code, 200)
        # Guests see the save banner instead of a sign-in prompt (Issue #7)
        self.assertIn(b"Save this attempt", response.data)


    # ── Issue #7: Solo drill for guests with localStorage preservation ─────────

    # ── Issue #12: Adopt for My Department ───────────────────────────────────

    def test_instructor_with_department_can_adopt_scenario(self):
        """Instructor in a department can fork a public scenario."""
        with app.app_context():
            from models import Department, User as _User, UserRole as _UserRole
            # Ensure instructor has a department
            dept = Department(name="Fork Test Dept", invite_code="FORKDEPT1")
            db.session.add(dept)
            db.session.flush()
            instructor = _User.query.filter_by(email="instructor@demo.local").first()
            instructor.department_id = dept.id
            db.session.commit()
            scenario = Scenario.query.filter_by(status="approved", is_active=True, is_public=True).first()
            self.assertIsNotNone(scenario)
            scenario_id = scenario.id
            dept_id = dept.id
            instructor_id = instructor.id

        self._login_as_instructor()
        resp = self.client.post(
            "/scenario/adopt",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(scenario_id),
                "next": "/board",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        with app.app_context():
            forked = Scenario.query.filter_by(forked_from_scenario_id=scenario_id).first()
            self.assertIsNotNone(forked)
            self.assertEqual(forked.status, "draft")
            self.assertEqual(forked.created_by_user_id, instructor_id)
            self.assertEqual(forked.department_id, dept_id)
            self.assertFalse(forked.is_official)
            # Questions should be copied
            original = Scenario.query.get(scenario_id)
            self.assertEqual(
                len([q for q in forked.questions if q.is_active]),
                len([q for q in original.questions if q.is_active]),
            )

    def test_instructor_without_department_cannot_adopt(self):
        """Instructor without a department gets a warning flash, not an error."""
        with app.app_context():
            from models import Scenario as _Scenario
            instructor = User.query.filter_by(email="instructor@demo.local").first()
            instructor.department_id = None
            db.session.commit()
            # Create a fresh scenario that hasn't been forked yet
            base_scenario = _Scenario(
                title="No-Fork Test Scenario", dispatch_text="Test dispatch.",
                base_image_path="images/house1.jpg", status="approved",
                is_active=True, is_public=True, is_official=False,
            )
            db.session.add(base_scenario)
            db.session.commit()
            scenario_id = base_scenario.id

        self._login_as_instructor()
        resp = self.client.post(
            "/scenario/adopt",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(scenario_id),
                "next": "/board",
            },
            follow_redirects=False,
        )
        # Should redirect with a flash message, not a hard error
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            forked = Scenario.query.filter_by(forked_from_scenario_id=scenario_id).first()
            self.assertIsNone(forked)

    def test_guest_solo_submit_shows_instructor_answers(self):
        """After a guest submits, instructor answers should be revealed."""
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)
        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id
        payload = {"csrf_token": self.csrf_token}
        for i, q in enumerate(questions, start=1):
            payload[f"q_{q['id']}"] = f"Guest answer {i}"
        resp = self.client.post("/submit", data=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Instructor Answer", resp.data)

    def test_save_guest_drill_auto_saves_from_localstorage(self):
        """POST /save-guest-drill persists a drill attempt from localStorage data."""
        import json
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        self._login_as_instructor()

        # Build fake localStorage payload
        answers = {str(q["id"]): f"Saved answer {i}" for i, q in enumerate(questions, start=1)}

        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id

        resp = self.client.post(
            "/save-guest-drill",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "answers_json": json.dumps(answers),
            },
        )
        self.assertEqual(resp.status_code, 200)
        with app.app_context():
            from models import User as _User
            user = _User.query.filter_by(email="instructor@demo.local").first()
            attempt = DrillAttempt.query.filter_by(
                user_id=user.id, scenario_id=training_session.scenario_id
            ).first()
            self.assertIsNotNone(attempt)

    def test_save_guest_drill_returns_409_if_already_saved(self):
        """Returns 409 if a drill attempt already exists (idempotent)."""
        import json
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)
        answers = {str(q["id"]): "answer" for q in questions}

        self._login_as_instructor()
        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id

        # First save
        self.client.post(
            "/save-guest-drill",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "answers_json": json.dumps(answers),
            },
        )
        # Second save should return 409
        resp = self.client.post(
            "/save-guest-drill",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "answers_json": json.dumps(answers),
            },
        )
        self.assertEqual(resp.status_code, 409)

    def test_save_guest_drill_requires_auth(self):
        """Guest (unauthenticated) gets 403 from the save endpoint."""
        import json
        training_session = self._create_training_session()
        with self.client.session_transaction() as s:
            s["scenario_id"] = training_session.scenario_id
        resp = self.client.post(
            "/save-guest-drill",
            data={
                "csrf_token": self.csrf_token,
                "scenario_id": str(training_session.scenario_id),
                "answers_json": json.dumps({}),
            },
        )
        # Unauthenticated users don't have PERM_SUBMIT_ANSWERS via the normal
        # route decorator — actually guests DO have submit_answers permission.
        # The endpoint checks db_user is None and aborts 401.
        # The decorator allows guests through, but the manual check returns 401.
        self.assertIn(resp.status_code, [401, 403])

    # ── Issue #6: End Session / read-only state ──────────────────────────────

    def test_host_can_close_active_session(self):
        training_session = self._create_training_session()
        self._login_as_instructor()

        with self.client.session_transaction() as s:
            s[app_module.HOST_TRAINING_SESSION_ID_KEY] = training_session.id

        resp = self.client.post(
            f"/sessions/{training_session.id}/close",
            data={"csrf_token": self.csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        with app.app_context():
            updated = TrainingSession.query.get(training_session.id)
            self.assertEqual(updated.status, "closed")
            self.assertIsNotNone(updated.ends_at)

    def test_close_nonexistent_session_returns_404(self):
        self._login_as_instructor()
        resp = self.client.post(
            "/sessions/999999/close",
            data={"csrf_token": self.csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 404)

    def test_close_already_closed_session_returns_409(self):
        training_session = self._create_training_session()
        with app.app_context():
            ts = TrainingSession.query.get(training_session.id)
            ts.status = "closed"
            db.session.commit()

        self._login_as_instructor()
        resp = self.client.post(
            f"/sessions/{training_session.id}/close",
            data={"csrf_token": self.csrf_token},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 409)

    def test_closed_session_blocks_new_submissions(self):
        training_session = self._create_training_session()
        instructor_client, instructor_csrf = self._build_instructor_client()

        # Join the session as a participant
        participant_client, participant_csrf = self._build_participant_client("participant-csrf")
        resp = participant_client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": participant_csrf,
                "shift_label": "A Shift",
                "identity_mode": "anonymous",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        # Close the session
        resp = instructor_client.post(
            f"/sessions/{training_session.id}/close",
            data={"csrf_token": instructor_csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        # Participant tries to submit — should be blocked
        questions = self._active_questions_for_scenario(training_session.scenario_id)
        payload = {"csrf_token": participant_csrf}
        for i, q in enumerate(questions, start=1):
            payload[f"q_{q['id']}"] = f"late answer {i}"

        resp = self._submit_answers_for_session(participant_client, participant_csrf, training_session, "late")
        # Submissions to a closed session should fail
        with app.app_context():
            count = Submission.query.filter_by(training_session_id=training_session.id).count()
        self.assertEqual(count, 0, "No submissions should be saved for a closed session")

    def test_closed_session_cannot_be_joined(self):
        training_session = self._create_training_session()
        with app.app_context():
            ts = TrainingSession.query.get(training_session.id)
            ts.status = "closed"
            db.session.commit()

        participant_client, participant_csrf = self._build_participant_client("join-csrf")
        resp = participant_client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": participant_csrf,
                "shift_label": "A Shift",
                "identity_mode": "anonymous",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 409)


if __name__ == "__main__":
    unittest.main()
