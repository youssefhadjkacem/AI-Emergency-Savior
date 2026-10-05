"""
Jeu de contrôle en français : 24 cas écrits APRÈS le gel du lexique français.

Pourquoi ce jeu existe : le lexique `french_lexicon.py` (dans le Space) a
été rédigé par la même personne que les 29 cas de `pipeline_cases.py`, et
après les avoir lus. Les résultats du lexique sur ces 29 cas sont donc
optimistes : rien ne garantit que les formulations qu'il reconnaît ne sont
pas, en partie, celles du jeu de test.

Protocole suivi pour limiter ce biais :
  1. le lexique a été écrit, puis gelé ;
  2. ces 24 cas ont été écrits ensuite, avec d'autres tableaux cliniques et
     d'autres tournures, sans consulter le lexique ;
  3. le lexique n'a PAS été modifié après leur première exécution, quels
     que soient les échecs observés.

Limite qui demeure : auteur unique. Ces cas mesurent la robustesse du
lexique à des formulations nouvelles du même auteur, pas à celles de vrais
patients.

Composition : un cas par spécialité (H01-H22) et deux cas de négation
(H23, H24). Français uniquement (`text_en` vide). Même statut de vérité
terrain que `pipeline_cases.py` : hypothèse de test, pas vérité clinique.
"""

from __future__ import annotations

from typing import List

from .pipeline_cases import PipelineCase, Severity


def _case(case_id, title, text_fr, age, severity, expected_specialty, expected_symptoms, rationale) -> PipelineCase:
    return PipelineCase(
        case_id=case_id, title=title, text_fr=text_fr, text_en="", age=age, severity=severity,
        location=None, budget=None, expected_specialty=expected_specialty,
        expected_symptoms=tuple(expected_symptoms), rationale=rationale,
    )


HELDOUT_CASES: List[PipelineCase] = [
    _case("H01", "Eczéma",
          "J'ai plein de plaques rouges qui me grattent sur les bras et derrière les genoux, la peau est toute "
          "sèche et ça suinte un peu par endroits.",
          31, Severity.low, "Dermatologist", ["skin_rash", "itching", "dry_skin"],
          "Lésions cutanées prurigineuses sur peau sèche : dermatologie."),
    _case("H02", "Allergie alimentaire",
          "Dès que je mange des cacahuètes j'ai les lèvres qui gonflent, des plaques d'urticaire partout et ça "
          "me démange.",
          22, Severity.medium, "Allergist", ["food_allergy_reaction", "angioedema", "urticaria", "itching"],
          "Réaction reproductible à un aliment, avec œdème des lèvres et urticaire : allergologie."),
    _case("H03", "Dysphonie et obstruction nasale",
          "J'ai la voix complètement cassée depuis dix jours, ça me fait mal quand j'avale et j'ai l'impression "
          "d'avoir le nez pris en permanence.",
          44, Severity.low, "Otolaryngologist", ["hoarseness", "throat_irritation", "congestion"],
          "Voix, gorge et nez : ORL."),
    _case("H04", "Insuffisance cardiaque débutante",
          "Depuis quelques semaines je m'essouffle dès que je monte un étage, mes chevilles enflent le soir et "
          "je sens mon cœur qui s'emballe par moments.",
          66, Severity.medium, "Cardiologist",
          ["shortness_of_breath_on_exertion", "breathlessness", "ankle_swelling", "palpitations", "fast_heart_rate"],
          "Dyspnée d'effort, œdèmes des chevilles, palpitations : cardiologie."),
    _case("H05", "Hémoptysie et toux prolongée",
          "Je crache du sang depuis deux jours, j'ai une toux qui ne passe pas depuis un mois et j'ai du mal à "
          "reprendre mon souffle.",
          58, Severity.high, "Pulmonologist",
          ["hemoptysis", "blood_in_sputum", "chronic_cough", "cough", "breathlessness"],
          "Crachats de sang et toux chronique : pneumologie."),
    _case("H06", "Saignements répétés",
          "Je saigne du nez presque tous les jours sans raison, la moindre coupure met une éternité à s'arrêter "
          "et j'ai des hématomes partout sur les jambes.",
          35, Severity.medium, "Hematologist", ["easy_bleeding", "prolonged_bleeding", "bruising"],
          "Épistaxis répétées, saignement prolongé, hématomes : hématologie."),
    _case("H07", "Varices douloureuses",
          "J'ai des varices sur les deux jambes qui me font mal en fin de journée, mes jambes enflent et la peau "
          "autour des chevilles a changé de couleur.",
          61, Severity.low, "Phlebologist", ["swollen_blood_vessels", "swollen_legs"],
          "Varices, œdème des jambes, troubles cutanés des chevilles : phlébologie."),
    _case("H08", "Reflux et sang dans les selles",
          "J'ai des brûlures d'estomac après chaque repas, des remontées acides la nuit et il y a du sang dans "
          "mes selles depuis hier.",
          48, Severity.medium, "Gastroenterologist", ["heartburn", "acidity", "bloody_stool"],
          "Pyrosis, reflux, rectorragie : gastro-entérologie."),
    _case("H09", "Hépatomégalie avec ascite",
          "Le médecin de garde a trouvé que j'avais le foie gros, j'ai le teint jaune, je me gratte tout le "
          "temps et mon ventre se remplit d'eau.",
          57, Severity.high, "Hepatologist", ["hepatomegaly", "yellowish_skin", "itching", "fluid_overload"],
          "Gros foie, ictère, prurit, ascite : hépatologie."),
    _case("H10", "Masse du sein avec amaigrissement",
          "J'ai senti une boule dure dans le sein gauche, j'ai beaucoup maigri sans raison ces derniers mois et "
          "je suis crevée en permanence.",
          52, Severity.medium, "Oncologist",
          ["breast_lump", "unexplained_weight_loss", "weight_loss", "fatigue", "fatigue_severe"],
          "Masse mammaire, amaigrissement inexpliqué, asthénie : oncologie."),
    _case("H11", "Retard staturo-pondéral du nourrisson",
          "Mon bébé de huit mois ne prend plus de poids, il est en retard par rapport aux autres pour se tenir "
          "assis et il pleure sans arrêt.",
          1, Severity.medium, "Pediatrician", ["failure_to_thrive", "delayed_development", "excessive_crying"],
          "Nourrisson, stagnation pondérale, retard des acquisitions : pédiatrie."),
    _case("H12", "Crise d'épilepsie",
          "Mon mari a fait une crise d'épilepsie ce matin, depuis il a des trous de mémoire, la main qui "
          "tremble et il cherche ses mots.",
          49, Severity.high, "Neurologist", ["seizures", "memory_loss", "tremor", "speech_difficulty"],
          "Crise convulsive, troubles mnésiques et du langage, tremblement : neurologie."),
    _case("H13", "Œil rouge douloureux",
          "J'ai l'œil gauche tout rouge et très douloureux depuis hier, je vois flou et la lumière me fait mal.",
          39, Severity.medium, "Ophthalmologist",
          ["eye_redness", "redness_of_eyes", "eye_pain", "blurred_and_distorted_vision", "visual_disturbances",
           "photophobia"],
          "Œil rouge, douloureux, baisse de vision, photophobie : ophtalmologie."),
    _case("H14", "Épisode dépressif",
          "Depuis la mort de mon père je n'ai plus goût à rien, je ne dors presque plus, je m'isole et j'ai des "
          "idées noires.",
          41, Severity.medium, "Psychiatrist", ["depression", "sleep_disturbance", "social_withdrawal"],
          "Anhédonie, insomnie, retrait social, idées noires : psychiatrie."),
    _case("H15", "Polyarthrite des mains",
          "J'ai les poignets et les doigts gonflés et douloureux des deux côtés, c'est pire au réveil où je "
          "suis complètement rouillée pendant une bonne heure.",
          47, Severity.low, "Rheumatologist", ["swelling_joints", "joint_pain", "morning_stiffness"],
          "Arthrites bilatérales avec dérouillage matinal prolongé : rhumatologie."),
    _case("H16", "Entorse du genou",
          "Je me suis tordu le genou au foot, depuis il est bloqué, je ne peux plus le plier et je marche en "
          "boitant.",
          27, Severity.medium, "Orthopedic", ["limited_range_of_motion", "limping"],
          "Traumatisme du genou avec blocage et boiterie : orthopédie."),
    _case("H17", "Fièvre après morsure",
          "J'ai été mordu par un chien errant il y a une semaine, la plaie est rouge et depuis trois jours j'ai "
          "de la fièvre qui monte par pics avec de gros frissons et des ganglions sous le bras.",
          33, Severity.high, "Infectiologist",
          ["animal_contact", "high_fever_spiking", "rigors", "chills", "shivering", "lymph_node_swelling",
           "swelled_lymph_nodes"],
          "Morsure animale, fièvre en pics, frissons, adénopathie : infectiologie."),
    _case("H18", "Hypothyroïdie",
          "J'ai pris dix kilos en six mois sans manger plus, j'ai tout le temps froid, je perds mes cheveux et "
          "on m'a dit que j'avais la thyroïde gonflée.",
          45, Severity.low, "Endocrinologist", ["weight_gain", "cold_intolerance", "hair_loss", "enlarged_thyroid"],
          "Prise de poids, frilosité, chute de cheveux, goitre : endocrinologie."),
    _case("H19", "Aménorrhée avec bouffées de chaleur",
          "Je n'ai plus mes règles depuis quatre mois alors que je ne suis pas enceinte, j'ai des bouffées de "
          "chaleur et des douleurs dans le bas du ventre.",
          43, Severity.low, "Gynecologist", ["amenorrhea", "hot_flashes", "pelvic_pain"],
          "Aménorrhée, bouffées de chaleur, douleurs pelviennes : gynécologie."),
    _case("H20", "Troubles urinaires chez l'homme",
          "J'ai des fuites urinaires quand je tousse ou que je ris, j'ai du mal à uriner le matin et une "
          "douleur dans un testicule.",
          62, Severity.low, "Urologist", ["urinary_incontinence", "dysuria", "testicular_pain"],
          # « quand je tousse » décrit une circonstance, pas une toux : un
          # extracteur parfait ne doit pas en tirer `cough`.
          "Incontinence d'effort, dysurie, douleur testiculaire : urologie."),
    _case("H21", "Protéinurie et oligurie",
          "On m'a trouvé des protéines dans les urines, j'ai les paupières gonflées au réveil, je n'urine "
          "presque plus et ma tension est montée à 18.",
          55, Severity.high, "Nephrologist",
          ["proteinuria", "periorbital_edema", "oliguria", "decreased_urine_output", "hypertension_renal"],
          "Protéinurie, œdème des paupières, oligurie, hypertension : néphrologie."),
    _case("H22", "Fièvre prolongée",
          "Je traîne une fièvre depuis dix jours avec des maux de tête, des courbatures, je vomis de temps en "
          "temps et je me sens patraque.",
          38, Severity.medium, "Internal Medicine", ["high_fever", "headache", "muscle_pain", "vomiting", "malaise"],
          "Fièvre prolongée avec signes généraux non localisés : médecine interne."),
    # ── Négation ──────────────────────────────────────────────────────────
    _case("H23", "Toux productive, autres signes niés",
          "Je tousse beaucoup depuis une semaine avec des crachats jaunes, mais je n'ai pas de fièvre, pas "
          "d'essoufflement et je ne crache pas de sang.",
          36, Severity.low, "Pulmonologist", ["cough", "phlegm"],
          # Fièvre, essoufflement et crachats de sang sont niés.
          "Toux et expectoration sans signe de gravité : pneumologie."),
    _case("H24", "Palpitations, douleur thoracique niée",
          "Je n'ai aucune douleur dans la poitrine, par contre j'ai des palpitations et la tête qui tourne "
          "quand je me lève.",
          53, Severity.medium, "Cardiologist", ["palpitations", "dizziness"],
          # La douleur thoracique est niée ; ce qui suit « par contre » ne l'est pas.
          "Palpitations et lipothymies : cardiologie."),
]
