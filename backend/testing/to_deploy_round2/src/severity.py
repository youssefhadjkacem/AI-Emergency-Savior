"""
Estimation de la gravité d'un cas, à partir du texte du patient et des
symptômes extraits. Quatre niveaux : LOW, MEDIUM, HIGH, CRITICAL.

Ce module remplace l'ancienne « gestion de l'urgence », qui se réduisait à
un booléen `urgent` fourni par l'appelant et à une seule règle dans
`K1Brain.score` : doubler le score de la cardiologie quand il était vrai,
quels que soient les symptômes.

C'est un système de RÈGLES, pas un modèle appris ni un outil de triage
médical validé. Chaque estimation renvoie la liste des indices qui l'ont
produite (`reasons`), pour pouvoir être relue et contestée.

── Indices utilisés ──────────────────────────────────────────────────────
1. SYMPTÔMES EXTRAITS, classés en trois paliers (ensembles ci-dessous).
   Le palier le plus élevé présent donne le score de départ :
       aucun symptôme classé ......... 1   (symptômes bénins seulement)
       palier MODÉRÉ ................. 2
       palier ÉLEVÉ .................. 4
       palier CRITIQUE ............... 6
2. COMBINAISONS de symptômes connues comme des urgences vitales
   (`CRITICAL_COMBINATIONS`) : portent le score à 6 au moins.
3. FORMULATIONS D'URGENCE VITALE dans le texte, indépendantes de
   l'extraction (« ne respire plus », « unconscious », « heart attack ») :
   portent le score à 6 au moins. Elles couvrent le cas où l'extracteur de
   symptômes n'a rien reconnu.
4. MODIFICATEURS, +1 ou -1 chacun :
       +1  au moins deux symptômes du palier ÉLEVÉ
       +1  intensité exprimée (« atroce », « très forte », « unbearable »)
       +1  début brutal (« d'un seul coup », « suddenly »)
       +1  appel à l'aide (« vite », « ambulance », « au secours », « help »)
       +1  âge vulnérable (moins de 5 ans, ou 75 ans et plus)
       -1  ancienneté (« depuis des mois », « chaque printemps », « for years »)

── Du score au niveau ────────────────────────────────────────────────────
       score >= 6 ... CRITICAL
       score 4-5 .... HIGH
       score 2-3 .... MEDIUM
       score <= 1 ... LOW
Les seuils suivent les scores de départ : un symptôme du palier ÉLEVÉ suffit
à atteindre HIGH, et il faut deux modificateurs pour qu'un symptôme MODÉRÉ
y arrive. Un modificateur seul ne fait jamais sauter deux niveaux.

── Deux cas particuliers ─────────────────────────────────────────────────
  - `UNKNOWN` : aucun symptôme et aucun indice textuel. L'absence
    d'information n'est pas une preuve de bénignité ; renvoyer LOW serait
    rassurant à tort.
  - `urgent=True` fourni par l'appelant (case cochée par l'opérateur) :
    plancher à HIGH. Un humain qui signale une urgence n'est jamais
    contredit à la baisse par les règles.

── Négation ──────────────────────────────────────────────────────────────
Les indices textuels sont cherchés dans le texte dont les portées de
négation ont été masquées (`negation.py`) : « pas de douleur atroce » ne
compte pas comme intensité. Une formulation qui contient elle-même une
négation (« ne respire plus », « not breathing ») est cherchée dans le
texte non masqué. Les symptômes, eux, arrivent déjà débarrassés des
négations par l'extracteur.

── Limites connues ───────────────────────────────────────────────────────
  - La gravité dépend de l'extraction : un symptôme grave non reconnu
    n'est pas compté (rappel de l'extracteur anglais : 0,27).
  - Les paliers sont posés à partir de connaissances médicales générales,
    sans validation par un clinicien ni calibration sur des données.
  - Pas de constantes vitales, pas d'antécédents, pas de durée chiffrée.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

from src.french_lexicon import normalize_french
from src.negation import contains_negation_cue, mask_negated_scopes

LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
UNKNOWN = "UNKNOWN"

# ── Paliers de symptômes ─────────────────────────────────────────────────
# CRITIQUE : signe qui, à lui seul, évoque une urgence vitale ou
# neurologique (AVC, trouble de conscience, hémorragie digestive, détresse
# respiratoire, risque suicidaire).
CRITICAL_SYMPTOMS = frozenset({
    "weakness_of_one_body_side", "facial_drooping", "slurred_speech", "altered_sensorium", "coma",
    "suicidal_thoughts", "hematemesis", "stomach_bleeding", "cyanosis", "acute_liver_failure",
})

# ÉLEVÉ : signe qui justifie un avis médical dans la journée.
HIGH_SYMPTOMS = frozenset({
    # cœur · poumons
    "chest_pain", "chest_tightness", "left_arm_pain", "irregular_heartbeat", "breathlessness", "orthopnea",
    "pleuritic_chest_pain", "hemoptysis", "blood_in_sputum", "syncope",
    # neurologie · œil · psychiatrie
    "seizures", "febrile_seizures", "speech_difficulty", "stiff_neck", "vision_loss", "diplopia",
    "double_vision", "hallucinations", "delusions",
    # saignements · digestif · foie
    "bloody_stool", "rectal_bleeding", "melena", "prolonged_bleeding", "yellowing_of_eyes", "yellowish_skin",
    "jaundice",
    # rein · urologie
    "hematuria", "renal_colic", "oliguria", "decreased_urine_output", "testicular_pain",
    # infectieux · allergie · vasculaire · traumatisme
    "high_fever_spiking", "rash_spreading", "angioedema", "deep_vein_clot", "thrombosis", "bone_deformity",
})

# MODÉRÉ : symptôme franc mais sans caractère d'urgence par lui-même.
MODERATE_SYMPTOMS = frozenset({
    "high_fever", "vomiting", "diarrhoea", "dehydration", "stomach_pain", "abdominal_pain", "pelvic_pain",
    "flank_pain", "palpitations", "fast_heart_rate", "wheezing", "dizziness", "spinning_movements",
    "loss_of_balance", "unsteadiness", "numbness_tingling", "tremor", "memory_loss", "eye_pain",
    "flashing_lights", "floaters", "easy_bleeding", "menorrhagia", "panic_attacks", "depression",
    "limited_range_of_motion", "muscle_spasm", "dysphagia", "unexplained_weight_loss",
    "night_sweats_drenching", "lymphadenopathy", "breast_lump", "cachexia", "excessive_thirst",
    "irregular_sugar_level", "periorbital_edema", "frothy_urine", "hypertension_renal", "fluid_overload",
    "swelling_of_stomach", "failure_to_thrive", "dark_urine", "animal_contact", "weakness_in_limbs",
})

FEVER = frozenset({"high_fever", "high_fever_spiking", "mild_fever"})

# (symptôme pivot, symptômes associés, libellé) : le pivot avec AU MOINS UN
# des associés constitue une urgence vitale.
CRITICAL_COMBINATIONS = (
    ("chest_pain", frozenset({"left_arm_pain", "sweating", "chest_tightness", "breathlessness",
                              "irregular_heartbeat", "syncope"}),
     "douleur thoracique avec signe associé (suspicion de syndrome coronarien)"),
    ("breathlessness", frozenset({"cyanosis", "angioedema"}),
     "gêne respiratoire avec cyanose ou œdème (détresse respiratoire)"),
    ("stiff_neck", FEVER, "raideur de nuque avec fièvre (suspicion de méningite)"),
)

# ── Indices textuels (expressions régulières sur texte normalisé :
#    minuscules, sans accents, apostrophes remplacées par des espaces) ──────
VITAL_PHRASES = (
    r"(?:ne |n )?respire plus", r"(?:n arrive|peut|peux) (?:plus|pas) (?:a )?respirer", "etouffe",
    "not breathing", r"can ?(?:not| t) breathe", "choking",
    "inconsciente?", "ne repond plus", "perdu connaissance", "unconscious", "unresponsive", "not responding",
    "passed out",
    "crise cardiaque", "infarctus", "arret cardiaque", "avc", "heart attack", "cardiac arrest", "stroke",
    "overdose", "surdose", r"avale (?:plein|beaucoup|tous ses|une boite) de medicaments",
    r"saigne (?:beaucoup|abondamment|enormement)", "hemorragie", r"bleeding (?:heavily|a lot|profusely)",
    r"won t stop bleeding",
    r"(?:veut|veux|vais) en finir", r"se suicider|me suicider|suicid\w*", r"kill (?:him|her|my)self",
    r"end (?:his|her|my) life",
)
INTENSITY_PHRASES = (
    r"tres forte?s?", "atroce", "insupportable", r"violente?s?", "terrible", "intense", "horrible",
    "epouvantable", r"enormement mal", r"tellement mal",
    "unbearable", "severe", "excruciating", "worst", "very strong", "very bad", "awful", "violent",
)
SUDDEN_PHRASES = (
    "d un seul coup", "d un coup", "soudain", r"soudainement|subitement|brutalement|brusquement",
    "tout a coup", "suddenly", "all of a sudden", "sudden", "out of nowhere",
)
HELP_PHRASES = (
    # « vite » seul est exclu : « mon cœur bat très vite » décrit un symptôme.
    r"urgen(?:t|te|ce)", r"(?:venez|faites|viens) vite", "au secours", r"a l aide", "ambulance", "samu", "envoyez quelqu un",
    "depechez", "emergency", "help", "hurry", "send someone", "right now",
)
CHRONIC_PHRASES = (
    r"depuis (?:des|plusieurs|quelques|\w+) (?:mois|annees|ans)", "depuis longtemps",
    r"chaque (?:printemps|annee|hiver|ete|automne)", r"tous les (?:ans|printemps|hivers)",
    r"for (?:several |many |a few |\w+ )?(?:months|years)", "for a long time",
    r"every (?:spring|year|winter|summer)",
)

VULNERABLE_AGE_BELOW = 5
VULNERABLE_AGE_FROM = 75
# Jeune enfant et grand âge : une même plainte y est plus à risque. Bornes
# volontairement larges et simples ; elles n'ajoutent qu'un point.

SCORE_MODERATE, SCORE_HIGH, SCORE_CRITICAL = 2, 4, 6


def _compile(phrases: Iterable[str]):
    return [(re.compile(rf"(?<!\w)(?:{p})(?!\w)"), contains_negation_cue(p)) for p in phrases]


_VITAL, _INTENSITY, _SUDDEN, _HELP, _CHRONIC = (
    _compile(g) for g in (VITAL_PHRASES, INTENSITY_PHRASES, SUDDEN_PHRASES, HELP_PHRASES, CHRONIC_PHRASES)
)


def _first_match(compiled, plain: str, masked: str) -> Optional[str]:
    for pattern, negative_phrasing in compiled:
        found = pattern.search(plain if negative_phrasing else masked)
        if found:
            return found.group(0)
    return None


def level_from_score(score: int) -> str:
    if score >= SCORE_CRITICAL:
        return "CRITICAL"
    if score >= SCORE_HIGH:
        return "HIGH"
    if score >= SCORE_MODERATE:
        return "MEDIUM"
    return "LOW"


def estimate_severity(text: str, symptoms: Iterable[str], age: Optional[float] = None,
                      urgent: bool = False) -> dict:
    """
    Retourne {"level", "score", "reasons", "driving_symptoms"}.

    `driving_symptoms` : les symptômes du palier le plus élevé atteint
    (ÉLEVÉ ou CRITIQUE), c'est-à-dire ceux qui font la gravité du cas. La
    classification de spécialité s'en sert pour donner la priorité à la
    spécialité qui en est responsable (voir `K1Brain.score`).
    """
    symptoms = set(symptoms)
    # Masquage des négations avant ET après normalisation : les contractions
    # anglaises (« doesn't ») ne sont reconnues qu'avec leur apostrophe, les
    # tournures françaises (« n'ai plus ») qu'une fois l'apostrophe retirée.
    plain = normalize_french(text or "")
    masked = mask_negated_scopes(normalize_french(mask_negated_scopes((text or "").lower())))
    reasons: List[str] = []

    critical = sorted(symptoms & CRITICAL_SYMPTOMS)
    high = sorted(symptoms & HIGH_SYMPTOMS)
    moderate = sorted(symptoms & MODERATE_SYMPTOMS)

    if critical:
        score, driving = SCORE_CRITICAL, critical
        reasons.append(f"symptôme critique : {', '.join(critical)}")
    elif high:
        score, driving = SCORE_HIGH, high
        reasons.append(f"symptôme de gravité élevée : {', '.join(high)}")
    elif moderate:
        score, driving = SCORE_MODERATE, []
        reasons.append(f"symptôme de gravité modérée : {', '.join(moderate)}")
    elif symptoms:
        score, driving = 1, []
        reasons.append("symptômes bénins uniquement")
    else:
        score, driving = 0, []

    for pivot, associated, label in CRITICAL_COMBINATIONS:
        if pivot in symptoms and symptoms & associated:
            score = max(score, SCORE_CRITICAL)
            driving = sorted(set(driving) | {pivot} | (symptoms & associated & (HIGH_SYMPTOMS | CRITICAL_SYMPTOMS)))
            reasons.append(f"combinaison critique : {label}")

    vital = _first_match(_VITAL, plain, masked)
    if vital:
        score = max(score, SCORE_CRITICAL)
        reasons.append(f"formulation d'urgence vitale : « {vital} »")

    textual_evidence = bool(vital)
    if len(high) >= 2 and not critical:
        score += 1
        reasons.append("plusieurs symptômes de gravité élevée (+1)")
    for compiled, label, delta in ((_INTENSITY, "intensité", 1), (_SUDDEN, "début brutal", 1),
                                   (_HELP, "appel à l'aide", 1), (_CHRONIC, "ancienneté", -1)):
        found = _first_match(compiled, plain, masked)
        if found:
            score += delta
            textual_evidence = textual_evidence or delta > 0
            reasons.append(f"{label} : « {found} » ({delta:+d})")
    if age is not None and age > 0 and (age < VULNERABLE_AGE_BELOW or age >= VULNERABLE_AGE_FROM):
        if symptoms or textual_evidence:  # l'âge seul ne fait pas un cas
            score += 1
            reasons.append(f"âge vulnérable : {age:g} ans (+1)")

    if not symptoms and not textual_evidence:
        level = UNKNOWN
        reasons.append("aucun symptôme reconnu ni indice de gravité dans le texte")
    else:
        level = level_from_score(score)

    if urgent and level in (UNKNOWN, "LOW", "MEDIUM"):
        level = "HIGH"
        reasons.append("urgence signalée par l'appelant : niveau relevé à HIGH")

    return {"level": level, "score": score, "reasons": reasons, "driving_symptoms": list(driving)}
