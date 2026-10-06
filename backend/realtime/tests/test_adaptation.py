"""
Tests unitaires de la réaction à la saturation (`adaptation.py`).

Les tests de la règle utilisent des classements construits à la main. Les
tests marqués `needs_space_clone` vérifient la non-régression contre le
vrai classement du Space (clone local, non modifié).

    cd backend
    python -m pytest realtime/tests/test_adaptation.py -v
"""

import random

import pytest

from realtime.adaptation import SPACE_DIR, AdaptiveRanker, Candidate, SpaceRankingSource, rerank
from realtime.saturation import ProviderStatus, SaturationConfig, SaturationMonitor

NORMAL, ALERT, SATURATED = ProviderStatus.normal, ProviderStatus.alert, ProviderStatus.saturated

needs_space_clone = pytest.mark.skipif(
    not (SPACE_DIR / "src" / "filtering.py").exists(), reason="clone local du Space absent"
)


def ranking(*rows):
    """Classement de départ à partir de (id, ville, front, score)."""
    return [Candidate(pid, "Cardiologist", city, front, score, base_rank=i)
            for i, (pid, city, front, score) in enumerate(rows)]


def ids(result_or_list):
    order = result_or_list.order if hasattr(result_or_list, "order") else result_or_list
    return [c.provider_id for c in order]


# Cinq cardiologues : A, B, C sur le premier front, D et E sur le second.
BASE = ranking(
    ("A", "Tunis", 0, 0.20),
    ("B", "Sfax", 0, 0.23),
    ("C", "Tunis", 0, 0.30),
    ("D", "Tunis", 1, 0.25),
    ("E", "Sousse", 1, 0.40),
)


# ═════════════════════════════════════════════════════════════════════════
# Règle de reclassement
# ═════════════════════════════════════════════════════════════════════════


def test_no_saturation_means_no_change():
    assert ids(rerank(BASE, {})) == ["A", "B", "C", "D", "E"]
    assert ids(rerank(BASE, {c.provider_id: NORMAL for c in BASE})) == ["A", "B", "C", "D", "E"]


def test_saturated_provider_goes_behind_every_non_saturated_one():
    result = rerank(BASE, {"A": SATURATED})

    assert ids(result) == ["B", "C", "D", "E", "A"]
    assert result.saturated_in_top(3) == []
    assert not result.all_saturated


def test_alert_penalty_moves_a_provider_back_inside_its_front_only():
    # A (0,20) + 0,05 = 0,25 : derrière B (0,23), devant C (0,30).
    assert ids(rerank(BASE, {"A": ALERT}, alert_penalty=0.05)) == ["B", "A", "C", "D", "E"]
    # Pénalité énorme : A recule au bout de SON front, pas derrière D et E.
    assert ids(rerank(BASE, {"A": ALERT}, alert_penalty=5.0)) == ["B", "C", "A", "D", "E"]


def test_alert_penalty_too_small_to_matter_changes_nothing():
    assert ids(rerank(BASE, {"A": ALERT}, alert_penalty=0.01)) == ["A", "B", "C", "D", "E"]


def test_all_saturated_is_reported_and_the_list_is_not_empty():
    result = rerank(BASE, {c.provider_id: SATURATED for c in BASE})

    assert result.all_saturated
    assert ids(result) == ["A", "B", "C", "D", "E"]      # classement habituel
    assert "saturés" in result.message
    assert len(result.top(3)) == 3


def test_saturated_provider_stays_in_top3_when_there_is_no_alternative():
    """Deux prestataires non saturés seulement : le Top 3 est complété par
    le mieux classé des saturés, et c'est signalé."""
    result = rerank(BASE, {"A": SATURATED, "B": SATURATED, "C": SATURATED})

    assert ids(result)[:3] == ["D", "E", "A"]
    assert result.saturated_in_top(3) == ["A"]
    assert "A" in result.message


def test_reaction_returns_the_same_providers_in_another_order():
    rng = random.Random(0)
    for _ in range(200):
        statuses = {c.provider_id: rng.choice([NORMAL, ALERT, SATURATED]) for c in BASE}
        severity = rng.choice([None, "HIGH", "CRITICAL"])
        result = rerank(BASE, statuses, severity, rng.choice([None, "Tunis", "Sfax"]))

        assert sorted(ids(result)) == ["A", "B", "C", "D", "E"]
        assert {c.specialty for c in result.order} == {"Cardiologist"}


# ═════════════════════════════════════════════════════════════════════════
# Règle CRITICAL
# ═════════════════════════════════════════════════════════════════════════


def test_non_critical_patient_may_be_sent_to_another_city():
    """Patient à Tunis, A (Tunis) saturé : le n°1 devient B, à Sfax."""
    assert ids(rerank(BASE, {"A": SATURATED}, "MEDIUM", "Tunis"))[0] == "B"
    assert ids(rerank(BASE, {"A": SATURATED}, "HIGH", "Tunis"))[0] == "B"


def test_critical_patient_is_never_sent_to_another_city_by_the_reaction():
    """Même situation, cas CRITICAL : le remplaçant de A est pris à Tunis
    (C), pas à Sfax. Les positions de chaque groupe ne bougent pas."""
    result = rerank(BASE, {"A": SATURATED}, "CRITICAL", "Tunis")

    assert ids(result) == ["C", "B", "D", "A", "E"]
    assert [c.city == "Tunis" for c in result.order] == [c.city == "Tunis" for c in BASE]


def test_critical_patient_keeps_local_providers_when_all_of_them_are_saturated():
    """A, C, D (tous à Tunis) saturés : ils sont conservés à leur rang et
    signalés, plutôt que de détourner un patient critique vers Sfax."""
    result = rerank(BASE, {"A": SATURATED, "C": SATURATED, "D": SATURATED}, "CRITICAL", "Tunis")

    assert ids(result) == ["A", "B", "C", "D", "E"]
    assert result.saturated_in_top(3) == ["A", "C"]
    assert result.message


def test_critical_rule_needs_a_known_city():
    """Sans ville, il n'y a pas de notion de proximité : règle générale."""
    assert ids(rerank(BASE, {"A": SATURATED}, "CRITICAL", None)) == ["B", "C", "D", "E", "A"]


def test_critical_rule_keeps_the_locality_pattern_for_any_state():
    rng = random.Random(1)
    for _ in range(300):
        statuses = {c.provider_id: rng.choice([NORMAL, ALERT, SATURATED]) for c in BASE}
        result = rerank(BASE, statuses, "CRITICAL", "Tunis", alert_penalty=rng.choice([0.02, 0.2, 2.0]))

        assert [c.city == "Tunis" for c in result.order] == [c.city == "Tunis" for c in BASE]


# ═════════════════════════════════════════════════════════════════════════
# Non-régression contre le classement du Space
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def source():
    return SpaceRankingSource()


def idle_monitor(source):
    monitor = SaturationMonitor(SaturationConfig())
    for pid in source.providers["ID"]:
        monitor.register_provider(pid, 5)
    return monitor


CONTEXTS = [
    (None, None, None), ("Tunis", None, None), ("Sfax", "HIGH", None), ("Sousse", "CRITICAL", None),
    ("Tunis", "MEDIUM", 60.0), (None, "CRITICAL", 120.0), ("Gafsa", "LOW", None),
]


@needs_space_clone
@pytest.mark.parametrize("location, severity, budget", CONTEXTS)
def test_full_ranking_is_the_one_of_the_space(source, location, severity, budget):
    """Le classement complet repris ici est, prestataire par prestataire,
    celui que renvoie `optimize_providers_nsga` pour les 22 spécialités."""
    pf = source.provider_filter
    for specialty in source.specialties():
        expected = pf.optimize_providers_nsga(specialty, top_k=10**9, budget=budget, location=location,
                                              severity_level=severity)["ID"].tolist()

        assert ids(source.ranking(specialty, location, severity, budget)) == expected


@needs_space_clone
@pytest.mark.parametrize("location, severity, budget", CONTEXTS)
def test_without_saturation_the_top3_is_strictly_the_current_one(source, location, severity, budget):
    """Moniteur branché, aucun prestataire en alerte ni saturé : le Top 3
    est exactement celui du Space, pour les 22 spécialités."""
    pf = source.provider_filter
    ranker = AdaptiveRanker(source, idle_monitor(source))
    for specialty in source.specialties():
        expected = pf.optimize_providers_nsga(specialty, top_k=3, budget=budget, location=location,
                                              severity_level=severity)["ID"].tolist()

        result = ranker.recommend(specialty, now=0.0, location=location, severity_level=severity, budget=budget)

        assert ids(result.top(3)) == expected
        assert not result.all_saturated and not result.message


@needs_space_clone
def test_reaction_never_changes_the_specialty(source):
    rng = random.Random(7)
    for specialty in source.specialties():
        base = source.ranking(specialty, "Tunis", None)
        for _ in range(20):
            statuses = {c.provider_id: rng.choice([NORMAL, ALERT, SATURATED]) for c in base}
            result = rerank(base, statuses, rng.choice([None, "HIGH", "CRITICAL"]), "Tunis")

            assert {c.specialty for c in result.order} == {specialty}
            assert sorted(ids(result)) == sorted(ids(base))


@needs_space_clone
def test_saturating_the_number_one_removes_it_from_the_top3(source):
    """Bout en bout : le n°1 des cardiologues reçoit assez de réservations
    pour être saturé ; il sort du Top 3, remplacé par un cardiologue."""
    monitor = idle_monitor(source)
    ranker = AdaptiveRanker(source, monitor)
    before = ranker.recommend("Cardiologist", 0.0, "Tunis").top(3)
    for k in range(5):  # capacité 5 dans ce test
        monitor.reserve(f"r{k}", before[0].provider_id, 0.0)

    after = ranker.recommend("Cardiologist", 1.0, "Tunis").top(3)

    assert before[0].provider_id not in ids(after)
    assert ids(after)[:2] == ids(before)[1:]
    assert {c.specialty for c in after} == {"Cardiologist"}
