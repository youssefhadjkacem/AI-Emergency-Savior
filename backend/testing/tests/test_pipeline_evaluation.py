"""
Tests unitaires de l'évaluation du pipeline principal.

Deux groupes :
  - les métriques de `pipeline_evaluation.py`, sur de petits exemples
    construits à la main dont la réponse est connue à l'avance ;
  - l'intégrité du jeu de cas (`pipeline_cases.py`) par rapport aux données
    réelles du système (vocabulaire de symptômes, spécialités, villes).
    Ces derniers ont besoin du clone du Space `emergency-savior-output` et
    sont ignorés s'il est absent.

Lancer avec :
    cd backend
    python -m pytest testing/tests -v
"""

import pandas as pd
import pytest

from testing import pipeline_evaluation as ev
from testing.pipeline_cases import CASES, Severity
from testing.pipeline_runner import SPACE_DIR

BRANCHES = {"Cardiologist": "B2", "Pulmonologist": "B2", "Neurologist": "B4", "Gastroenterologist": "B3"}


# ═════════════════════════════════════════════════════════════════════════
# Extraction NLP
# ═════════════════════════════════════════════════════════════════════════


def test_symptom_extraction_scores_hand_computed():
    """Attendus {a, b, c, d}, extraits {a, b, x} : TP=2, FP=1 (x), FN=2 (c, d)
    -> précision 2/3, rappel 2/4, F1 = 2·(2/3)·(1/2) / (2/3 + 1/2) = 4/7."""
    scores = ev.symptom_extraction_scores(["a", "b", "x"], ["a", "b", "c", "d"])

    assert (scores["true_positives"], scores["false_positives"], scores["false_negatives"]) == (2, 1, 2)
    assert scores["precision"] == pytest.approx(2 / 3)
    assert scores["recall"] == pytest.approx(0.5)
    assert scores["f1"] == pytest.approx(4 / 7)
    assert scores["spurious_symptoms"] == ["x"]
    assert scores["missed_symptoms"] == ["c", "d"]


def test_symptom_extraction_with_nothing_detected_has_undefined_precision():
    scores = ev.symptom_extraction_scores([], ["a", "b"])

    assert scores["precision"] is None
    assert scores["recall"] == 0.0
    assert scores["f1"] is None


def test_aggregate_symptom_extraction_is_a_micro_average():
    """Cas 1 : TP=2, FP=1, FN=2. Cas 2 : rien d'extrait, FN=2.
    Micro : TP=2, FP=1, FN=4 -> précision 2/3, rappel 2/6."""
    per_case = [ev.symptom_extraction_scores(["a", "b", "x"], ["a", "b", "c", "d"]),
                ev.symptom_extraction_scores([], ["e", "f"])]

    total = ev.aggregate_symptom_extraction(per_case)

    assert total["precision"] == pytest.approx(2 / 3)
    assert total["recall"] == pytest.approx(2 / 6)
    assert total["cases_with_no_symptom_detected"] == 1


# ═════════════════════════════════════════════════════════════════════════
# Classification
# ═════════════════════════════════════════════════════════════════════════


def test_classification_outcome_levels():
    exact = ev.classification_outcome("Pulmonologist", "Cardiologist", ["Pulmonologist", "Cardiologist"])
    alternative = ev.classification_outcome("Pulmonologist", "Cardiologist", ["Cardiologist", "Pulmonologist"])
    third = ev.classification_outcome("Pulmonologist", None, ["Neurologist", "Cardiologist", "Pulmonologist"])
    nothing = ev.classification_outcome("Pulmonologist", "Cardiologist", [])

    assert (exact["top1_strict"], exact["top1_lenient"], exact["top3"]) == (True, True, True)
    assert (alternative["top1_strict"], alternative["top1_lenient"], alternative["top3"]) == (False, True, True)
    assert (third["top1_strict"], third["top1_lenient"], third["top3"]) == (False, False, True)
    assert (nothing["predicted"], nothing["top1_strict"], nothing["top1_lenient"], nothing["top3"]) == \
        (None, False, False, False)


def test_classification_outcome_without_alternative_is_not_lenient_on_empty_prediction():
    """`None in (expected, None)` ne doit pas faire passer une absence de
    prédiction pour une alternative acceptable."""
    assert ev.classification_outcome("Pulmonologist", None, [])["top1_lenient"] is False


def test_branch_metrics_hand_computed():
    """
    5 cas (attendu -> prédit) :
      Cardiologist  -> Cardiologist   B2 -> B2  correct
      Cardiologist  -> Pulmonologist  B2 -> B2  correct AU NIVEAU BRANCHE
      Pulmonologist -> Neurologist    B2 -> B4
      Neurologist   -> Neurologist    B4 -> B4  correct
      Gastroenterologist -> aucune    B3 -> NO_PREDICTION

    B2 : TP=2, FN=1, FP=0 -> P=1,   R=2/3, F1=0.8
    B4 : TP=1, FN=0, FP=1 -> P=1/2, R=1,   F1=2/3
    B3 : TP=0, FN=1, FP=0 -> P non définie, R=0, F1 non défini (compté 0)
    Exactitude branche = 3/5 ; F1 macro = (0.8 + 2/3 + 0) / 3.
    """
    pairs = [("Cardiologist", "Cardiologist"), ("Cardiologist", "Pulmonologist"),
             ("Pulmonologist", "Neurologist"), ("Neurologist", "Neurologist"),
             ("Gastroenterologist", None)]

    result = ev.branch_metrics(pairs, BRANCHES)

    assert result["confusion_matrix"]["B2"] == {"B2": 2, "B3": 0, "B4": 1, ev.NO_PREDICTION: 0}
    assert result["confusion_matrix"]["B3"][ev.NO_PREDICTION] == 1
    assert result["per_branch"]["B2"]["f1"] == pytest.approx(0.8)
    assert result["per_branch"]["B4"]["precision"] == pytest.approx(0.5)
    assert result["per_branch"]["B4"]["f1"] == pytest.approx(2 / 3)
    assert result["per_branch"]["B3"]["recall"] == 0.0
    assert result["per_branch"]["B3"]["f1"] is None
    assert result["accuracy"] == pytest.approx(3 / 5)
    assert result["macro_f1"] == pytest.approx((0.8 + 2 / 3 + 0.0) / 3)


def test_classification_summary_rates():
    outcomes = [
        ev.classification_outcome("A", None, ["A"]),
        ev.classification_outcome("A", "B", ["B", "A"]),
        ev.classification_outcome("A", None, ["C", "D", "A"]),
        ev.classification_outcome("A", None, []),
    ]

    summary = ev.classification_summary(outcomes)

    assert summary["top1_strict_accuracy"] == pytest.approx(1 / 4)
    assert summary["top1_lenient_accuracy"] == pytest.approx(2 / 4)
    assert summary["top3_coverage"] == pytest.approx(3 / 4)
    assert summary["no_prediction"] == 1


def test_provider_coverage():
    same = [{"specialty": "Cardiologist"}] * 3
    mixed = [{"specialty": "Neurologist"}, {"specialty": "Cardiologist"}, {"specialty": "Neurologist"}]

    assert ev.provider_coverage("Cardiologist", same) == {
        "n_providers": 3, "provider_top1": True, "provider_top3": True, "distinct_specialties_in_top3": 1}
    assert ev.provider_coverage("Cardiologist", mixed)["provider_top1"] is False
    assert ev.provider_coverage("Cardiologist", mixed)["provider_top3"] is True
    assert ev.provider_coverage("Cardiologist", [])["provider_top3"] is False


# ═════════════════════════════════════════════════════════════════════════
# Latence et classement
# ═════════════════════════════════════════════════════════════════════════


def test_latency_stats_hand_computed():
    """0.010 s à 0.100 s par pas de 0.010 : moyenne 55 ms, médiane 55 ms,
    P95 par interpolation linéaire = 90 + 0.55 · 10 = 95.5 ms."""
    stats = ev.latency_stats([i / 100 for i in range(1, 11)])

    assert stats["n"] == 10
    assert stats["mean_ms"] == pytest.approx(55.0)
    assert stats["median_ms"] == pytest.approx(55.0)
    assert stats["p95_ms"] == pytest.approx(95.5)
    assert stats["max_ms"] == pytest.approx(100.0)


def test_latency_stats_on_empty_input():
    assert ev.latency_stats([])["mean_ms"] is None


def test_summarize_rank_shifts():
    """Rang 1 = meilleur : 5 -> 2 est une amélioration, 2 -> 6 une dégradation."""
    shifts = [{"rank_before": 5, "rank_after": 2}, {"rank_before": 2, "rank_after": 6},
              {"rank_before": 4, "rank_after": 4}, {"rank_before": 9, "rank_after": 1}]

    summary = ev.summarize_rank_shifts(shifts, top_k=3)

    assert (summary["improved"], summary["unchanged"], summary["worsened"]) == (2, 1, 1)
    assert summary["in_top_k_before"] == 1
    assert summary["in_top_k_after"] == 2
    assert summary["mean_rank_before"] == pytest.approx(5.0)
    assert summary["mean_rank_after"] == pytest.approx(3.25)


class _FakeFilter:
    """Filtre minimal : classe les prestataires par coût croissant."""

    def __init__(self, rows):
        self.df_providers = pd.DataFrame(rows)

    def optimize_providers_nsga(self, specialty_name, top_k=3, budget=None, location=None):
        df = self.df_providers[self.df_providers["specialty"] == specialty_name]
        return df.sort_values("average_cost").head(top_k)


def test_rank_shift_modifies_only_a_copy():
    rows = [{"ID": "D1", "specialty": "S", "average_cost": 50},
            {"ID": "D2", "specialty": "S", "average_cost": 60},
            {"ID": "D3", "specialty": "S", "average_cost": 70}]
    provider_filter = _FakeFilter(rows)

    shift = ev.rank_shift(provider_filter, "S", "D3", {"average_cost": 10})

    assert shift == {"provider_id": "D3", "rank_before": 3, "rank_after": 1}
    assert provider_filter.df_providers["average_cost"].tolist() == [50, 60, 70]  # base d'origine intacte


# ═════════════════════════════════════════════════════════════════════════
# Intégrité du jeu de cas
# ═════════════════════════════════════════════════════════════════════════


def test_case_ids_are_unique_and_set_is_large_enough():
    ids = [c.case_id for c in CASES]

    assert len(ids) == len(set(ids))
    assert len(CASES) >= 20


def test_cases_cover_all_severity_levels():
    assert {c.severity for c in CASES} == set(Severity)


def test_ambiguous_cases_declare_a_distinct_alternative():
    for case in CASES:
        if case.ambiguous:
            assert case.acceptable_alternative and case.acceptable_alternative != case.expected_specialty
        else:
            assert case.acceptable_alternative is None


def test_every_case_has_both_languages_and_expected_symptoms():
    for case in CASES:
        assert case.text_fr.strip() and case.text_en.strip()
        assert case.expected_symptoms


needs_space_clone = pytest.mark.skipif(
    not (SPACE_DIR / "src" / "pipeline.py").exists(),
    reason="clone du Space emergency-savior-output absent",
)


@pytest.fixture(scope="module")
def pipeline():
    from testing.pipeline_runner import LocalPipeline

    return LocalPipeline()


@needs_space_clone
def test_expected_symptoms_belong_to_the_system_vocabulary(pipeline):
    vocabulary = set(pipeline.vocabulary)
    for case in CASES:
        unknown = [s for s in case.expected_symptoms if s not in vocabulary]
        assert not unknown, f"{case.case_id} : symptômes hors vocabulaire {unknown}"


@needs_space_clone
def test_expected_specialties_exist_and_all_22_are_covered(pipeline):
    specialties = set(pipeline.specialties)
    for case in CASES:
        assert case.expected_specialty in specialties
        assert case.acceptable_alternative is None or case.acceptable_alternative in specialties

    assert {c.expected_specialty for c in CASES} == specialties
    assert len(set(pipeline.branch_by_specialty().values())) == 7


@needs_space_clone
def test_case_locations_exist_in_the_provider_base(pipeline):
    cities = set(pipeline.provider_filter.df_providers["location"].dropna())
    for case in CASES:
        assert case.location is None or case.location in cities, f"{case.case_id} : {case.location}"
