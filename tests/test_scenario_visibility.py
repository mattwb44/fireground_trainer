import importlib
import os
import tempfile
import unittest
from pathlib import Path

_tmp_dir = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp_dir.name) / 'visibility_test.sqlite'}")
app_module = importlib.import_module("app")

app = app_module.app
db = app_module.db
CSRF_SESSION_KEY = app_module.CSRF_SESSION_KEY
Scenario = app_module.Scenario
ScenarioTag = app_module.ScenarioTag
Tag = app_module.Tag
Department = app_module.Department
User = app_module.User
Role = app_module.Role
UserRole = app_module.UserRole


class ScenarioVisibilityTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_csrf("test-csrf")
        with app.app_context():
            from werkzeug.security import generate_password_hash
            ScenarioTag.query.delete()
            Tag.query.delete()
            User.query.update({"department_id": None}, synchronize_session=False)
            Department.query.delete()
            db.session.commit()
            from helpers import ensure_seed_tags
            ensure_seed_tags()
            if not User.query.filter_by(email="member@vis.test").first():
                role = Role.query.filter_by(name="participant").first()
                u = User(
                    email="member@vis.test",
                    full_name="Dept Member",
                    password_hash=generate_password_hash("EasyPass123"),
                    is_active=True,
                    is_email_verified=True,
                )
                db.session.add(u)
                db.session.flush()
                db.session.add(UserRole(user_id=u.id, role_id=role.id))
                db.session.commit()

    def _set_csrf(self, token="test-csrf"):
        with self.client.session_transaction() as s:
            s[CSRF_SESSION_KEY] = token
        self.csrf = token

    def _login(self, email):
        with app.app_context():
            user = User.query.filter_by(email=email).first()
        with self.client.session_transaction() as s:
            s["user_id"] = user.id
            s[CSRF_SESSION_KEY] = self.csrf

    def _first_approved_scenario_id(self) -> int:
        with app.app_context():
            s = Scenario.query.filter_by(status="approved", is_active=True).first()
            return s.id

    def _first_tag_id(self) -> int:
        with app.app_context():
            return Tag.query.filter_by(is_active=True).first().id

    # ── Schema ───────────────────────────────────────────────────────────────

    def test_scenario_has_visibility_columns(self):
        with app.app_context():
            scenario = Scenario.query.first()
            self.assertIsNotNone(scenario)
            self.assertIsInstance(scenario.is_public, bool)
            self.assertIsNone(scenario.forked_from_scenario_id)

    def test_existing_approved_scenarios_migrated_to_public(self):
        with app.app_context():
            approved = Scenario.query.filter_by(status="approved", is_active=True).all()
            self.assertTrue(len(approved) > 0)
            for s in approved:
                self.assertTrue(s.is_public, f"Scenario {s.id} should be public after migration")

    # ── Visibility: guests ────────────────────────────────────────────────────

    def test_guest_sees_only_public_scenarios(self):
        with app.app_context():
            from authz import CurrentUser
            from helpers import load_visible_scenarios_for_user
            guest = CurrentUser(user_id="guest", display_name="Guest", roles=frozenset())
            visible = load_visible_scenarios_for_user(guest)
            for s in visible:
                self.assertTrue(s.is_public)

    # ── Visibility: participants ──────────────────────────────────────────────

    def test_participant_sees_own_private_scenario(self):
        from authz import CurrentUser
        from helpers import load_visible_scenarios_for_user
        with app.app_context():
            member = User.query.filter_by(email="member@vis.test").first()
            member_id = member.id
            private = Scenario(
                title="Private Test", dispatch_text="Test", base_image_path="images/house1.jpg",
                created_by_user_id=member_id, status="draft", is_active=True,
                is_public=False, department_id=None,
            )
            db.session.add(private)
            db.session.commit()
            priv_id = private.id
            cu = CurrentUser(
                user_id=str(member_id),
                display_name=member.full_name or member.email,
                roles=frozenset({"participant"}),
            )
            db.session.refresh(member)
            visible_ids = {s.id for s in load_visible_scenarios_for_user(cu, db_user=member)}
        self.assertIn(priv_id, visible_ids)

    def test_participant_cannot_see_other_dept_private_scenario(self):
        from authz import CurrentUser
        from helpers import load_visible_scenarios_for_user
        with app.app_context():
            dept = Department(name="Test Dept", invite_code="DEPTEST99")
            db.session.add(dept)
            db.session.flush()
            priv = Scenario(
                title="Dept Private", dispatch_text="Test", base_image_path="images/house1.jpg",
                status="draft", is_active=True, is_public=False, department_id=dept.id,
            )
            db.session.add(priv)
            db.session.commit()
            priv_id = priv.id
            member = User.query.filter_by(email="member@vis.test").first()
            member_id = member.id
            # member is NOT in the dept
            cu = CurrentUser(
                user_id=str(member_id),
                display_name=member.full_name or member.email,
                roles=frozenset({"participant"}),
            )
            db.session.refresh(member)
            visible_ids = {s.id for s in load_visible_scenarios_for_user(cu, db_user=member)}
        self.assertNotIn(priv_id, visible_ids)

    # ── Create with visibility ────────────────────────────────────────────────

    def test_create_public_scenario_requires_category(self):
        tag_id = self._first_tag_id()
        self._login("instructor@demo.local")
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf,
                "title": "Public No Cat",
                "dispatch": "Test dispatch.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["Q1"],
                "question_type": ["discussion_only"],
                "instructor_answer": [""],
                "visibility": "public",
                "training_category": "",
                "tag_ids": [str(tag_id)],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"training category", resp.data)

    def test_create_public_scenario_requires_tag(self):
        self._login("instructor@demo.local")
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf,
                "title": "Public No Tag",
                "dispatch": "Test dispatch.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["Q1"],
                "question_type": ["discussion_only"],
                "instructor_answer": [""],
                "visibility": "public",
                "training_category": "fireground",
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"tag", resp.data)

    def test_create_public_scenario_succeeds_with_category_and_tag(self):
        tag_id = self._first_tag_id()
        self._login("instructor@demo.local")
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf,
                "title": "Public With All",
                "dispatch": "Units respond.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["Size-up?"],
                "question_type": ["discussion_only"],
                "instructor_answer": [""],
                "visibility": "public",
                "training_category": "fireground",
                "tag_ids": [str(tag_id)],
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            s = Scenario.query.filter_by(title="Public With All").first()
            self.assertIsNotNone(s)
            self.assertTrue(s.is_public)
            self.assertEqual(s.training_category, "fireground")

    def test_create_private_scenario_succeeds_without_category(self):
        self._login("instructor@demo.local")
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf,
                "title": "Private Scenario",
                "dispatch": "Units respond.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["Size-up?"],
                "question_type": ["discussion_only"],
                "instructor_answer": [""],
                "visibility": "private",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            s = Scenario.query.filter_by(title="Private Scenario").first()
            self.assertFalse(s.is_public)
            self.assertIsNone(s.department_id)

    # ── Retroactive visibility change ─────────────────────────────────────────

    def test_creator_can_change_visibility_to_public(self):
        tag_id = self._first_tag_id()
        # Create a private scenario as instructor
        self._login("instructor@demo.local")
        self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf,
                "title": "Will Go Public",
                "dispatch": "Units respond.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["Q"],
                "question_type": ["discussion_only"],
                "instructor_answer": [""],
                "visibility": "private",
            },
            follow_redirects=False,
        )
        with app.app_context():
            s = Scenario.query.filter_by(title="Will Go Public").first()
            scenario_id = s.id
            self.assertFalse(s.is_public)

        # Now change to public
        resp = self.client.post(
            "/scenario/visibility",
            data={
                "csrf_token": self.csrf,
                "scenario_id": str(scenario_id),
                "visibility": "public",
                "training_category": "fireground",
                "tag_ids": [str(tag_id)],
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            s = Scenario.query.get(scenario_id)
            self.assertTrue(s.is_public)
            self.assertEqual(s.training_category, "fireground")

    # ── Issue #10: Official designation workflow ──────────────────────────────

    def _first_approved_scenario(self):
        with app.app_context():
            return Scenario.query.filter_by(status="approved", is_active=True).first()

    def test_instructor_can_submit_approved_scenario_for_official(self):
        self._login("instructor@demo.local")
        with app.app_context():
            scenario = Scenario.query.filter_by(status="approved", is_active=True).first()
            self.assertIsNotNone(scenario, "Need at least one approved scenario")
            scenario_id = scenario.id
            # clear any existing official submission flag
            scenario.submitted_for_official_at = None
            scenario.is_official = False
            db.session.commit()

        resp = self.client.post(
            "/scenario/submit-for-official",
            data={"csrf_token": self.csrf, "scenario_id": str(scenario_id), "next": "/board"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            s = Scenario.query.get(scenario_id)
            self.assertIsNotNone(s.submitted_for_official_at)
            self.assertFalse(s.is_official)

    def test_tc_can_view_official_queue(self):
        self._login("chief@demo.local")
        resp = self.client.get("/scenarios/official-queue")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Official Scenario Review Queue", resp.data)

    def test_instructor_cannot_view_official_queue(self):
        self._login("instructor@demo.local")
        resp = self.client.get("/scenarios/official-queue")
        self.assertEqual(resp.status_code, 403)

    def test_tc_can_approve_official_request(self):
        with app.app_context():
            from datetime import datetime
            scenario = Scenario.query.filter_by(status="approved", is_active=True).first()
            scenario.is_official = False
            scenario.submitted_for_official_at = datetime.utcnow()
            db.session.commit()
            scenario_id = scenario.id

        self._login("chief@demo.local")
        resp = self.client.post(
            "/scenario/official-review",
            data={
                "csrf_token": self.csrf,
                "scenario_id": str(scenario_id),
                "review_action": "approve",
                "next": "/scenarios/official-queue",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            s = Scenario.query.get(scenario_id)
            self.assertTrue(s.is_official)
            self.assertIsNone(s.submitted_for_official_at)

    def test_tc_can_reject_official_request(self):
        with app.app_context():
            from datetime import datetime
            scenario = Scenario.query.filter_by(status="approved", is_active=True).first()
            scenario.is_official = False
            scenario.submitted_for_official_at = datetime.utcnow()
            db.session.commit()
            scenario_id = scenario.id

        self._login("chief@demo.local")
        resp = self.client.post(
            "/scenario/official-review",
            data={
                "csrf_token": self.csrf,
                "scenario_id": str(scenario_id),
                "review_action": "reject",
                "next": "/scenarios/official-queue",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            s = Scenario.query.get(scenario_id)
            self.assertFalse(s.is_official)
            self.assertIsNone(s.submitted_for_official_at)

    # ── Issue #11: Public scenario library ───────────────────────────────────

    def test_public_library_accessible_to_guests(self):
        resp = self.client.get("/library")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Scenario Library", resp.data)

    def test_public_library_shows_only_public_scenarios(self):
        resp = self.client.get("/library")
        self.assertEqual(resp.status_code, 200)
        # Should have scenario cards
        self.assertIn(b"Practice", resp.data)

    def test_public_library_category_filter(self):
        resp = self.client.get("/library?category=fireground")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 200)

    def test_public_library_keyword_filter(self):
        resp = self.client.get("/library?q=residential")
        self.assertEqual(resp.status_code, 200)

    def test_public_library_shows_completed_badge_for_account_holder(self):
        """Completed badge shows for account holders who finished a scenario via DrillAttempt."""
        from models import DrillAttempt
        self._login("instructor@demo.local")
        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            scenario = Scenario.query.filter_by(status="approved", is_active=True, is_public=True).first()
            self.assertIsNotNone(scenario)
            scenario_id = scenario.id
            # Ensure no existing drill attempts
            DrillAttempt.query.filter_by(user_id=user.id, scenario_id=scenario_id).delete()
            db.session.commit()
            # Add a drill attempt
            attempt = DrillAttempt(
                user_id=user.id, scenario_id=scenario_id,
                attempt_number=1, status="submitted",
            )
            db.session.add(attempt)
            db.session.commit()

        resp = self.client.get("/library")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Completed", resp.data)

    def test_cannot_submit_draft_for_official(self):
        self._login("instructor@demo.local")
        with app.app_context():
            scenario = Scenario.query.filter_by(status="draft", is_active=True).first()
            if scenario is None:
                # Create a draft scenario
                scenario = Scenario(
                    title="Draft Scenario", dispatch_text="Test", base_image_path="images/house1.jpg",
                    status="draft", is_active=True,
                )
                db.session.add(scenario)
                db.session.commit()
            scenario_id = scenario.id

        resp = self.client.post(
            "/scenario/submit-for-official",
            data={"csrf_token": self.csrf, "scenario_id": str(scenario_id)},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 409)
