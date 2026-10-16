//! Clinical gazetteer, false-positive mitigation whitelist, and fast pure-Rust
//! PHI redactor (`PERSON`, `PHONE_NUMBER`, `EMAIL_ADDRESS`, `ID_NUMBER`).

use ahash::AHashSet;
use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::BTreeMap;

static CLINICAL_WHITELIST: Lazy<AHashSet<&'static str>> = Lazy::new(|| {
    let terms = [
        // Cardiovascular & Antithrombotic
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
        // Pressors, Inotropes & Critical Care
        "levophed", "norepinephrine", "epinephrine", "vasopressin", "phenylephrine",
        "dopamine", "dobutamine", "milrinone", "propofol", "diprivan", "precedex",
        "dexmedetomidine", "versed", "midazolam", "fentanyl", "dilaudid", "hydromorphone",
        "morphine", "ketamine", "rocuronium", "vecuronium", "cisatracurium",
        // Antibiotics & Antimicrobials
        "vancomycin", "vancocin", "zosyn", "piperacillin", "tazobactam", "ceftriaxone",
        "rocephin", "cefepime", "maxipime", "cefdinir", "keflex", "cephalexin",
        "ancef", "cefazolin", "meropenem", "merrem", "ertapenem", "invanz",
        "aztreonam", "ciprofloxacin", "cipro", "levofloxacin", "levaquin",
        "moxifloxacin", "avelox", "azithromycin", "zithromax", "clarithromycin",
        "amoxicillin", "augmentin", "ampicillin", "unasyn", "doxycycline",
        "vibramycin", "bactrim", "septra", "sulfamethoxazole", "trimethoprim",
        "flagyl", "metronidazole", "zyvox", "linezolid", "cubicin", "daptomycin",
        "gentamicin", "tobramycin", "amikacin", "diflucan", "fluconazole",
        // Endocrine & Diabetes
        "metformin", "glucophage", "glipizide", "glucotrol", "glimepiride", "amaryl",
        "januvia", "sitagliptin", "jardiance", "empagliflozin", "farxiga", "dapagliflozin",
        "ozempic", "wegovy", "semaglutide", "mounjaro", "zepbound", "tirzepatide",
        "trulicity", "dulaglutide", "lantus", "glargine", "levemir", "detemir",
        "humalog", "lispro", "novolog", "aspart", "tresiba", "degludec",
        "synthroid", "levothyroxine", "cytomel", "liothyronine", "tapazole", "methimazole",
        // GI, Pulmonary & Analgesia
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
        // Psych & Neurologic
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
        // Brand names overlapping human names
        "tamar", "grace", "jordan", "victoria", "charlotte", "haven",
        // Anatomical
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
        // Clinical Signs, Diagnostics, Pathology & Labs
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
        // Labs & Specimens
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
        // EMR Structure, Devices, & Protocols
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
    ];
    terms.into_iter().collect()
});

static ROLE_WORDS: Lazy<AHashSet<&'static str>> = Lazy::new(|| {
    let words = [
        "family", "mother", "father", "mom", "dad", "parent", "parents", "spouse", "wife", "husband",
        "partner", "son", "daughter", "sister", "brother", "sibling", "siblings", "aunt", "uncle",
        "niece", "nephew", "cousin", "grandmother", "grandfather", "grandson", "granddaughter",
        "children", "child", "friend", "caregiver", "guardian", "hcp", "poa", "surrogate",
        "patient", "pt", "nurse", "rn", "np", "pa", "md", "do", "attending", "resident", "fellow",
        "intern", "team", "chaplain", "pharmacist", "pharmacy", "therapist", "dietitian", "and", "or",
    ];
    words.into_iter().collect()
});

static WORD_SPLIT: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-zA-Z0-9\-]+").unwrap());
static ROLE_SPLIT: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z]+").unwrap());

pub fn is_role_phrase(candidate: &str) -> bool {
    let low = candidate.to_lowercase();
    let words: Vec<&str> = ROLE_SPLIT.split(&low).filter(|w| !w.is_empty()).collect();
    !words.is_empty() && words.iter().all(|w| ROLE_WORDS.contains(w))
}

pub fn is_non_person_text(candidate: &str) -> bool {
    let s = candidate.trim();
    if s.is_empty() || is_role_phrase(s) {
        return true;
    }
    if s.chars().any(|ch| {
        matches!(
            ch,
            '\n' | '\r' | '\t' | '/' | '\\' | '<' | '>' | '=' | '@' | '#' | '{' | '}' | '[' | ']' | ':' | ';'
        )
    }) {
        return true;
    }
    let has_digit = s.chars().any(|c| c.is_ascii_digit());
    let has_alpha = s.chars().any(|c| c.is_ascii_alphabetic());
    has_digit && has_alpha
}

pub fn is_clinical_term(candidate: &str) -> bool {
    let norm = candidate.trim().to_lowercase();
    if norm.is_empty() {
        return false;
    }
    if CLINICAL_WHITELIST.contains(norm.as_str()) {
        return true;
    }
    let parts: Vec<&str> = WORD_SPLIT.split(&norm).filter(|p| !p.is_empty()).collect();
    if parts.len() > 1
        && (parts.iter().all(|p| CLINICAL_WHITELIST.contains(p))
            || parts
                .iter()
                .any(|p| p.len() >= 4 && CLINICAL_WHITELIST.contains(p)))
    {
        return true;
    }
    false
}

// ---------------------------------------------------------------------------
// Pure-Rust fast PHI entity detector (PERSON, PHONE_NUMBER, EMAIL_ADDRESS, ID_NUMBER)
// ---------------------------------------------------------------------------

static EMAIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b").unwrap()
});

static PHONE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?:\+?1[\s.\-]?)?(?:\(\d{3}\)[\s.\-]?|\b\d{3}[\s.\-])\d{3}[\s.\-]\d{4}\b|\b\d{10}\b").unwrap()
});

static ID_LABEL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(?:accession|acc|mrn|csn|fin|har|order|referral|ref|encounter|account|acct|specimen|case|req(?:uisition)?|id)\b\s*(?:no\.?|number|num|#)?\s*[:#]?\s*\(?$"
    )
    .unwrap()
});

// Contextual person patterns:
// 1. Honorific / title: Dr. First Last, Mr./Ms./Mrs. First Last
// 2. Credential suffix: First Last, MD/DO/RN/NP/PA-C/PhD
// 3. Labelled name: Patient: First Last, Attending: First Last, Seen by First Last, Signed by First Last, Dictated by First Last, Call First Last
// 4. Relative + Name: Spouse John Doe, Wife Mary Smith, Son David Lee
static TITLED_PERSON_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?:Drs?\.?|Mr\.?|Mrs\.?|Ms\.?|Miss|Prof\.?)\s+([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z'\-]{1,25}){0,2})\b"
    )
    .unwrap()
});

static CREDENTIAL_PERSON_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'\-]{1,25})(?:,\s*(?:M\.?D\.?|D\.?O\.?|R\.?N\.?|N\.?P\.?|P\.?A\.?(?:-C)?|Ph\.?D\.?|Pharm\.?D\.?|B\.?S\.?N\.?|APRN|MSN|LCSW))\b"
    )
    .unwrap()
});

static CONTEXT_PERSON_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(?:patient(?:\s+name)?|pt(?:\s+name)?|attending|provider|author|signed\s+by|seen\s+by|evaluated\s+by|dictated\s+by|transcribed\s+by|discussed\s+with|spoke\s+with|contact(?:ed)?|called|paging|page|dr\.|spouse|wife|husband|mother|father|son|daughter|sister|brother|partner|guardian|caregiver|surrogate|poa|pcp)\s*[:\-]?\s*([A-Z][a-z]{1,20}(?:\s+[A-Z]\.?)?\s+[A-Z][a-z'\-]{1,25})\b"
    )
    .unwrap()
});

static COMMON_FIRST_NAMES: Lazy<AHashSet<&'static str>> = Lazy::new(|| {
    [
        "james", "john", "robert", "michael", "william", "david", "richard", "joseph", "thomas",
        "charles", "christopher", "daniel", "matthew", "anthony", "mark", "donald", "steven",
        "paul", "andrew", "joshua", "kenneth", "kevin", "brian", "george", "timothy", "ronald",
        "edward", "jason", "jeffrey", "ryan", "jacob", "gary", "nicholas", "eric", "jonathan",
        "stephen", "larry", "justin", "scott", "brandon", "benjamin", "samuel", "gregory",
        "alexander", "frank", "patrick", "raymond", "jack", "dennis", "jerry", "tyler", "aaron",
        "jose", "adam", "nathan", "henry", "douglas", "zachary", "peter", "kyle", "ethan",
        "walter", "noah", "jeremy", "christian", "keith", "roger", "terry", "gerald", "harold",
        "sean", "austin", "carl", "arthur", "lawrence", "dylan", "jesse", "jordan", "bryan",
        "billy", "joe", "bruce", "gabriel", "logan", "albert", "willie", "alan", "juan", "wayne",
        "elijah", "randy", "roy", "vincent", "ralph", "eugene", "russell", "bobby", "mason",
        "philip", "louis", "mary", "patricia", "jennifer", "linda", "elizabeth", "barbara",
        "susan", "jessica", "sarah", "karen", "lisa", "nancy", "betty", "margaret", "sandra",
        "ashley", "kimberly", "emily", "donna", "michelle", "carol", "amanda", "dorothy",
        "melissa", "deborah", "stephanie", "rebecca", "sharon", "laura", "cynthia", "kathleen",
        "amy", "angela", "shirley", "anna", "brenda", "pamela", "emma", "nicole", "helen",
        "samantha", "katherine", "christine", "debra", "rachel", "carolyn", "janet", "catherine",
        "maria", "heather", "diane", "ruth", "julie", "olivia", "joyce", "virginia", "victoria",
        "kelly", "lauren", "christina", "joan", "evelyn", "judith", "megan", "andrea", "cheryl",
        "hannah", "jacqueline", "martha", "gloria", "teresa", "ann", "sara", "madison", "frances",
        "kathryn", "janice", "jean", "abigail", "alice", "judy", "sophia", "grace", "denise",
        "amber", "doris", "marilyn", "danielle", "beverly", "isabella", "theresa", "diana",
        "natalie", "brittany", "charlotte", "marie", "kayla", "alexis", "lori", "jane", "john",
        "elena", "marcus", "priya", "ahmed", "fatima", "wei", "chen", "raj", "carlos", "sofia",
    ]
    .into_iter()
    .collect()
});

static TWO_CAP_WORDS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b([A-Z][a-z]{2,18})\s+([A-Z][a-z]{2,20})\b").unwrap()
});

#[derive(Debug, Clone)]
pub struct DetectedPhi {
    pub entity_type: String,
    pub start: usize,
    pub end: usize,
    pub score: f64,
}

/// Pure-Rust sub-millisecond PHI redactor (`nlp_redaction` stage).
pub fn run_nlp_redaction(
    text: &str,
    cfg: &serde_json::Value,
) -> (String, usize, BTreeMap<String, usize>) {
    let ncfg = cfg.get("nlp_redaction");
    let mut entities: BTreeMap<String, String> = BTreeMap::new();
    if let Some(ent_obj) = ncfg.and_then(|v| v.get("entities")).and_then(|v| v.as_object()) {
        for (k, v) in ent_obj {
            if let Some(s) = v.as_str() {
                entities.insert(k.clone(), s.to_string());
            }
        }
    }
    if entities.is_empty() {
        entities.insert("PERSON".to_string(), "[REDACTED_NAME]".to_string());
        entities.insert("PHONE_NUMBER".to_string(), "[REDACTED_PHONE]".to_string());
        entities.insert("EMAIL_ADDRESS".to_string(), "[REDACTED_EMAIL]".to_string());
    }

    let threshold = ncfg
        .and_then(|v| v.get("score_threshold"))
        .and_then(|v| v.as_f64());
    let protect_clinical = ncfg
        .and_then(|v| v.get("protect_clinical_terms"))
        .and_then(|v| v.as_bool())
        .unwrap_or(true);

    let mut allow: AHashSet<String> = AHashSet::new();
    if let Some(arr) = cfg.get("nlp_allow_list").and_then(|v| v.as_array()) {
        for item in arr {
            if let Some(s) = item.as_str() {
                allow.insert(s.to_lowercase());
            }
        }
    }

    let mut raw: Vec<DetectedPhi> = Vec::new();

    if entities.contains_key("EMAIL_ADDRESS") {
        for m in EMAIL_RE.find_iter(text) {
            raw.push(DetectedPhi {
                entity_type: "EMAIL_ADDRESS".to_string(),
                start: m.start(),
                end: m.end(),
                score: 0.99,
            });
        }
    }

    if entities.contains_key("PHONE_NUMBER") {
        for m in PHONE_RE.find_iter(text) {
            let lookback_start = m.start().saturating_sub(30);
            // Make sure lookback_start is on a char boundary
            let mut lb = lookback_start;
            while lb > 0 && !text.is_char_boundary(lb) {
                lb -= 1;
            }
            let prefix_slice = &text[lb..m.start()];
            let etype = if ID_LABEL_RE.is_match(prefix_slice) {
                "ID_NUMBER"
            } else {
                "PHONE_NUMBER"
            };
            raw.push(DetectedPhi {
                entity_type: etype.to_string(),
                start: m.start(),
                end: m.end(),
                score: 0.90,
            });
        }
    }

    if entities.contains_key("PERSON") {
        for caps in TITLED_PERSON_RE.captures_iter(text) {
            if let Some(m1) = caps.get(1) {
                raw.push(DetectedPhi {
                    entity_type: "PERSON".to_string(),
                    start: m1.start(),
                    end: m1.end(),
                    score: 0.95,
                });
            }
        }
        for caps in CREDENTIAL_PERSON_RE.captures_iter(text) {
            if let Some(m1) = caps.get(1) {
                raw.push(DetectedPhi {
                    entity_type: "PERSON".to_string(),
                    start: m1.start(),
                    end: m1.end(),
                    score: 0.94,
                });
            }
        }
        for caps in CONTEXT_PERSON_RE.captures_iter(text) {
            if let Some(m1) = caps.get(1) {
                raw.push(DetectedPhi {
                    entity_type: "PERSON".to_string(),
                    start: m1.start(),
                    end: m1.end(),
                    score: 0.92,
                });
            }
        }
        for caps in TWO_CAP_WORDS_RE.captures_iter(text) {
            let m0 = caps.get(0).unwrap();
            let first = caps.get(1).unwrap().as_str().to_lowercase();
            let second = caps.get(2).unwrap().as_str().to_lowercase();
            if COMMON_FIRST_NAMES.contains(first.as_str())
                && !CLINICAL_WHITELIST.contains(second.as_str())
                && !ROLE_WORDS.contains(second.as_str())
            {
                raw.push(DetectedPhi {
                    entity_type: "PERSON".to_string(),
                    start: m0.start(),
                    end: m0.end(),
                    score: 0.85,
                });
            }
        }
    }

    // Filter by score threshold, allow list, and clinical whitelist
    raw.retain(|r| {
        if let Some(th) = threshold {
            if r.score < th {
                return false;
            }
        }
        let span_str = &text[r.start..r.end];
        let low = span_str.to_lowercase();
        if allow.contains(&low) {
            return false;
        }
        // Also check individual words in allow list if span has multiple words and all words are allowed
        if span_str
            .split_whitespace()
            .all(|w| allow.contains(&w.to_lowercase()))
        {
            return false;
        }
        if protect_clinical
            && r.entity_type == "PERSON"
            && (is_clinical_term(span_str) || is_non_person_text(span_str))
        {
            return false;
        }
        true
    });

    // Deduplicate overlapping spans (prefer longest, then highest score, then earliest start)
    raw.sort_by(|a, b| {
        let len_a = a.end - a.start;
        let len_b = b.end - b.start;
        len_b
            .cmp(&len_a)
            .then_with(|| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal))
            .then_with(|| a.start.cmp(&b.start))
    });

    let mut accepted: Vec<DetectedPhi> = Vec::new();
    for cand in raw {
        if accepted
            .iter()
            .any(|existing| !(cand.end <= existing.start || cand.start >= existing.end))
        {
            continue;
        }
        accepted.push(cand);
    }

    accepted.sort_by(|a, b| b.start.cmp(&a.start));
    let mut out = text.to_string();
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let total = accepted.len();

    for r in accepted {
        let repl = if r.entity_type == "ID_NUMBER" {
            "[REDACTED_ID]"
        } else {
            entities
                .get(&r.entity_type)
                .map(|s| s.as_str())
                .unwrap_or("[REDACTED]")
        };
        out.replace_range(r.start..r.end, repl);
        *counts.entry(r.entity_type).or_insert(0) += 1;
    }

    (out, total, counts)
}
