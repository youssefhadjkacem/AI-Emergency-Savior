"""
Réglage des trois paramètres de l'adaptation (Phase 3) sur les seeds de
réglage 100/101/102 — jamais sur les seeds d'évaluation 7/1/42.

    cd backend
    python -m realtime.run_feedback_tuning

Paramètres réglés (grille complète, 4 x 4 x 4 = 64 configurations) :
  - marge d'hystérésis (`readmission_margin`)   : 0.05, 0.10, 0.15, 0.20
  - durée de vie d'une réservation provisoire  : 30, 60, 90, 120 min
  - pénalité d'un prestataire en alerte        : 0.02, 0.05, 0.10, 0.20
Les seuils 80 % et 90 % viennent du papier et ne sont pas réglés.

Condition réglée : C (système complet). Charges : moyenne et forte (à
faible charge il n'y a presque rien à corriger).

CRITÈRE, fixé avant de lire les résultats :
  1. minimiser le taux de patients refusés faute de place, en moyenne sur
     les 3 seeds et les 2 charges ;
  2. parmi les configurations à moins de 0,3 point du meilleur taux (écart
     de l'ordre du bruit entre seeds), garder celle qui dégrade le moins la
     note du prestataire n°1.
"""

from __future__ import annotations

import json
import time
from itertools import product
from pathlib import Path

import numpy as np

from .feedback_runner import Task, run_many
from .feedback_simulator import LOAD_LEVELS, SystemParams

TUNING_SEEDS = [100, 101, 102]
TUNING_LOADS = ["medium", "high"]
MARGINS = [0.05, 0.10, 0.15, 0.20]
TTL_MINUTES = [30, 60, 90, 120]
PENALTIES = [0.02, 0.05, 0.10, 0.20]
TOLERANCE = 0.003

RESULTS_DIR = Path(__file__).parent / "results"


def main() -> None:
    started = time.time()
    grid = [SystemParams(margin, ttl * 60.0, penalty) for margin, ttl, penalty in product(MARGINS, TTL_MINUTES, PENALTIES)]
    tasks = [Task(seed, load, "C", params) for seed in TUNING_SEEDS for load in TUNING_LOADS for params in grid]
    # Référence statique, pour situer les chiffres (et fixer les niveaux de charge).
    reference_tasks = [Task(seed, load, "A") for seed in TUNING_SEEDS for load in LOAD_LEVELS]
    results = run_many(tasks + reference_tasks)
    tuned, reference = results[:len(tasks)], results[len(tasks):]

    rows = []
    for params in grid:
        runs = [r for r in tuned if r["params"] == {
            "readmission_margin": params.readmission_margin,
            "reservation_ttl_seconds": params.reservation_ttl_seconds,
            "alert_penalty": params.alert_penalty}]
        row = {
            "readmission_margin": params.readmission_margin,
            "reservation_ttl_minutes": params.reservation_ttl_seconds / 60.0,
            "alert_penalty": params.alert_penalty,
            "n_runs": len(runs),
        }
        for key in ("refused_rate", "sent_to_saturated_rate", "delta_quality", "delta_same_city",
                    "top1_changed_rate", "false_declaration_rate"):
            row[key] = float(np.mean([r["metrics"][key] for r in runs]))
            for load in TUNING_LOADS:
                row[f"{key}_{load}"] = float(np.mean([r["metrics"][key] for r in runs if r["load"] == load]))
        rows.append(row)

    best_refused = min(row["refused_rate"] for row in rows)
    shortlist = [row for row in rows if row["refused_rate"] <= best_refused + TOLERANCE]
    chosen = max(shortlist, key=lambda row: row["delta_quality"])  # delta <= 0 : le moins négatif

    static = {}
    for load in LOAD_LEVELS:
        runs = [r for r in reference if r["load"] == load]
        static[load] = {key: float(np.mean([r["metrics"][key] for r in runs]))
                        for key in ("refused_rate", "sent_to_saturated_rate", "top3_providers_share", "patients")}

    report = {
        "seeds": TUNING_SEEDS, "loads": TUNING_LOADS, "load_levels_patients_per_hour": LOAD_LEVELS,
        "criterion": "min refused_rate (moyenne seeds x charges) ; a moins de 0.003 du meilleur, plus petite perte de note",
        "static_reference": static,
        "best_refused_rate": best_refused,
        "shortlist_size": len(shortlist),
        "chosen": chosen,
        "grid": sorted(rows, key=lambda row: row["refused_rate"]),
        "seconds": time.time() - started,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "feedback_tuning_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("Référence statique (A) :", json.dumps(static, indent=1))
    print(f"\n{'marge':>6} {'ttl':>5} {'pénal.':>7} {'refus':>7} {'saturé':>7} {'Δnote':>7} {'Top1≠':>6}")
    for row in report["grid"][:15]:
        print(f"{row['readmission_margin']:>6.2f} {row['reservation_ttl_minutes']:>5.0f} {row['alert_penalty']:>7.2f} "
              f"{row['refused_rate']:>7.4f} {row['sent_to_saturated_rate']:>7.4f} {row['delta_quality']:>7.3f} "
              f"{row['top1_changed_rate']:>6.3f}")
    print(f"\nRetenu ({len(shortlist)} configurations dans la tolérance) : {json.dumps(chosen, indent=1)}")
    print(f"{time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
