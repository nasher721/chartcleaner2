"""Synthetic labeled chart generator — a local "final exam" for the pipeline.

asq-phi-inspired: rather than trusting one golden file, generate any number of
synthetic Epic-style notes with KNOWN planted PHI (names, MRNs, DOBs, phones,
emails, SSNs, URLs, addresses, ages > 89) and measure how much survives the
cleaning pipeline. Deterministic per (n, seed) so results are reproducible.
"""

from __future__ import annotations

import random

__all__ = ["generate", "PHI_TYPES"]

FIRST_NAMES = ["James", "Maria", "Robert", "Linda", "Michael", "Patricia",
               "David", "Jennifer", "William", "Elena", "Thomas", "Aisha",
               "Carlos", "Nina", "Henry", "Fatima", "Louis", "Greta"]
LAST_NAMES = ["Smith", "Garcia", "Miller", "Davis", "Wilson", "Moore",
              "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris",
              "Lopez", "Klein", "Novak", "Rossi"]
STREETS = ["Maple St", "Oak Ave", "Cedar Ln", "Elm Dr", "Willow Way", "Birch Rd"]
CITIES = ["Springfield", "Riverside", "Fairview", "Georgetown", "Salem"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CHIEF_COMPLAINTS = [
    ("chest pain", "aspirin 81 mg daily, obtain ECG and troponins"),
    ("shortness of breath", "nebulizer treatments, chest X-ray, monitor O2 sat"),
    ("persistent cough", "guafenesin, trial of levofloxacin if worsens"),
    ("hyperglycemia", "adjust basal insulin, diabetes education consult"),
    ("fall at home", "orthostatic vitals, PT evaluation, head CT if worse"),
    ("abdominal pain", "NPO, abdominal ultrasound, surgical consult pending"),
]
SECTIONS = ["Hospital Course", "Vitals", "Labs", "Imaging", "Medications"]

PHI_TYPES = ["name", "mrn", "dob", "phone", "email", "ssn", "url",
             "address", "age_over_89"]


def generate(n: int = 25, seed: int = 42) -> list[dict]:
    """n synthetic charts: [{name, text, phi: [{type, value}]}]."""
    # Not crypto: seeded RNG on purpose — identical (n, seed) must reproduce
    # the same charts so evaluation results are comparable run over run.
    rng = random.Random(seed)
    charts: list[dict] = []
    for i in range(n):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        mrn = f"{rng.randint(1_000_000, 9_999_999)}"
        dob = f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/{rng.randint(1930, 2005)}"
        phone = f"({rng.randint(201, 989)}) {rng.randint(200, 999)}-{rng.randint(0, 9999):04d}"
        email = f"{name.split()[0].lower()}.{name.split()[1].lower()}{rng.randint(1, 99)}@example.com"
        ssn = f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        url = f"https://portal.example-health.com/p/{rng.randint(100000, 999999)}"
        address = f"{rng.randint(10, 9999)} {rng.choice(STREETS)}, {rng.choice(CITIES)}, KS {rng.randint(10000, 99999)}"
        age = rng.randint(90, 104)

        complaint, plan = rng.choice(CHIEF_COMPLAINTS)
        sections = rng.sample(SECTIONS, k=rng.randint(2, 4))
        phi = [
            {"type": "name", "value": name},
            {"type": "mrn", "value": mrn},
            {"type": "dob", "value": dob},
            {"type": "phone", "value": phone},
            {"type": "email", "value": email},
            {"type": "ssn", "value": ssn},
            {"type": "url", "value": url},
            {"type": "address", "value": address},
            {"type": "age_over_89", "value": str(age)},
        ]

        body = [
            f"Progress Notes by Dr. {rng.choice(LAST_NAMES)}, MD on {dob[:5]}/2026",
            f"Editor: SYSTEM, (build 4.2)",
            f"Version 1 of 1",
            "",
            f"Patient Name: {name}",
            f"MRN: {mrn}",
            f"DOB: {dob}",
            f"Age: {age}",
            f"Phone: {phone}",
            f"Email: {email}",
            f"SSN: {ssn}",
            f"Portal: {url}",
            f"Address: {address}",
            "",
            f"Subjective: Patient presents with {complaint}. History reviewed.",
        ]
        for sec in sections:
            body += [f"{sec}:", f"Reviewed and documented by care team ({i + 1} of {n}).", ""]
        body += [f"Assessment & Plan: {plan}.",
                 "Electronically signed by Attending Physician"]
        charts.append({"name": f"synthetic_chart_{i + 1:03d}", "text": "\n".join(body), "phi": phi})
    return charts
