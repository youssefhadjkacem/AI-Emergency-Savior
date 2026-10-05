"""
Script d'évaluation quantitative de la détection de concept drift
(section 3.8, Phase 2) : ADWIN contre Page-Hinkley.

Pour chaque seed (7, 1, 42 — les mêmes qu'en Phase 1) et chaque profil de
drift (progressif, brusque), génère un scénario avec vérité terrain
(`drift_simulator.py`), le fait passer par le pipeline de filtrage de la
Phase 1, applique les deux détecteurs à la disponibilité lissée de chaque
provider et mesure rappel / précision / F1 au niveau épisode, taux de faux
positifs et délai de détection (`drift_evaluation.py`).

Trois blocs dans le rapport (`results/drift_evaluation_report.json`) :
  - `results`                  : hyperparamètres retenus (ceux de
                                 `drift_detection.py`), algorithme x profil x seed,
                                 plus la combinaison des deux en parallèle ;
  - `river_defaults_baseline`  : mêmes scénarios, hyperparamètres par défaut
                                 de River (point de départ du réglage) ;
  - `reduced_amplitude_check`  : hyperparamètres retenus, mais drift de 2 à
                                 3 places au lieu de 4 à 6 — pour savoir ce
                                 que valent les réglages hors de l'amplitude
                                 sur laquelle ils ont été choisis.

Les hyperparamètres ont été réglés sur d'autres seeds (100/101/102, voir
`run_drift_tuning.py`) : les seeds évalués ici n'ont pas servi au réglage.

Lancer avec :
    cd backend
    python -m realtime.run_drift_evaluation
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .drift_detection import DETECTOR_FACTORY, RIVER_DEFAULT_PARAMS, DriftAlgorithm, ParallelDriftDetector
from .drift_evaluation import DRIFT_SETTLE_POINTS, evaluate_scenario, summarize_across_seeds
from .drift_simulator import DriftSimulatorConfig, DriftType, simulate_smoothed_series

# Mêmes seeds que la Phase 1 (`run_evaluation.py`).
PRIMARY_SEED = 7
ADDITIONAL_SEEDS = [1, 42]
ALL_SEEDS = [PRIMARY_SEED] + ADDITIONAL_SEEDS
DRIFT_TYPES = [DriftType.gradual, DriftType.abrupt]

REDUCED_DROP_MIN = 2.0
REDUCED_DROP_MAX = 3.0
# Drift "discret" pour le test de robustesse : 2 à 3 places, soit la moitié
# de l'amplitude du scénario principal (4 à 6) et seulement 4 à 6
# écarts-types du bruit résiduel de la série lissée (~0.5 place).

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = RESULTS_DIR / "drift_evaluation_report.json"


def _build_data(**config_overrides) -> Dict[DriftType, Dict[int, tuple]]:
    return {
        drift_type: {
            seed: simulate_smoothed_series(
                DriftSimulatorConfig(drift_type=drift_type, seed=seed, **config_overrides)
            )
            for seed in ALL_SEEDS
        }
        for drift_type in DRIFT_TYPES
    }


def _evaluate(
    data: dict,
    params_by_algorithm: Optional[Dict[DriftAlgorithm, dict]] = None,
    with_parallel: bool = False,
) -> dict:
    """results[détecteur][profil] = {"by_seed": {seed: rapport}, "across_seeds": résumé}.
    `with_parallel` ajoute la combinaison ADWIN OU Page-Hinkley (hyperparamètres retenus)."""
    detectors = [DETECTOR_FACTORY[a](**(params_by_algorithm or {}).get(a, {})) for a in DriftAlgorithm]
    if with_parallel:
        detectors.append(ParallelDriftDetector())

    results: Dict[str, dict] = {}
    for detector in detectors:
        results[detector.name] = {"hyperparameters": detector.params}
        for drift_type in DRIFT_TYPES:
            by_seed = {
                seed: evaluate_scenario(scenario, series, detector.detect)
                for seed, (scenario, series) in data[drift_type].items()
            }
            results[detector.name][drift_type.value] = {
                "by_seed": {str(seed): report for seed, report in by_seed.items()},
                "across_seeds": summarize_across_seeds(by_seed),
            }
    return results


def _series_stats(data: dict) -> dict:
    lengths, gaps = [], []
    for by_seed in data.values():
        for _, series_by_provider in by_seed.values():
            for s in series_by_provider.values():
                lengths.append(len(s.values))
                # Les points périmés (timestamp reculé de plusieurs heures)
                # donnent des écarts négatifs ou aberrants : la médiane y
                # est insensible.
                gaps.extend((b - a).total_seconds() for a, b in zip(s.timestamps, s.timestamps[1:]))
    return {
        "n_series": len(lengths),
        "mean_points_per_series": statistics.mean(lengths),
        "min_points_per_series": min(lengths),
        "max_points_per_series": max(lengths),
        "median_seconds_between_points": statistics.median(gaps),
    }


def _fmt(value, spec=".3f") -> str:
    return format(value, spec) if value is not None else "n/a"


def _mean_std(summary: dict, metric: str, spec=".3f") -> str:
    m = summary[metric]
    if m["mean"] is None:
        return "n/a"
    std = _fmt(m["std"], spec) if m["std"] is not None else "n/a"
    return f"{_fmt(m['mean'], spec)} ± {std}"


W = 24  # largeur de la colonne "détecteur" dans les tableaux console


def _print_block(title: str, results: dict) -> None:
    print("=" * 128)
    print(f"  {title}")
    print("=" * 128)

    print("Par seed (épisodes détectés / réels ; fausses alertes = stables + avant drift + après épisode) :")
    header = (f"{'détecteur':<{W}}{'profil':<10}{'seed':>5}{'détectés':>10}{'P':>8}{'R':>8}{'F1':>8}"
              f"{'FPR':>7}{'fausses alertes':>24}{'délai moy.':>12}{'délai max':>11}")
    print(header)
    print("-" * len(header))
    for name in results:
        for drift_type in DRIFT_TYPES:
            for seed, r in results[name][drift_type.value]["by_seed"].items():
                alarms = (f"{r['false_alarms']} ({r['false_alarms_on_stable_series']}+"
                          f"{r['false_alarms_before_drift']}+{r['false_alarms_after_episode']})")
                print(f"{name:<{W}}{drift_type.value:<10}{seed:>5}"
                      f"{str(r['episodes_detected']) + '/' + str(r['n_drift_episodes']):>10}"
                      f"{_fmt(r['precision']):>8}{_fmt(r['recall']):>8}{_fmt(r['f1']):>8}"
                      f"{_fmt(r['false_positive_rate'], '.2f'):>7}{alarms:>24}"
                      f"{_fmt(r['mean_detection_delay'], '.1f'):>12}{_fmt(r['max_detection_delay'], 'd'):>11}")
    print()

    print("Moyenne ± écart-type inter-seeds (3 seeds) :")
    header = (f"{'détecteur':<{W}}{'profil':<10}{'précision':>16}{'rappel':>16}{'F1':>16}"
              f"{'FPR (séries stables)':>24}{'délai moyen (points)':>24}")
    print(header)
    print("-" * len(header))
    for name in results:
        for drift_type in DRIFT_TYPES:
            s = results[name][drift_type.value]["across_seeds"]
            print(f"{name:<{W}}{drift_type.value:<10}"
                  f"{_mean_std(s, 'precision'):>16}{_mean_std(s, 'recall'):>16}{_mean_std(s, 'f1'):>16}"
                  f"{_mean_std(s, 'false_positive_rate', '.2f'):>24}"
                  f"{_mean_std(s, 'mean_detection_delay', '.1f'):>24}")
    print()


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    data = _build_data()
    stats = _series_stats(data)
    results = _evaluate(data, with_parallel=True)
    baseline = _evaluate(data, RIVER_DEFAULT_PARAMS)
    reduced = _evaluate(_build_data(drop_min=REDUCED_DROP_MIN, drop_max=REDUCED_DROP_MAX), with_parallel=True)

    reference = DriftSimulatorConfig()
    print(f"{stats['n_series']} séries, {stats['mean_points_per_series']:.0f} points lissés en moyenne, "
          f"écart médian entre deux points : {stats['median_seconds_between_points']:.0f} s")
    print()
    _print_block("HYPERPARAMÈTRES RETENUS — drift de 4 à 6 places", results)
    _print_block("POINT DE DÉPART — hyperparamètres par défaut de River, mêmes scénarios", baseline)
    _print_block(f"ROBUSTESSE — hyperparamètres retenus, drift réduit à {REDUCED_DROP_MIN:g}-{REDUCED_DROP_MAX:g} places", reduced)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Detection sur la disponibilite lissee (EWMA) produite par le pipeline de la Phase 1. "
            "Metriques au niveau episode (voir drift_evaluation.py). Pour un seed donne, les "
            "providers stables sont les MEMES dans les scenarios 'gradual' et 'abrupt' (meme "
            "tirage) : le taux de faux positifs sur series stables est donc identique entre les "
            "deux profils et ne doit pas etre compte deux fois. Hyperparametres regles sur les "
            "seeds 100/101/102 (drift_tuning_report.json), pas sur les seeds evalues ici."
        ),
        "primary_seed": PRIMARY_SEED,
        "seeds_evaluated": ALL_SEEDS,
        "drift_types": [t.value for t in DRIFT_TYPES],
        "simulation": {
            "n_drift_providers_per_scenario": reference.n_drift_providers,
            "n_stable_providers_per_scenario": reference.n_stable_providers,
            "raw_updates_per_provider": reference.updates_per_provider,
            "drift_amplitude_slots": [reference.drop_min, reference.drop_max],
            "gradual_transition_raw_updates": reference.gradual_transition_updates,
            "abrupt_transition_raw_updates": reference.abrupt_transition_updates,
            "noise_std_slots": reference.noise_std,
            "episode_settle_points": DRIFT_SETTLE_POINTS,
            **stats,
        },
        "results": results,
        "river_defaults_baseline": baseline,
        "reduced_amplitude_check": {
            "drift_amplitude_slots": [REDUCED_DROP_MIN, REDUCED_DROP_MAX],
            "results": reduced,
        },
    }
    REPORT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Rapport sauvegardé dans : {REPORT_PATH}")


if __name__ == "__main__":
    main()
