"""Synthetic labeled chart generator — a local "final exam" for the pipeline.

asq-phi-inspired: generates realistic Epic-style synthetic clinical notes with
known planted PHI spanning 18 HIPAA Safe Harbor categories (names, MRNs, DOBs,
phones, emails, SSNs, URLs, addresses, ages > 89, NPIs, DEAs, device IDs).

Includes stratified demographic cohorts (Anglo, Hispanic, African-American,
East Asian, South Asian, Middle Eastern) and planted clinical terminology
(e.g. medications and anatomical terms) to enable demographic fairness
auditing and false-positive sensitivity testing.

Deterministic per (n, seed) so evaluation results are reproducible run over run.
"""

from __future__ import annotations

import random
from typing import Any

from .clinical_identifiers import is_valid_dea, is_valid_npi

__all__ = ["generate", "PHI_TYPES", "DEMOGRAPHIC_COHORTS"]

DEMOGRAPHIC_COHORTS = {
    "anglo": {
        "first": ["James", "William", "Robert", "Jennifer", "Patricia", "Linda", "David", "Thomas"],
        "last": ["Smith", "Miller", "Davis", "Wilson", "Taylor", "Anderson", "White", "Moore"],
    },
    "hispanic": {
        "first": ["Carlos", "Maria", "Elena", "Mateo", "Sofia", "Alejandro", "Rosa", "Gabriel"],
        "last": ["Garcia", "Rodriguez", "Hernandez", "Lopez", "Martinez", "Perez", "Sanchez", "Ramirez"],
    },
    "african_american": {
        "first": ["Malik", "Aaliyah", "Jamal", "Keisha", "Darius", "Imani", "Marcus", "Ebony"],
        "last": ["Washington", "Jackson", "Williams", "Harris", "Robinson", "Jefferson", "Banks", "Booker"],
    },
    "east_asian": {
        "first": ["Wei", "Mei", "Jun", "Min-Jun", "Hao", "Yuki", "Lin", "Ji-Woo"],
        "last": ["Chen", "Wang", "Zhang", "Kim", "Tanaka", "Li", "Watanabe", "Park"],
    },
    "south_asian": {
        "first": ["Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Deepa", "Arjun", "Kavita"],
        "last": ["Patel", "Sharma", "Singh", "Gupta", "Deshmukh", "Reddy", "Iyer", "Khan"],
    },
    "middle_eastern": {
        "first": ["Fatima", "Aisha", "Tariq", "Zainab", "Omar", "Layla", "Yousef", "Mariam"],
        "last": ["Al-Mansoor", "Hassan", "Khalil", "Ibrahim", "Nasser", "Qasim", "Farah", "Habib"],
    },
}

# Flattened fallback lists for backwards compatibility
FIRST_NAMES = [name for cohort in DEMOGRAPHIC_COHORTS.values() for name in cohort["first"]]
LAST_NAMES = [name for cohort in DEMOGRAPHIC_COHORTS.values() for name in cohort["last"]]

STREETS = ["Maple St", "Oak Ave", "Cedar Ln", "Elm Dr", "Willow Way", "Birch Rd", "Highland Blvd"]
CITIES = ["Springfield", "Riverside", "Fairview", "Georgetown", "Salem", "Oakland", "Aurora"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CHIEF_COMPLAINTS = [
    ("chest pain", "aspirin 81 mg daily, Plavix 75 mg PO, obtain ECG and troponins"),
    ("shortness of breath", "nebulizer treatments with Albuterol, chest X-ray, monitor O2 sat"),
    ("persistent cough", "guaifenesin, trial of levofloxacin if worsens, sputum culture"),
    ("hyperglycemia", "adjust basal insulin glargine, diabetes education consult"),
    ("fall at home", "orthostatic vitals, PT evaluation, head CT without contrast"),
    ("abdominal pain", "NPO, Levophed on standby if MAP < 65, abdominal ultrasound of Colon"),
]

CLINICAL_TEST_TERMS = ["Plavix", "Levophed", "Albuterol", "Colon", "troponins", "insulin"]

SECTIONS = ["Hospital Course", "Vitals", "Labs", "Imaging", "Medications"]

PHI_TYPES = [
    "name", "mrn", "dob", "phone", "email", "ssn", "url",
    "address", "age_over_89", "npi", "dea", "udi",
]


def _make_valid_npi(rng: random.Random) -> str:
    """Generate a mathematically valid 10-digit NPI."""
    while True:
        prefix_digit = rng.choice(["1", "2"])
        nine_digits = prefix_digit + "".join(str(rng.randint(0, 9)) for _ in range(8))
        for check in range(10):
            candidate = nine_digits + str(check)
            if is_valid_npi(candidate):
                return candidate


def _make_valid_dea(rng: random.Random, last_name: str) -> str:
    """Generate a mathematically valid 9-char DEA registration number."""
    first_letter = rng.choice(["A", "B", "F", "M"])
    second_letter = (last_name[0] if last_name else "S").upper()
    while True:
        six_digits = [rng.randint(0, 9) for _ in range(6)]
        sum1 = six_digits[0] + six_digits[2] + six_digits[4]
        sum2 = (six_digits[1] + six_digits[3] + six_digits[5]) * 2
        check_digit = (sum1 + sum2) % 10
        dea_str = f"{first_letter}{second_letter}{''.join(str(d) for d in six_digits)}{check_digit}"
        if is_valid_dea(dea_str):
            return dea_str


def generate(n: int = 25, seed: int = 42) -> list[dict[str, Any]]:
    """Generate n synthetic charts with stratified demographic cohorts and planted PHI."""
    rng = random.Random(seed)
    cohort_keys = list(DEMOGRAPHIC_COHORTS.keys())
    charts: list[dict[str, Any]] = []

    for i in range(n):
        # Round-robin cohort assignment with random perturbation
        cohort_name = cohort_keys[i % len(cohort_keys)]
        cohort_data = DEMOGRAPHIC_COHORTS[cohort_name]

        first_name = rng.choice(cohort_data["first"])
        last_name = rng.choice(cohort_data["last"])
        name = f"{first_name} {last_name}"

        mrn = f"{rng.randint(1_000_000, 9_999_999)}"
        dob = f"{rng.randint(1, 12):02d}/{rng.randint(1, 28):02d}/{rng.randint(1930, 2005)}"
        phone = f"({rng.randint(201, 989)}) {rng.randint(200, 999)}-{rng.randint(0, 9999):04d}"
        clean_first = first_name.lower().replace("-", "").replace("'", "")
        clean_last = last_name.lower().replace("-", "").replace("'", "").replace(" ", "")
        email = f"{clean_first}.{clean_last}{rng.randint(1, 99)}@example.com"
        ssn = f"{rng.randint(100, 899)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}"
        url = f"https://portal.example-health.com/p/{rng.randint(100000, 999999)}"
        address = f"{rng.randint(10, 9999)} {rng.choice(STREETS)}, {rng.choice(CITIES)}, KS {rng.randint(10000, 99999)}"
        age = rng.randint(90, 104)

        provider_last = rng.choice(LAST_NAMES)
        npi = _make_valid_npi(rng)
        dea = _make_valid_dea(rng, provider_last)
        udi = f"(01){rng.randint(10000000000000, 99999999999999)}(17)281231(10)LOT{rng.randint(1000, 9999)}"

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
            {"type": "npi", "value": npi},
            {"type": "dea", "value": dea},
            {"type": "udi", "value": udi},
        ]

        # Clinical terms planted in text that should NOT be redacted
        planted_clinical = [t for t in CLINICAL_TEST_TERMS if t in complaint or t in plan]

        body = [
            f"Progress Notes by Dr. {provider_last}, MD (NPI: {npi}, DEA: {dea}) on {dob[:5]}/2026",
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
            f"Device Implant: UDI {udi}",
            "",
            f"Subjective: Patient presents with {complaint}. History reviewed.",
        ]
        for sec in sections:
            body += [f"{sec}:", f"Reviewed and documented by care team ({i + 1} of {n}).", ""]
        body += [
            f"Assessment & Plan: {plan}.",
            "Electronically signed by Attending Physician",
        ]

        charts.append({
            "name": f"synthetic_chart_{i + 1:03d}",
            "text": "\n".join(body),
            "phi": phi,
            "cohort": cohort_name,
            "clinical_terms": planted_clinical,
        })

    return charts
