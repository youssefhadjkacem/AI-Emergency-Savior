"""
Réglage des hyperparamètres d'ADWIN et de Page-Hinkley (section 3.8,
Phase 2) — à lancer AVANT `run_drift_evaluation.py`, dont il justifie les
valeurs retenues dans `drift_detection.py`.

── Séparation réglage / évaluation ──────────────────────────────────────
La grille est évaluée sur des seeds de RÉGLAGE (100, 101, 102) qui ne
servent à rien d'autre. Les seeds 7, 1 et 42 sont réservés à l'évaluation
finale et ne sont jamais consultés ici : les chiffres du rapport
d'évaluation sont donc mesurés sur des données que le réglage n'a pas vues.

── Critère de sélection (fixé avant de regarder les résultats finaux) ───
Pour chaque algorithme, on retient la configuration qui maximise la moyenne
du F1 épisode sur les deux profils (progressif et brusque), séries des 3
seeds de réglage poolées ; à égalité, celle dont le délai de détection
moyen (moyenne des deux profils) est le plus court.

Lancer avec :
    cd backend
    python -m realtime.run_drift_tuning
"""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .drift_detection import DriftAlgorithm, detect_drift
from .drift_evaluation import aggregate, evaluate_scenario
from .drift_simulator import DriftSimulatorConfig, DriftType, simulate_smoothed_series

TUNING_SEEDS = [100, 101, 102]
DRIFT_TYPES = [DriftType.gradual, DriftType.abrupt]

# Grilles. Les valeurs par défaut de River y figurent (ADWIN : delta=0.002,
# clock=32 ; Page-Hinkley : delta=0.005, threshold=50) pour situer le gain
# du réglage par rapport au point de départ.
PARAM_GRIDS: Dict[DriftAlgorithm, Dict[str, list]] = {
    DriftAlgorithm.adwin: {
        "delta": [2e-3, 1e-4, 1e-6, 1e-8, 1e-10, 1e-12, 1e-15, 1e-20],
        "clock": [1, 32],
    },
    DriftAlgorithm.page_hinkley: {
        "delta": [0.005, 0.1, 0.25, 0.5, 1.0, 1.5],
        "threshold": [5.0, 10.0, 20.0, 35.0, 50.0, 80.0],
    },
}

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = RESULTS_DIR / "drift_tuning_report.json"

_KEPT_METRICS = (
    "precision", "recall", "f1", "false_positive_rate", "false_alarms",
    "false_alarms_per_1000_points", "mean_detection_delay",
)


def _evaluate_config(algorithm: DriftAlgorithm, params: dict, data: dict) -> dict:
    by_type = {}
    for drift_type in DRIFT_TYPES:
        pooled: List[dict] = []
        for scenario, series in data[drift_type]:
            report = evaluate_scenario(scenario, series, lambda v: detect_drift(v, algorithm, **params))
            pooled.extend(report["per_series"].values())
        metrics = aggregate(pooled)
        by_type[drift_type.value] = {k: metrics[k] for k in _KEPT_METRICS}

    f1s = [by_type[t.value]["f1"] or 0.0 for t in DRIFT_TYPES]
    delays = [by_type[t.value]["mean_detection_delay"] for t in DRIFT_TYPES]
    return {
        "params": params,
        "by_drift_type": by_type,
        "mean_f1": sum(f1s) / len(f1s),
        # Un profil sans aucune détection n'a pas de délai : la config est
        # alors classée derrière toutes les autres à F1 égal.
        "mean_delay": (sum(delays) / len(delays)) if all(d is not None for d in delays) else None,
    }


def _selection_key(entry: dict):
    delay = entry["mean_delay"] if entry["mean_delay"] is not None else float("inf")
    return (-entry["mean_f1"], delay)


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    data = {
        drift_type: [
            simulate_smoothed_series(DriftSimulatorConfig(drift_type=drift_type, seed=seed))
            for seed in TUNING_SEEDS
        ]
        for drift_type in DRIFT_TYPES
    }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Grille evaluee sur les seeds de reglage uniquement ; les seeds d'evaluation "
            "(7, 1, 42) ne sont pas utilises ici. Critere : F1 episode moyen sur les deux "
            "profils (series poolees sur les seeds), puis delai moyen le plus court."
        ),
        "tuning_seeds": TUNING_SEEDS,
        "algorithms": {},
    }

    for algorithm, grid in PARAM_GRIDS.items():
        names = list(grid)
        entries = [
            _evaluate_config(algorithm, dict(zip(names, values)), data)
            for values in itertools.product(*(grid[n] for n in names))
        ]
        entries.sort(key=_selection_key)
        output["algorithms"][algorithm.value] = {"selected": entries[0]["params"], "grid": entries}

        print("=" * 100)
        print(f"  {algorithm.value} — {len(entries)} configurations, classées (meilleure en premier)")
        print("=" * 100)
        header = f"{'paramètres':<34}" + "".join(
            f"{t.value + ' F1':>12}{'FPR':>7}{'délai':>8}" for t in DRIFT_TYPES
        )
        print(header)
        print("-" * len(header))
        for e in entries:
            row = f"{str(e['params']):<34}"
            for t in DRIFT_TYPES:
                m = e["by_drift_type"][t.value]
                delay = f"{m['mean_detection_delay']:.1f}" if m["mean_detection_delay"] is not None else "n/a"
                row += f"{(m['f1'] or 0.0):>12.3f}{m['false_positive_rate']:>7.2f}{delay:>8}"
            print(row)
        print(f"-> retenu : {entries[0]['params']}")
        print()

    REPORT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Rapport de réglage sauvegardé dans : {REPORT_PATH}")


if __name__ == "__main__":
    main()
