"""Clinical gazetteer and false-positive mitigation whitelist.

Generic NLP/NER models (e.g. spaCy's en_core_web_sm, standard Presidio) often
misclassify medical entities as person names when capitalized, such as:
- Medications: Plavix, Levophed, Tamar, Grace, Jordan, Maxipime, Keflex, Haldol...
- Anatomical terms: Colon, Appendix, Ileum, Cecum, Spleen, Cornea, Femur, Radius...
- Common abbreviations / clinical units / lab markers: Troponin, Procalcitonin...

This module provides a fast, normalized lookup set that protects clinical
vocabulary from accidental redaction while preserving real patient names.
"""

from __future__ import annotations

import re
from typing import FrozenSet

# Top medications, brand names, and active ingredients commonly misidentified as names
_COMMON_MEDICATIONS = {
    # Cardiovascular & Antithrombotic
    "plavix", "clopidogrel", "brilinta", "ticagrelor", "effient", "prasugrel",
    "eliquis", "apixaban", "xarelto", "rivaroxaban", "pradaxa", "dabigatran",
    "coumadin", "warfarin", "heparin", "lovenox", "enoxaparin", "arixtra",
    "aspirin", "lisinopril", "enalapril", "ramipril", "losartan", "valsartan",
    "amlodipine", "nifedipine", "diltiazem", "cardizem", "verapamil",
    "metoprolol", "lopressor", "toprol", "atenolol", "carvedilol", "coreg",
    "labetalol", "propranolol", "hydralazine", "clonidine", "norvasc",
    "atorvastatin", "lipitor", "rosuvastatin", "crestor", "simvastatin", "zocor",
    "lasix", "furosemide", "bumex", "bumetanide", "spironolactone", "aldactone",
    "hydrochlorothiazide", "hctz", "chlorthalidone", "digoxin", "lanoxin",
    "amiodarone", "cordarone", "flecainide", "sotalol", "entresto",
    # Pressors, Inotropes & Critical Care
    "levophed", "norepinephrine", "epinephrine", "vasopressin", "phenylephrine",
    "dopamine", "dobutamine", "milrinone", "propofol", "diprivan", "precedex",
    "dexmedetomidine", "versed", "midazolam", "fentanyl", "dilaudid", "hydromorphone",
    "morphine", "ketamine", "rocuronium", "vecuronium", "cisatracurium",
    # Antibiotics & Antimicrobials
    "vancomycin", "vancocin", "zosyn", "piperacillin", "tazobactam", "ceftriaxone",
    "rocephin", "cefepime", "maxipime", "cefdinir", "keflex", "cephalexin",
    "ancef", "cefazolin", "meropenem", "merrem", "ertapenem", "invanz",
    "aztreonam", "ciprofloxacin", "cipro", "levofloxacin", "levaquin",
    "moxifloxacin", "avelox", "azithromycin", "zithromax", "clarithromycin",
    "amoxicillin", "augmentin", "ampicillin", "unasyn", "doxycycline",
    "vibramycin", "bactrim", "septra", "sulfamethoxazole", "trimethoprim",
    "flagyl", "metronidazole", "zyvox", "linezolid", "cubicin", "daptomycin",
    "gentamicin", "tobramycin", "amikacin", "diflucan", "fluconazole",
    # Endocrine & Diabetes
    "metformin", "glucophage", "glipizide", "glucotrol", "glimepiride", "amaryl",
    "januvia", "sitagliptin", "jardiance", "empagliflozin", "farxiga", "dapagliflozin",
    "ozempic", "wegovy", "semaglutide", "mounjaro", "zepbound", "tirzepatide",
    "trulicity", "dulaglutide", "lantus", "glargine", "levemir", "detemir",
    "humalog", "lispro", "novolog", "aspart", "tresiba", "degludec",
    "synthroid", "levothyroxine", "cytomel", "liothyronine", "tapazole", "methimazole",
    # GI, Pulmonary & Analgesia
    "protonix", "pantoprazole", "prilosec", "omeprazole", "nexium", "esomeprazole",
    "pepcid", "famotidine", "reglan", "metoclopramide", "zofran", "ondansetron",
    "phenergan", "promethazine", "compazine", "prochlorperazine",
    "albuterol", "ventolin", "proair", "atrovent", "ipratropium", "duoneb",
    "spiriva", "tiotropium", "symbicort", "advair", "flovent", "pulmicort",
    "singulair", "montelukast", "prednisone", "solumedrol", "methylprednisolone",
    "decadron", "dexamethasone", "hydrocortisone",
    "tylenol", "acetaminophen", "motrin", "advil", "ibuprofen", "aleve", "naproxen",
    "toradol", "ketorolac", "celebrex", "celecoxib", "tramadol", "ultram",
    "oxycodone", "oxycontin", "percocet", "norco", "vicodin", "gabapentin",
    "neurontin", "lyrica", "pregabalin", "flexeril", "cyclobenzaprine",
    # Psych & Neurologic
    "haldol", "haloperidol", "ativan", "lorazepam", "xanax", "alprazolam",
    "klonopin", "clonazepam", "valium", "diazepam", "seroquel", "quetiapine",
    "zyprexa", "olanzapine", "risperdal", "risperidone", "geodon", "ziprasidone",
    "abilify", "aripiprazole", "prozac", "fluoxetine", "zoloft", "sertraline",
    "celexa", "citalopram", "lexapro", "escitalopram", "effexor", "venlafaxine",
    "cymbalta", "duloxetine", "wellbutrin", "bupropion", "remeron", "mirtazapine",
    "trazodone", "keppra", "levetiracetam", "dilantin", "phenytoin", "depakote",
    "valproate", "lamictal", "lamotrigine", "tegretol", "carbamazepine",
    # Common brand names that overlap with human names
    "tamar", "grace", "jordan", "victoria", "charlotte", "haven",
}

# Anatomical, surgical, and diagnostic terms commonly capitalized at sentence start
_ANATOMICAL_AND_CLINICAL_TERMS = {
    # Anatomical
    "colon", "appendix", "ileum", "cecum", "duodenum", "jejunum", "rectum", "anus",
    "spleen", "thymus", "thyroid", "adrenal", "prostate", "uterus", "cervix", "ovary",
    "cornea", "retina", "iris", "sclera", "cochlea", "stapes", "incus", "malleus",
    "cranium", "maxilla", "mandible", "clavicle", "scapula", "sternum", "patella",
    "fibula", "tibia", "femur", "radius", "ulna", "humerus", "sacrum", "coccyx",
    "vertebra", "atlas", "axis", "carpal", "tarsal", "phalanx", "phalanges",
    "gluteus", "psoas", "soleus", "deltoid", "biceps", "triceps", "trapezius",
    "peritoneum", "pleura", "pericardium", "meninges", "dura", "pia", "arachnoid",
    "cerebrum", "cerebellum", "pons", "medulla", "thalamus", "hypothalamus",
    "hippocampus", "amygdala", "gallbladder", "pancreas", "trachea", "bronchus",
    # Clinical Signs, Diagnostics & Labs
    "troponin", "procalcitonin", "hemoglobin", "hematocrit", "platelets", "creatinine",
    "bilirubin", "albumin", "lactate", "ferritin", "myoglobin", "d-dimer",
    "tachycardia", "bradycardia", "tachypnea", "hypoxemia", "normocephalic",
    "atraumatic", "supple", "murmur", "rub", "gallop", "wheezing", "rhonchi",
    "crackles", "guarding", "rebound", "cyanosis", "edema", "erythema",
    "intubation", "extubation", "tracheostomy", "thoracentesis", "paracentesis",
    "arthroplasty", "appendectomy", "cholecystectomy", "colectomy", "lobectomy",
}

# Combined clinical whitelist lookup
CLINICAL_WHITELIST: FrozenSet[str] = frozenset(
    _COMMON_MEDICATIONS | _ANATOMICAL_AND_CLINICAL_TERMS
)

_WORD_SPLIT = re.compile(r"[^a-zA-Z0-9\-]+")


def is_clinical_term(candidate: str) -> bool:
    """Return True if candidate is a known medication or anatomical term."""
    norm = candidate.strip().lower()
    if not norm:
        return False
    if norm in CLINICAL_WHITELIST:
        return True

    # Check hyphenated or multi-word terms (e.g. "Albuterol-Ipratropium")
    parts = [p for p in _WORD_SPLIT.split(norm) if p]
    if len(parts) > 1 and all(p in CLINICAL_WHITELIST for p in parts):
        return True

    return False


def filter_clinical_false_positives(entities: list[dict | tuple[str, int, int]]) -> list:
    """Filter out entities whose text matches the clinical whitelist."""
    filtered = []
    for ent in entities:
        if isinstance(ent, dict):
            val = ent.get("value") or ent.get("text") or ""
            if not is_clinical_term(val):
                filtered.append(ent)
        else:
            filtered.append(ent)
    return filtered
