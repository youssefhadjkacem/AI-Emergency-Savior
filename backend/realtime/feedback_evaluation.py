"""
Métriques de la boucle de rétroaction (Phase 3), calculées contre la vérité
terrain du simulateur (`feedback_simulator.RunLog`).

Tout ce qui est "réel" ici vient de l'occupation réelle des prestataires,
que le système ne voit jamais. Les fonctions de ce module ne sont appelées
qu'après la simulation.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence

import numpy as np

from .feedback_simulator import RunLog
from .saturation import ALERT_THRESHOLD, SATURATION_THRESHOLD

MIN_EPISODE_SAMPLES = 5
# Un épisode de saturation réelle = au moins 5 échantillons consécutifs
# (5 minutes) à 90 % ou plus. En dessous, c'est un passage instantané à
# pleine charge, qu'aucun système ne peut suivre à la minute.

LOOKBACK_SAMPLES = 10
# Pour le délai de détection, l'état du système est regardé à partir de 10
# minutes AVANT le début de l'épisode : un système qui a déjà déclaré le
# prestataire saturé a anticipé (délai négatif, borné à -10 min).

FALSE_ALERT_HORIZON_SAMPLES = 30
# Une déclaration de saturation est "à tort" si l'occupation réelle est
# sous 80 % à ce moment et n'atteint pas 90 % dans les 30 minutes.


def gini(values: Sequence[float]) -> float:
    """Indice de Gini : 0 = charge égale entre prestataires, proche de 1 =
    toute la charge sur un seul."""
    x = np.sort(np.asarray(values, dtype=float))
    if len(x) == 0 or x.sum() == 0:
        return 0.0
    n = len(x)
    return float((2 * np.arange(1, n + 1) - n - 1).dot(x) / (n * x.sum()))


def _runs(mask: np.ndarray) -> List[tuple]:
    """Plages [début, fin) où `mask` est vrai."""
    padded = np.concatenate([[False], mask, [False]])
    change = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(change[::2], change[1::2]))


def evaluate_run(log: RunLog) -> Dict[str, Optional[float]]:
    world = log.world
    capacity = np.asarray(world.capacity, dtype=float)
    index = world.index()
    records = log.records
    arrived = [r for r in records if r.outcome in ("served", "refused")]
    m: Dict[str, Optional[float]] = {"patients": len(records), "arrived": len(arrived)}

    # ── Patients envoyés vers un prestataire saturé ou plein ─────────────
    m["sent_to_saturated_rate"] = float(np.mean([r.occupancy_at_arrival >= SATURATION_THRESHOLD for r in arrived]))
    m["refused_rate"] = float(np.mean([r.outcome == "refused" for r in arrived]))
    m["refused"] = sum(r.outcome == "refused" for r in arrived)

    # ── Occupation réelle ────────────────────────────────────────────────
    occupancy = np.asarray(log.occupied, dtype=float) / capacity          # [temps, prestataire]
    per_provider = occupancy.mean(axis=0)
    m["mean_occupancy"] = float(occupancy.mean())
    m["max_provider_mean_occupancy"] = float(per_provider.max())
    m["provider_time_saturated"] = float((occupancy >= SATURATION_THRESHOLD).mean())
    m["provider_time_full"] = float((occupancy >= 1.0).mean())

    # ── Équilibrage de la charge, par spécialité ─────────────────────────
    received = Counter(r.chosen.provider_id for r in records)
    by_specialty = defaultdict(list)
    for pid, specialty in zip(world.provider_ids, world.specialty):
        by_specialty[specialty].append(pid)
    ginis, stds, top3_shares, unused = [], [], [], []
    for specialty, pids in by_specialty.items():
        counts = [received.get(pid, 0) for pid in pids]
        if sum(counts) == 0:
            continue
        ginis.append(gini(counts))
        stds.append(float(np.std([per_provider[index[pid]] for pid in pids])))
        top3_shares.append(sum(sorted(counts, reverse=True)[:3]) / sum(counts))
        unused.append(sum(c == 0 for c in counts) / len(counts))
    m["gini_patients"] = float(np.mean(ginis))
    m["std_occupancy_within_specialty"] = float(np.mean(stds))
    m["top3_providers_share"] = float(np.mean(top3_shares))
    m["providers_never_chosen_share"] = float(np.mean(unused))

    # ── Coût de l'adaptation, par rapport au classement statique ─────────
    def same_city(candidate, patient) -> bool:
        return candidate.city.strip().lower() == patient.city.strip().lower()

    m["top1_quality"] = float(np.mean([r.top[0].quality for r in records]))
    m["top1_cost"] = float(np.mean([r.top[0].cost for r in records]))
    m["top1_wait_days"] = float(np.mean([r.top[0].wait_days for r in records]))
    m["top1_same_city"] = float(np.mean([same_city(r.top[0], r.patient) for r in records]))
    m["delta_quality"] = float(np.mean([r.top[0].quality - r.base_top[0].quality for r in records]))
    m["delta_cost"] = float(np.mean([r.top[0].cost - r.base_top[0].cost for r in records]))
    m["delta_wait_days"] = float(np.mean([r.top[0].wait_days - r.base_top[0].wait_days for r in records]))
    m["delta_same_city"] = float(np.mean(
        [same_city(r.top[0], r.patient) - same_city(r.base_top[0], r.patient) for r in records]))
    m["top1_changed_rate"] = float(np.mean([r.top[0].provider_id != r.base_top[0].provider_id for r in records]))
    m["top3_changed_rate"] = float(np.mean(
        [{c.provider_id for c in r.top} != {c.provider_id for c in r.base_top} for r in records]))

    # ── Contrôles de sécurité ────────────────────────────────────────────
    m["specialty_preserved_rate"] = float(np.mean(
        [all(c.specialty == r.patient.specialty for c in r.top) for r in records]))
    critical = [r for r in records if r.patient.severity == "CRITICAL"]
    m["critical_patients"] = len(critical)
    m["critical_moved_out_of_city"] = sum(
        [same_city(c, r.patient) for c in r.top] != [same_city(c, r.patient) for c in r.base_top] for r in critical)
    m["all_saturated_responses"] = sum(r.all_saturated for r in records)
    m["responses_with_saturated_provider"] = sum(r.saturated_in_top > 0 for r in records)

    # ── Détection de la saturation (conditions adaptatives) ──────────────
    if not log.system_status:
        return m
    status = np.asarray(log.system_status)                               # 0 normal, 1 alerte, 2 saturé
    committed = (np.asarray(log.occupied, dtype=float) + np.asarray(log.en_route, dtype=float)) / capacity
    really_saturated = occupancy >= SATURATION_THRESHOLD

    delays_saturated, delays_alert, episodes = [], [], 0
    for j in range(len(world.provider_ids)):
        for start, end in _runs(really_saturated[:, j]):
            if end - start < MIN_EPISODE_SAMPLES:
                continue
            episodes += 1
            window_start = max(0, start - LOOKBACK_SAMPLES)
            for level, delays in ((2, delays_saturated), (1, delays_alert)):
                hits = np.flatnonzero(status[window_start:end, j] >= level)
                if len(hits):
                    delays.append(int(window_start + hits[0] - start))
    m["saturation_episodes"] = episodes
    m["episodes_detected_rate"] = len(delays_saturated) / episodes if episodes else None
    m["episodes_alerted_rate"] = len(delays_alert) / episodes if episodes else None
    m["detection_delay_median_min"] = float(statistics.median(delays_saturated)) if delays_saturated else None
    m["detection_delay_mean_min"] = float(np.mean(delays_saturated)) if delays_saturated else None
    m["alert_delay_median_min"] = float(statistics.median(delays_alert)) if delays_alert else None
    m["episodes_anticipated_rate"] = (
        sum(d <= 0 for d in delays_saturated) / episodes if episodes else None)

    # Déclarations de saturation à tort.
    declarations = false_real = false_committed = 0
    for j in range(len(world.provider_ids)):
        for start, _ in _runs(status[:, j] == 2):
            declarations += 1
            horizon = occupancy[start:start + FALSE_ALERT_HORIZON_SAMPLES, j]
            if occupancy[start, j] < ALERT_THRESHOLD and horizon.max() < SATURATION_THRESHOLD:
                false_real += 1
                # Occupation "engagée" : places occupées + patients du
                # système en chemin. Une déclaration fondée sur des patients
                # réellement en route n'est pas une erreur d'observation.
                if committed[start, j] < ALERT_THRESHOLD:
                    false_committed += 1
    m["saturation_declarations"] = declarations
    m["false_declaration_rate"] = false_real / declarations if declarations else None
    m["false_declaration_rate_excluding_en_route"] = false_committed / declarations if declarations else None
    m["system_time_saturated"] = float((status == 2).mean())
    m["system_time_alert"] = float((status == 1).mean())

    # Signal de tendance (Page-Hinkley).
    m["drift_alerts"] = len(log.drift_alerts)
    if log.drift_alerts:
        times = np.asarray(log.sample_times)
        followed = 0
        counted = 0
        for when, pid in log.drift_alerts:
            if when < times[0]:
                continue  # pendant la mise en régime
            counted += 1
            start = int(np.searchsorted(times, when))
            if really_saturated[start:start + FALSE_ALERT_HORIZON_SAMPLES, index[pid]].any():
                followed += 1
        m["drift_alerts"] = counted
        m["drift_alerts_followed_by_saturation_rate"] = followed / counted if counted else None
    return m


def aggregate(per_seed: List[Dict[str, Optional[float]]]) -> Dict[str, Dict[str, Optional[float]]]:
    """Moyenne et écart-type inter-seeds (n - 1) de chaque métrique."""
    out: Dict[str, Dict[str, Optional[float]]] = {}
    for key in sorted({k for metrics in per_seed for k in metrics}):
        values = [metrics[key] for metrics in per_seed if metrics.get(key) is not None]
        if not values:
            out[key] = {"mean": None, "std": None, "n": 0}
            continue
        out[key] = {
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
            "n": len(values),
            "values": [float(v) for v in values],
        }
    return out
