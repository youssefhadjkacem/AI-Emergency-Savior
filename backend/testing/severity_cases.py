"""
Cas de test de l'estimation de gravité : 24 descriptions annotées à la main,
6 par niveau (LOW, MEDIUM, HIGH, CRITICAL), 12 en français et 12 en anglais.

── Protocole ─────────────────────────────────────────────────────────────
Les règles de `severity.py` (dans le Space) ont été écrites, puis gelées ;
ces 24 cas ont été écrits ensuite et les règles n'ont pas été modifiées
après leur première exécution, quels que soient les échecs. Même précaution
que pour le jeu de contrôle français (`heldout_cases_fr.py`), et même
limite : l'auteur des cas est aussi l'auteur des règles.

── Statut de la vérité terrain ──────────────────────────────────────────
`expected_level` est une hypothèse de test posée par bon sens médical, pas
un triage validé par un clinicien. Repères utilisés :

    LOW       gêne sans retentissement, ancienne ou banale ; consultation
              programmée
    MEDIUM    symptôme franc et récent, sans signe de danger ; consultation
              dans les jours qui viennent
    HIGH      signe qui demande un avis médical dans la journée
    CRITICAL  risque vital ou neurologique immédiat ; secours sans délai

La frontière entre deux niveaux voisins est discutable par nature : c'est
pourquoi l'évaluation rapporte aussi l'exactitude « à un niveau près » et,
séparément, les sous-estimations, qui sont l'erreur dangereuse.

Ces cas sont évalués avec `urgent=False` : on mesure ce que le texte seul
permet d'estimer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SeverityCase:
    case_id: str
    language: str  # "fr" ou "en"
    text: str
    age: int
    expected_level: str
    rationale: str


SEVERITY_CASES: List[SeverityCase] = [
    # ── LOW ───────────────────────────────────────────────────────────────
    SeverityCase("S01", "fr", "J'ai le nez qui coule et j'éternue depuis deux jours, rien de grave, je voudrais "
                 "juste un conseil.", 30, "LOW", "Rhume banal."),
    SeverityCase("S02", "en", "I have had dry, itchy skin on my hands for a few months, it is annoying but not "
                 "painful.", 41, "LOW", "Gêne cutanée ancienne, sans douleur."),
    SeverityCase("S03", "fr", "J'ai des boutons d'acné sur le front depuis plusieurs mois et j'aimerais voir "
                 "quelqu'un.", 17, "LOW", "Acné chronique."),
    SeverityCase("S04", "en", "My knee is a little stiff in the morning, it has been like that for years.",
                 63, "LOW", "Raideur ancienne et légère."),
    SeverityCase("S05", "fr", "Je suis un peu fatigué ces temps-ci et je dors mal, sans autre problème.",
                 35, "LOW", "Fatigue et sommeil, sans autre signe."),
    SeverityCase("S06", "en", "I have a sore throat and a blocked nose since yesterday, no fever.",
                 28, "LOW", "Rhinopharyngite sans fièvre (fièvre niée)."),
    # ── MEDIUM ────────────────────────────────────────────────────────────
    SeverityCase("S07", "fr", "J'ai de la fièvre depuis hier avec des vomissements et la diarrhée, je n'arrive "
                 "pas à manger.", 33, "MEDIUM", "Gastro-entérite fébrile récente, sans signe de danger."),
    SeverityCase("S08", "en", "I have had a bad stomach pain since this morning and I threw up twice.",
                 26, "MEDIUM", "Douleur gastrique aiguë avec vomissements."),
    SeverityCase("S09", "fr", "J'ai des vertiges quand je me lève et des palpitations depuis deux jours.",
                 52, "MEDIUM", "Vertiges et palpitations récents, sans douleur ni malaise."),
    SeverityCase("S10", "en", "It burns when I urinate and I have had pain in my lower back since last night, "
                 "with a fever.", 37, "MEDIUM", "Infection urinaire fébrile probable."),
    SeverityCase("S11", "fr", "Je me suis tordu la cheville en courant, elle est gonflée et j'ai du mal à "
                 "marcher.", 24, "MEDIUM", "Entorse de cheville."),
    SeverityCase("S12", "en", "My daughter has had a high fever and an earache since last night and she is very "
                 "tired.", 8, "MEDIUM", "Otite fébrile chez un enfant de 8 ans."),
    # ── HIGH ──────────────────────────────────────────────────────────────
    SeverityCase("S13", "fr", "Je crache du sang depuis ce matin et je suis essoufflé.",
                 60, "HIGH", "Hémoptysie avec dyspnée."),
    SeverityCase("S14", "en", "I suddenly lost the vision in my left eye an hour ago.",
                 58, "HIGH", "Perte brutale de la vision d'un œil."),
    SeverityCase("S15", "fr", "J'ai une douleur atroce dans le côté qui descend vers l'aine et il y a du sang "
                 "dans mes urines.", 40, "HIGH", "Colique néphrétique avec hématurie."),
    SeverityCase("S16", "en", "My father is seeing things that are not there and he is convinced we want to "
                 "poison him.", 70, "HIGH", "Hallucinations et idées délirantes."),
    SeverityCase("S17", "fr", "Mon fils de deux ans a fait une convulsion avec de la fièvre tout à l'heure, il "
                 "va mieux maintenant.", 2, "HIGH", "Convulsion fébrile résolue chez un enfant de 2 ans."),
    SeverityCase("S18", "en", "My skin and eyes turned yellow this week and my stools are black.",
                 47, "HIGH", "Ictère avec méléna."),
    # ── CRITICAL ──────────────────────────────────────────────────────────
    SeverityCase("S19", "fr", "Mon père ne respire plus et il ne répond plus, venez vite !",
                 68, "CRITICAL", "Arrêt respiratoire, inconscience."),
    SeverityCase("S20", "en", "My wife collapsed, she is unconscious and not breathing, please send an "
                 "ambulance.", 55, "CRITICAL", "Inconscience et arrêt respiratoire."),
    SeverityCase("S21", "fr", "Ma mère a la bouche de travers d'un seul coup, elle n'arrive plus à parler et "
                 "son bras droit ne bouge plus.", 77, "CRITICAL", "Tableau d'AVC."),
    SeverityCase("S22", "en", "I have a crushing pain in my chest going down my left arm, I am sweating and I "
                 "feel like I am going to die.", 61, "CRITICAL", "Suspicion d'infarctus."),
    SeverityCase("S23", "fr", "Mon frère a avalé plein de médicaments, il dit qu'il veut en finir.",
                 23, "CRITICAL", "Intoxication médicamenteuse volontaire."),
    SeverityCase("S24", "en", "He is vomiting blood and he is very confused, he does not know where he is.",
                 50, "CRITICAL", "Hématémèse avec confusion."),
]
