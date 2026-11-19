"""Educational screening examples, not a validated clinical triage protocol."""

import math
import re


BOUNDS = {
    "spo2": (1, 100),
    "pulse": (1, 350),
    "respiratoryRate": (1, 100),
    "systolic": (1, 300),
    "temperature": (25, 45),
}

WARNINGS = {
    "breathing difficulty",
    "unresponsive",
    "heavy bleeding",
    "stroke warning signs",
    "chest pain",
}

RULE_VERSION = "educational-adult-v1"


def validate_clinical(data):
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object.")

    symptoms = data.get("symptoms", "")
    age = data.get("age")

    if not isinstance(symptoms, str) or len(symptoms) > 1000:
        raise ValueError(
            "Symptoms must be text of at most 1000 characters."
        )

    if (
        isinstance(age, bool)
        or not isinstance(age, int)
        or not 0 <= age <= 120
    ):
        raise ValueError("Age must be an integer between 0 and 120.")

    warnings = data.get("warnings", [])

    if not isinstance(warnings, list) or any(
        not isinstance(warning, str) or warning not in WARNINGS
        for warning in warnings
    ):
        raise ValueError("Unknown warning sign.")

    reviewed = data.get("warningsReviewed", False)

    if not isinstance(reviewed, bool):
        raise ValueError("warningsReviewed must be true or false.")

    vitals = data.get("vitals", {})

    if not isinstance(vitals, dict) or set(vitals) - set(BOUNDS):
        raise ValueError("Invalid vital sign fields.")

    clean_vitals = {}

    for key, (minimum, maximum) in BOUNDS.items():
        value = vitals.get(key)

        if value is None or value == "":
            continue

        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            raise ValueError(
                f"Check {key}: expected {minimum}–{maximum}."
            )

        clean_vitals[key] = value

    return {
        "age": age,
        "symptoms": symptoms.strip(),
        "warnings": sorted(set(warnings)),
        "warningsReviewed": reviewed,
        "vitals": clean_vitals,
    }


def assess(data):
    data = validate_clinical(data)

    reasons = []
    matched = []

    def flag(level, reason):
        matched.append(level)
        reasons.append(reason)

    for warning in data["warnings"]:
        flag(1, f"Current emergency warning sign: {warning}.")

    ambiguous = False
    symptoms = data["symptoms"].lower().replace("’", "'")

    for clause in re.split(
        r"[.,;!?\n]|\bbut\b|\bhowever\b", symptoms
    ):
        clause = re.sub(
            r"\bnot breathing\b", "breathing stopped", clause
        )
        clause = re.sub(
            r"\bnot responding\b", "unresponsive", clause
        )

        emergency = re.search(
            r"\b(chest pain|chest pressure|difficulty breathing|"
            r"breathing difficulty|shortness of breath|cannot breathe|"
            r"can't breathe|breathing stopped|unconscious|unresponsive|"
            r"severe bleeding|heavy bleeding|blue lips|seizure|"
            r"facial droop|slurred speech|sudden weakness)\b",
            clause,
        )

        if re.search(
            r"\b(no|not|denies|denied|without|history|previous|"
            r"resolved|yesterday)\b",
            clause,
        ):
            ambiguous = True

            if emergency:
                flag(
                    1,
                    f"Possible emergency phrase “{emergency[0]}” "
                    "in ambiguous text: immediate staff review required.",
                )

            continue

        high = re.search(
            r"\b(fracture|broken (arm|leg|bone)|severe pain|"
            r"high fever|persistent vomiting|severe abdominal pain|"
            r"dehydration)\b",
            clause,
        )

        medium = re.search(
            r"\b(fever|vomiting|abdominal pain|dizziness|headache)\b",
            clause,
        )

        low = re.search(
            r"\b(mild cough|runny nose|minor scratch|minor cut|"
            r"mild sore throat)\b",
            clause,
        )

        if emergency:
            flag(
                1,
                f"Emergency symptom phrase: “{emergency[0]}”.",
            )
        elif high:
            flag(
                2,
                f"High-priority symptom phrase: “{high[0]}”.",
            )
        elif medium:
            flag(
                3,
                f"Symptom requiring review: “{medium[0]}”.",
            )
        elif low:
            matched.append(4)

    vitals = data["vitals"]
    adult = data["age"] >= 18

    if adult:
        rules = [
            (
                "spo2",
                lambda value: value <= 90,
                1,
                "Oxygen saturation ≤90%.",
            ),
            (
                "spo2",
                lambda value: 90 < value < 94,
                2,
                "Oxygen saturation <94%.",
            ),
            (
                "systolic",
                lambda value: value < 90,
                1,
                "Systolic blood pressure <90 mmHg.",
            ),
            (
                "systolic",
                lambda value: value >= 180,
                2,
                "Systolic blood pressure ≥180 mmHg.",
            ),
            (
                "respiratoryRate",
                lambda value: value < 8 or value > 30,
                1,
                "Respiratory rate outside 8–30 breaths/min.",
            ),
            (
                "respiratoryRate",
                lambda value: 24 < value <= 30,
                2,
                "Respiratory rate >24 breaths/min.",
            ),
            (
                "pulse",
                lambda value: value < 40 or value > 140,
                1,
                "Pulse outside 40–140 beats/min.",
            ),
            (
                "pulse",
                lambda value: 40 <= value < 50 or 120 < value <= 140,
                2,
                "Pulse outside 50–120 beats/min.",
            ),
            (
                "temperature",
                lambda value: value < 35 or value >= 40,
                2,
                "Temperature <35°C or ≥40°C.",
            ),
            (
                "temperature",
                lambda value: 38 <= value < 40,
                3,
                "Temperature ≥38°C.",
            ),
        ]

        for key, predicate, level, reason in rules:
            if key in vitals and predicate(vitals[key]):
                flag(level, reason)

    severity = min(matched, default=3)

    if (
        not adult
        or not data["warningsReviewed"]
        or ambiguous
    ) and severity > 3:
        severity = 3

    if not adult:
        reasons.append(
            "Age is under 18: adult vital thresholds are not applied. "
            "Manual assessment required."
        )

    if ambiguous:
        reasons.append(
            "Negated or historical text needs human interpretation; "
            "it is not evidence of low risk."
        )

    if len(vitals) < 5:
        reasons.append(
            "Some vital signs are unavailable; "
            "missing values are not assumed normal."
        )

    if not data["warningsReviewed"]:
        reasons.append(
            "Review current emergency warning signs before considering Low."
        )

    if not matched:
        reasons.append(
            "No supported symptom rule matched. "
            "Medium is a provisional review priority, not reassurance."
        )

    if severity == 4:
        reasons.append(
            "Only supported mild symptoms matched and warning signs "
            "were reviewed. Confirm clinically."
        )

    return {
        "severity": severity,
        "emergency": 1 in matched,
        "reasons": reasons,
        "errors": [],
        "ruleVersion": RULE_VERSION,
    }