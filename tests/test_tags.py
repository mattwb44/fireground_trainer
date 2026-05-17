import importlib
import os
import tempfile
import unittest
from pathlib import Path

_tmp_dir = tempfile.TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmp_dir.name) / 'tags_test.sqlite'}")
app_module = importlib.import_module("app")

app = app_module.app
db = app_module.db
CSRF_SESSION_KEY = app_module.CSRF_SESSION_KEY
Tag = app_module.Tag
ScenarioTag = app_module.ScenarioTag
Scenario = app_module.Scenario
User = app_module.User


class TagAdminTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_csrf("test-csrf")
        with app.app_context():
            ScenarioTag.query.delete()
            Tag.query.delete()
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

    def _create_tag(self, name="High-Rise", slug="high-rise") -> Tag:
        with app.app_context():
            tag = Tag(name=name, slug=slug, is_active=True)
            db.session.add(tag)
            db.session.commit()
            db.session.refresh(tag)
            return tag

    # ── Seed tags ────────────────────────────────────────────────────────────

    def test_seed_tags_are_created_on_startup(self):
        with app.app_context():
            from helpers import ensure_seed_tags
            ensure_seed_tags()
            count = Tag.query.filter_by(is_active=True).count()
        self.assertGreaterEqual(count, 9)

    # ── Admin: create tag ─────────────────────────────────────────────────────

    def test_admin_can_create_tag(self):
        self._login("admin@demo.local")
        resp = self.client.post(
            "/admin/tags/create",
            data={"csrf_token": self.csrf, "name": "Wildland Interface"},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            tag = Tag.query.filter_by(slug="wildland-interface").first()
            self.assertIsNotNone(tag)
            self.assertTrue(tag.is_active)

    def test_admin_tags_page_loads(self):
        self._login("admin@demo.local")
        resp = self.client.get("/admin/tags")
        self.assertEqual(resp.status_code, 200)

    def test_non_admin_cannot_access_admin_tags(self):
        self._login("instructor@demo.local")
        resp = self.client.get("/admin/tags")
        self.assertEqual(resp.status_code, 403)

    def test_create_tag_requires_name(self):
        self._login("admin@demo.local")
        resp = self.client.post(
            "/admin/tags/create",
            data={"csrf_token": self.csrf, "name": ""},
        )
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_slug_rejected(self):
        self._create_tag("High-Rise", "high-rise")
        self._login("admin@demo.local")
        resp = self.client.post(
            "/admin/tags/create",
            data={"csrf_token": self.csrf, "name": "High-Rise"},
        )
        self.assertEqual(resp.status_code, 409)

    # ── Toggle active/inactive ────────────────────────────────────────────────

    def test_toggle_deactivates_active_tag(self):
        tag = self._create_tag()
        self._login("admin@demo.local")
        resp = self.client.post(
            f"/admin/tags/{tag.id}/toggle",
            data={"csrf_token": self.csrf},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            updated = db.session.get(Tag, tag.id)
            self.assertFalse(updated.is_active)

    def test_toggle_activates_inactive_tag(self):
        with app.app_context():
            tag = Tag(name="Trauma", slug="trauma", is_active=False)
            db.session.add(tag)
            db.session.commit()
            tag_id = tag.id
        self._login("admin@demo.local")
        self.client.post(f"/admin/tags/{tag_id}/toggle", data={"csrf_token": self.csrf})
        with app.app_context():
            updated = db.session.get(Tag, tag_id)
            self.assertTrue(updated.is_active)

    # ── Rename tag ────────────────────────────────────────────────────────────

    def test_admin_can_rename_tag(self):
        tag = self._create_tag("Old Name", "old-name")
        self._login("admin@demo.local")
        self.client.post(
            f"/admin/tags/{tag.id}/rename",
            data={"csrf_token": self.csrf, "name": "New Name"},
        )
        with app.app_context():
            updated = db.session.get(Tag, tag.id)
            self.assertEqual(updated.name, "New Name")
            self.assertEqual(updated.slug, "new-name")

    # ── Scenario create with tags ─────────────────────────────────────────────

    def test_create_scenario_page_shows_available_tags(self):
        self._create_tag("Extrication", "extrication")
        self._login("instructor@demo.local")
        resp = self.client.get("/scenarios/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Extrication", resp.data)

    def test_create_scenario_saves_selected_tags(self):
        tag = self._create_tag("Trauma", "trauma")
        self._login("instructor@demo.local")
        resp = self.client.post(
            "/scenarios/new",
            data={
                "csrf_token": self.csrf,
                "title": "Tagged Scenario",
                "dispatch": "Units respond to a structural fire.",
                "base_image_path": "images/house1.jpg",
                "overlay_image_path": "",
                "question_prompt": ["What is your size-up?"],
                "question_type": ["discussion_only"],
                "instructor_answer": [""],
                "tag_ids": [str(tag.id)],
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            scenario = Scenario.query.filter_by(title="Tagged Scenario").first()
            self.assertIsNotNone(scenario)
            self.assertEqual(len(scenario.tag_links), 1)
            self.assertEqual(scenario.tag_links[0].tag.name, "Trauma")

    def test_library_summary_includes_tags(self):
        tag = self._create_tag("Duplex", "duplex")
        with app.app_context():
            from helpers import summarize_scenario_for_library
            scenario = Scenario.query.filter(Scenario.is_active.is_(True)).first()
            db.session.add(ScenarioTag(scenario_id=scenario.id, tag_id=tag.id))
            db.session.commit()
            db.session.refresh(scenario)
            summary = summarize_scenario_for_library(scenario)
        self.assertIn("Duplex", summary["tags"])
