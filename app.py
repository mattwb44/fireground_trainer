import random

from flask import Flask, g, redirect, render_template, request, session, url_for

from authz import (
    CurrentUser,
    PERM_APPROVE_SCENARIOS,
    PERM_EXPORT_REPORTS,
    PERM_REVEAL_INSTRUCTOR_ANSWERS,
    PERM_SUBMIT_ANSWERS,
    PERM_VIEW_REPORTS,
    PERM_VIEW_SCENARIOS,
    ROLE_ADMIN,
    ROLE_INSTRUCTOR,
    ROLE_PARTICIPANT,
    ROLE_TRAINING_CHIEF,
    normalize_roles,
    requires_permission,
)

app = Flask(__name__)
app.secret_key = "change-me-to-something-random"

PERMISSION_KEYS = {
    "view_scenarios": PERM_VIEW_SCENARIOS,
    "submit_answers": PERM_SUBMIT_ANSWERS,
    "reveal_instructor_answers": PERM_REVEAL_INSTRUCTOR_ANSWERS,
    "approve_scenarios": PERM_APPROVE_SCENARIOS,
    "view_reports": PERM_VIEW_REPORTS,
    "export_reports": PERM_EXPORT_REPORTS,
}

ROLE_LABELS = {
    ROLE_ADMIN: "Admin",
    ROLE_TRAINING_CHIEF: "Training Chief",
    ROLE_INSTRUCTOR: "Instructor",
    ROLE_PARTICIPANT: "Participant",
}

DEMO_USERS = {
    "participant_alex": {
        "display_name": "Alex Participant",
        "roles": [ROLE_PARTICIPANT],
    },
    "instructor_sam": {
        "display_name": "Sam Instructor",
        "roles": [ROLE_INSTRUCTOR],
    },
    "delegate_lee": {
        "display_name": "Lee Trusted Delegate",
        "roles": [ROLE_INSTRUCTOR],
        "permission_overrides": [PERM_APPROVE_SCENARIOS],
    },
    "chief_casey": {
        "display_name": "Casey Training Chief",
        "roles": [ROLE_TRAINING_CHIEF],
    },
    "admin_jordan": {
        "display_name": "Jordan Admin",
        "roles": [ROLE_ADMIN],
    },
}


SCENARIOS = [
    {
        "title": "Residential Fire - Bravo Side Smoke Showing",
        "dispatch": (
            "0200 hours. Single-story residential. Neighbors report smoke showing. "
            "Wind 10 mph from the west. First-due engine staffed with 3."
        ),
        "image": {
            "base": "images/house1.jpg",
            "overlay": None,
        },
        "questions": [
            {
                "id": "q1",
                "prompt": (
                    "After performing a 3-sided search by pulling up to and past the house, "
                    "you notice smoke/fire conditions on the Alpha/Bravo/Charlie/Delta side. "
                    "Give your scene size-up and plan of action."
                ),
                "instructor_answer": (
                    "Size-up construction, occupancy, conditions, and life hazard first; "
                    "declare strategy, line selection, and task assignments."
                ),
            },
            {
                "id": "q2",
                "prompt": (
                    "You pull an attack line of 200 ft of 1 3/4-inch with a low-pressure smooth bore nozzle. "
                    "What is your PDP to obtain correct pressure at the nozzle?"
                ),
                "instructor_answer": (
                    "Show nozzle pressure target and friction loss assumptions, then compute "
                    "and state your final PDP."
                ),
            },
            {
                "id": "q3",
                "prompt": (
                    "Given the area of the home, approximately how much GPM should be needed to extinguish the fire?"
                ),
                "instructor_answer": (
                    "Use your department fire flow method and state the estimate with assumptions."
                ),
            },
            {
                "id": "q4",
                "prompt": (
                    "The attack mode turns defensive. You pull a blitz line of 100 ft of 3-inch to protect the exposure "
                    "on the Delta side. What would your PDP be to supply both the blitz and attack line?"
                ),
                "instructor_answer": (
                    "Account for both lines, appliance/elevation loss if present, and provide total PDP."
                ),
            },
        ],
    },
    {
        "title": "Two-Story Residential - Possible Victims Trapped",
        "dispatch": (
            "1730 hours. Two-story residential. Caller reports smoke alarms and someone possibly still inside. "
            "Light smoke from the Alpha side. Engine staffed with 4."
        ),
        "image": {
            "base": "images/house2.jpg",
            "overlay": None,
        },
        "questions": [
            {
                "id": "q1",
                "prompt": "Give your size-up (construction, occupancy, fire location cues, life hazard) and first 5 minutes plan.",
                "instructor_answer": "Prioritize life hazard, place first line to protect interior access, and define search/vent tasks.",
            },
            {
                "id": "q2",
                "prompt": "Where would you place the first ladder and why? Window base vs offset, and what you are setting up for.",
                "instructor_answer": "Choose placement based on egress, rescue potential, and expected interior movement.",
            },
            {
                "id": "q3",
                "prompt": "Describe how you would control the flow path while still making progress on search and attack.",
                "instructor_answer": "Coordinate door control and ventilation timing with line advancement and search benchmarks.",
            },
            {
                "id": "q4",
                "prompt": "What are your early Mayday warning signs on interior crews, and what triggers RIT deployment in your plan?",
                "instructor_answer": "Define objective triggers (lost/disoriented/trapped/air emergency) and immediate RIT actions.",
            },
        ],
    },
    {
        "title": "Attic Involvement - Wind-Influenced Fire",
        "dispatch": (
            "2315 hours. Single-story residential. Smoke pushing from eaves on the Charlie/Delta corner. "
            "Wind gusts 15-20 mph. First-due engine staffed with 3."
        ),
        "image": {
            "base": "images/house3.jpg",
            "overlay": None,
        },
        "questions": [
            {
                "id": "q1",
                "prompt": "What indicators suggest attic involvement, and how does that change your initial line placement?",
                "instructor_answer": "Read extension indicators and position for quickest control of attic pathways.",
            },
            {
                "id": "q2",
                "prompt": "Walk through ventilation choice/timing (horizontal vs vertical) and how you prevent making things worse.",
                "instructor_answer": "Delay or sequence ventilation to support suppression and avoid wind-driven intensification.",
            },
            {
                "id": "q3",
                "prompt": "When do you call for additional resources (truck/second alarm) and what is your reasoning?",
                "instructor_answer": "Call early when extension risk, staffing limits, or rescue complexity exceeds first alarm capacity.",
            },
            {
                "id": "q4",
                "prompt": "If a civilian is removed with suspected smoke inhalation, what is your immediate EMS plan (airway, oxygen, CO/cyanide considerations)?",
                "instructor_answer": "Prioritize airway/oxygenation, monitor toxidrome clues, and treat per protocol rapidly.",
            },
        ],
    },
]


def role_label(role_name: str) -> str:
    return ROLE_LABELS.get(role_name, role_name.replace("_", " ").title())


def get_current_user() -> CurrentUser:
    default_key = "participant_alex"
    user_key = session.get("demo_user_key", default_key)
    if user_key not in DEMO_USERS:
        user_key = default_key
        session["demo_user_key"] = user_key

    raw_user = DEMO_USERS[user_key]
    roles = normalize_roles(raw_user.get("roles", []))
    permission_overrides = frozenset(raw_user.get("permission_overrides", []))
    return CurrentUser(
        user_id=user_key,
        display_name=raw_user["display_name"],
        roles=roles,
        permission_overrides=permission_overrides,
    )


def get_current_scenario():
    idx = session.get("scenario_idx", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(SCENARIOS):
        idx = 0
        session["scenario_idx"] = idx

    scenario = SCENARIOS[idx]
    scenario.setdefault("approval_status", "practice")
    scenario.setdefault("approved_by", None)
    return scenario


def safe_redirect_target(raw_target: str | None) -> str:
    if raw_target and raw_target.startswith("/"):
        return raw_target
    return url_for("index")


@app.before_request
def load_current_user():
    g.current_user = get_current_user()


@app.context_processor
def inject_template_context():
    return {
        "current_user": g.current_user,
        "demo_users": DEMO_USERS,
        "role_label": role_label,
        "permission_keys": PERMISSION_KEYS,
    }


@app.post("/switch-user")
def switch_user():
    user_key = request.form.get("user_key", "")
    if user_key in DEMO_USERS:
        session["demo_user_key"] = user_key
    return redirect(safe_redirect_target(request.form.get("next")))


@app.get("/")
@requires_permission(PERM_VIEW_SCENARIOS)
def index():
    scenario = get_current_scenario()
    return render_template("scenario.html", scenario=scenario, answers={}, submitted=False)


@app.get("/new")
@requires_permission(PERM_VIEW_SCENARIOS)
def new_scenario():
    current = session.get("scenario_idx", 0)
    if len(SCENARIOS) == 1:
        session["scenario_idx"] = 0
    else:
        choices = [i for i in range(len(SCENARIOS)) if i != current]
        session["scenario_idx"] = random.choice(choices)
    return redirect(url_for("index"))


@app.post("/submit")
@requires_permission(PERM_SUBMIT_ANSWERS)
def submit():
    scenario = get_current_scenario()
    answers = {q["id"]: request.form.get(q["id"], "").strip() for q in scenario["questions"]}
    return render_template("scenario.html", scenario=scenario, answers=answers, submitted=True)


@app.post("/scenario/approve")
@requires_permission(PERM_APPROVE_SCENARIOS)
def approve_scenario():
    scenario = get_current_scenario()
    scenario["approval_status"] = "approved"
    scenario["approved_by"] = g.current_user.display_name
    return redirect(url_for("index"))


@app.get("/reports")
@requires_permission(PERM_VIEW_REPORTS)
def reports():
    return (
        "<h1>Reports</h1>"
        "<p>Placeholder for TODO #19/#20. Access is currently limited to Training Chief and Admin.</p>"
    )


@app.get("/offline")
def offline():
    return render_template("offline.html")


@app.errorhandler(403)
def forbidden(_err):
    return render_template("forbidden.html"), 403


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
