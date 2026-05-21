from flask import Flask, render_template, jsonify, request
import random
import time
import json
from openai import OpenAI

app = Flask(__name__)

# 🔑 API KEY
client = OpenAI(api_key="")

# ── Rate limiting ──────────────────────────────────────────
last_api_call_time = 0
RATE_LIMIT_SECONDS = 3

# ── Dedup ──────────────────────────────────────────────────
asked_questions: set = set()
MAX_HISTORY = 50

# ── Per-session ability tier storage ──────────────────────
session_ability: dict = {}   # session_id → "BEGINNER" | "INTERMEDIATE" | "ADVANCED"

# ── Per-session performance tracking ──────────────────────
session_performance: dict = {}  # session_id → { subject: { correct: int, wrong: int } }

# ── Adaptive confidence-boost state ───────────────────────
session_consecutive_wrong: dict = {}   # (session_id, subject) → int
session_boost_mode: dict = {}          # (session_id, subject) → { active: bool, boost_correct: int }

BOOST_TRIGGER_WRONG   = 3
BOOST_EXIT_CORRECT    = 2
UPGRADE_TRIGGER_CORRECT = 3
session_consecutive_correct: dict = {}
session_last_difficulty:     dict = {}


# ── Difficulty matrix: (stress, ability) → difficulty ──────
DIFFICULTY_MATRIX = {
    ("LOW",      "BEGINNER"):     "MEDIUM",
    ("LOW",      "INTERMEDIATE"): "HARD",
    ("LOW",      "ADVANCED"):     "VERY HARD",
    ("MODERATE", "BEGINNER"):     "EASY",
    ("MODERATE", "INTERMEDIATE"): "MEDIUM",
    ("MODERATE", "ADVANCED"):     "HARD",
    ("HIGH",     "BEGINNER"):     "VERY EASY",
    ("HIGH",     "INTERMEDIATE"): "EASY",
    ("HIGH",     "ADVANCED"):     "MEDIUM",
}

DIFFICULTY_DESC = {
    "VERY EASY": "very basic recall or definition question — fundamental concept, single-step",
    "EASY":      "simple single-concept application question with straightforward calculation",
    "MEDIUM":    "moderate multi-concept question requiring some reasoning or 2-3 step calculation",
    "HARD":      "challenging derivation or multi-step analytical problem testing deep understanding",
    "VERY HARD": "advanced integration of multiple concepts, derivation-heavy or graph-based analysis",
}

# ── Exam metadata ──────────────────────────────────────────
EXAM_SUBJECTS = {
    "JEE":    ["Physics", "Chemistry", "Mathematics"],
    "NEET":   ["Physics", "Chemistry", "Biology"],
    "KCET":   ["Physics", "Chemistry", "Mathematics"],
    "COMEDK": ["Physics", "Chemistry", "Mathematics"],
}

EXAM_PATTERNS = {
    "JEE": {
        "full_name": "JEE (Joint Entrance Examination)",
        "style": "JEE Advanced/Mains style — conceptually deep, formula-intensive, tricky options that test fundamental understanding. Questions often have multiple valid-looking distractors. May involve multi-concept integration.",
        "Physics":     "mechanics (kinematics, NLM, WEP, rotational motion), waves and SHM, thermodynamics, electrostatics, current electricity, magnetism, electromagnetic induction, optics (ray and wave), modern physics, semiconductors",
        "Chemistry":   "physical chemistry (mole concept, thermodynamics, equilibrium, kinetics, electrochemistry, solutions), organic chemistry (GOC, IUPAC, named reactions: Aldol, Cannizzaro, Sandmeyer, reaction mechanisms), inorganic (periodic trends, p-block, d-block, coordination compounds)",
        "Mathematics": "calculus (limits, L'Hopital, differentiation, applications, integration, definite integrals, differential equations), algebra (complex numbers, quadratic equations, sequences, permutation/combination, binomial theorem, matrices/determinants), coordinate geometry (straight lines, circles, parabola, ellipse, hyperbola), vectors and 3D geometry, probability and statistics",
    },
    "NEET": {
        "full_name": "NEET (National Eligibility cum Entrance Test)",
        "style": "NEET style — strictly NCERT Class 11 & 12 based. Questions test precise factual recall, diagram-based understanding, and NCERT definitions. Single-concept, clear distractors. Biology has factual assertion questions.",
        "Physics":   "physical world and measurement, kinematics, laws of motion, work-energy-power, rotational motion, gravitation, properties of matter, thermodynamics, kinetic theory, oscillations and waves, electrostatics, current electricity, magnetic effects, EMI, alternating current, optics, dual nature, atoms and nuclei, electronic devices",
        "Chemistry": "mole concept and stoichiometry, structure of atom, periodic classification, chemical bonding and molecular structure, states of matter, thermodynamics, equilibrium, redox reactions, hydrogen and s-block, p-block elements, organic chemistry basics (IUPAC, functional groups, isomerism), hydrocarbons, environmental chemistry, polymers, biomolecules, chemistry in everyday life",
        "Biology":   "cell: the unit of life (cell organelles, cell division — mitosis/meiosis), biomolecules (enzymes, proteins, nucleic acids), plant kingdom and animal kingdom taxonomy, morphology and anatomy of plants and animals, transport in plants, mineral nutrition, photosynthesis, respiration, plant growth, digestion and absorption, breathing, body fluids and circulation, excretion, locomotion, nervous and chemical coordination, reproduction in organisms, sexual reproduction in plants and humans, genetics (Mendel's laws, DNA structure/replication, transcription, translation), evolution, human health and disease, ecology (ecosystem, biodiversity, environmental issues), biotechnology",
    },
    "KCET": {
        "full_name": "KCET (Karnataka Common Entrance Test)",
        "style": "KCET style — Karnataka 1st and 2nd PUC syllabus based. Moderate difficulty, direct formula application. Questions are more straightforward than JEE; focus on concept clarity and accurate formula use. Numerical questions use simple round numbers.",
        "Physics":     "Karnataka PUC: units and measurement, kinematics, laws of motion, work-energy-power, gravitation, mechanical properties, thermal properties, thermodynamics, kinetic theory, oscillations, waves, ray optics, wave optics, electrostatics, current electricity, magnetic effects of current, magnetism, EMI and AC, semiconductor electronics",
        "Chemistry":   "Karnataka PUC: atomic structure, classification of elements, chemical bonding and molecular structure, states of matter, thermodynamics, equilibrium, hydrogen, s-block, p-block (Groups 13–18), d and f block elements, coordination compounds, haloalkanes and haloarenes, alcohols/phenols/ethers, aldehydes/ketones/carboxylic acids, amines, biomolecules and polymers, chemistry in everyday life, environmental chemistry",
        "Mathematics": "Karnataka PUC: sets, relations and functions, trigonometry (inverse functions, general solution), algebra (principle of mathematical induction, complex numbers, linear inequalities, permutations/combinations, binomial theorem, sequences), coordinate geometry (straight lines, conic sections), calculus (limits and derivatives, continuity and differentiability, applications, integration, differential equations), vectors and 3D geometry, linear programming, probability",
    },
    "COMEDK": {
        "full_name": "COMEDK (Consortium of Medical Engineering and Dental Colleges of Karnataka)",
        "style": "COMEDK style — similar to KCET but slightly more application-oriented with numerical problems. Karnataka PUC based but questions may have one extra reasoning step compared to KCET. Tests understanding alongside recall.",
        "Physics":     "Karnataka PUC Physics: physical world, units/dimensions, kinematics, laws of motion, work/energy/power, system of particles, gravitation, mechanical properties of solids and fluids, thermal properties, thermodynamics, kinetic theory, oscillations, waves, electric charges, Gauss's law, potential and capacitance, current electricity (Ohm's law, Kirchhoff's laws, Wheatstone bridge), magnetic effects, magnetism, electromagnetic induction, AC circuits, electromagnetic waves, ray optics, wave optics, dual nature, atoms, nuclei, semiconductor devices",
        "Chemistry":   "Karnataka PUC Chemistry: atomic structure (Bohr model, quantum numbers, aufbau), periodic table and properties, chemical bonding (VSEPR, hybridization, MO theory), gaseous and liquid state, chemical thermodynamics, chemical equilibrium (Kc, Kp, Le Chatelier), ionic equilibrium, redox, electrochemistry, chemical kinetics, surface chemistry, p-block, d-block, coordination chemistry, organic reactions and mechanisms, carbonyl compounds, amines, polymers and biomolecules",
        "Mathematics": "Karnataka PUC Mathematics: sets and relations, functions, trigonometric functions, inverse trigonometric functions, matrices and determinants, continuity and differentiability, application of derivatives (maxima/minima, tangent-normal), integration (substitution, by parts, definite integrals), area under curves, differential equations, vectors (dot/cross product), 3D geometry (lines and planes), linear programming, probability (Bayes theorem, distributions)",
    },
}

# ── Calibration questions (mixed, no GPT) ──────────────────
CALIBRATION_QUESTIONS = [
    {"question": "What is the SI unit of electric charge?",
     "options": ["Coulomb", "Ampere", "Volt", "Farad"],
     "answer": "Coulomb", "difficulty": "EASY"},
    {"question": "Newton's Second Law of Motion states that force equals:",
     "options": ["Mass × Acceleration", "Mass × Velocity", "Mass / Acceleration", "Velocity / Time"],
     "answer": "Mass × Acceleration", "difficulty": "EASY"},
    {"question": "The pH of a neutral solution at 25°C is:",
     "options": ["7", "0", "14", "1"],
     "answer": "7", "difficulty": "EASY"},
    {"question": "A body moves with uniform circular motion. Which quantity remains constant?",
     "options": ["Speed", "Velocity", "Acceleration", "Kinetic energy"],
     "answer": "Speed", "difficulty": "MEDIUM"},
    {"question": "The hybridization of carbon in benzene (C₆H₆) is:",
     "options": ["sp²", "sp³", "sp", "sp³d"],
     "answer": "sp²", "difficulty": "MEDIUM"},
    {"question": "The derivative of ln(x) with respect to x is:",
     "options": ["1/x", "x", "e^x", "-1/x²"],
     "answer": "1/x", "difficulty": "MEDIUM"},
    {"question": "In photoelectric effect, increasing the intensity of light increases the:",
     "options": ["Number of emitted electrons", "Maximum kinetic energy", "Threshold frequency", "Work function"],
     "answer": "Number of emitted electrons", "difficulty": "MEDIUM"},
    {"question": "A projectile is fired at 60° to the horizontal with speed u. Its range on a horizontal plane is:",
     "options": ["u²√3/(2g)", "u²/g", "u²√3/g", "u²/(2g)"],
     "answer": "u²√3/(2g)", "difficulty": "HARD"},
    {"question": "For the reaction N₂ + 3H₂ ⇌ 2NH₃, the equilibrium shifts towards products when:",
     "options": ["Pressure is increased", "Pressure is decreased", "Temperature is increased", "An inert gas is added at constant volume"],
     "answer": "Pressure is increased", "difficulty": "HARD"},
    {"question": "The area enclosed between the curves y = x² and y = x is:",
     "options": ["1/6", "1/3", "1/2", "1/4"],
     "answer": "1/6", "difficulty": "HARD"},
]

# ── Fallback MCQs keyed by difficulty ──────────────────────
fallback_mcqs = {
    "VERY EASY": [
        {"question": "The acceleration due to gravity on Earth's surface is approximately:",
         "options": ["9.8 m/s²", "8.9 m/s²", "10.8 m/s²", "6.67 m/s²"], "answer": "9.8 m/s²"},
        {"question": "Which of the following is a base?",
         "options": ["NaOH", "HCl", "H₂SO₄", "HNO₃"], "answer": "NaOH"},
        {"question": "The value of sin(90°) is:",
         "options": ["1", "0", "-1", "√2/2"], "answer": "1"},
    ],
    "EASY": [
        {"question": "If R = 10Ω and I = 2A, the voltage V is:",
         "options": ["20 V", "5 V", "12 V", "0.2 V"], "answer": "20 V"},
        {"question": "The atomic number of Carbon is:",
         "options": ["6", "12", "8", "14"], "answer": "6"},
        {"question": "The derivative of x³ with respect to x is:",
         "options": ["3x²", "x²", "3x", "2x³"], "answer": "3x²"},
    ],
    "MEDIUM": [
        {"question": "A body of mass 2 kg moves with velocity 3 m/s. Its kinetic energy is:",
         "options": ["9 J", "6 J", "3 J", "18 J"], "answer": "9 J"},
        {"question": "Which quantum number determines the shape of an orbital?",
         "options": ["Azimuthal quantum number (l)", "Principal quantum number (n)",
                     "Magnetic quantum number (mₗ)", "Spin quantum number (ms)"],
         "answer": "Azimuthal quantum number (l)"},
        {"question": "∫sin(x) dx equals:",
         "options": ["-cos(x) + C", "cos(x) + C", "tan(x) + C", "-sin(x) + C"],
         "answer": "-cos(x) + C"},
    ],
    "HARD": [
        {"question": "Two capacitors C₁ = 4μF and C₂ = 6μF connected in series. Equivalent capacitance:",
         "options": ["2.4 μF", "10 μF", "1.2 μF", "5 μF"], "answer": "2.4 μF"},
        {"question": "For reaction A → B with rate r = k[A]², if [A] doubles, the rate becomes:",
         "options": ["4 times", "2 times", "8 times", "Unchanged"], "answer": "4 times"},
        {"question": "Equation of the tangent to y = x² at x = 3 is:",
         "options": ["y = 6x - 9", "y = 3x - 9", "y = 6x + 9", "y = 6x"], "answer": "y = 6x - 9"},
    ],
    "VERY HARD": [
        {"question": "A charged particle moves perpendicular to magnetic field B. Its time period is:",
         "options": ["2πm/qB", "qB/2πm", "2πqB/m", "mv/qB"], "answer": "2πm/qB"},
        {"question": "The van't Hoff factor for K₂SO₄ (complete dissociation) is:",
         "options": ["3", "2", "4", "1"], "answer": "3"},
    ],
}


# ── MCQ Generator ───────────────────────────────────────────
def generate_mcq(stress: str, subject: str, ability: str = "INTERMEDIATE",
                 exam: str = "JEE", session_id: str = "default",
                 forced_difficulty: str = None) -> dict:
    global last_api_call_time, asked_questions

    now = time.time()
    difficulty      = forced_difficulty if forced_difficulty else DIFFICULTY_MATRIX.get((stress, ability), "MEDIUM")
    difficulty_desc = DIFFICULTY_DESC[difficulty]

    if now - last_api_call_time < RATE_LIMIT_SECONDS:
        fb = random.choice(fallback_mcqs.get(difficulty, fallback_mcqs["MEDIUM"]))
        return {**fb, "difficulty": difficulty}

    exam_data   = EXAM_PATTERNS.get(exam, EXAM_PATTERNS["JEE"])
    exam_style  = exam_data["style"]
    topic_hints = exam_data.get(subject, f"core {subject} topics for {exam}")

    avoid_str = ""
    if asked_questions:
        avoid_list = "\n".join(f"- {q}" for q in list(asked_questions)[-15:])
        avoid_str  = f"\nDo NOT repeat or closely paraphrase:\n{avoid_list}\n"

    perf    = session_performance.get(session_id, {}).get(subject, {})
    perf_str = ""
    if perf:
        total   = perf.get("correct", 0) + perf.get("wrong", 0)
        correct = perf.get("correct", 0)
        if total > 0:
            pct = round(correct / total * 100)
            perf_str = (
                f"\nStudent's {subject} performance this session: "
                f"{correct}/{total} correct ({pct}%). "
            )
            if pct < 40:
                perf_str += "They are struggling — test fundamentals within the difficulty level."
            elif pct > 75:
                perf_str += "They are excelling — try a trickier variant within the difficulty level."

    prompt = f"""You are an expert question setter for {exam_data['full_name']}.

Exam: {exam}
Subject: {subject}
Syllabus topics: {topic_hints}
Question style: {exam_style}

Difficulty: {difficulty} — {difficulty_desc}
Student ability tier: {ability}
{perf_str}{avoid_str}
STRICT RULES:
1. The question must be strictly about {subject} as tested in {exam} — no cross-subject mixing.
2. Follow {exam} style and syllabus precisely.
3. Exactly 4 answer options.
4. Exactly ONE correct answer appearing verbatim in the options list.
5. Distractors must be plausible (not obviously wrong).
6. HARD/VERY HARD questions should require numerical work or multi-step reasoning.
7. No trivial or meta questions.
8. Plain text only — no markdown, no LaTeX, no explanation.

Return ONLY valid JSON:
{{
  "question": "string",
  "options": ["opt1", "opt2", "opt3", "opt4"],
  "answer": "one exact option string"
}}"""

    subject_keywords = {
        "Physics":     ["force","energy","mass","velocity","acceleration","wave","electric",
                        "magnetic","field","quantum","photon","momentum","thermodynamics",
                        "optics","gravity","charge","flux","current","resistance","capacitor",
                        "inductor","motion","work","power","nucleus","wavelength","frequency"],
        "Chemistry":   ["atom","molecule","bond","reaction","element","compound","acid","base",
                        "pH","orbital","electron","oxidation","reduction","equilibrium",
                        "enthalpy","entropy","organic","hybridization","periodic","electrolyte",
                        "catalyst","polymer","ester","aldehyde","ketone","amine","alkane"],
        "Mathematics": ["equation","matrix","integral","derivative","function","vector",
                        "probability","theorem","series","polynomial","limit","continuity",
                        "geometry","trigonometry","complex","determinant","coordinate",
                        "angle","slope","area","differential","logarithm","binomial"],
        "Biology":     ["cell","gene","dna","rna","protein","enzyme","organism","evolution",
                        "ecology","photosynthesis","respiration","tissue","organ","chromosome",
                        "mitosis","meiosis","hormone","neuron","ecosystem","reproduction",
                        "taxonomy","biomolecule","heredity","mutation","reflex","digestion"],
    }

    for attempt in range(5):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": (
                        f"You are an expert {exam} question setter for {subject}. "
                        f"Output only valid JSON. Never include questions outside {subject}."
                    )},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.65
            )

            raw = response.choices[0].message.content.strip()
            raw = raw.replace("```json", "").replace("```", "").strip()
            mcq = json.loads(raw)

            if (
                "question" not in mcq
                or "options" not in mcq
                or "answer"  not in mcq
                or len(mcq["options"]) != 4
                or mcq["answer"] not in mcq["options"]
            ):
                print(f"⚠️  Attempt {attempt+1}: invalid structure, retrying…")
                continue

            keywords = subject_keywords.get(subject, [])
            if keywords:
                combined = (mcq["question"] + " ".join(mcq["options"])).lower()
                if not any(kw in combined for kw in keywords):
                    print(f"⚠️  Attempt {attempt+1}: off-topic for {subject}, retrying…")
                    continue

            if mcq["question"] in asked_questions:
                print(f"⚠️  Attempt {attempt+1}: duplicate, retrying…")
                continue

            asked_questions.add(mcq["question"])
            if len(asked_questions) > MAX_HISTORY:
                asked_questions.discard(next(iter(asked_questions)))

            last_api_call_time = now
            return {**mcq, "difficulty": difficulty}

        except json.JSONDecodeError as e:
            print(f"⚠️  Attempt {attempt+1}: JSON error — {e}")
        except Exception as e:
            print(f"⚠️  Attempt {attempt+1}: API error — {e}")
            break

    print("⚠️  Using fallback MCQ")
    fb = random.choice(fallback_mcqs.get(difficulty, fallback_mcqs["MEDIUM"]))
    return {**fb, "difficulty": difficulty}


# ── Routes ──────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/get_exam_subjects")
def get_exam_subjects():
    exam = request.args.get("exam", "JEE").strip().upper()
    subjects = EXAM_SUBJECTS.get(exam, EXAM_SUBJECTS["JEE"])
    return jsonify({"exam": exam, "subjects": subjects})


@app.route("/get_calibration_questions")
def get_calibration_questions():
    questions = []
    for q in CALIBRATION_QUESTIONS:
        opts = q["options"][:]
        random.shuffle(opts)
        questions.append({
            "question":   q["question"],
            "options":    opts,
            "correct":    q["answer"],
            "difficulty": q["difficulty"],
        })
    return jsonify({"questions": questions})


@app.route("/set_ability_tier", methods=["POST"])
def set_ability_tier():
    data       = request.get_json(silent=True) or {}
    score      = int(data.get("score", 0))
    session_id = data.get("session_id", "default")

    if score <= 3:
        tier = "BEGINNER"
    elif score <= 7:
        tier = "INTERMEDIATE"
    else:
        tier = "ADVANCED"

    session_ability[session_id] = tier
    print(f"✅ Session '{session_id}' scored {score}/10 → {tier}")
    return jsonify({"tier": tier})


@app.route("/get_question")
def get_question():
    subject    = request.args.get("subject",    "Physics").strip()
    stress     = request.args.get("stress",     "MODERATE").strip().upper()
    exam       = request.args.get("exam",       "JEE").strip().upper()
    session_id = request.args.get("session_id", "default")

    if stress not in ("LOW", "MODERATE", "HIGH"):
        stress = "MODERATE"
    if exam not in EXAM_SUBJECTS:
        exam = "JEE"

    ability    = session_ability.get(session_id, "INTERMEDIATE")
    difficulty = DIFFICULTY_MATRIX.get((stress, ability), "MEDIUM")

    boost_key  = (session_id, subject)
    boost_info = session_boost_mode.get(boost_key, {"active": False, "boost_correct": 0})
    in_boost   = boost_info["active"]

    if (
        not in_boost
        and session_consecutive_wrong.get(boost_key, 0) >= BOOST_TRIGGER_WRONG
    ):
        in_boost   = True
        boost_info = {"active": True, "boost_correct": 0}
        session_boost_mode[boost_key]      = boost_info
        session_consecutive_wrong[boost_key] = 0
        print(f"🔄 Boost mode ACTIVATED for session='{session_id}' subject='{subject}' "
              f"(ability={ability}, stress={stress})")

    if in_boost:
        difficulty       = "EASY"
        override_ability = "BEGINNER"
        print(f"💪 Boost mode active — serving EASY question for session='{session_id}' "
              f"subject='{subject}' (boost_correct={boost_info['boost_correct']}/{BOOST_EXIT_CORRECT})")
    else:
        override_ability = ability

        STEP_UP = {"EASY": "MEDIUM", "MEDIUM": "HARD"}
        if (
            difficulty in STEP_UP
            and session_consecutive_correct.get(boost_key, 0) >= UPGRADE_TRIGGER_CORRECT
        ):
            difficulty = STEP_UP[difficulty]
            session_consecutive_correct[boost_key] = 0
            print(f"🚀 Upgrade triggered — stepping up to {difficulty} "
                  f"for session='{session_id}' subject='{subject}'")

    session_last_difficulty[boost_key] = difficulty

    mcq = generate_mcq(stress, subject, override_ability, exam, session_id, forced_difficulty=difficulty if (in_boost or difficulty != DIFFICULTY_MATRIX.get((stress, ability), "MEDIUM")) else None)

    return jsonify({
        "question":                mcq["question"],
        "options":                 mcq["options"],
        "correct":                 mcq["answer"],
        "difficulty":              mcq.get("difficulty", difficulty),
        "ability":                 ability,
        "exam":                    exam,
        "boost_mode":              in_boost,
        "boost_progress":          f"{boost_info['boost_correct']}/{BOOST_EXIT_CORRECT}" if in_boost else None,
        "consecutive_wrong":       session_consecutive_wrong.get(boost_key, 0),
        "consecutive_correct":     session_consecutive_correct.get(boost_key, 0),
    })


@app.route("/record_performance", methods=["POST"])
def record_performance():
    data       = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    subject    = data.get("subject",    "Physics")
    is_correct = bool(data.get("correct", data.get("is_correct", False)))
    stress     = data.get("stress", "MODERATE").strip().upper()

    if session_id not in session_performance:
        session_performance[session_id] = {}
    if subject not in session_performance[session_id]:
        session_performance[session_id][subject] = {"correct": 0, "wrong": 0}

    key = "correct" if is_correct else "wrong"
    session_performance[session_id][subject][key] += 1

    ability   = session_ability.get(session_id, "INTERMEDIATE")
    boost_key = (session_id, subject)
    boost_info = session_boost_mode.get(boost_key, {"active": False, "boost_correct": 0})

    if boost_info["active"]:
        if is_correct:
            boost_info["boost_correct"] += 1
            print(f"💪 Boost progress: {boost_info['boost_correct']}/{BOOST_EXIT_CORRECT} "
                  f"for session='{session_id}' subject='{subject}'")
            if boost_info["boost_correct"] >= BOOST_EXIT_CORRECT:
                boost_info["active"] = False
                session_consecutive_wrong[boost_key] = 0
                print(f"✅ Boost mode DEACTIVATED — resuming normal difficulty "
                      f"for session='{session_id}' subject='{subject}'")
        session_boost_mode[boost_key] = boost_info
    else:
        last_diff = session_last_difficulty.get(boost_key, "MEDIUM")
        if is_correct:
            session_consecutive_wrong[boost_key] = 0
            if last_diff in ("EASY", "MEDIUM"):
                session_consecutive_correct[boost_key] = \
                    session_consecutive_correct.get(boost_key, 0) + 1
                cc = session_consecutive_correct[boost_key]
                print(f"✔️  Consecutive correct ({last_diff}): {cc}/{UPGRADE_TRIGGER_CORRECT} "
                      f"for session='{session_id}' subject='{subject}'")
            else:
                session_consecutive_correct[boost_key] = 0
        else:
            session_consecutive_wrong[boost_key] = \
                session_consecutive_wrong.get(boost_key, 0) + 1
            session_consecutive_correct[boost_key] = 0
            consec = session_consecutive_wrong[boost_key]
            print(f"⚠️  Consecutive wrong: {consec}/{BOOST_TRIGGER_WRONG} "
                  f"for session='{session_id}' subject='{subject}' "
                  f"(ability={ability}, stress={stress})")

    boost_info_out = session_boost_mode.get(boost_key, {"active": False, "boost_correct": 0})
    return jsonify({
        "status":              "ok",
        "boost_mode":          boost_info_out["active"],
        "boost_progress":      f"{boost_info_out['boost_correct']}/{BOOST_EXIT_CORRECT}",
        "consecutive_wrong":   session_consecutive_wrong.get(boost_key, 0),
        "consecutive_correct": session_consecutive_correct.get(boost_key, 0),
    })


@app.route("/record_answer", methods=["POST"])
def record_answer():
    data       = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "default")
    subject    = data.get("subject",    "Physics")
    is_correct = bool(data.get("correct", False))
    stress     = data.get("stress", "MODERATE").strip().upper()

    if session_id not in session_performance:
        session_performance[session_id] = {}
    if subject not in session_performance[session_id]:
        session_performance[session_id][subject] = {"correct": 0, "wrong": 0}

    key = "correct" if is_correct else "wrong"
    session_performance[session_id][subject][key] += 1

    ability   = session_ability.get(session_id, "INTERMEDIATE")
    boost_key = (session_id, subject)
    boost_info = session_boost_mode.get(boost_key, {"active": False, "boost_correct": 0})

    if boost_info["active"]:
        if is_correct:
            boost_info["boost_correct"] += 1
            print(f"💪 Boost progress: {boost_info['boost_correct']}/{BOOST_EXIT_CORRECT} "
                  f"for session='{session_id}' subject='{subject}'")
            if boost_info["boost_correct"] >= BOOST_EXIT_CORRECT:
                boost_info["active"] = False
                session_consecutive_wrong[boost_key] = 0
                print(f"✅ Boost mode DEACTIVATED — resuming normal difficulty "
                      f"for session='{session_id}' subject='{subject}'")
        session_boost_mode[boost_key] = boost_info
    else:
        last_diff = session_last_difficulty.get(boost_key, "MEDIUM")
        if is_correct:
            session_consecutive_wrong[boost_key] = 0
            if last_diff in ("EASY", "MEDIUM"):
                session_consecutive_correct[boost_key] = \
                    session_consecutive_correct.get(boost_key, 0) + 1
                cc = session_consecutive_correct[boost_key]
                print(f"✔️  Consecutive correct ({last_diff}): {cc}/{UPGRADE_TRIGGER_CORRECT} "
                      f"for session='{session_id}' subject='{subject}'")
            else:
                session_consecutive_correct[boost_key] = 0
        else:
            session_consecutive_wrong[boost_key] = \
                session_consecutive_wrong.get(boost_key, 0) + 1
            session_consecutive_correct[boost_key] = 0
            consec = session_consecutive_wrong[boost_key]
            print(f"⚠️  Consecutive wrong: {consec}/{BOOST_TRIGGER_WRONG} "
                  f"for session='{session_id}' subject='{subject}' "
                  f"(ability={ability}, stress={stress})")

    boost_info_out = session_boost_mode.get(boost_key, {"active": False, "boost_correct": 0})
    return jsonify({
        "status":              "ok",
        "boost_mode":          boost_info_out["active"],
        "boost_progress":      f"{boost_info_out['boost_correct']}/{BOOST_EXIT_CORRECT}",
        "consecutive_wrong":   session_consecutive_wrong.get(boost_key, 0),
        "consecutive_correct": session_consecutive_correct.get(boost_key, 0),
    })


@app.route("/explain_answer", methods=["POST"])
def explain_answer():
    data     = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    correct  = data.get("correct",  "").strip()
    selected = data.get("selected", "").strip()
    exam     = data.get("exam",     "JEE").strip()
    subject  = data.get("subject",  "Physics").strip()

    if not question or not correct:
        return jsonify({"explanation": "Correct answer is shown above."})

    prompt = f"""Exam: {exam} | Subject: {subject}
Question: {question}
Student chose: {selected}
Correct answer: {correct}

Give a concise, student-friendly explanation (2–3 lines) of why the correct answer is right
and (if different) why the student's choice was wrong. Keep it relevant to {exam} level.
No markdown. Plain text only."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"You are a concise {exam} tutor. Plain text only."},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.3
        )
        return jsonify({"explanation": response.choices[0].message.content.strip()})
    except Exception as e:
        print(f"⚠️  Explanation error: {e}")
        return jsonify({"explanation": f"The correct answer is '{correct}'. Review this concept in your {exam} notes."})


if __name__ == "__main__":
    app.run(debug=True, port=5050)