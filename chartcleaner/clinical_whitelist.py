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
    "robaxin", "methocarbamol", "topamax", "topiramate", "baclofen",
    "marinol", "dronabinol", "butrans", "buprenorphine", "slynd", "drospirenone",
    "narcan", "naloxone", "nizoral", "ketoconazole", "dulcolax", "bisacodyl",
    "senna", "docusate", "colace", "polyethylene glycol", "miralax", "klor-con",
    "sublimaze", "febuxostat", "uloric", "ezetimibe", "zetia", "definity",
    "perflutren", "glydo", "micronefrin", "racepinephrine",
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
    "quadriceps", "dorsiflexion", "plantarflexion", "ehl", "intrinsics",
    "peritoneum", "pleura", "pericardium", "meninges", "dura", "pia", "arachnoid",
    "cerebrum", "cerebellum", "pons", "medulla", "thalamus", "hypothalamus",
    "hippocampus", "amygdala", "gallbladder", "pancreas", "trachea", "bronchus",
    "cervical", "thoracic", "lumbar", "sacral", "spine", "spinal", "cord",
    "pubic", "ramus", "rami", "pelvis", "pelvic", "hip", "wrist", "shoulder",
    "knee", "neck", "back", "flank", "scalp", "chin",
    # Clinical Signs, Diagnostics, Pathology & Labs
    "troponin", "procalcitonin", "hemoglobin", "hematocrit", "platelets", "platelet",
    "creatinine", "bilirubin", "albumin", "lactate", "ferritin", "myoglobin", "d-dimer",
    "tachycardia", "bradycardia", "tachypnea", "hypoxemia", "normocephalic",
    "atraumatic", "supple", "murmur", "rub", "gallop", "wheezing", "rhonchi",
    "crackles", "guarding", "rebound", "cyanosis", "edema", "erythema",
    "intubation", "extubation", "tracheostomy", "thoracentesis", "paracentesis",
    "arthroplasty", "appendectomy", "cholecystectomy", "colectomy", "lobectomy",
    "laminectomy", "detethering", "resection", "fusion", "craniotomy",
    "astrocytoma", "glioma", "tumor", "cervicalgia", "headache", "weakness",
    "spastic", "ataxia", "paresthesias", "deficits", "gout", "trauma", "seizure",
    "coma", "stroke", "infarction", "pneumonia", "colonization", "leukocytosis",
    "thrombocytopenia", "transaminitis", "intoxication", "encephalopathy",
    "alkalosis", "acidosis", "hypomagnesemia", "hyperglycemia", "hypoglycemia",
    # Labs & Specimens
    "whole blood", "blood", "urine", "csf", "sputum", "glucose", "sodium", "potassium",
    "chloride", "co2", "pco2", "po2", "fio2", "peep", "bicarbonate", "anion gap",
    "calcium", "calcium ionized", "magnesium", "phosphorus", "phosphate", "bun",
    "inr", "pt", "aptt", "wbc", "rbc", "mpv", "nrbc", "mcv", "mch", "mchc", "rdw",
    "rdw-cv", "neutrophils", "lymphocytes", "monocytes", "eosinophils", "basophils",
    "immature granulocytes", "abs neut", "abs lymph", "abs mono", "abs eosin", "abs baso",
    "alkaline phosphatase", "ast", "alt", "ggt", "creatine kinase", "ck",
    "temp corrected", "temp src", "spo2", "sbp", "dbp", "map",
    "base deficit", "carboxyhemoglobin", "oxyhemoglobin", "ventricular rate",
    "respiratory rate", "ventilator mode", "rhythm", "bazett", "qtc", "abnormal labs",
    "abnormal", "appearance",
    # EMR Structure, Devices, & Protocols
    "foley", "foley catheter", "catheter", "drain", "drains", "hemovac",
    "straight cath", "foley cath", "cath", "chlorhexidine", "chlorhexidine bath",
    "handoff", "templates", "administrative", "context", "delirium", "sedation",
    "analgesia", "respiratory therapy", "continuous", "frequency", "dose", "route",
    "intravenous", "subcutaneous", "intramuscular", "oral", "topical", "inhalation",
    "feeding tube", "liter bags", "order received", "order comments", "linked order",
    "shift total", "cam negative", "cam positive", "rass", "gcs", "miami j", "collar",
    "c-collar", "wbat", "npo", "prn", "tid", "bid", "qid", "daily", "at bedtime",
    "neuro", "neurology", "cardiovascular", "pulmonary", "gastrointestinal",
    "hematology", "endocrinology", "neurosurgery", "nsgy", "ambulation", "spontaneous",
    "alert", "calm", "numbness", "sensation", "rshoulder", "lshoulder", "rarm", "larm",
    "rleg", "lleg", "subarachnoid", "hemorrhagic", "ortho", "pacu", "avr",
    "physician consult", "consult-physician", "swallow screening", "mobilize starting",
    "alcohol", "etoh", "hx etoh", "marijuana", "nacl", "vitamin k antagonist",
    "lpm", "dl", "hrs", "order status", "product info", "progress",
    "cuff", "rate", "temp", "tx loc", "addendum", "encounter written", "pat written", "types",
    "discontinue", "insert", "inserted", "restart", "resume", "swallow screening for patients",
    "al", "akron ambula", "avon ed", "main campus",
}

# Combined clinical whitelist lookup
CLINICAL_WHITELIST: FrozenSet[str] = frozenset(
    _COMMON_MEDICATIONS | _ANATOMICAL_AND_CLINICAL_TERMS
)

_WORD_SPLIT = re.compile(r"[^a-zA-Z0-9\-]+")


def is_non_person_text(candidate: str) -> bool:
    """Return True if candidate structurally cannot be a person's name."""
    s = candidate.strip()
    if not s:
        return True
    # Contains newlines, tabs, slashes, comparison operators, or structural punctuation
    if any(ch in s for ch in ("\n", "\r", "\t", "/", "\\", "<", ">", "=", "@", "#", "{", "}", "[", "]")):
        return True
    # Contains colon or semicolon inside
    if ":" in s or ";" in s:
        return True
    # Contains numbers and letters mixed together (e.g. q1h, SBP<180, 50mcg, 10mg, 400mg)
    if re.search(r"\d", s) and re.search(r"[a-zA-Z]", s):
        return True
    return False


def is_clinical_term(candidate: str) -> bool:
    """Return True if candidate is a known medication, lab, or anatomical/clinical term."""
    norm = candidate.strip().lower()
    if not norm:
        return False
    if norm in CLINICAL_WHITELIST:
        return True

    # Check hyphenated or multi-word terms (e.g. "Albuterol-Ipratropium", "Whole Blood")
    parts = [p for p in _WORD_SPLIT.split(norm) if p]
    if len(parts) > 1 and (all(p in CLINICAL_WHITELIST for p in parts) or any(p in CLINICAL_WHITELIST for p in parts if len(p) >= 4)):
        return True

    return False


def filter_clinical_false_positives(entities: list[dict | tuple[str, int, int]]) -> list:
    """Filter out entities whose text matches the clinical whitelist."""
    filtered = []
    for ent in entities:
        if isinstance(ent, dict):
            val = ent.get("value") or ent.get("text") or ""
            if not is_clinical_term(val) and not is_non_person_text(val):
                filtered.append(ent)
        else:
            filtered.append(ent)
    return filtered
