from flask import Flask, render_template, request, redirect, url_for, session
import random

app = Flask(__name__)
app.secret_key = "change-me-to-something-random"  # needed for session


# --- Multiple scenarios (MVP: simple list) ---
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
            },
            {
                "id": "q2",
                "prompt": (
                    "You pull an attack line of 200 ft of 1¾-inch with a low-pressure smooth bore nozzle. "
                    "What is your PDP to obtain correct pressure at the nozzle?"
                ),
            },
            {
                "id": "q3",
                "prompt": (
                    "Given the area of the home, approximately how much GPM should be needed to extinguish the fire?"
                ),
            },
            {
                "id": "q4",
                "prompt": (
                    "The attack mode turns defensive. You pull a blitz line of 100 ft of 3-inch to protect the exposure "
                    "on the Delta side. What would your PDP be to supply both the blitz and attack line?"
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
            {"id": "q1", "prompt": "Give your size-up (construction, occupancy, fire location cues, life hazard) and first 5 minutes plan."},
            {"id": "q2", "prompt": "Where would you place the first ladder and why? Window base vs offset, and what you’re setting up for."},
            {"id": "q3", "prompt": "Describe how you would control the flow path while still making progress on search and attack."},
            {"id": "q4", "prompt": "What are your early Mayday warning signs on interior crews, and what triggers RIT deployment in your plan?"},
        ],
    },
    {
        "title": "Attic Involvement - Wind-Influenced Fire",
        "dispatch": (
            "2315 hours. Single-story residential. Smoke pushing from eaves on the Charlie/Delta corner. "
            "Wind gusts 15–20 mph. First-due engine staffed with 3."
        ),
        "image": {
            "base": "images/house3.jpg",
            "overlay": None,
        },
        "questions": [
            {"id": "q1", "prompt": "What indicators suggest attic involvement, and how does that change your initial line placement?"},
            {"id": "q2", "prompt": "Walk through ventilation choice/timing (horizontal vs vertical) and how you prevent making things worse."},
            {"id": "q3", "prompt": "When do you call for additional resources (truck/second alarm) and what’s your reasoning?"},
            {"id": "q4", "prompt": "If a civilian is removed with suspected smoke inhalation, what’s your immediate EMS plan (airway, oxygen, CO/cyanide considerations)?"},
        ],
    },
]


def get_current_scenario():
    """Return scenario dict based on session selection (defaults to 0)."""
    idx = session.get("scenario_idx", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= len(SCENARIOS):
        idx = 0
        session["scenario_idx"] = idx
    return SCENARIOS[idx]


@app.get("/")
def index():
    scenario = get_current_scenario()
    return render_template("scenario.html", scenario=scenario, answers={}, submitted=False)


@app.get("/new")
def new_scenario():
    # Pick a new index (try not to repeat the same one)
    current = session.get("scenario_idx", 0)
    if len(SCENARIOS) == 1:
        session["scenario_idx"] = 0
    else:
        choices = [i for i in range(len(SCENARIOS)) if i != current]
        session["scenario_idx"] = random.choice(choices)
    return redirect(url_for("index"))


@app.post("/submit")
def submit():
    scenario = get_current_scenario()
    answers = {q["id"]: request.form.get(q["id"], "").strip() for q in scenario["questions"]}
    return render_template("scenario.html", scenario=scenario, answers=answers, submitted=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
