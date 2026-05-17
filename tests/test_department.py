import importlib
import os
import tempfile
import unittest
from pathlib import Path

_tmp_dir = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp_dir.name) / 'dept_test.sqlite'}")
app_module = importlib.import_module("app")

app = app_module.app
db = app_module.db
CSRF_SESSION_KEY = app_module.CSRF_SESSION_KEY
Department = app_module.Department
User = app_module.User


class DepartmentTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_csrf("test-csrf")
        with app.app_context():
            from werkzeug.security import generate_password_hash
            from models import Role, UserRole
            User.query.update({"department_id": None}, synchronize_session=False)
            Department.query.delete()
            db.session.commit()
            # Ensure a plain participant user exists for join tests
            if not User.query.filter_by(email="member@dept.test").first():
                participant_role = Role.query.filter_by(name="participant").first()
                u = User(
                    email="member@dept.test",
                    full_name="Test Member",
                    password_hash=generate_password_hash("EasyPass123"),
                    is_active=True,
                    is_email_verified=True,
                )
                db.session.add(u)
                db.session.flush()
                db.session.add(UserRole(user_id=u.id, role_id=participant_role.id))
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

    def _create_dept(self, name="Station 7") -> Department:
        with app.app_context():
            dept = Department(name=name, invite_code="TESTCODE123", created_by_user_id=None)
            db.session.add(dept)
            db.session.commit()
            db.session.refresh(dept)
            return dept

    # ── Join flow ────────────────────────────────────────────────────────────

    def test_join_page_redirects_guests_to_login(self):
        resp = self.client.get("/department/join")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_join_with_valid_code_assigns_department(self):
        dept = self._create_dept()
        self._login("member@dept.test")

        resp = self.client.post(
            "/department/join",
            data={"csrf_token": self.csrf, "invite_code": "TESTCODE123"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        with app.app_context():
            user = User.query.filter_by(email="member@dept.test").first()
            self.assertEqual(user.department_id, dept.id)

    def test_join_with_invalid_code_shows_error(self):
        self._create_dept()
        self._login("member@dept.test")

        resp = self.client.post(
            "/department/join",
            data={"csrf_token": self.csrf, "invite_code": "WRONGCODE"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"not valid", resp.data)

    def test_join_with_empty_code_shows_error(self):
        self._login("member@dept.test")

        resp = self.client.post(
            "/department/join",
            data={"csrf_token": self.csrf, "invite_code": ""},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn(b"Enter an invite code", resp.data)

    # ── Department settings ──────────────────────────────────────────────────

    def test_settings_requires_training_chief(self):
        self._login("member@dept.test")
        resp = self.client.get("/department/settings")
        self.assertEqual(resp.status_code, 403)

    def test_settings_shows_invite_code_for_tc_in_dept(self):
        dept = self._create_dept("Station 9")
        # assign TC to the department
        with app.app_context():
            tc = User.query.filter_by(email="chief@demo.local").first()
            tc.department_id = dept.id
            db.session.commit()

        self._login("chief@demo.local")
        resp = self.client.get("/department/settings")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"TESTCODE123", resp.data)
        self.assertIn(b"Station 9", resp.data)

    def test_settings_redirects_tc_without_dept(self):
        self._login("chief@demo.local")
        resp = self.client.get("/department/settings", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)

    def test_regenerate_code_replaces_invite_code(self):
        dept = self._create_dept()
        with app.app_context():
            tc = User.query.filter_by(email="chief@demo.local").first()
            tc.department_id = dept.id
            db.session.commit()

        self._login("chief@demo.local")
        resp = self.client.post(
            "/department/settings/regenerate-code",
            data={"csrf_token": self.csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        with app.app_context():
            updated = Department.query.get(dept.id)
            self.assertNotEqual(updated.invite_code, "TESTCODE123")

    # ── Admin: create department ─────────────────────────────────────────────

    def test_admin_can_create_department(self):
        self._login("admin@demo.local")
        resp = self.client.post(
            "/admin/departments/create",
            data={"csrf_token": self.csrf, "name": "Ladder 12"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)

        with app.app_context():
            dept = Department.query.filter_by(name="Ladder 12").first()
            self.assertIsNotNone(dept)
            self.assertTrue(len(dept.invite_code) > 0)

    def test_admin_departments_page_loads(self):
        self._login("admin@demo.local")
        resp = self.client.get("/admin/departments")
        self.assertEqual(resp.status_code, 200)

    def test_non_admin_cannot_access_admin_departments(self):
        self._login("member@dept.test")
        resp = self.client.get("/admin/departments")
        self.assertEqual(resp.status_code, 403)
