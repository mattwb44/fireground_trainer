import importlib
import os
import tempfile
import unittest
from pathlib import Path


_tmp_dir = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir.name) / 'submission_flow.sqlite'}"
app_module = importlib.import_module("app")

app = app_module.app
db = app_module.db
CSRF_SESSION_KEY = app_module.CSRF_SESSION_KEY
Scenario = app_module.Scenario
TrainingSession = app_module.TrainingSession
Participant = app_module.Participant
ScenarioLike = app_module.ScenarioLike
Submission = app_module.Submission
SubmissionAnswer = app_module.SubmissionAnswer
SubmissionAuditLog = app_module.SubmissionAuditLog
User = app_module.User


class SubmissionFlowTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self._set_csrf_token()

        with app.app_context():
            ScenarioLike.query.delete()
            SubmissionAnswer.query.delete()
            SubmissionAuditLog.query.delete()
            Submission.query.delete()
            Participant.query.delete()
            TrainingSession.query.delete()
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
            training_session = TrainingSession(
                scenario_id=scenario.id,
                join_code="SMOKE1",
                title="Smoke Test Session",
                status="active",
            )
            db.session.add(training_session)
            db.session.commit()
            db.session.refresh(training_session)
            return training_session

    def _login_as_instructor(self) -> None:
        with app.app_context():
            instructor = User.query.filter_by(email="instructor@demo.local").first()
        with self.client.session_transaction() as flask_session:
            flask_session["user_id"] = instructor.id
            flask_session[CSRF_SESSION_KEY] = self.csrf_token

    def _build_instructor_client(self):
        return self._build_staff_client("instructor@demo.local", "instructor-csrf-token")

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
        original_detect_lan_ip = app_module.detect_lan_ip
        app.config["PUBLIC_BASE_URL"] = None
        app_module.detect_lan_ip = lambda: "192.168.1.50"
        try:
            with app.test_request_context("/", base_url="http://127.0.0.1:5000"):
                training_session = TrainingSession(join_code="LANJOIN")
                join_url, join_url_warning = app_module.build_join_url_for_session(training_session)
        finally:
            app.config["PUBLIC_BASE_URL"] = original_public_base_url
            app_module.detect_lan_ip = original_detect_lan_ip

        self.assertEqual(join_url, "http://192.168.1.50:5000/join/LANJOIN")
        self.assertIn("detected LAN address", join_url_warning)

    def test_homepage_routes_to_training_categories(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Blitzfire Training", response.data)
        self.assertIn(b"Fireground Training", response.data)
        self.assertIn(b"Motor Vehicle Accidents", response.data)
        self.assertIn(b"Emergency Medical Services", response.data)

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
        self.assertIn(b"Primary Host Workspace", board_response.data)
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
        self.assertIn(b"Attempt #1 saved.", submit_response.data)

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
        self.assertIn(b"Join an active session before submitting answers.", response.data)
        with app.app_context():
            self.assertEqual(Submission.query.count(), 0)
            self.assertEqual(SubmissionAnswer.query.count(), 0)

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
        self.assertIn(b"Attempt #1 saved.", first_response.data)
        self.assertIn(b"Attempt #2 saved.", second_response.data)

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
        self.assertIn(b"Live Revealed Answers", participant_board_response.data)
        self.assertIn(b"Reveal Tester", participant_board_response.data)
        self.assertIn(b"Reveal Backup", participant_board_response.data)
        self.assertIn(b"Reveal alpha answer 1", participant_board_response.data)
        self.assertIn(b"Reveal bravo answer 2", participant_board_response.data)
        self.assertIn(b"Live Revealed Answers", revealed_partial_response.data)

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

    def test_instructor_can_review_and_approve_submission_for_reporting(self):
        training_session = self._create_training_session()
        questions = self._active_questions_for_scenario(training_session.scenario_id)

        join_response = self.client.post(
            f"/join/{training_session.join_code}",
            data={
                "csrf_token": self.csrf_token,
                "shift_label": "D Shift",
                "identity_mode": "named",
                "display_name": "Approval Tester",
            },
            follow_redirects=False,
        )
        self.assertEqual(join_response.status_code, 302)

        payload = {"csrf_token": self.csrf_token}
        for index, question in enumerate(questions, start=1):
            payload[f"q_{question['id']}"] = f"Approval answer {index}"
        submit_response = self.client.post("/submit", data=payload)
        self.assertEqual(submit_response.status_code, 200)

        with app.app_context():
            submission = Submission.query.filter_by(training_session_id=training_session.id).first()

        instructor_client, instructor_csrf_token = self._build_instructor_client()
        approve_response = instructor_client.post(
            f"/sessions/{training_session.id}/submissions/{submission.id}/review",
            data={
                "csrf_token": instructor_csrf_token,
                "review_notes": "Solid tactical reasoning. Approved for reporting.",
                "review_action": "approve",
            },
            follow_redirects=False,
        )
        self.assertEqual(approve_response.status_code, 302)

        partial_response = instructor_client.get(f"/sessions/{training_session.id}/submissions")
        detail_response = instructor_client.get(
            f"/sessions/{training_session.id}/submissions/{submission.id}"
        )

        self.assertIn(b"Approved For Reporting", partial_response.data)
        self.assertIn(b"Solid tactical reasoning. Approved for reporting.", detail_response.data)
        self.assertIn(b"Approved By", detail_response.data)

        reopen_response = instructor_client.post(
            f"/sessions/{training_session.id}/submissions/{submission.id}/review",
            data={
                "csrf_token": instructor_csrf_token,
                "review_notes": "Needs another pass before reporting.",
                "review_action": "reopen",
            },
            follow_redirects=False,
        )
        self.assertEqual(reopen_response.status_code, 302)

        reopened_detail_response = instructor_client.get(
            f"/sessions/{training_session.id}/submissions/{submission.id}"
        )
        self.assertIn(b"Pending Review", reopened_detail_response.data)
        self.assertIn(b"Needs another pass before reporting.", reopened_detail_response.data)

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

            self.assertIsNone(refreshed_session.revealed_submission_id)
            self.assertEqual(refreshed_submission.status, app_module.SUBMISSION_STATUS_EXCLUDED)
            self.assertIn("exclude", audit_actions)

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


if __name__ == "__main__":
    unittest.main()
