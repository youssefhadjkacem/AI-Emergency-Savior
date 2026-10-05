"""
Tests du second round de corrections : classement monotone des prestataires
(chantier 1) et estimation de la gravité (chantier 2), sur des exemples
construits à la main.

Le code testé est celui du clone du Space `emergency-savior-output` ; ces
tests sont ignorés si le clone est absent. Aucun n'a besoin du réseau.

Lancer avec :
    cd backend
    python -m pytest testing/tests/test_round2_fixes.py -v
"""

import importlib.util
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from testing import pipeline_evaluation as ev
from testing.pipeline_runner import ROUND1_CONFIG, SPACE_DIR, LocalPipeline
from testing.severity_cases import SEVERITY_CASES

needs_space_clone = pytest.mark.skipif(
    not (SPACE_DIR / "src" / "pipeline.py").exists(),
    reason="clone du Space emergency-savior-output absent",
)


@pytest.fixture(scope="module")
def fixed():
    return LocalPipeline(translation_chain=())


@pytest.fixture(scope="module")
def round1():
    """Pipeline d'avant le second round : ancien tri, ancienne règle d'urgence."""
    return LocalPipeline(translation_chain=(), **ROUND1_CONFIG)


@pytest.fixture(scope="module")
def nsga2(fixed):
    import src.nsga2 as module
    return module


@pytest.fixture(scope="module")
def severity(fixed):
    import src.severity as module
    return module


# ═════════════════════════════════════════════════════════════════════════
# Chantier 1 — classement
# ═════════════════════════════════════════════════════════════════════════

# 5 points non dominés sur 2 objectifs à minimiser (coût, délai).
FRONT = np.array([[10.0, 50.0], [20.0, 40.0], [30.0, 30.0], [40.0, 20.0], [50.0, 10.0]])


@needs_space_clone
def test_compromise_scores_hand_computed(nsga2):
    """Objectifs normalisés entre 0 et 1 sur l'ensemble des points, puis
    moyenne : [0, 1] -> 0.5 ; [1, 0] -> 0.5 ; [0.5, 0.5] -> 0.5."""
    objs = np.array([[10.0, 30.0], [20.0, 20.0], [30.0, 10.0], [30.0, 30.0]])

    scores = nsga2.compromise_scores(objs)

    assert scores == pytest.approx([0.5, 0.5, 0.5, 1.0])
    # Avec un poids 3 sur le premier objectif : (3·0 + 1·1) / 4 = 0.25, etc.
    assert nsga2.compromise_scores(objs, [3, 1]) == pytest.approx([0.25, 0.5, 0.75, 1.0])


@needs_space_clone
def test_new_ranking_never_rewards_a_degradation(nsga2):
    """Chaque point du front est dégradé tour à tour sur chaque objectif,
    par plusieurs amplitudes : son rang ne s'améliore jamais."""
    for point in range(len(FRONT)):
        base_rank = nsga2.nsga2_rank(FRONT).index(point)
        for objective in (0, 1):
            previous = base_rank
            for extra in (1.0, 5.0, 20.0, 100.0, 1000.0):
                degraded = FRONT.copy()
                degraded[point, objective] += extra
                rank = nsga2.nsga2_rank(degraded).index(point)
                assert rank >= previous, (point, objective, extra)
                previous = rank


@needs_space_clone
def test_new_ranking_keeps_pareto_dominance(nsga2):
    """Un point dominé reste derrière celui qui le domine."""
    objs = np.array([[10.0, 10.0], [20.0, 20.0], [5.0, 100.0], [100.0, 5.0]])

    order = nsga2.nsga2_rank(objs)

    assert order.index(0) < order.index(1)


@needs_space_clone
def test_real_provider_made_ten_times_more_expensive_does_not_climb(fixed, round1):
    """Le cas du diagnostic : D00033, cardiologue 10e à son prix réel."""
    def rank(pipeline, factor):
        pf = pipeline.provider_filter
        cost = float(pf.df_providers.loc[pf.df_providers["ID"] == "D00033", "average_cost"].iloc[0])
        modified = ev.with_modified_provider(pf, "D00033", average_cost=cost * factor)
        return ev.full_ranking(modified, "Cardiologist").index("D00033") + 1

    assert rank(round1, 10.0) < rank(round1, 1.0)   # avant : il monte
    assert rank(fixed, 10.0) >= rank(fixed, 1.0)    # après : il ne monte plus
    assert rank(fixed, 1.2) >= rank(fixed, 1.0)


@needs_space_clone
def test_objective_weights_quality_x3_and_severity(fixed):
    """La note pèse 3, les autres critères 1 ; pour un cas grave, délai et
    proximité pèsent 2 (HIGH) ou 3 (CRITICAL)."""
    from src.filtering import OBJECTIVES, QUALITY_WEIGHT, objective_weights

    assert QUALITY_WEIGHT == 3.0
    assert objective_weights(None) == [3.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert objective_weights("MEDIUM") == objective_weights(None)
    high = dict(zip(OBJECTIVES, objective_weights("HIGH")))
    critical = dict(zip(OBJECTIVES, objective_weights("CRITICAL")))
    assert (high["quality"], high["wait"], high["location"], high["cost"]) == (3.0, 2.0, 2.0, 1.0)
    assert (critical["quality"], critical["wait"], critical["location"]) == (3.0, 3.0, 3.0)


@needs_space_clone
def test_no_degradation_improves_rank_with_final_weights(fixed):
    """Avec le poids x3 sur la note : coût x10, délai à un an et note à zéro
    n'améliorent le rang d'aucun cardiologue ni d'aucun pneumologue."""
    pf = fixed.provider_filter
    for specialty in ("Cardiologist", "Pulmonologist"):
        candidates = pf.filter_by_specialty_name(specialty)
        max_cost = float(candidates["average_cost"].max())
        for pid in candidates["ID"]:
            for changes in ({"average_cost": 10 * max_cost}, {"waiting_time_days": 365}, {"quality_score": 0.0}):
                shift = ev.rank_shift(pf, specialty, pid, changes)
                assert shift["rank_after"] >= shift["rank_before"], (specialty, pid, changes)


# ── backend/hospital.py : même défaut, même correction ──────────────────────


def _hospital_rank_shifts(mode, column, value_for):
    """Dégrade un attribut de chaque hôpital, un à la fois, et renvoie
    [(rang avant, rang après)]."""
    import copy

    import hospital

    base_filter = copy.copy(hospital.hospital_filter)
    base_filter.ranking_mode = mode
    df = base_filter.df_hospitals
    shifts = []
    for specialty in sorted(df["specialty"].unique()):
        rows = df[df["specialty"] == specialty]
        if len(rows) < 3:
            continue
        before = base_filter.optimize_hospitals_nsga(specialty, top_k=10**6)["hospital_name"].tolist()
        for index, row in rows.iterrows():
            modified = copy.copy(base_filter)
            modified.df_hospitals = df.copy()
            modified.df_hospitals.loc[index, column] = value_for(rows[column])
            after = modified.optimize_hospitals_nsga(specialty, top_k=10**6)["hospital_name"].tolist()
            shifts.append((before.index(row["hospital_name"]), after.index(row["hospital_name"])))
    return shifts


@pytest.mark.parametrize("column, value_for", [
    ("average_cost", lambda col: 10 * col.max()),
    ("waiting_time_days", lambda col: 365),
    ("quality_score", lambda col: 0.0),
])
def test_hospital_ranking_never_rewards_a_degradation(column, value_for):
    shifts = _hospital_rank_shifts("compromise", column, value_for)

    assert len(shifts) > 100
    assert all(after >= before for before, after in shifts)


def test_hospital_old_ranking_did_reward_a_ten_times_higher_cost():
    """Le défaut d'origine, conservé sous `ranking_mode="crowding"`."""
    shifts = _hospital_rank_shifts("crowding", "average_cost", lambda col: 10 * col.max())

    assert sum(1 for before, after in shifts if after < before) > 0


def test_hospital_urgent_flag_now_weighs_the_emergency_service():
    """Avant, `is_urgent` multipliait la colonne d'un objectif par 2, sans
    aucun effet sur le classement. Il doit maintenant pouvoir le changer."""
    import hospital

    hf = hospital.hospital_filter
    changed = 0
    for specialty in sorted(hf.df_hospitals["specialty"].unique()):
        calm = hf.optimize_hospitals_nsga(specialty, top_k=10**6)["hospital_name"].tolist()
        urgent = hf.optimize_hospitals_nsga(specialty, top_k=10**6, is_urgent=True)["hospital_name"].tolist()
        changed += calm != urgent
    assert changed > 0


# ═════════════════════════════════════════════════════════════════════════
# Chantier 2 — gravité
# ═════════════════════════════════════════════════════════════════════════


@needs_space_clone
def test_level_thresholds(severity):
    assert [severity.level_from_score(s) for s in (0, 1, 2, 3, 4, 5, 6, 9)] == \
        ["LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH", "CRITICAL", "CRITICAL"]


@needs_space_clone
def test_symptom_tiers_set_the_starting_level(severity):
    assert severity.estimate_severity("", ["runny_nose"])["level"] == "LOW"
    assert severity.estimate_severity("", ["vomiting"])["level"] == "MEDIUM"
    assert severity.estimate_severity("", ["hematuria"])["level"] == "HIGH"
    assert severity.estimate_severity("", ["facial_drooping"])["level"] == "CRITICAL"


@needs_space_clone
def test_critical_combination(severity):
    """Douleur thoracique seule : HIGH. Avec sueurs : combinaison critique."""
    alone = severity.estimate_severity("", ["chest_pain"])
    combined = severity.estimate_severity("", ["chest_pain", "sweating"])

    assert alone["level"] == "HIGH"
    assert combined["level"] == "CRITICAL"
    assert "chest_pain" in combined["driving_symptoms"]
    assert any("combinaison critique" in r for r in combined["reasons"])


@needs_space_clone
def test_modifiers_add_or_remove_one_point(severity):
    base = severity.estimate_severity("j'ai vomi", ["vomiting"])
    intense_sudden = severity.estimate_severity("j'ai vomi d'un seul coup, c'est atroce", ["vomiting"])
    chronic = severity.estimate_severity("je vomis depuis des mois", ["vomiting"])
    toddler = severity.estimate_severity("il a vomi", ["vomiting"], age=2)

    assert (base["score"], base["level"]) == (2, "MEDIUM")
    assert (intense_sudden["score"], intense_sudden["level"]) == (4, "HIGH")
    assert (chronic["score"], chronic["level"]) == (1, "LOW")
    assert toddler["score"] == 3


@needs_space_clone
def test_vital_phrases_work_without_any_extracted_symptom(severity):
    for text in ("Mon père ne respire plus", "She is unconscious", "I think he is having a heart attack"):
        assert severity.estimate_severity(text, [])["level"] == "CRITICAL", text


@needs_space_clone
def test_negated_cues_do_not_count(severity):
    """« pas de douleur atroce » n'est pas une intensité."""
    negated = severity.estimate_severity("j'ai vomi mais pas de douleur atroce", ["vomiting"])

    assert negated["score"] == 2


@needs_space_clone
def test_fast_heartbeat_is_not_a_call_for_help(severity):
    """« bat très vite » décrit un symptôme : « vite » seul n'est pas un appel à l'aide."""
    result = severity.estimate_severity("mon coeur bat très vite", ["palpitations"])

    assert not any("appel" in r for r in result["reasons"])


@needs_space_clone
def test_no_information_is_unknown_not_low(severity):
    assert severity.estimate_severity("Bonjour, je voudrais un renseignement.", [])["level"] == "UNKNOWN"
    # L'âge seul ne fait pas un cas.
    assert severity.estimate_severity("Bonjour.", [], age=80)["level"] == "UNKNOWN"


@needs_space_clone
def test_urgent_flag_is_a_floor_at_high(severity):
    assert severity.estimate_severity("", ["runny_nose"], urgent=True)["level"] == "HIGH"
    assert severity.estimate_severity("", [], urgent=True)["level"] == "HIGH"
    # Il ne rabaisse jamais un cas déjà critique.
    assert severity.estimate_severity("", ["facial_drooping"], urgent=True)["level"] == "CRITICAL"


@needs_space_clone
def test_severity_boosts_the_specialty_owning_the_severe_symptom(fixed, round1):
    """Déficit hémicorporel + palpitations. Avec l'ancienne règle, `urgent`
    double la cardiologie ; avec la nouvelle, c'est la neurologie,
    responsable du signe grave, qui est favorisée."""
    symptoms = ["weakness_of_one_body_side", "palpitations", "chest_pain"]
    scorer = fixed.scorer

    neutral = scorer.score(symptoms)
    old = scorer.score(symptoms, is_urgent=True)
    new = scorer.score(symptoms, is_urgent=True,
                       severity={"level": "CRITICAL", "driving_symptoms": ["weakness_of_one_body_side"]})

    ratio = lambda s: s["Neurologist"] / s["Cardiologist"]  # noqa: E731
    assert ratio(old) < ratio(neutral) < ratio(new)


@needs_space_clone
def test_severity_does_not_change_scores_below_high(fixed):
    symptoms = ["chest_pain", "cough"]

    assert fixed.scorer.score(symptoms, severity={"level": "MEDIUM", "driving_symptoms": ["chest_pain"]}) == \
        fixed.scorer.score(symptoms)


@needs_space_clone
def test_predict_returns_severity_and_local_providers_for_a_critical_case(fixed):
    text = ("Mon mari a une douleur très forte dans la poitrine, ça lui serre comme un étau et ça descend dans "
            "le bras gauche. Il transpire beaucoup.")

    result = fixed.recommender.predict(text, age=58, location="Tunis")

    assert result["severity"]["level"] == "CRITICAL"
    assert result["recommendations"][0][0] == "Cardiologist"
    assert (result["top_providers"]["location"] == "Tunis").sum() >= 2


# ═════════════════════════════════════════════════════════════════════════
# Métriques et jeu de cas de gravité
# ═════════════════════════════════════════════════════════════════════════


def test_severity_metrics_hand_computed():
    """
    6 cas (attendu -> estimé) :
      LOW -> LOW            exact
      MEDIUM -> HIGH        surestimé d'un cran
      HIGH -> HIGH          exact
      HIGH -> LOW           sous-estimé de deux crans
      CRITICAL -> HIGH      sous-estimé d'un cran
      MEDIUM -> UNKNOWN     inconnu
    Exact 2/6 ; à un niveau près 4/6 ; 2 sous-estimés, 1 surestimé, 1 inconnu.
    HIGH : estimé 3 fois dont 1 juste -> précision 1/3 ; 2 attendus dont 1 trouvé -> rappel 1/2.
    """
    pairs = [("LOW", "LOW"), ("MEDIUM", "HIGH"), ("HIGH", "HIGH"), ("HIGH", "LOW"), ("CRITICAL", "HIGH"),
             ("MEDIUM", "UNKNOWN")]

    m = ev.severity_metrics(pairs)

    assert m["accuracy"] == pytest.approx(2 / 6)
    assert m["within_one_level"] == pytest.approx(4 / 6)
    assert (m["under_triage"], m["over_triage"], m["unknown"]) == (2, 1, 1)
    assert m["per_level"]["HIGH"]["precision"] == pytest.approx(1 / 3)
    assert m["per_level"]["HIGH"]["recall"] == pytest.approx(1 / 2)
    assert m["confusion_matrix"]["MEDIUM"]["UNKNOWN"] == 1


def test_severity_cases_are_balanced():
    assert Counter(c.expected_level for c in SEVERITY_CASES) == {"LOW": 6, "MEDIUM": 6, "HIGH": 6, "CRITICAL": 6}
    assert Counter(c.language for c in SEVERITY_CASES) == {"fr": 12, "en": 12}
    assert len({c.case_id for c in SEVERITY_CASES}) == len(SEVERITY_CASES)


def test_backend_parser_reads_severity():
    path = Path(__file__).resolve().parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("backend_main_round2", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    parsed = module.parse_optimization_output(
        "Gravité estimée : CRITICAL\n\nSymptômes détectés : chest_pain\n\nSpécialités recommandées :\n"
        "- Cardiologist (97.5%)\n\nMeilleur médecin : Dr. A\n")
    old_format = module.parse_optimization_output("Symptômes détectés : chest_pain\n")

    assert parsed["severity"] == "CRITICAL"
    assert parsed["detected_symptoms"] == ["chest_pain"]
    assert old_format["severity"] is None
