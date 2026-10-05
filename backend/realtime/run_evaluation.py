"""
Script d'évaluation quantitative du pipeline de filtrage de bruit —
clôture l'étape "filtrage" (section 3.8) avant la détection de drift.

Génère un flux étiqueté (vérité terrain) avec le simulateur, le fait passer
par le pipeline complet, calcule précision/rappel/F1 par catégorie de bruit
+ la matrice de confusion, répète sur plusieurs seeds pour donner une
indication de variance, et sauvegarde le tout dans
`backend/realtime/results/evaluation_report.json` (fichier versionné, pour
pouvoir citer ces chiffres de façon traçable dans le papier).

Lancer avec :
    cd backend
    python -m realtime.run_evaluation
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from .evaluation import run_pipeline_and_evaluate
from .noise_filter import EventStatus, NoiseFilterPipeline
from .simulator import GroundTruthLabel, SimulatorConfig, generate_labeled_stream

# Mêmes paramètres que `demo_run.py` pour le seed principal, afin que les
# deux scripts restent directement comparables.
PRIMARY_SEED = 7
ADDITIONAL_SEEDS = [1, 42]
N_EVENTS = 1200
N_PROVIDERS = 6

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = RESULTS_DIR / "evaluation_report.json"


def _run_for_seed(seed: int) -> dict:
    config = SimulatorConfig(n_events=N_EVENTS, n_providers=N_PROVIDERS, seed=seed)
    pipeline = NoiseFilterPipeline()
    report = run_pipeline_and_evaluate(pipeline, generate_labeled_stream(config))
    report["seed"] = seed
    report["n_events"] = N_EVENTS
    report["n_providers"] = N_PROVIDERS
    return report


def _print_prf_table(report: dict) -> None:
    header = f"{'Catégorie':<24}{'Support':>9}{'Précision':>12}{'Rappel':>10}{'F1':>10}"
    print(header)
    print("-" * len(header))
    for label in GroundTruthLabel:
        m = report["categories"][label.value]
        precision = f"{m['precision']:.3f}" if m["precision"] is not None else "n/a"
        recall = f"{m['recall']:.3f}" if m["recall"] is not None else "n/a"
        f1 = f"{m['f1']:.3f}" if m["f1"] is not None else "n/a"
        print(f"{label.value:<24}{m['support']:>9}{precision:>12}{recall:>10}{f1:>10}")
        if label == GroundTruthLabel.injected_stale:
            rate = m.get("suppression_rate")
            rate_str = f"{rate:.3f}" if rate is not None else "n/a"
            print(f"    -> suppression_rate (métrique auxiliaire, pas P/R classique) : {rate_str}"
                  f"  ({m.get('suppressed_count')}/{m.get('evaluable_count')} events périmés neutralisés)")


def _print_confusion_matrix(report: dict) -> None:
    matrix = report["confusion_matrix"]
    statuses = [s.value for s in EventStatus]
    col_width = 15
    print(f"{'':<24}" + "".join(f"{s:>{col_width}}" for s in statuses))
    for label in GroundTruthLabel:
        row = matrix[label.value]
        print(f"{label.value:<24}" + "".join(f"{row[s]:>{col_width}}" for s in statuses))


def _print_report(seed: int, report: dict) -> None:
    title = f"  Seed {seed} — {N_EVENTS} événements, {N_PROVIDERS} providers"
    print("=" * 78)
    print(title)
    print("=" * 78)
    print("Précision / rappel / F1 par catégorie de vérité terrain :")
    _print_prf_table(report)
    print()
    print("Matrice de confusion (label réel x statut décidé par le pipeline) :")
    _print_confusion_matrix(report)
    print()


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    all_seeds = [PRIMARY_SEED] + ADDITIONAL_SEEDS
    reports: Dict[int, dict] = {}
    for seed in all_seeds:
        reports[seed] = _run_for_seed(seed)
        _print_report(seed, reports[seed])

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "evaluation_report_v0.json = etat initial (bug de cle de dedoublonnage "
            "+ HalfSpaceTrees non retune). evaluation_report_v1.json = etat "
            "intermediaire (dedup corrige, anomalie pas encore retunee). "
            "Ce fichier = etat courant (les deux corriges)."
        ),
        "primary_seed": PRIMARY_SEED,
        "seeds_evaluated": all_seeds,
        "n_events": N_EVENTS,
        "n_providers": N_PROVIDERS,
        "reports_by_seed": {str(seed): reports[seed] for seed in all_seeds},
    }

    REPORT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Rapport sauvegardé dans : {REPORT_PATH}")


if __name__ == "__main__":
    main()
