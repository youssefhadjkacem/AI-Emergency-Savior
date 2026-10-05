"""
Tests unitaires de la détection de concept drift (Phase 2) : détecteurs
(`drift_detection.py`), calcul des métriques (`drift_evaluation.py`) et
vérité terrain du simulateur (`drift_simulator.py`).

Les tests des détecteurs et des métriques utilisent des séries construites
à la main, pas le flux simulé : la réponse attendue est connue à l'avance.

Lancer avec :
    cd backend
    python -m pytest realtime/tests/test_drift_detection.py -v
"""

import pytest

from realtime.drift_detection import (
    AdwinDriftDetector,
    DriftAlgorithm,
    PageHinkleyDriftDetector,
    ParallelDriftDetector,
    detect_drift,
)
from realtime.drift_evaluation import aggregate, detection_delay, evaluate_series
from realtime.drift_simulator import (
    DriftSimulatorConfig,
    DriftType,
    build_smoothed_series,
    generate_drift_scenario,
    latent_mean,
)

ALGORITHMS = [DriftAlgorithm.adwin, DriftAlgorithm.page_hinkley]

DRIFT_AT = 150
# 150 points à 8 places puis 150 points à 3 places : drift évident, marqué,
# et dont l'instant exact est connu.
STEP_SERIES = [8.0] * DRIFT_AT + [3.0] * 150


# ═════════════════════════════════════════════════════════════════════════
# Détecteurs, sur séries construites à la main
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_obvious_drift_is_detected(algorithm):
    detections = detect_drift(STEP_SERIES, algorithm)

    assert detections, "un passage net de 8 à 3 places doit être signalé"
    # Rien avant le drift, et un premier signalement peu après.
    assert min(detections) >= DRIFT_AT
    assert min(detections) < DRIFT_AT + 30


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_perfectly_stable_series_triggers_nothing(algorithm):
    assert detect_drift([8.0] * 400, algorithm) == []


@pytest.mark.parametrize("algorithm", ALGORITHMS)
def test_bounded_fluctuation_without_trend_triggers_nothing(algorithm):
    """Oscillation régulière de +/-0.4 place autour de 8 : de la variation,
    mais aucun changement de moyenne."""
    series = [8.4 if i % 2 == 0 else 7.6 for i in range(400)]
    assert detect_drift(series, algorithm) == []


def test_page_hinkley_delay_on_step_series_matches_hand_computation():
    """
    Délai connu à l'avance sur la série en marche d'escalier, avec les
    réglages retenus (delta=1.5, threshold=10).

    Tant que la série vaut 8, l'écart à la moyenne est nul : le cumul
    "baisse" de Page-Hinkley gagne +delta à chaque point et son maximum le
    suit. À partir du point 150 (valeur 3), la moyenne courante ne bouge
    presque pas (150 points à 8 contre 1 à 3 de plus à chaque fois) :

        point 150 : moyenne 7.967, écart -4.967, cumul -4.967 + 1.5 = -3.47
        point 151 : moyenne 7.934, écart -4.934, cumul -3.47 - 3.43 = -6.90
        point 152 : moyenne 7.902, écart -4.902, cumul -6.90 - 3.40 = -10.30

    Le cumul passe sous -threshold au 3e point : signalement à l'index 152,
    soit un délai de 2 points.
    """
    detections = detect_drift(STEP_SERIES, DriftAlgorithm.page_hinkley)

    assert detections[0] == 152
    assert detection_delay(detections, drift_start=DRIFT_AT) == 2


@pytest.mark.parametrize("detector_cls", [AdwinDriftDetector, PageHinkleyDriftDetector, ParallelDriftDetector])
def test_online_update_matches_offline_detect(detector_cls):
    online = detector_cls()
    online_detections = [i for i, value in enumerate(STEP_SERIES) if online.update(value)]

    assert online_detections == detector_cls().detect(STEP_SERIES)


def test_detect_does_not_carry_state_between_series():
    """`detect` repart d'un détecteur neuf : rejouer une série stable après
    une série driftée ne doit rien signaler."""
    detector = PageHinkleyDriftDetector()
    assert detector.detect(STEP_SERIES)
    assert detector.detect([3.0] * 200) == []


def test_parallel_detector_is_the_union_of_both():
    union = sorted(
        set(detect_drift(STEP_SERIES, DriftAlgorithm.adwin))
        | set(detect_drift(STEP_SERIES, DriftAlgorithm.page_hinkley))
    )
    assert ParallelDriftDetector().detect(STEP_SERIES) == union


# ═════════════════════════════════════════════════════════════════════════
# Délai de détection et métriques au niveau épisode
# ═════════════════════════════════════════════════════════════════════════


def test_detection_delay_uses_first_detection_at_or_after_drift_start():
    # 40 est avant le drift (fausse alerte) ; la première détection valide
    # est 120, soit 20 points après le début réel (100).
    assert detection_delay([40, 120, 130], drift_start=100) == 20
    assert detection_delay([100], drift_start=100) == 0
    assert detection_delay([40], drift_start=100) is None
    assert detection_delay([], drift_start=100) is None


def test_detection_delay_ignores_detections_after_episode_window():
    assert detection_delay([170], drift_start=100, window_end=150) is None
    assert detection_delay([150], drift_start=100, window_end=150) == 50


def test_evaluate_series_classifies_each_detection():
    """Drift de l'index 100 à 120, marge de 30 points : fenêtre d'épisode
    [100, 150]. 40 = fausse alerte avant drift, 112 et 140 = le même
    épisode (détecté, délai 12), 190 = signalement tardif."""
    result = evaluate_series([40, 112, 140, 190], n_points=300, drift_start=100, drift_end=120, settle_points=30)

    assert result["episode_window_end_index"] == 150
    assert result["detected"] is True
    assert result["detection_delay"] == 12
    assert result["false_alarms"] == [40, 190]
    assert result["late_alarms"] == [190]
    assert result["non_drift_points"] == 300 - 51


def test_evaluate_series_late_only_detection_is_a_miss_and_a_false_alarm():
    result = evaluate_series([190], n_points=300, drift_start=100, drift_end=120, settle_points=30)

    assert result["detected"] is False
    assert result["detection_delay"] is None
    assert result["false_alarms"] == [190]


def test_evaluate_series_on_stable_series_counts_every_detection_as_false_alarm():
    result = evaluate_series([50, 200], n_points=300)

    assert result["has_drift"] is False
    assert result["detected"] is False
    assert result["false_alarms"] == [50, 200]
    assert result["non_drift_points"] == 300


def test_aggregate_episode_metrics_hand_computed():
    """
    4 séries driftées (fenêtre [100, 150]) et 4 séries stables :

      driftée A : [110]        -> détectée, délai 10
      driftée B : [130, 140]   -> détectée, délai 30 (2 signalements, 1 épisode)
      driftée C : [60, 120]    -> détectée, délai 20, 1 fausse alerte avant drift
      driftée D : []           -> manquée
      stable  E : [80, 90]     -> 2 fausses alertes
      stable  F, G, H : []

    TP=3, FN=1, FP=3 -> précision 3/6 = 0.5, rappel 3/4, F1 = 0.6.
    Séries stables avec au moins une fausse alerte : 1/4.
    Délai moyen = (10 + 30 + 20) / 3 = 20.
    """
    drifted = [[110], [130, 140], [60, 120], []]
    stable = [[80, 90], [], [], []]
    results = [evaluate_series(d, 300, drift_start=100, drift_end=120, settle_points=30) for d in drifted]
    results += [evaluate_series(d, 300) for d in stable]

    report = aggregate(results)

    assert (report["episodes_detected"], report["episodes_missed"], report["false_alarms"]) == (3, 1, 3)
    assert report["precision"] == pytest.approx(0.5)
    assert report["recall"] == pytest.approx(0.75)
    assert report["f1"] == pytest.approx(0.6)
    assert report["false_positive_rate"] == pytest.approx(0.25)
    assert report["false_alarms_on_stable_series"] == 2
    assert report["false_alarms_before_drift"] == 1
    assert report["false_alarms_after_episode"] == 0
    assert report["mean_detection_delay"] == pytest.approx(20.0)
    assert report["max_detection_delay"] == 30


def test_aggregate_without_any_detection_has_undefined_precision_and_delay():
    report = aggregate([evaluate_series([], 300, drift_start=100, drift_end=120)])

    assert report["recall"] == 0.0
    assert report["precision"] is None
    assert report["f1"] is None
    assert report["mean_detection_delay"] is None


# ═════════════════════════════════════════════════════════════════════════
# Simulateur : vérité terrain
# ═════════════════════════════════════════════════════════════════════════

SMALL_CONFIG = dict(n_drift_providers=3, n_stable_providers=3, updates_per_provider=200,
                    drift_start_min=80, drift_start_max=120, gradual_transition_updates=50)


def test_scenario_has_requested_mix_of_drifted_and_stable_providers():
    scenario = generate_drift_scenario(DriftSimulatorConfig(drift_type=DriftType.gradual, seed=7, **SMALL_CONFIG))

    drifted = [gt for gt in scenario.ground_truth.values() if gt.has_drift]
    stable = [gt for gt in scenario.ground_truth.values() if not gt.has_drift]
    assert (len(drifted), len(stable)) == (3, 3)
    for gt in drifted:
        assert 80 <= gt.drift_start_update <= 120
        assert gt.drift_end_update == gt.drift_start_update + 50
        assert 4.0 <= gt.pre_mean - gt.post_mean <= 6.0
    for gt in stable:
        assert gt.drift_start_update is None
        assert gt.pre_mean == gt.post_mean


def test_abrupt_drift_uses_short_transition():
    scenario = generate_drift_scenario(DriftSimulatorConfig(drift_type=DriftType.abrupt, seed=7, **SMALL_CONFIG))

    for gt in scenario.ground_truth.values():
        if gt.has_drift:
            assert gt.drift_end_update - gt.drift_start_update == 5


def test_latent_mean_follows_the_injected_profile():
    scenario = generate_drift_scenario(DriftSimulatorConfig(drift_type=DriftType.gradual, seed=7, **SMALL_CONFIG))
    gt = next(g for g in scenario.ground_truth.values() if g.has_drift)
    midpoint = (gt.drift_start_update + gt.drift_end_update) // 2

    assert latent_mean(gt, 0) == gt.pre_mean
    assert latent_mean(gt, gt.drift_start_update - 1) == gt.pre_mean
    assert latent_mean(gt, midpoint) == pytest.approx((gt.pre_mean + gt.post_mean) / 2)
    assert latent_mean(gt, gt.drift_end_update) == gt.post_mean
    assert latent_mean(gt, 199) == gt.post_mean


def test_same_seed_gives_same_smoothed_series():
    config = DriftSimulatorConfig(drift_type=DriftType.gradual, seed=7, **SMALL_CONFIG)
    first = build_smoothed_series(generate_drift_scenario(config).events)
    second = build_smoothed_series(generate_drift_scenario(config).events)

    assert {pid: s.values for pid, s in first.items()} == {pid: s.values for pid, s in second.items()}


def test_smoothed_series_reflects_injected_drift_and_ground_truth_index():
    """La série lissée (sortie du pipeline de la Phase 1) d'un provider
    drifté descend bien de `pre_mean` vers `post_mean`, et l'index de début
    de drift traduit dans la série lissée tombe là où la descente commence."""
    scenario = generate_drift_scenario(DriftSimulatorConfig(drift_type=DriftType.abrupt, seed=7, **SMALL_CONFIG))
    series_by_provider = build_smoothed_series(scenario.events)

    for pid, gt in scenario.ground_truth.items():
        series = series_by_provider[pid]
        if not gt.has_drift:
            continue
        start = series.index_of_raw_update(gt.drift_start_update, scenario.raw_update_index)
        assert start is not None and 0 < start < len(series.values)
        # Le pipeline écarte ~35% des mises à jour : l'index lissé est
        # toujours en deçà de l'index brut.
        assert start < gt.drift_start_update

        before = series.values[start - 20:start]
        after = series.values[-20:]
        assert sum(before) / len(before) == pytest.approx(gt.pre_mean, abs=1.5)
        assert sum(after) / len(after) == pytest.approx(gt.post_mean, abs=1.5)
