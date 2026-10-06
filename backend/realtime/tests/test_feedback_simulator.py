"""
Tests du simulateur de la boucle de rétroaction (`feedback_simulator.py`)
et de ses métriques (`feedback_evaluation.py`).

Les simulations de ces tests sont petites (2 spécialités, 2 heures) : elles
vérifient des invariants, pas des résultats chiffrés.

    cd backend
    python -m pytest realtime/tests/test_feedback_simulator.py -v
"""

import numpy as np
import pytest

from realtime.adaptation import SPACE_DIR, SpaceRankingSource
from realtime.feedback_evaluation import _runs, aggregate, evaluate_run, gini
from realtime.feedback_simulator import (
    CONDITIONS,
    FeedbackConfig,
    build_world,
    capacity_from_weekly_slots,
    run_condition,
)

needs_space_clone = pytest.mark.skipif(
    not (SPACE_DIR / "src" / "filtering.py").exists(), reason="clone local du Space absent"
)

SMALL = dict(arrivals_per_hour=120.0, warmup_hours=1.5, duration_hours=2.0,
             specialties=("Cardiologist", "Pulmonologist"))
# 120 patients/heure sur 2 spécialités : charge forte, pour que la réaction
# ait quelque chose à faire.


# ═════════════════════════════════════════════════════════════════════════
# Fonctions pures
# ═════════════════════════════════════════════════════════════════════════


def test_capacity_hypothesis():
    assert capacity_from_weekly_slots(15) == 3      # minimum de la base
    assert capacity_from_weekly_slots(60) == 12     # maximum de la base
    assert capacity_from_weekly_slots(1) == 1       # jamais zéro place


def test_gini():
    assert gini([4, 4, 4, 4]) == pytest.approx(0.0)
    assert gini([0, 0, 0, 12]) == pytest.approx(0.75)   # (n - 1) / n pour n = 4
    assert gini([]) == 0.0
    assert gini([0, 0]) == 0.0


def test_runs_finds_consecutive_true_ranges():
    mask = np.array([False, True, True, False, True, False, True, True, True])
    assert [(int(a), int(b)) for a, b in _runs(mask)] == [(1, 3), (4, 5), (6, 9)]


def test_aggregate_reports_mean_and_between_seed_std():
    stats = aggregate([{"x": 1.0, "y": None}, {"x": 2.0, "y": None}, {"x": 3.0, "y": 4.0}])

    assert stats["x"]["mean"] == pytest.approx(2.0)
    assert stats["x"]["std"] == pytest.approx(1.0)
    assert stats["y"]["n"] == 1


# ═════════════════════════════════════════════════════════════════════════
# Simulation
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def source():
    return SpaceRankingSource()


@pytest.fixture(scope="module")
def world(source):
    return build_world(FeedbackConfig(seed=5, **SMALL), source)


@pytest.fixture(scope="module")
def logs(world, source):
    return {name: run_condition(world, source, CONDITIONS[name]) for name in ("A", "B", "C", "D")}


@needs_space_clone
def test_same_seed_gives_the_same_world(world, source):
    again = build_world(FeedbackConfig(seed=5, **SMALL), source)

    assert again.patients == world.patients
    assert again.static_events == world.static_events
    assert build_world(FeedbackConfig(seed=6, **SMALL), source).patients != world.patients


@needs_space_clone
def test_same_run_twice_gives_the_same_result(world, source, logs):
    again = run_condition(world, source, CONDITIONS["C"])

    assert [(r.chosen.provider_id, r.outcome) for r in again.records] == \
           [(r.chosen.provider_id, r.outcome) for r in logs["C"].records]


@needs_space_clone
def test_every_condition_sees_the_same_patients(logs):
    reference = [r.patient for r in logs["A"].records]
    for name, log in logs.items():
        assert [r.patient for r in log.records] == reference, name
        assert [r.base_top for r in log.records] == [r.base_top for r in logs["A"].records], name


@needs_space_clone
def test_static_condition_recommends_the_static_top3(logs):
    assert all(r.top == r.base_top for r in logs["A"].records)
    assert logs["A"].system_status == []


@needs_space_clone
def test_real_occupancy_stays_within_capacity(world, logs):
    for log in logs.values():
        occupied = np.asarray(log.occupied)
        assert occupied.min() >= 0
        assert (occupied <= np.asarray(world.capacity)).all()


@needs_space_clone
def test_reaction_changes_recommendations_under_load(logs):
    """Charge forte : le système complet modifie des Top 3, et envoie moins
    de patients vers un prestataire plein que le classement statique."""
    static, full = evaluate_run(logs["A"]), evaluate_run(logs["C"])

    assert static["top1_changed_rate"] == 0.0
    assert full["top1_changed_rate"] > 0.0
    assert full["refused_rate"] < static["refused_rate"]


@needs_space_clone
@pytest.mark.parametrize("name", ["B", "C", "D"])
def test_safety_controls_hold_in_simulation(logs, name):
    metrics = evaluate_run(logs[name])

    assert metrics["specialty_preserved_rate"] == 1.0
    assert metrics["critical_patients"] > 0
    assert metrics["critical_moved_out_of_city"] == 0


@needs_space_clone
def test_refused_patient_found_the_provider_full(logs):
    for log in logs.values():
        for record in log.records:
            if record.outcome == "refused":
                assert record.occupancy_at_arrival == 1.0
            if record.outcome == "served":
                assert record.occupancy_at_arrival < 1.0
