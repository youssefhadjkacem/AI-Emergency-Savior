"""
Mesure avant / après du second round de corrections, en deux chantiers
évalués séparément.

CHANTIER 1 — classement des prestataires (`nsga2.py`)
    avant : tri par densité k-NN à l'intérieur d'un front de Pareto
    après : tri par score de compromis (monotone)
  Les deux pipelines ont la gravité DÉSACTIVÉE : seul le tri change.
  Méthode du rapport précédent : modifier un seul attribut d'un seul
  prestataire et regarder son rang, sur les 504 prestataires des 22
  spécialités (`run_optimization_checks`), plus deux tests ajoutés ici :
    - balayage de prix : le prix d'un prestataire est multiplié par 1,2 à
      10 ; son rang ne doit jamais s'améliorer d'un palier au suivant ;
    - éloignement : un prestataire de la ville du patient est déplacé
      ailleurs ; son rang ne doit pas s'améliorer.

CHANTIER 2 — gravité (`severity.py`)
    avant : booléen `urgent` -> score de la cardiologie x2
    après : niveau LOW / MEDIUM / HIGH / CRITICAL estimé depuis le texte
  Les deux pipelines ont le classement monotone ACTIVÉ : seule la gravité
  change.
    - qualité de l'estimation : 24 cas annotés (`severity_cases.py`), et les
      29 cas d'origine dont la gravité avait été annotée lors du premier
      test, en français et en anglais ;
    - effet sur le reste : Top-1 / Top-3 sur les trois jeux habituels ;
    - effet sur le classement des prestataires, par niveau de gravité.

Tout tourne hors ligne (aucun service de traduction, aucun appel au Space).
Rien n'est déployé par ce script.

Lancer avec :
    cd backend
    python -m testing.run_round2_evaluation
"""

from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from . import pipeline_evaluation as ev
from .heldout_cases_fr import HELDOUT_CASES
from .pipeline_cases import CASES
from .pipeline_runner import ROUND1_CONFIG, LocalPipeline
from .run_fix_evaluation import evaluate
from .run_pipeline_evaluation import OPT_CITY, run_optimization_checks
from .severity_cases import SEVERITY_CASES

OFFLINE = dict(translation_chain=())
PRICE_FACTORS = [1.0, 1.2, 1.5, 2.0, 3.0, 5.0, 10.0]
DATASETS = {"en": (CASES, "en"), "fr": (CASES, "fr"), "heldout": (HELDOUT_CASES, "fr")}

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = RESULTS_DIR / "round2_evaluation_report.json"


# ═════════════════════════════════════════════════════════════════════════
# Chantier 1
# ═════════════════════════════════════════════════════════════════════════


def price_sweep(pipeline: LocalPipeline) -> dict:
    """Pour chaque prestataire, rang obtenu quand son prix est multiplié par
    chaque facteur de `PRICE_FACTORS`. Une « inversion » est un passage à un
    prix plus élevé qui AMÉLIORE le rang."""
    pf = pipeline.provider_filter
    providers = inversions = providers_with_inversion = better_at_10x = 0
    examples = []
    for specialty in pipeline.specialties:
        candidates = pf.filter_by_specialty_name(specialty).set_index("ID")
        for pid in candidates.index:
            base = float(candidates.loc[pid, "average_cost"])
            ranks = []
            for factor in PRICE_FACTORS:
                modified = ev.with_modified_provider(pf, pid, average_cost=base * factor)
                ranks.append(ev.full_ranking(modified, specialty).index(pid) + 1)
            steps = sum(1 for a, b in zip(ranks, ranks[1:]) if b < a)
            providers += 1
            inversions += steps
            providers_with_inversion += steps > 0
            better_at_10x += ranks[-1] < ranks[0]
            if steps and len(examples) < 5:
                examples.append({"provider_id": pid, "specialty": specialty, "base_cost": base,
                                 "rank_by_factor": dict(zip(map(str, PRICE_FACTORS), ranks))})
    return {
        "factors": PRICE_FACTORS,
        "providers": providers,
        "providers_with_a_rank_improvement_when_price_rises": providers_with_inversion,
        "rank_improvements_when_price_rises": inversions,
        "providers_better_ranked_at_10x_than_at_real_price": better_at_10x,
        "examples": examples,
    }


def moved_away(pipeline: LocalPipeline) -> dict:
    """Chaque prestataire de la ville du patient est déplacé dans une autre
    ville : son rang ne doit pas s'améliorer."""
    pf = pipeline.provider_filter
    shifts = []
    for specialty in pipeline.specialties:
        candidates = pf.filter_by_specialty_name(specialty)
        for pid in candidates.loc[candidates["location"] == OPT_CITY, "ID"]:
            shifts.append(ev.rank_shift(pf, specialty, pid, {"location": "Ailleurs"}, location=OPT_CITY))
    return ev.summarize_rank_shifts(shifts)


def chantier1(before: LocalPipeline, after: LocalPipeline) -> dict:
    out = {}
    for label, pipeline in (("before", before), ("after", after)):
        checks = run_optimization_checks(pipeline)
        out[label] = {
            "ranking_mode": pipeline.provider_filter.ranking_mode,
            "summary": checks["summary"],
            "single_provider_perturbations": checks["single_provider_perturbations"],
            "moved_away_from_patient_city": moved_away(pipeline),
            "price_sweep": price_sweep(pipeline),
        }
    return out


# ═════════════════════════════════════════════════════════════════════════
# Chantier 2
# ═════════════════════════════════════════════════════════════════════════


def severity_quality(pipeline: LocalPipeline) -> dict:
    """Niveau estimé contre niveau annoté. `urgent=False` partout : on
    mesure ce que le texte seul permet d'estimer."""
    def run(text, age):
        symptoms = pipeline.extractor.extract(text)
        return pipeline.estimate_severity(text, symptoms, age, urgent=False), sorted(symptoms)

    sets = {
        "severity_cases": [(c.case_id, c.text, c.age, c.expected_level) for c in SEVERITY_CASES],
        "severity_cases_fr": [(c.case_id, c.text, c.age, c.expected_level) for c in SEVERITY_CASES if c.language == "fr"],
        "severity_cases_en": [(c.case_id, c.text, c.age, c.expected_level) for c in SEVERITY_CASES if c.language == "en"],
        "original_29_fr": [(c.case_id, c.text_fr, c.age, c.severity.value) for c in CASES],
        "original_29_en": [(c.case_id, c.text_en, c.age, c.severity.value) for c in CASES],
    }
    out = {}
    for name, items in sets.items():
        per_case, pairs = {}, []
        for case_id, text, age, expected in items:
            severity, symptoms = run(text, age)
            pairs.append((expected, severity["level"]))
            per_case[case_id] = {"expected": expected, "estimated": severity["level"], "score": severity["score"],
                                 "reasons": severity["reasons"], "symptoms": symptoms, "text": text}
        out[name] = {**ev.severity_metrics(pairs), "per_case": per_case}
    return out


def pipeline_effect(before: LocalPipeline, after: LocalPipeline) -> dict:
    branches = after.branch_by_specialty()
    out = {}
    for dataset, (cases, language) in DATASETS.items():
        b = evaluate(before, cases, language, branches)
        a = evaluate(after, cases, language, branches)
        keep = ("top1_strict_accuracy", "top1_lenient_accuracy", "top3_coverage", "provider_top1_coverage",
                "provider_top3_coverage", "no_prediction", "staged_equals_real_predict")
        changed = {
            cid: {"expected": a["per_case"][cid]["expected_specialty"],
                  "before": b["per_case"][cid]["predicted"], "after": a["per_case"][cid]["predicted"],
                  "severity": a["per_case"][cid]["severity"]["level"]}
            for cid in a["per_case"] if a["per_case"][cid]["predicted"] != b["per_case"][cid]["predicted"]
        }
        out[dataset] = {
            "before": {k: b["summary"][k] for k in keep},
            "after": {k: a["summary"][k] for k in keep},
            "cases_where_top1_changes": changed,
            "estimated_levels": {cid: e["severity"]["level"] for cid, e in a["per_case"].items()},
        }
    return out


def ranking_effect(pipeline: LocalPipeline) -> dict:
    """Top 3 de chaque spécialité pour un patient de `OPT_CITY`, selon le
    niveau de gravité transmis au classement."""
    pf = pipeline.provider_filter
    out = {}
    for level in ("MEDIUM", "HIGH", "CRITICAL"):
        waits, in_city, quality, cost = [], [], [], []
        for specialty in pipeline.specialties:
            top = pf.optimize_providers_nsga(specialty, top_k=3, location=OPT_CITY, severity_level=level)
            waits.append(float(top["waiting_time_days"].mean()))
            in_city.append(int((top["location"] == OPT_CITY).sum()))
            quality.append(float(top["quality_score"].mean()))
            cost.append(float(top["average_cost"].mean()))
        out[level] = {"mean_wait_days_top3": statistics.mean(waits),
                      "mean_providers_in_city_top3": statistics.mean(in_city),
                      "mean_quality_top3": statistics.mean(quality),
                      "mean_cost_top3": statistics.mean(cost)}
    return out


# ═════════════════════════════════════════════════════════════════════════


def _pct(v) -> str:
    return f"{100 * v:.1f}%" if v is not None else "n/a"


def main() -> None:
    logging.disable(logging.CRITICAL)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    round1 = LocalPipeline(**OFFLINE, **ROUND1_CONFIG)                      # avant les deux chantiers
    ranking_only = LocalPipeline(**OFFLINE, use_severity=False)             # + chantier 1
    full = LocalPipeline(**OFFLINE)                                         # + chantier 2 (réglages par défaut)

    c1 = chantier1(round1, ranking_only)
    c2 = {
        "severity_quality": severity_quality(full),
        "pipeline_effect": pipeline_effect(ranking_only, full),
        "ranking_effect_by_level": ranking_effect(full),
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": ("Mesures locales, hors ligne. Rien n'est deploye. Chantier 1 : seul le tri des prestataires "
                 "change (gravite desactivee des deux cotes). Chantier 2 : seule la gravite change (tri "
                 "monotone actif des deux cotes)."),
        "chantier1_ranking": c1,
        "chantier2_severity": c2,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    line = "=" * 104
    print(line + "\n  CHANTIER 1 — rang d'un prestataire après modification d'un seul attribut\n" + line)
    print(f"{'modification':<28}{'essais':>8}{'avant : rang amélioré':>26}{'après : rang amélioré':>26}"
          f"{'Top 3 après (avant -> après)':>32}")
    rows = dict(c1["before"]["single_provider_perturbations"], moved_away=c1["before"]["moved_away_from_patient_city"])
    rows_after = dict(c1["after"]["single_provider_perturbations"], moved_away=c1["after"]["moved_away_from_patient_city"])
    for name, b in rows.items():
        a = rows_after[name]
        print(f"{name:<28}{b['n_trials']:>8}{b['improved']:>14} ({_pct(b['improved_rate']):>6})"
              f"{a['improved']:>17} ({_pct(a['improved_rate']):>6}){b['in_top_k_after']:>18} -> {a['in_top_k_after']}")
    for label in ("before", "after"):
        s, sw = c1[label]["summary"], c1[label]["price_sweep"]
        print(f"\n[{label}] balayage de prix : {sw['providers_with_a_rank_improvement_when_price_rises']}/{sw['providers']} "
              f"prestataires gagnent un rang quand leur prix monte ; {sw['providers_better_ranked_at_10x_than_at_real_price']} "
              f"mieux classés à x10 qu'à leur prix réel")
        print(f"[{label}] n°1 = mieux noté : {s['default_top1_is_highest_quality']}/22 — places du Top 3 tenues par le pire "
              f"sur un critère : {s['default_top3_slots_worst_on_a_criterion']}/66 — médecins de la ville dans le Top 3 : "
              f"{s['mean_top3_in_city_without_location']:.2f} -> {s['mean_top3_in_city_with_location']:.2f} — écart au budget "
              f"du Top 3 : {s['mean_gap_to_budget_top3_without_budget']:.1f} -> {s['mean_gap_to_budget_top3_with_budget']:.1f} TND")

    print("\n" + line + "\n  CHANTIER 2 — estimation de la gravité\n" + line)
    print(f"{'jeu':<22}{'cas':>5}{'exact':>9}{'à 1 niveau':>12}{'sous-estimés':>14}{'surestimés':>12}{'inconnus':>10}")
    for name, m in c2["severity_quality"].items():
        print(f"{name:<22}{m['n_cases']:>5}{_pct(m['accuracy']):>9}{_pct(m['within_one_level']):>12}"
              f"{m['under_triage']:>14}{m['over_triage']:>12}{m['unknown']:>10}")
    matrix = c2["severity_quality"]["severity_cases"]["confusion_matrix"]
    print("\nMatrice de confusion, 24 cas de gravité (ligne = attendu, colonne = estimé) :")
    cols = list(next(iter(matrix.values())))
    print(f"{'':<10}" + "".join(f"{c:>10}" for c in cols))
    for level, row in matrix.items():
        print(f"{level:<10}" + "".join(f"{row[c]:>10}" for c in cols))
    print("\nErreurs sur les 24 cas :")
    for cid, e in c2["severity_quality"]["severity_cases"]["per_case"].items():
        if e["expected"] != e["estimated"]:
            print(f"  {cid} attendu {e['expected']:<8} estimé {e['estimated']:<8} symptômes {e['symptoms']} | {e['reasons']}")

    print("\nEffet sur le pipeline (avant = urgent x2 cardiologie ; après = gravité) :")
    print(f"{'jeu':<10}{'Top-1':>16}{'Top-3':>18}{'parmi 3 prest.':>20}   cas dont le Top-1 change")
    for dataset, r in c2["pipeline_effect"].items():
        b, a = r["before"], r["after"]
        print(f"{dataset:<10}{_pct(b['top1_strict_accuracy']):>7} -> {_pct(a['top1_strict_accuracy']):<6}"
              f"{_pct(b['top3_coverage']):>8} -> {_pct(a['top3_coverage']):<6}"
              f"{_pct(b['provider_top3_coverage']):>9} -> {_pct(a['provider_top3_coverage']):<7}   {r['cases_where_top1_changes'] or 'aucun'}")
    print("\nEffet sur le classement, patient à Tunis (moyenne des 22 spécialités, Top 3) :")
    for level, r in c2["ranking_effect_by_level"].items():
        print(f"  {level:<9} délai {r['mean_wait_days_top3']:.1f} j — médecins de la ville {r['mean_providers_in_city_top3']:.2f}/3 "
              f"— note {r['mean_quality_top3']:.2f} — coût {r['mean_cost_top3']:.0f} TND")
    print(f"\nRapport sauvegardé dans : {REPORT_PATH}")


if __name__ == "__main__":
    main()
