import importlib
import os
import tempfile
import unittest
from pathlib import Path

_tmp_dir = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp_dir.name) / 'lists_test.sqlite'}")
app_module = importlib.import_module("app")

app = app_module.app
db = app_module.db
CSRF_SESSION_KEY = app_module.CSRF_SESSION_KEY
Scenario = app_module.Scenario
User = app_module.User
UserList = app_module.UserList
UserListScenario = app_module.UserListScenario


class SaveToListTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_csrf("test-csrf")
        with app.app_context():
            UserListScenario.query.delete()
            UserList.query.delete()
            db.session.commit()

    def _set_csrf(self, token="test-csrf"):
        with self.client.session_transaction() as sess:
            sess[CSRF_SESSION_KEY] = token
        self.csrf = token

    def _login(self, email):
        with app.app_context():
            user = User.query.filter_by(email=email).first()
            self.assertIsNotNone(user)
            user_id = user.id
        with self.client.session_transaction() as s:
            s["user_id"] = user_id
            s[CSRF_SESSION_KEY] = self.csrf

    def _get_public_scenario_id(self):
        with app.app_context():
            s = Scenario.query.filter_by(status="approved", is_active=True, is_public=True).first()
            self.assertIsNotNone(s)
            return s.id

    def test_create_list_and_add_scenario(self):
        self._login("instructor@demo.local")
        scenario_id = self._get_public_scenario_id()

        resp = self.client.post(
            "/lists/new",
            data={
                "csrf_token": self.csrf,
                "list_name": "My Test List",
                "scenario_id": str(scenario_id),
                "next": "/library",
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, [302])

        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            user_list = UserList.query.filter_by(user_id=user.id, name="My Test List").first()
            self.assertIsNotNone(user_list)
            link = UserListScenario.query.filter_by(
                list_id=user_list.id, scenario_id=scenario_id
            ).first()
            self.assertIsNotNone(link)

    def test_remove_scenario_from_list(self):
        self._login("instructor@demo.local")
        scenario_id = self._get_public_scenario_id()

        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            user_list = UserList(user_id=user.id, name="Remove Test List")
            db.session.add(user_list)
            db.session.flush()
            db.session.add(UserListScenario(list_id=user_list.id, scenario_id=scenario_id))
            db.session.commit()
            list_id = user_list.id

        resp = self.client.post(
            f"/lists/{list_id}/remove",
            data={
                "csrf_token": self.csrf,
                "scenario_id": str(scenario_id),
                "next": "/library",
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, [302])

        with app.app_context():
            link = UserListScenario.query.filter_by(
                list_id=list_id, scenario_id=scenario_id
            ).first()
            self.assertIsNone(link)

    def test_duplicate_add_does_not_raise(self):
        self._login("instructor@demo.local")
        scenario_id = self._get_public_scenario_id()

        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            user_list = UserList(user_id=user.id, name="Duplicate Test List")
            db.session.add(user_list)
            db.session.flush()
            db.session.add(UserListScenario(list_id=user_list.id, scenario_id=scenario_id))
            db.session.commit()
            list_id = user_list.id

        resp = self.client.post(
            f"/lists/{list_id}/add",
            data={
                "csrf_token": self.csrf,
                "scenario_id": str(scenario_id),
                "next": "/library",
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, [302])

        with app.app_context():
            count = UserListScenario.query.filter_by(
                list_id=list_id, scenario_id=scenario_id
            ).count()
            self.assertEqual(count, 1)

    def test_guest_cannot_create_list(self):
        scenario_id = self._get_public_scenario_id()
        resp = self.client.post(
            "/lists/new",
            data={
                "csrf_token": self.csrf,
                "list_name": "Guest List",
                "scenario_id": str(scenario_id),
            },
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, [302, 403])

    def test_my_library_shows_saved_lists_section(self):
        self._login("instructor@demo.local")
        scenario_id = self._get_public_scenario_id()

        with app.app_context():
            user = User.query.filter_by(email="instructor@demo.local").first()
            ul = UserList(user_id=user.id, name="Library Display List")
            db.session.add(ul)
            db.session.flush()
            db.session.add(UserListScenario(list_id=ul.id, scenario_id=scenario_id))
            db.session.commit()

        resp = self.client.get("/scenarios")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Saved Lists", resp.data)
        self.assertIn(b"Library Display List", resp.data)
