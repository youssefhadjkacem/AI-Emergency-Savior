"""
Évaluation quantitative de la détection de concept drift (section 3.8,
section Experiments du papier) — même principe qu'`evaluation.py` en
Phase 1 : le simulateur (`drift_simulator.py`) connaît la vérité terrain,
les détecteurs (`drift_detection.py`) ne voient qu'une liste de flottants,
et ce module recolle les deux après coup.

── Unité d'évaluation : l'épisode, pas le point ─────────────────────────
Une série contient au plus UN épisode de drift. Un détecteur peut signaler
plusieurs fois le même épisode (ADWIN recoupe sa fenêtre plusieurs fois
pendant une dérive lente) : ces signalements répétés ne sont ni plusieurs
succès, ni des erreurs. D'où la notion de FENÊTRE D'ÉPISODE :

    [début réel du drift, fin réelle de la transition + DRIFT_SETTLE_POINTS]

  - épisode détecté (vrai positif)  : au moins un signalement dans la fenêtre ;
  - épisode manqué (faux négatif)   : aucun signalement dans la fenêtre ;
  - fausse alerte (faux positif)    : tout signalement HORS fenêtre — donc
    tout signalement sur une série stable, tout signalement avant le début
    du drift, et tout signalement tardif une fois le nouveau régime installé.
    Les trois origines sont ventilées dans le rapport (`false_alarms_on_stable_series`,
    `false_alarms_before_drift`, `false_alarms_after_episode`).

    rappel    = épisodes détectés / épisodes réels
    précision = épisodes détectés / (épisodes détectés + fausses alertes)

Chaque fausse alerte compte individuellement : en production, chacune
déclencherait à tort la logique de saturation.

── Limite connue de ce protocole ────────────────────────────────────────
Un signalement qui tombe dans la fenêtre par hasard (fausse alerte sans
rapport avec le drift) est indiscernable d'une vraie détection : il est
compté comme vrai positif, avec un délai flatteur. L'effet est négligeable
pour un détecteur qui donne peu de fausses alertes et notable sinon — le
rappel et le délai d'un détecteur bruyant sont donc à lire avec son taux
de fausses alertes (`false_alarms_per_1000_points`).
"""

from __future__ import annotations

import statistics
from typing import Callable, Dict, List, Optional, Sequence

from .drift_simulator import DriftScenario, SmoothedSeries

DRIFT_SETTLE_POINTS = 30
# Marge accordée après la fin réelle de la transition pour qu'un
# signalement soit encore rattaché à l'épisode. La série évaluée est une
# EWMA de mémoire ~10 points (voir `DriftSimulatorConfig.update_interval_seconds`) :
# elle continue de descendre après que la moyenne latente s'est stabilisée,
# et n'a rejoint son nouveau niveau à ~5% près qu'au bout de ~3 constantes
# de temps, soit ~30 points (≈ 65 min). Un signalement plus tardif n'est
# plus exploitable comme alerte de saturation : il est compté comme fausse
# alerte ET l'épisode comme manqué.

Detector = Callable[[Sequence[float]], List[int]]


def detection_delay(detections: Sequence[int], drift_start: int, window_end: Optional[int] = None) -> Optional[int]:
    """Nombre de points entre le début réel du drift et le premier
    signalement situé dans la fenêtre d'épisode. None si aucun."""
    for d in sorted(detections):
        if d >= drift_start and (window_end is None or d <= window_end):
            return d - drift_start
    return None


def evaluate_series(
    detections: Sequence[int],
    n_points: int,
    drift_start: Optional[int] = None,
    drift_end: Optional[int] = None,
    settle_points: int = DRIFT_SETTLE_POINTS,
) -> dict:
    """Évalue les signalements d'un détecteur sur UNE série.
    `drift_start`/`drift_end` sont des index dans la série lissée ;
    `drift_start=None` désigne une série stable."""
    has_drift = drift_start is not None
    if has_drift:
        window_end = min(n_points - 1, (drift_end if drift_end is not None else drift_start) + settle_points)
        delay = detection_delay(detections, drift_start, window_end)
        pre_drift_alarms = [d for d in detections if d < drift_start]
        post_episode_alarms = [d for d in detections if d > window_end]
        non_drift_points = n_points - (window_end - drift_start + 1)
    else:
        window_end, delay = None, None
        pre_drift_alarms, post_episode_alarms = list(detections), []
        non_drift_points = n_points

    return {
        "has_drift": has_drift,
        "n_points": n_points,
        "drift_start_index": drift_start,
        "drift_end_index": drift_end,
        "episode_window_end_index": window_end,
        "detections": list(detections),
        "detected": delay is not None,
        "detection_delay": delay,
        "false_alarms": pre_drift_alarms + post_episode_alarms,
        # Signalements tardifs, après la fenêtre d'épisode d'une série
        # driftée (toujours vide pour une série stable). Comptés comme
        # fausses alertes, mais ventilés à part : ce sont le plus souvent
        # des RE-détections du même drift, pas des alertes sans cause.
        "late_alarms": post_episode_alarms,
        "non_drift_points": non_drift_points,
    }


def _prf(tp: int, fp: int, fn: int):
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def aggregate(series_results: List[dict]) -> dict:
    """Métriques au niveau épisode sur un ensemble de séries évaluées."""
    drifted = [r for r in series_results if r["has_drift"]]
    stable = [r for r in series_results if not r["has_drift"]]

    tp = sum(1 for r in drifted if r["detected"])
    fn = len(drifted) - tp
    fp = sum(len(r["false_alarms"]) for r in series_results)
    precision, recall, f1 = _prf(tp, fp, fn)

    delays = [r["detection_delay"] for r in drifted if r["detected"]]
    stable_with_alarm = sum(1 for r in stable if r["false_alarms"])
    non_drift_points = sum(r["non_drift_points"] for r in series_results)

    return {
        "n_drift_episodes": len(drifted),
        "n_stable_series": len(stable),
        "episodes_detected": tp,
        "episodes_missed": fn,
        "false_alarms": fp,
        "false_alarms_on_stable_series": sum(len(r["false_alarms"]) for r in stable),
        "false_alarms_before_drift": sum(len(r["false_alarms"]) - len(r["late_alarms"]) for r in drifted),
        "false_alarms_after_episode": sum(len(r["late_alarms"]) for r in drifted),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        # Taux de faux positifs au niveau série : part des séries STABLES
        # sur lesquelles au moins un drift a été signalé.
        "false_positive_rate": (stable_with_alarm / len(stable)) if stable else None,
        "stable_series_with_false_alarm": stable_with_alarm,
        # Complément indépendant de la longueur des séries : fausses alertes
        # pour 1000 points hors fenêtre d'épisode (séries stables + segments
        # stables des séries driftées).
        "false_alarms_per_1000_points": (1000 * fp / non_drift_points) if non_drift_points else None,
        "mean_detection_delay": statistics.mean(delays) if delays else None,
        "median_detection_delay": statistics.median(delays) if delays else None,
        "std_detection_delay": statistics.stdev(delays) if len(delays) >= 2 else None,
        "max_detection_delay": max(delays) if delays else None,
        "detection_delays": delays,
    }


def evaluate_scenario(
    scenario: DriftScenario,
    series_by_provider: Dict[str, SmoothedSeries],
    detector: Detector,
    settle_points: int = DRIFT_SETTLE_POINTS,
) -> dict:
    """Applique `detector` à la série lissée de chaque provider — il ne
    reçoit QUE `series.values` — puis évalue contre la vérité terrain."""
    per_series: Dict[str, dict] = {}
    for provider_id in sorted(series_by_provider):
        series = series_by_provider[provider_id]
        detections = detector(series.values)  # le détecteur ne voit jamais la vérité terrain

        truth = scenario.ground_truth[provider_id]
        start = end = None
        if truth.has_drift:
            start = series.index_of_raw_update(truth.drift_start_update, scenario.raw_update_index)
            end = series.index_of_raw_update(truth.drift_end_update, scenario.raw_update_index)
        per_series[provider_id] = evaluate_series(detections, len(series.values), start, end, settle_points)

    report = aggregate(list(per_series.values()))
    report["per_series"] = per_series
    return report


SEED_SUMMARY_METRICS = (
    "precision",
    "recall",
    "f1",
    "false_positive_rate",
    "false_alarms_per_1000_points",
    "mean_detection_delay",
)


def summarize_across_seeds(reports_by_seed: Dict[int, dict]) -> dict:
    """Moyenne et écart-type inter-seeds de chaque métrique, plus les
    chiffres poolés (toutes séries de tous les seeds confondues).

    L'écart-type est l'estimateur d'échantillon (n-1) ; avec 3 seeds c'est
    un ordre de grandeur de la variabilité, pas un intervalle de confiance.
    Un seed où la métrique n'est pas définie (ex. délai moyen sans aucune
    détection) est exclu de la moyenne et signalé par `n_seeds_defined`."""
    summary: Dict[str, dict] = {}
    for metric in SEED_SUMMARY_METRICS:
        values = [r[metric] for r in reports_by_seed.values() if r[metric] is not None]
        summary[metric] = {
            "mean": statistics.mean(values) if values else None,
            "std": statistics.stdev(values) if len(values) >= 2 else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "n_seeds_defined": len(values),
        }

    pooled_series = [s for r in reports_by_seed.values() for s in r["per_series"].values()]
    pooled = aggregate(pooled_series)
    pooled.pop("detection_delays")
    summary["pooled"] = pooled
    return summary
