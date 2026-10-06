"""
Évaluation finale de l'adaptation performative (Phase 3) sur les seeds
7/1/42, avec les paramètres réglés sur 100/101/102 (défauts de
`SystemParams`).

    cd backend
    python -m realtime.run_feedback_evaluation

Écrit dans realtime/results/ :
  - feedback_evaluation_report.json : toutes les métriques, par seed et agrégées
  - feedback_tables.md              : les tableaux du rapport, générés
  - feedback_fig1_occupancy.png     : occupation du prestataire le plus chargé, A contre C
  - feedback_fig2_saturated_rate.png: patients envoyés vers un prestataire saturé
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

import numpy as np

from .adaptation import SpaceRankingSource
from .feedback_evaluation import aggregate
from .feedback_runner import Task, run_many
from .feedback_simulator import CONDITIONS, LOAD_LEVELS, FeedbackConfig, SystemParams, build_world, run_condition
from .saturation import ALERT_THRESHOLD, SATURATION_THRESHOLD

EVALUATION_SEEDS = [7, 1, 42]
LOADS = ["low", "medium", "high"]
LOAD_LABELS = {"low": "Faible", "medium": "Moyenne", "high": "Forte"}
CONDITION_ORDER = ["A", "B", "C", "D", "E", "F"]
SENSITIVITY_FOLLOW = [(0.50, 0.30, 0.20), (0.90, 0.07, 0.03)]
FIGURE_SEED, FIGURE_LOAD = 7, "high"

RESULTS_DIR = Path(__file__).parent / "results"


def _fmt(stat: dict, scale: float = 1.0, digits: int = 1, signed: bool = False) -> str:
    if stat is None or stat.get("mean") is None:
        return "—"
    sign = "+" if signed else ""
    return f"{stat['mean'] * scale:{sign}.{digits}f} ± {stat['std'] * scale:.{digits}f}".replace(".", ",")


def _table(header: List[str], rows: List[List[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def write_tables(agg: Dict[str, Dict[str, dict]], sensitivity: Dict[str, dict]) -> str:
    out = ["Tableaux générés par `run_feedback_evaluation.py`. Moyenne ± écart-type entre les seeds 7, 1 et 42.\n"]
    for load in LOADS:
        out.append(f"### Charge {LOAD_LABELS[load].lower()} ({LOAD_LEVELS[load]:.0f} patients/heure)\n")
        rows = [
            ("Patients envoyés vers un prestataire saturé (%)", "sent_to_saturated_rate", 100, 1, False),
            ("Patients refusés faute de place (%)", "refused_rate", 100, 1, False),
            ("Occupation moyenne (%)", "mean_occupancy", 100, 1, False),
            ("Occupation moyenne du prestataire le plus chargé (%)", "max_provider_mean_occupancy", 100, 1, False),
            ("Temps-prestataire à 90 % ou plus (%)", "provider_time_saturated", 100, 2, False),
            ("Gini des patients reçus, par spécialité", "gini_patients", 1, 3, False),
            ("Écart-type de l'occupation, par spécialité (points)", "std_occupancy_within_specialty", 100, 1, False),
            ("Part des patients chez les 3 plus sollicités (%)", "top3_providers_share", 100, 1, False),
            ("Top 1 modifié (%)", "top1_changed_rate", 100, 1, False),
            ("Top 3 modifié (%)", "top3_changed_rate", 100, 1, False),
            ("Variation de la note du n°1 (sur 10)", "delta_quality", 1, 2, True),
            ("Variation du coût du n°1 (TND)", "delta_cost", 1, 1, True),
            ("Variation du délai du n°1 (jours)", "delta_wait_days", 1, 2, True),
            ("Variation du n°1 dans la ville du patient (points)", "delta_same_city", 100, 1, True),
            ("Épisodes de saturation réelle", "saturation_episodes", 1, 0, False),
            ("Épisodes détectés (état saturé) (%)", "episodes_detected_rate", 100, 1, False),
            ("Épisodes anticipés (délai ≤ 0) (%)", "episodes_anticipated_rate", 100, 1, False),
            ("Délai de détection médian (min)", "detection_delay_median_min", 1, 1, True),
            ("Délai de détection moyen (min)", "detection_delay_mean_min", 1, 1, True),
            ("Déclarations de saturation", "saturation_declarations", 1, 0, False),
            ("Déclarations à tort (%)", "false_declaration_rate", 100, 1, False),
            ("… dont sans patient en route (%)", "false_declaration_rate_excluding_en_route", 100, 1, False),
            ("Alertes Page-Hinkley", "drift_alerts", 1, 0, False),
            ("Alertes suivies d'une saturation réelle (%)", "drift_alerts_followed_by_saturation_rate", 100, 1, False),
        ]
        header = ["Métrique"] + [f"{c} — {CONDITIONS[c].label}" for c in CONDITION_ORDER]
        body = [[label] + [_fmt(agg[load][c].get(key), scale, digits, signed) for c in CONDITION_ORDER]
                for label, key, scale, digits, signed in rows]
        out.append(_table(header, body) + "\n")

    out.append("### Contrôles de sécurité (somme sur les 3 seeds et les 3 charges)\n")
    rows = []
    for c in CONDITION_ORDER:
        def total(key):
            return sum(sum(agg[load][c][key]["values"]) for load in LOADS)
        patients = total("patients")
        preserved = min(min(agg[load][c]["specialty_preserved_rate"]["values"]) for load in LOADS)
        rows.append([f"{c} — {CONDITIONS[c].label}", f"{patients:.0f}", f"{preserved * 100:.0f} %",
                     f"{total('critical_patients'):.0f}", f"{total('critical_moved_out_of_city'):.0f}",
                     f"{total('all_saturated_responses'):.0f}", f"{total('responses_with_saturated_provider'):.0f}"])
    out.append(_table(["Condition", "Patients", "Spécialité conservée", "Patients CRITICAL",
                       "CRITICAL détournés hors de leur ville", "Réponses « tous saturés »",
                       "Top 3 gardant un prestataire saturé"], rows) + "\n")

    out.append("### Sensibilité au suivi des recommandations (charge forte)\n")
    rows = []
    for label, entry in sensitivity.items():
        for c in ("A", "C"):
            rows.append([label, f"{c} — {CONDITIONS[c].label}",
                         _fmt(entry[c]["sent_to_saturated_rate"], 100), _fmt(entry[c]["refused_rate"], 100),
                         _fmt(entry[c]["top3_providers_share"], 100), _fmt(entry[c]["delta_quality"], 1, 2, True)])
    out.append(_table(["Probabilités n°1 / n°2 / n°3", "Condition", "Envoyés vers un saturé (%)",
                       "Refusés (%)", "Part chez les 3 plus sollicités (%)", "Variation de la note du n°1"], rows) + "\n")
    return "\n".join(out)


# ═════════════════════════════════════════════════════════════════════════
# Figures
# ═════════════════════════════════════════════════════════════════════════

INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#ffffff"
BLUE, ORANGE, NEUTRAL = "#2a78d6", "#eb6834", "#9a9992"
# Deux premières teintes de la palette catégorielle de référence (validées
# en paire) ; gris neutre pour la référence statique et les seuils.


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def figure_occupancy(source: SpaceRankingSource, path: Path) -> dict:
    """Figure 1 : occupation réelle, dans le temps, du prestataire le plus
    chargé en condition statique ; le même prestataire en condition C."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    world = build_world(FeedbackConfig(seed=FIGURE_SEED, arrivals_per_hour=LOAD_LEVELS[FIGURE_LOAD]), source)
    logs = {c: run_condition(world, source, CONDITIONS[c]) for c in ("A", "C")}
    capacity = np.asarray(world.capacity, dtype=float)
    occupancy = {c: np.asarray(log.occupied) / capacity for c, log in logs.items()}
    j = int(occupancy["A"].mean(axis=0).argmax())
    hours = 8.0 + (np.asarray(logs["A"].sample_times) - world.config.warmup_seconds) / 3600.0

    fig, ax = plt.subplots(figsize=(8.0, 3.8), dpi=200)
    _style(ax)
    ax.step(hours, occupancy["A"][:, j] * 100, where="post", color=NEUTRAL, linewidth=1.6, label="A — Statique")
    ax.step(hours, occupancy["C"][:, j] * 100, where="post", color=BLUE, linewidth=2.0, label="C — Système complet")
    for level, name in ((SATURATION_THRESHOLD, "saturation 90 %"), (ALERT_THRESHOLD, "alerte 80 %")):
        ax.axhline(level * 100, color=MUTED, linewidth=0.9, linestyle=(0, (4, 3)))
        ax.text(hours[-1] + 0.08, level * 100, name, va="center", ha="left", fontsize=8.5, color=MUTED)
    ax.set_ylim(0, 105)
    ax.set_xlim(hours[0], hours[-1])
    ax.set_xticks(range(8, 19, 2))
    ax.set_xticklabels([f"{h} h" for h in range(8, 19, 2)])
    ax.set_ylabel("Occupation réelle (%)", fontsize=9.5, color=MUTED)
    specialty, cap = world.specialty[j], world.capacity[j]
    ax.set_title(f"Occupation du prestataire le plus chargé ({specialty}, {cap} places) — charge forte, seed {FIGURE_SEED}",
                 fontsize=10.5, color=INK, loc="left", pad=10)
    ax.legend(loc="lower right", frameon=False, fontsize=9, labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return {
        "provider_id": world.provider_ids[j], "specialty": specialty, "capacity": cap,
        "mean_occupancy": {c: float(occupancy[c][:, j].mean()) for c in occupancy},
        "time_at_or_above_90": {c: float((occupancy[c][:, j] >= SATURATION_THRESHOLD).mean()) for c in occupancy},
        "patients_sent": {c: sum(r.chosen.provider_id == world.provider_ids[j] for r in logs[c].records) for c in logs},
    }


def figure_saturated_rate(agg: Dict[str, Dict[str, dict]], path: Path) -> None:
    """Figure 2 : taux de patients envoyés vers un prestataire saturé, par
    condition, un panneau par niveau de charge (même échelle)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(10.0, 3.6), dpi=200, sharey=True)
    top = max(agg[load][c]["sent_to_saturated_rate"]["mean"] + agg[load][c]["sent_to_saturated_rate"]["std"]
              for load in LOADS for c in CONDITION_ORDER) * 100
    for ax, load in zip(axes, LOADS):
        _style(ax)
        means = [agg[load][c]["sent_to_saturated_rate"]["mean"] * 100 for c in CONDITION_ORDER]
        stds = [agg[load][c]["sent_to_saturated_rate"]["std"] * 100 for c in CONDITION_ORDER]
        colors = [NEUTRAL if c == "A" else BLUE for c in CONDITION_ORDER]
        ax.bar(CONDITION_ORDER, means, width=0.62, color=colors, yerr=stds,
               error_kw={"ecolor": INK, "elinewidth": 0.9, "capsize": 2.5})
        for x, (mean, std) in enumerate(zip(means, stds)):
            ax.text(x, mean + std + top * 0.025, f"{mean:.1f}".replace(".", ","), ha="center", va="bottom",
                    fontsize=8.5, color=INK)
        ax.set_ylim(0, top * 1.15)
        ax.set_title(f"Charge {LOAD_LABELS[load].lower()} ({LOAD_LEVELS[load]:.0f} patients/h)", fontsize=10,
                     color=INK, loc="left")
        ax.tick_params(axis="x", labelsize=10, colors=INK)
    axes[0].set_ylabel("Patients envoyés vers un\nprestataire saturé (%)", fontsize=9.5, color=MUTED)
    fig.text(0.5, 0.005, "A statique · B seuil seul · C système complet · D complet sur flux brut · "
                         "E sans réservations · F sans Page-Hinkley     (barres d'erreur : écart-type entre seeds)",
             ha="center", fontsize=8.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    started = time.time()
    main_tasks = [Task(seed, load, c) for seed in EVALUATION_SEEDS for load in LOADS for c in CONDITION_ORDER]
    sens_tasks = [Task(seed, "high", c, follow_probabilities=follow)
                  for follow in SENSITIVITY_FOLLOW for seed in EVALUATION_SEEDS for c in ("A", "C")]
    results = run_many(main_tasks + sens_tasks)
    main_results, sens_results = results[:len(main_tasks)], results[len(main_tasks):]

    agg = {load: {c: aggregate([r["metrics"] for r in main_results if r["load"] == load and r["condition"] == c])
                  for c in CONDITION_ORDER} for load in LOADS}

    sensitivity = {}
    default_follow = (0.70, 0.20, 0.10)
    for follow in [SENSITIVITY_FOLLOW[0], default_follow, SENSITIVITY_FOLLOW[1]]:
        label = " / ".join(f"{p:.2f}".replace(".", ",") for p in follow)
        if follow == default_follow:
            sensitivity[label + " (défaut)"] = {c: agg["high"][c] for c in ("A", "C")}
        else:
            sensitivity[label] = {c: aggregate([r["metrics"] for r in sens_results
                                                if tuple(r["follow_probabilities"]) == follow and r["condition"] == c])
                                  for c in ("A", "C")}

    RESULTS_DIR.mkdir(exist_ok=True)
    source = SpaceRankingSource()
    figure_1 = figure_occupancy(source, RESULTS_DIR / "feedback_fig1_occupancy.png")
    figure_saturated_rate(agg, RESULTS_DIR / "feedback_fig2_saturated_rate.png")

    report = {
        "seeds": EVALUATION_SEEDS,
        "load_levels_patients_per_hour": LOAD_LEVELS,
        "conditions": {c: asdict(CONDITIONS[c]) for c in CONDITION_ORDER},
        "system_params": asdict(SystemParams()),
        "simulation": asdict(FeedbackConfig()),
        "aggregated": agg,
        "sensitivity_follow_probabilities_high_load": sensitivity,
        "figure_1": figure_1,
        "runs": main_results + sens_results,
        "seconds": time.time() - started,
    }
    (RESULTS_DIR / "feedback_evaluation_report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    tables = write_tables(agg, sensitivity)
    (RESULTS_DIR / "feedback_tables.md").write_text(tables, encoding="utf-8")
    print(tables)
    print("\nFigure 1 :", json.dumps(figure_1, ensure_ascii=False))
    print(f"{time.time() - started:.0f} s")


if __name__ == "__main__":
    main()
