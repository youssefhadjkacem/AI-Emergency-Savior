"""
Jeu de cas de test du pipeline principal (entrée patient -> Top 3 prestataires).

29 cas écrits à la main, répartis en trois groupes :

  - 22 cas "standards" : un par spécialité de l'arbre K1 (les 22 feuilles,
    donc les 7 branches), avec un tableau clinique typique ;
  - 5 cas "ambigus" (`ambiguous=True`) : symptômes à cheval sur deux
    spécialités, avec une spécialité attendue ET une alternative acceptable ;
  - 2 cas de robustesse : une négation explicite et une plainte vague sans
    vocabulaire médical.

── Statut de la vérité terrain ──────────────────────────────────────────
`expected_specialty` est une HYPOTHÈSE DE TEST, pas une vérité clinique :
c'est la spécialité vers laquelle le bon sens médical et la logique de
l'arbre hiérarchique K1 (symptômes clés / exclusifs de chaque feuille)
orientent le tableau décrit. Elle a été fixée avant toute exécution du
pipeline et n'a pas été retouchée ensuite. Le raisonnement est donné dans
`rationale` pour chaque cas.

`expected_symptoms` liste les identifiants du vocabulaire du système (les
300 symptômes de `Specialist_Enhanced.xlsx`) qu'un extracteur parfait
devrait tirer du texte. Elle sert à mesurer l'étape NLP isolément et à
rejouer la classification avec une extraction parfaite ("oracle"), pour
savoir si une erreur vient de l'extraction ou du classifieur.

── Deux langues ──────────────────────────────────────────────────────────
`text_fr` est la formulation d'origine, telle qu'un patient ou un proche la
dirait (le Space speech configure Whisper en français). `text_en` en est la
traduction fidèle, écrite à la main : c'est ce qu'une traduction correcte
produirait. Les textes sont rédigés en langage courant, sans chercher à
coller au dictionnaire de synonymes de l'extracteur.

── Gravité ───────────────────────────────────────────────────────────────
Le pipeline ne connaît pas les niveaux LOW / MEDIUM / HIGH / CRITICAL : il
ne reçoit qu'un booléen `urgent`. La gravité est donc une métadonnée du
cas, convertie avec la même règle que `backend/main.py`
(`risk_level in ("HIGH", "CRITICAL")`) — voir `PipelineCase.urgent`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple


class Severity(str, Enum):
    low = "LOW"
    medium = "MEDIUM"
    high = "HIGH"
    critical = "CRITICAL"


@dataclass(frozen=True)
class PipelineCase:
    case_id: str
    title: str
    text_fr: str
    text_en: str
    age: int
    severity: Severity
    # Ville du patient (correspondance exacte avec la colonne "Ville" de la
    # base K2) et budget en TND. None = contrainte non renseignée.
    location: Optional[str]
    budget: Optional[float]
    expected_specialty: str
    expected_symptoms: Tuple[str, ...]
    rationale: str
    ambiguous: bool = False
    # Cas ambigus uniquement : seconde spécialité défendable cliniquement.
    acceptable_alternative: Optional[str] = None

    @property
    def urgent(self) -> bool:
        return self.severity in (Severity.high, Severity.critical)


CASES: List[PipelineCase] = [
    # ══════════════════════════════════════════════════════════════════════
    # Branche B1 — Peau · Allergie · ORL
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C01",
        title="Acné pustuleuse étendue",
        text_fr=(
            "Depuis deux semaines j'ai des boutons remplis de pus sur le visage et dans le dos, "
            "avec des points noirs et quelques cloques. Ça me démange beaucoup et la peau pèle."
        ),
        text_en=(
            "For two weeks I have had pimples full of pus on my face and back, with blackheads "
            "and a few blisters. It itches a lot and the skin is peeling."
        ),
        age=19, severity=Severity.low, location="Monastir", budget=50,
        expected_specialty="Dermatologist",
        expected_symptoms=("pus_filled_pimples", "blackheads", "blister", "itching", "skin_peeling"),
        # Lésions cutanées pures (pustules, cloques) : symptômes exclusifs de
        # la feuille Dermatologist. Le piège : `skin_peeling` est une clé de
        # Phlebologist dans l'arbre.
        rationale="Lésions cutanées sans signe général : dermatologie.",
    ),
    PipelineCase(
        case_id="C02",
        title="Rhinite allergique saisonnière",
        text_fr=(
            "Chaque printemps c'est pareil : j'éternue toute la journée, j'ai le nez qui coule, "
            "les yeux qui pleurent et qui grattent, et le nez bouché la nuit."
        ),
        text_en=(
            "Every spring it is the same thing: I sneeze all day, my nose runs, my eyes water "
            "and itch, and my nose is blocked at night."
        ),
        age=27, severity=Severity.low, location="Bizerte", budget=60,
        expected_specialty="Allergist",
        expected_symptoms=("continuous_sneezing", "runny_nose", "watering_from_eyes", "itching",
                           "congestion", "seasonal_symptoms"),
        # Caractère saisonnier + éternuements + larmoiement : les trois
        # symptômes exclusifs de la feuille Allergist.
        rationale="Symptômes ORL récurrents et saisonniers : allergologie plutôt qu'ORL.",
    ),
    PipelineCase(
        case_id="C03",
        title="Vertiges rotatoires et anosmie",
        text_fr=(
            "Depuis trois jours la pièce tourne dès que je me lève, je ne tiens pas bien debout, "
            "j'ai la gorge irritée et j'ai perdu l'odorat."
        ),
        text_en=(
            "For three days the room has been spinning whenever I stand up, I feel unsteady on my "
            "feet, my throat is irritated and I have lost my sense of smell."
        ),
        age=45, severity=Severity.medium, location="Tunis", budget=None,
        expected_specialty="Otolaryngologist",
        expected_symptoms=("spinning_movements", "unsteadiness", "throat_irritation", "loss_of_smell"),
        # Vertige rotatoire + instabilité + gorge + odorat : les quatre
        # symptômes exclusifs de la feuille Otolaryngologist.
        rationale="Vertige d'allure périphérique avec atteinte de la gorge et de l'odorat : ORL.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Branche B2 — Cœur · Poumons · Sang · Veines
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C04",
        title="Douleur thoracique constrictive irradiant au bras",
        text_fr=(
            "Mon mari a une douleur très forte dans la poitrine depuis vingt minutes, ça lui serre "
            "comme un étau et ça descend dans le bras gauche. Il transpire beaucoup et il dit que "
            "son cœur bat de façon irrégulière."
        ),
        text_en=(
            "My husband has had a very strong pain in his chest for twenty minutes, it feels tight "
            "like a vise and it goes down his left arm. He is sweating a lot and he says his heart "
            "is beating irregularly."
        ),
        age=58, severity=Severity.critical, location="Tunis", budget=120,
        expected_specialty="Cardiologist",
        expected_symptoms=("chest_pain", "chest_tightness", "left_arm_pain", "sweating", "irregular_heartbeat"),
        rationale="Tableau de syndrome coronarien aigu : cardiologie.",
    ),
    PipelineCase(
        case_id="C05",
        title="Toux chronique, sifflements et crachats rouillés",
        text_fr=(
            "Mon père tousse depuis des semaines, et depuis ce matin il a la respiration qui siffle "
            "et il crache des glaires couleur rouille avec un peu de sang. Il est essoufflé rien "
            "qu'en allant aux toilettes et il a de la fièvre."
        ),
        text_en=(
            "My father has been coughing for weeks, and since this morning he is wheezing and "
            "coughing up rust-colored phlegm with a little blood. He gets out of breath just "
            "walking to the bathroom and he has a fever."
        ),
        age=67, severity=Severity.high, location="Sfax", budget=70,
        expected_specialty="Pulmonologist",
        expected_symptoms=("chronic_cough", "cough", "wheezing", "rusty_sputum", "phlegm",
                           "blood_in_sputum", "breathlessness", "high_fever"),
        # Piège de l'arbre : `blood_in_sputum` est un symptôme exclusif de
        # Internal Medicine, alors que `hemoptysis` est exclusif de Pulmonologist.
        rationale="Toux prolongée, sibilants, expectoration rouillée et sanglante : pneumologie.",
    ),
    PipelineCase(
        case_id="C06",
        title="Syndrome hémorragique",
        text_fr=(
            "Je fais des bleus au moindre choc, mes gencives saignent longtemps quand je me brosse "
            "les dents, des petits points rouges sont apparus sur mes jambes et je suis très pâle "
            "et fatigué."
        ),
        text_en=(
            "I bruise at the slightest bump, my gums bleed for a long time when I brush my teeth, "
            "small red dots have appeared on my legs and I am very pale and tired."
        ),
        age=42, severity=Severity.medium, location="Le Kef", budget=60,
        expected_specialty="Hematologist",
        expected_symptoms=("easy_bruising", "bruising", "prolonged_bleeding", "easy_bleeding",
                           "petechiae", "pallor", "fatigue"),
        rationale="Ecchymoses faciles, saignements prolongés, pétéchies, pâleur : hématologie.",
    ),
    PipelineCase(
        case_id="C07",
        title="Insuffisance veineuse",
        text_fr=(
            "J'ai les jambes lourdes et gonflées le soir, de grosses veines tordues sur les "
            "mollets et des crampes la nuit."
        ),
        text_en=(
            "My legs are heavy and swollen in the evening, I have big twisted veins on my calves "
            "and cramps at night."
        ),
        age=50, severity=Severity.low, location="Bizerte", budget=50,
        expected_specialty="Phlebologist",
        expected_symptoms=("swollen_legs", "prominent_veins_on_calf", "swollen_blood_vessels", "cramps"),
        rationale="Jambes gonflées, varices des mollets, crampes nocturnes : phlébologie.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Branche B3 — Digestif · Foie · Oncologie · Pédiatrie
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C08",
        title="Gastro-entérite aiguë",
        text_fr=(
            "Depuis hier soir j'ai très mal à l'estomac avec des crampes, j'ai vomi trois fois et "
            "j'ai la diarrhée. J'ai tout le temps la nausée et je ne garde rien."
        ),
        text_en=(
            "Since last night I have had strong stomach pain with cramps, I vomited three times "
            "and I have diarrhea. I feel nauseous all the time and I cannot keep anything down."
        ),
        age=34, severity=Severity.medium, location="Nabeul", budget=60,
        expected_specialty="Gastroenterologist",
        # "crampes" désigne ici des crampes d'estomac : un extracteur parfait
        # ne devrait PAS en tirer `cramps`, qui est dans l'arbre un symptôme
        # exclusif de Phlebologist (crampes des jambes).
        expected_symptoms=("stomach_pain", "vomiting", "diarrhoea", "nausea"),
        rationale="Douleur gastrique, vomissements, diarrhée, nausées : gastro-entérologie.",
    ),
    PipelineCase(
        case_id="C09",
        title="Ictère chez un patient alcoolique",
        text_fr=(
            "Mon frère a les yeux et la peau qui sont devenus jaunes ces derniers jours, ses "
            "urines sont très foncées et son ventre est gonflé. Il boit beaucoup d'alcool et il "
            "est épuisé."
        ),
        text_en=(
            "My brother's eyes and skin have turned yellow over the last few days, his urine is "
            "very dark and his belly is swollen. He drinks a lot of alcohol and he is exhausted."
        ),
        age=49, severity=Severity.high, location="Tunis", budget=None,
        expected_specialty="Hepatologist",
        expected_symptoms=("yellowing_of_eyes", "yellowish_skin", "dark_urine", "swelling_of_stomach",
                           "history_of_alcohol_consumption", "fatigue"),
        rationale="Ictère, urines foncées, ascite probable, alcool : hépatologie.",
    ),
    PipelineCase(
        case_id="C10",
        title="Altération de l'état général avec adénopathies",
        text_fr=(
            "J'ai perdu douze kilos en trois mois sans faire de régime, je me réveille trempé de "
            "sueur la nuit, je suis épuisé en permanence et j'ai trouvé des boules dures dans le "
            "cou et sous le bras."
        ),
        text_en=(
            "I have lost twelve kilos in three months without dieting, I wake up drenched in sweat "
            "at night, I am exhausted all the time and I found hard lumps in my neck and under my arm."
        ),
        age=59, severity=Severity.medium, location="Sfax", budget=100,
        expected_specialty="Oncologist",
        expected_symptoms=("unexplained_weight_loss", "weight_loss", "night_sweats_drenching",
                           "night_sweats", "fatigue_severe", "fatigue", "lymphadenopathy"),
        rationale="Amaigrissement inexpliqué, sueurs nocturnes profuses, adénopathies : oncologie.",
    ),
    PipelineCase(
        case_id="C11",
        title="Convulsion fébrile chez un enfant de 3 ans",
        text_fr=(
            "Mon fils de trois ans a une forte fièvre et il a fait une convulsion avec la fièvre "
            "cette nuit. Il a mal au ventre, il pleure tout le temps et il refuse de manger."
        ),
        text_en=(
            "My three-year-old son has a high fever and he had a seizure with the fever last night. "
            "He has belly pain, he cries all the time and he refuses to eat."
        ),
        age=3, severity=Severity.medium, location="Tunis", budget=80,
        expected_specialty="Pediatrician",
        expected_symptoms=("high_fever", "febrile_seizures", "belly_pain", "excessive_crying",
                           "loss_of_appetite"),
        rationale="Enfant de 3 ans, convulsion fébrile, douleur abdominale : pédiatrie.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Branche B4 — Neuro · Ophtalmo · Psychiatrie
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C12",
        title="Suspicion d'AVC",
        text_fr=(
            "Ma mère n'arrive plus à bouger le bras droit ni la jambe droite d'un seul coup, un "
            "côté de son visage tombe et elle parle de façon pâteuse. Elle a l'air confuse et ne "
            "me reconnaît pas."
        ),
        text_en=(
            "My mother suddenly cannot move her right arm and right leg, one side of her face is "
            "drooping and her speech is slurred. She seems confused and does not recognize me."
        ),
        age=71, severity=Severity.critical, location="Sousse", budget=None,
        expected_specialty="Neurologist",
        expected_symptoms=("weakness_of_one_body_side", "facial_drooping", "slurred_speech",
                           "altered_sensorium"),
        rationale="Déficit hémicorporel brutal, paralysie faciale, dysarthrie, confusion : neurologie.",
    ),
    PipelineCase(
        case_id="C13",
        title="Suspicion de décollement de rétine",
        text_fr=(
            "Depuis ce matin je vois des éclairs lumineux et des points noirs qui flottent dans "
            "l'œil droit, puis une ombre a recouvert une partie de ma vue. Maintenant je ne vois "
            "presque plus de cet œil et il me fait mal."
        ),
        text_en=(
            "Since this morning I have been seeing flashes of light and black floating spots in my "
            "right eye, then a shadow covered part of my sight. Now I can hardly see with that eye "
            "and it hurts."
        ),
        age=61, severity=Severity.high, location="Nabeul", budget=90,
        expected_specialty="Ophthalmologist",
        expected_symptoms=("flashing_lights", "floaters", "vision_loss", "eye_pain"),
        rationale="Phosphènes, corps flottants, amputation du champ visuel d'un œil : ophtalmologie.",
    ),
    PipelineCase(
        case_id="C14",
        title="Épisode psychotique aigu",
        text_fr=(
            "Ma sœur ne dort plus depuis des jours, elle entend des voix qui n'existent pas et elle "
            "est persuadée que les voisins l'espionnent. Elle a fait des crises de panique et elle "
            "dit qu'elle veut en finir avec la vie."
        ),
        text_en=(
            "My sister has not slept for days, she hears voices that are not there and she is "
            "convinced the neighbors are spying on her. She has had panic attacks and she says she "
            "wants to end her life."
        ),
        age=24, severity=Severity.high, location="Sfax", budget=None,
        expected_specialty="Psychiatrist",
        expected_symptoms=("sleep_disturbance", "hallucinations", "delusions", "panic_attacks",
                           "suicidal_thoughts"),
        rationale="Insomnie, hallucinations auditives, idées délirantes, idées suicidaires : psychiatrie.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Branche B5 — Articulations · Os · Infectieux
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C15",
        title="Polyarthrite avec raideur matinale",
        text_fr=(
            "Tous les matins j'ai les doigts et les genoux raides pendant plus d'une heure, les "
            "articulations sont gonflées et douloureuses, et ça empire depuis plusieurs mois."
        ),
        text_en=(
            "Every morning my fingers and knees are stiff for more than an hour, the joints are "
            "swollen and painful, and it has been getting worse for several months."
        ),
        age=55, severity=Severity.low, location="Monastir", budget=70,
        expected_specialty="Rheumatologist",
        expected_symptoms=("morning_stiffness", "swelling_joints", "joint_pain", "knee_pain"),
        rationale="Raideur matinale prolongée et gonflements articulaires chroniques : rhumatologie.",
    ),
    PipelineCase(
        case_id="C16",
        title="Traumatisme du poignet après chute",
        text_fr=(
            "Je suis tombé de moto hier. Mon poignet est déformé et je n'arrive plus à le bouger, "
            "l'articulation est complètement raide et j'ai des spasmes musculaires dans l'avant-bras."
        ),
        text_en=(
            "I fell off my motorbike yesterday. My wrist is deformed and I cannot move it, the "
            "joint is completely stiff and I have muscle spasms in my forearm."
        ),
        age=31, severity=Severity.medium, location="Sousse", budget=None,
        expected_specialty="Orthopedic",
        expected_symptoms=("bone_deformity", "limited_range_of_motion", "joint_stiffness", "muscle_spasm"),
        rationale="Déformation post-traumatique avec impotence fonctionnelle : orthopédie.",
    ),
    PipelineCase(
        case_id="C17",
        title="Fièvre au retour de voyage",
        text_fr=(
            "Je suis rentré d'un voyage en Afrique subsaharienne il y a dix jours. Depuis j'ai des "
            "accès de fièvre très élevée avec des frissons violents, des ganglions gonflés dans le "
            "cou et une éruption qui s'étend sur tout le corps."
        ),
        text_en=(
            "I returned from a trip to sub-Saharan Africa ten days ago. Since then I have had bouts "
            "of very high fever with violent shivering, swollen lymph nodes in my neck and a rash "
            "that is spreading all over my body."
        ),
        age=36, severity=Severity.high, location="Tunis", budget=150,
        expected_specialty="Infectiologist",
        expected_symptoms=("travel_history", "high_fever_spiking", "rigors", "shivering",
                           "lymph_node_swelling", "swelled_lymph_nodes", "rash_spreading", "skin_rash"),
        rationale="Fièvre en pics avec frissons, adénopathies et éruption après un voyage : infectiologie.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Branche B6 — Hormones · Gynécologie · Urologie · Rein
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C18",
        title="Diabète déséquilibré",
        text_fr=(
            "J'ai soif tout le temps, j'urine très souvent même la nuit, j'ai toujours faim mais "
            "j'ai maigri. Mes mesures de glycémie partent dans tous les sens."
        ),
        text_en=(
            "I am thirsty all the time, I urinate very often even at night, I am always hungry but "
            "I have lost weight. My blood sugar readings are all over the place."
        ),
        age=52, severity=Severity.medium, location="Kairouan", budget=80,
        expected_specialty="Endocrinologist",
        expected_symptoms=("excessive_thirst", "polyuria", "polyuria_nocturia", "excessive_hunger",
                           "weight_loss", "irregular_sugar_level"),
        rationale="Polyurie, polydipsie, polyphagie, glycémie instable : endocrinologie.",
    ),
    PipelineCase(
        case_id="C19",
        title="Règles douloureuses et abondantes",
        text_fr=(
            "J'ai des règles très douloureuses avec des saignements abondants, des douleurs dans le "
            "bas-ventre et le bassin, et des pertes vaginales inhabituelles depuis une semaine."
        ),
        text_en=(
            "I have very painful periods with heavy bleeding, pain in my lower belly and pelvis, "
            "and an unusual vaginal discharge for a week."
        ),
        age=29, severity=Severity.medium, location="Tunis", budget=90,
        expected_specialty="Gynecologist",
        expected_symptoms=("dysmenorrhea", "menorrhagia", "pelvic_pain", "vaginal_discharge"),
        rationale="Dysménorrhée, ménorragies, douleurs pelviennes, leucorrhées : gynécologie.",
    ),
    PipelineCase(
        case_id="C20",
        title="Colique néphrétique",
        text_fr=(
            "J'ai une douleur atroce dans le côté qui vient par vagues et qui descend vers l'aine, "
            "ça brûle quand j'urine et il y a du sang dans mes urines."
        ),
        text_en=(
            "I have a terrible pain in my side that comes in waves and goes down to the groin, it "
            "burns when I urinate and there is blood in my urine."
        ),
        age=38, severity=Severity.high, location="Sousse", budget=100,
        expected_specialty="Urologist",
        expected_symptoms=("renal_colic", "flank_pain", "burning_micturition", "dysuria", "hematuria"),
        rationale="Douleur lombaire paroxystique irradiant à l'aine, brûlures, hématurie : urologie.",
    ),
    PipelineCase(
        case_id="C21",
        title="Syndrome néphrotique / insuffisance rénale",
        text_fr=(
            "Depuis une semaine j'urine très peu et c'est mousseux. J'ai le visage bouffi autour "
            "des yeux le matin, les chevilles gonflées et une tension très élevée."
        ),
        text_en=(
            "For a week I have been passing very little urine and it is foamy. My face is puffy "
            "around the eyes in the morning, my ankles are swollen and my blood pressure is very high."
        ),
        age=64, severity=Severity.high, location="Tunis", budget=None,
        expected_specialty="Nephrologist",
        expected_symptoms=("oliguria", "decreased_urine_output", "frothy_urine", "periorbital_edema",
                           "puffy_face_and_eyes", "ankle_swelling", "hypertension_renal"),
        rationale="Oligurie, urines mousseuses, œdème périorbitaire, hypertension : néphrologie.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Branche B7 — Médecine interne
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="C22",
        title="Syndrome fébrile avec douleurs rétro-orbitaires",
        text_fr=(
            "J'ai une forte fièvre depuis quatre jours avec un gros mal de tête, une douleur "
            "derrière les yeux, je vomis et je me sens mal partout avec des courbatures."
        ),
        text_en=(
            "I have had a high fever for four days with a bad headache, pain behind my eyes, I am "
            "vomiting and I feel unwell all over with aching muscles."
        ),
        age=40, severity=Severity.medium, location="Sousse", budget=70,
        expected_specialty="Internal Medicine",
        expected_symptoms=("high_fever", "headache", "pain_behind_the_eyes", "vomiting", "malaise",
                           "muscle_pain"),
        rationale="Fièvre, céphalées, douleur rétro-orbitaire, malaise : les clés de la feuille Internal Medicine.",
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Cas ambigus — deux spécialités défendables
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="A01",
        title="Douleur thoracique à l'inspiration avec toux (pneumo ou cardio)",
        text_fr=(
            "J'ai une douleur dans la poitrine quand j'inspire à fond et je suis essoufflé, avec "
            "une toux sèche depuis hier. Mon cœur bat très vite."
        ),
        text_en=(
            "I have chest pain when I breathe in deeply and I am short of breath, with a dry cough "
            "since yesterday. My heart is beating very fast."
        ),
        age=63, severity=Severity.high, location="Tunis", budget=80,
        expected_specialty="Pulmonologist",
        acceptable_alternative="Cardiologist",
        expected_symptoms=("pleuritic_chest_pain", "chest_pain", "breathlessness", "cough", "fast_heart_rate"),
        # Douleur rythmée par la respiration + toux : origine pleuro-pulmonaire
        # d'abord. La tachycardie et la douleur thoracique rendent la
        # cardiologie défendable. Cas marqué urgent : il sollicite aussi le
        # bonus x2 appliqué à Cardiologist par `K1Brain.score`.
        rationale="Douleur pleurale et toux : pneumologie ; tachycardie et douleur thoracique : cardiologie.",
        ambiguous=True,
    ),
    PipelineCase(
        case_id="A02",
        title="Douleur abdominale avec subictère (hépato ou gastro)",
        text_fr=(
            "J'ai mal en haut à droite du ventre, des nausées et des vomissements, je n'ai plus "
            "d'appétit, et ma femme trouve que mes yeux sont un peu jaunes."
        ),
        text_en=(
            "I have pain in the upper right of my belly, nausea and vomiting, I have no appetite, "
            "and my wife says my eyes look a little yellow."
        ),
        age=46, severity=Severity.medium, location="Sfax", budget=70,
        expected_specialty="Hepatologist",
        acceptable_alternative="Gastroenterologist",
        expected_symptoms=("abdominal_pain", "nausea", "vomiting", "loss_of_appetite", "yellowing_of_eyes"),
        # Trois symptômes digestifs contre un seul signe hépatique : mais
        # l'ictère est le signe discriminant, donc hépatologie en premier.
        rationale="Signes digestifs non spécifiques, mais l'ictère oriente vers le foie.",
        ambiguous=True,
    ),
    PipelineCase(
        case_id="A03",
        title="Diplopie brutale avec céphalée (neuro ou ophtalmo)",
        text_fr=(
            "Je vois double d'un seul coup et j'ai un mal de tête violent, avec des "
            "engourdissements et des fourmillements dans la main gauche."
        ),
        text_en=(
            "I suddenly see double and I have a violent headache, with numbness and tingling in my "
            "left hand."
        ),
        age=54, severity=Severity.high, location="Monastir", budget=None,
        expected_specialty="Neurologist",
        acceptable_alternative="Ophthalmologist",
        expected_symptoms=("diplopia", "double_vision", "headache", "numbness_tingling"),
        # `diplopia` est un symptôme EXCLUSIF d'Ophthalmologist dans l'arbre,
        # mais associée à une céphalée brutale et à des paresthésies, la
        # diplopie relève d'une cause neurologique.
        rationale="Diplopie isolée : ophtalmologie ; avec céphalée et paresthésies : neurologie.",
        ambiguous=True,
    ),
    PipelineCase(
        case_id="A04",
        title="Brûlures urinaires et douleur pelvienne chez une femme (uro ou gynéco)",
        text_fr=(
            "Ça me brûle quand j'urine, j'ai tout le temps envie d'y aller, avec une gêne dans la "
            "vessie et une douleur dans le bas du bassin."
        ),
        text_en=(
            "It burns when I urinate, I constantly feel like I need to go, with discomfort in my "
            "bladder and pain in my lower pelvis."
        ),
        age=33, severity=Severity.low, location="Ariana", budget=60,
        expected_specialty="Urologist",
        acceptable_alternative="Gynecologist",
        expected_symptoms=("burning_micturition", "dysuria", "continuous_feel_of_urine",
                           "bladder_discomfort", "pelvic_pain"),
        # `bladder_discomfort` est une clé des deux feuilles ; `pelvic_pain`
        # est exclusif de Gynecologist, `dysuria` exclusif de Urologist.
        rationale="Tableau de cystite : urologie ; douleur pelvienne chez une femme : gynécologie défendable.",
        ambiguous=True,
    ),
    PipelineCase(
        case_id="A05",
        title="Gonalgie et coxalgie mécaniques (ortho ou rhumato)",
        text_fr=(
            "J'ai mal au genou et à la hanche quand je marche et je boite, le genou est gonflé et "
            "raide, surtout après être resté assis longtemps."
        ),
        text_en=(
            "My knee and my hip hurt when I walk and I limp, the knee is swollen and stiff, "
            "especially after sitting for a long time."
        ),
        age=68, severity=Severity.low, location="Mahdia", budget=50,
        expected_specialty="Orthopedic",
        acceptable_alternative="Rheumatologist",
        expected_symptoms=("knee_pain", "hip_joint_pain", "limping", "painful_walking",
                           "swelling_joints", "joint_stiffness"),
        # `knee_pain` et `hip_joint_pain` sont des clés communes aux deux
        # feuilles. Douleur à la marche et boiterie sans raideur matinale :
        # plutôt mécanique (arthrose), donc orthopédie.
        rationale="Douleur mécanique à la marche : orthopédie ; gonflement et raideur : rhumatologie défendable.",
        ambiguous=True,
    ),
    # ══════════════════════════════════════════════════════════════════════
    # Cas de robustesse
    # ══════════════════════════════════════════════════════════════════════
    PipelineCase(
        case_id="R01",
        title="Négations explicites",
        text_fr=(
            "J'ai mal à la gorge et le nez bouché depuis deux jours, mais je n'ai pas de fièvre, "
            "pas de toux et aucune douleur dans la poitrine."
        ),
        text_en=(
            "I have had a sore throat and a blocked nose for two days, but I have no fever, no "
            "cough and no chest pain."
        ),
        age=26, severity=Severity.low, location="Ben Arous", budget=50,
        expected_specialty="Otolaryngologist",
        # Fièvre, toux et douleur thoracique sont NIÉES : elles ne doivent
        # pas être extraites. `pipeline.py` annonce une extraction qui "gère
        # la négation".
        expected_symptoms=("throat_irritation", "congestion"),
        rationale="Gorge et nez seulement, sans signe général ni thoracique : ORL.",
    ),
    PipelineCase(
        case_id="R02",
        title="Plainte vague sans vocabulaire médical",
        text_fr=(
            "Je ne me sens vraiment pas bien, je me sens bizarre et j'ai mal partout, "
            "envoyez quelqu'un s'il vous plaît."
        ),
        text_en=(
            "I really do not feel well, I feel strange and I hurt everywhere, please send someone."
        ),
        age=47, severity=Severity.medium, location="Gafsa", budget=None,
        expected_specialty="Internal Medicine",
        # Aucun symptôme localisé : la seule orientation raisonnable est une
        # spécialité généraliste. Ce cas teste surtout ce que le pipeline
        # renvoie quand l'extraction ne trouve presque rien.
        expected_symptoms=("malaise",),
        rationale="Malaise général non localisé : médecine interne par défaut.",
    ),
]


def get_case(case_id: str) -> PipelineCase:
    for case in CASES:
        if case.case_id == case_id:
            return case
    raise KeyError(case_id)
