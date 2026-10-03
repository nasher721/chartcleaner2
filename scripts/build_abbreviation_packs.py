"""Rebuild chartcleaner/packs/abbreviations/*.json from the candidate lists below.

Each candidate is dropped when its term is already in the bundled dictionary,
when its abbreviation already means something different there (unless listed
in SYNONYMS), or when it is a Do Not Use abbreviation. Run from the repo root:

    python scripts/build_abbreviation_packs.py
"""
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from chartcleaner.abbreviation_safety import _bundled_pairs, check
from chartcleaner.abbreviations import _same_meaning, display_term

P = {
"Neuro ICU": ("Neurocritical care: stroke, hemorrhage, ICP and neuromonitoring.", """
intracranial pressure|ICP
cerebral perfusion pressure|CPP
Glasgow Coma Scale|GCS
NIH Stroke Scale|NIHSS
intraventricular hemorrhage|IVH
intracerebral hemorrhage|ICH
intraparenchymal hemorrhage|IPH
subdural hematoma|SDH
epidural hematoma|EDH
transcranial Doppler|TCD
delayed cerebral ischemia|DCI
continuous EEG|cEEG
nonconvulsive status epilepticus|NCSE
status epilepticus|SE
traumatic brain injury|TBI
hypoxic-ischemic brain injury|HIBI
middle cerebral artery|MCA
anterior cerebral artery|ACA
posterior cerebral artery|PCA
basilar artery|BA
cerebral venous sinus thrombosis|CVST
large vessel occlusion|LVO
endovascular thrombectomy|EVT
tissue plasminogen activator|tPA
modified Rankin Scale|mRS
Richmond Agitation-Sedation Scale|RASS
cerebrospinal fluid|CSF
lumbar drain|LD
decompressive hemicraniectomy|DHC
ventriculoperitoneal shunt|VPS
Guillain-Barre syndrome|GBS
myasthenia gravis|MG
intravenous immunoglobulin|IVIG
plasma exchange|PLEX
negative inspiratory force|NIF
forced vital capacity|FVC
spinal cord injury|SCI
posterior reversible encephalopathy syndrome|PRES
reversible cerebral vasoconstriction syndrome|RCVS
pupillary light reflex|PLR
neurological pupil index|NPi
brain tissue oxygen|PbtO2
"""),
"Critical Care": ("Adult ICU: hemodynamics, ventilation, renal support and lines.", """
mean arterial pressure|MAP
central venous pressure|CVP
positive end-expiratory pressure|PEEP
fraction of inspired oxygen|FiO2
acute respiratory distress syndrome|ARDS
extracorporeal membrane oxygenation|ECMO
continuous renal replacement therapy|CRRT
spontaneous breathing trial|SBT
spontaneous awakening trial|SAT
ventilator-associated pneumonia|VAP
central line-associated bloodstream infection|CLABSI
catheter-associated urinary tract infection|CAUTI
disseminated intravascular coagulation|DIC
acute kidney injury|AKI
diabetic ketoacidosis|DKA
high-flow nasal cannula|HFNC
bilevel positive airway pressure|BiPAP
noninvasive positive pressure ventilation|NIPPV
pressure support ventilation|PSV
assist-control ventilation|ACV
tidal volume|Vt
arterial blood gas|ABG
venous blood gas|VBG
packed red blood cells|pRBC
fresh frozen plasma|FFP
goals of care|GOC
do not resuscitate|DNR
do not intubate|DNI
peripherally inserted central catheter|PICC
central venous catheter|CVC
endotracheal tube|ETT
systemic inflammatory response syndrome|SIRS
Sequential Organ Failure Assessment|SOFA
return of spontaneous circulation|ROSC
cardiopulmonary resuscitation|CPR
targeted temperature management|TTM
"""),
"Cardiology": ("Cardiology and cardiac ICU.", """
heart failure with reduced ejection fraction|HFrEF
heart failure with preserved ejection fraction|HFpEF
left ventricular ejection fraction|LVEF
coronary artery disease|CAD
coronary artery bypass graft|CABG
percutaneous coronary intervention|PCI
ST-elevation myocardial infarction|STEMI
non-ST-elevation myocardial infarction|NSTEMI
acute coronary syndrome|ACS
transthoracic echocardiogram|TTE
transesophageal echocardiogram|TEE
left ventricular hypertrophy|LVH
mitral regurgitation|MR
tricuspid regurgitation|TR
aortic regurgitation|AR
transcatheter aortic valve replacement|TAVR
implantable cardioverter-defibrillator|ICD
cardiac resynchronization therapy|CRT
ventricular tachycardia|VT
ventricular fibrillation|VF
supraventricular tachycardia|SVT
left bundle branch block|LBBB
right bundle branch block|RBBB
direct oral anticoagulant|DOAC
guideline-directed medical therapy|GDMT
intra-aortic balloon pump|IABP
left ventricular assist device|LVAD
pulmonary capillary wedge pressure|PCWP
brain natriuretic peptide|BNP
peripheral artery disease|PAD
atrial flutter|AFL
paroxysmal atrial fibrillation|PAF
"""),
"Medicine": ("General internal medicine and hospital medicine.", """
chronic kidney disease|CKD
end-stage renal disease|ESRD
chronic obstructive pulmonary disease|COPD
community-acquired pneumonia|CAP
hospital-acquired pneumonia|HAP
urinary tract infection|UTI
gastrointestinal bleed|GIB
upper gastrointestinal bleed|UGIB
lower gastrointestinal bleed|LGIB
type 2 diabetes mellitus|T2DM
type 1 diabetes mellitus|T1DM
obstructive sleep apnea|OSA
gastroesophageal reflux disease|GERD
hepatic encephalopathy|HE
skilled nursing facility|SNF
physical therapy|PT
occupational therapy|OT
speech-language pathology|SLP
review of systems|ROS
history of present illness|HPI
past medical history|PMH
nothing by mouth|NPO
complete blood count|CBC
basic metabolic panel|BMP
liver function tests|LFTs
chest X-ray|CXR
electrocardiogram|ECG
nausea and vomiting|N/V
altered mental status|AMS
left lower extremity|LLE
right lower extremity|RLE
left upper extremity|LUE
right upper extremity|RUE
bilateral lower extremities|BLE
emergency department|ED
primary care physician|PCP
against medical advice|AMA
oxygen saturation|SpO2
blood pressure|BP
heart rate|HR
deep vein thrombosis prophylaxis|DVT ppx
congestive heart failure|CHF
benign prostatic hyperplasia|BPH
rheumatoid arthritis|RA
"""),
"Nursing": ("Bedside nursing documentation.", """
intake and output|I&O
range of motion|ROM
bowel movement|BM
head of bed|HOB
out of bed|OOB
bedside commode|BSC
nasogastric tube|NGT
orogastric tube|OGT
percutaneous endoscopic gastrostomy|PEG
peripheral IV|PIV
weight-bearing as tolerated|WBAT
non-weight-bearing|NWB
vital signs|VS
fingerstick blood glucose|FSBG
point of care|POC
sequential compression devices|SCDs
within normal limits|WNL
no acute distress|NAD
alert and oriented|A&O
pressure injury|PI
call light within reach|CLWR
standby assist|SBA
contact guard assist|CGA
minimum assist|min A
moderate assist|mod A
maximum assist|max A
turn every 2 hours|q2h turns
patient education|pt ed
fall precautions|fall prec
aspiration precautions|asp prec
"""),
}

# Pack wording that differs from the bundled row but means the same thing.
SYNONYMS = {"NIHSS", "cEEG", "EVT", "CXR", "PIV", "SpO2"}

bundled = list(_bundled_pairs())
bundled_terms = {t.casefold() for t, _ in bundled}
bundled_by_abbr = {}
for t, a in bundled:
    bundled_by_abbr.setdefault(a, []).append(display_term(t))

for name, (desc, body) in P.items():
    keep, dropped = [], []
    for line in body.strip().splitlines():
        term, abbr = line.split("|")
        reason = None
        if term.casefold() in bundled_terms:
            reason = "already bundled"
        elif abbr in bundled_by_abbr and not any(_same_meaning(display_term(term), m) for m in bundled_by_abbr[abbr]) \
                and abbr not in SYNONYMS:
            reason = f"abbr means {bundled_by_abbr[abbr][:2]}"
        elif any(i.level == "block" for i in check(term, abbr)):
            reason = "do not use"
        if reason:
            dropped.append((term, abbr, reason))
        else:
            keep.append({"term": term, "replacement": abbr})
    path = __import__("pathlib").Path(__file__).resolve().parent.parent / f"chartcleaner/packs/abbreviations/{name}.json"
    json.dump({"_pack": {"name": name, "description": desc, "version": "1.0"},
               "abbreviations": {"custom": keep}}, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print(f"{name}: kept {len(keep)}; dropped {len(dropped)}")
    for d in dropped: print("   -", d)
