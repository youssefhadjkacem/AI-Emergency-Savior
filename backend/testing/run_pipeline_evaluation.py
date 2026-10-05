"""
Évaluation de bout en bout du pipeline principal : entrée patient ->
extraction NLP -> classification de spécialité -> optimisation
multi-objectifs -> Top 3 prestataires.

Fait passer les 29 cas de `pipeline_cases.py` par le vrai code du Space
`emergency-savior-output` et sauvegarde tout (sorties intermédiaires,
durées, métriques) dans `results/pipeline_evaluation_report.json`.

Conditions exécutées :

  en_local    : texte anglais, exécution locale étape par étape, répétée
                `TIMING_REPETITIONS` fois pour la latence. C'est la condition
                principale pour les métriques de classification.
  en_deployed : mêmes entrées envoyées au Space déployé (conditions réelles) ;
                sert à vérifier que le code local est bien celui qui tourne
                en production, et à mesurer la latence réseau comprise.
  oracle      : classification rejouée avec les symptômes ATTENDUS de chaque
                cas (extraction parfaite), pour séparer les erreurs de
                l'extraction de celles du classifieur.
  urgent      : classification rejouée avec urgent=False puis urgent=True,
                pour mesurer l'effet du drapeau d'urgence.
  fr          : texte français d'origine, en local et sur le Space déployé.
  optimization: tests de cohérence du classement des prestataires, sur les
                22 spécialités (voir `pipeline_evaluation.py`).

Dépendances réseau : le Space déployé et Google Translate (appelé par
l'extracteur). Si l'une ne répond pas, c'est enregistré dans le rapport
(`network`) et les autres conditions s'exécutent quand même.

Lancer avec :
    cd backend
    python -m testing.run_pipeline_evaluation
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import httpx

from . import pipeline_evaluation as ev
from .pipeline_cases import CASES, PipelineCase
from .pipeline_runner import (DEPLOYED_SPACE_URL, REMOTE_TIMEOUT, LocalPipeline, StageTrace,
                              call_deployed_space, same_as_deployed)

TIMING_REPETITIONS = 3
# Chaque cas est exécuté 3 fois en local : 87 mesures par étape, assez pour
# un P95 qui ne repose pas sur une seule valeur. Le résultat lui-même est
# déterministe (vérifié : `deterministic` dans le rapport).

# ── Scénario des tests de cohérence de l'optimisation ───────────────────────
OPT_CITY = "Tunis"
# Ville du patient : la plus représentée dans la base K2 (87 médecins),
# donc présente dans toutes les spécialités.
OPT_BUDGET = 70.0
# Budget en TND, sous le coût médian de la base (90 TND) : ramener un
# médecin à ce coût est une vraie amélioration pour la plupart d'entre eux.
DEGRADED_COST_FACTOR = 10.0
DEGRADED_WAIT_DAYS = 365
DEGRADED_QUALITY = 0.0
IMPROVED_QUALITY = 10.5
# Valeurs hors de la plage de la base (coût 50-150 TND, délai 16-48 jours,
# qualité 4,2-10) : le prestataire modifié devient sans ambiguïté le pire
# (ou le meilleur) de sa spécialité sur ce critère.

STAGES = ["nlp_translation_call", "nlp_extraction", "classification", "optimization", "recommendation",
          "end_to_end", "end_to_end_without_translation_call"]

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = RESULTS_DIR / "pipeline_evaluation_report.json"


def _trace_dict(trace: StageTrace) -> dict:
    d = asdict(trace)
    d["predicted_specialty"] = trace.predicted_specialty
    d["score_margin"] = trace.score_margin
    return d


def _same_result(a: StageTrace, b: StageTrace) -> bool:
    return (a.detected_symptoms, a.top_specialties, a.top_providers) == \
           (b.detected_symptoms, b.top_specialties, b.top_providers)


def _outcome(case: PipelineCase, top_specialties: List[tuple]) -> dict:
    return ev.classification_outcome(case.expected_specialty, case.acceptable_alternative,
                                     [s for s, _ in top_specialties])


def _classification_block(outcomes: Dict[str, dict], branches: Dict[str, str]) -> dict:
    cases = [c for c in CASES if c.case_id in outcomes]
    block = ev.classification_summary([outcomes[c.case_id] for c in cases])
    block["by_branch"] = ev.branch_metrics(
        [(c.expected_specialty, outcomes[c.case_id]["predicted"]) for c in cases], branches
    )
    standard = [outcomes[c.case_id] for c in cases if not c.ambiguous]
    ambiguous = [outcomes[c.case_id] for c in cases if c.ambiguous]
    block["non_ambiguous_cases"] = ev.classification_summary(standard)
    block["ambiguous_cases"] = ev.classification_summary(ambiguous)
    return block


# ═════════════════════════════════════════════════════════════════════════
# Conditions
# ═════════════════════════════════════════════════════════════════════════


def run_local_condition(pipeline: LocalPipeline, language: str, repetitions: int) -> dict:
    per_case, timings = {}, {stage: [] for stage in STAGES}
    for case in CASES:
        text = case.text_en if language == "en" else case.text_fr
        traces = [pipeline.run(text, case.age, case.urgent, case.budget, case.location)
                  for _ in range(repetitions)]
        for trace in traces:
            for stage in STAGES:
                timings[stage].append(trace.timings[stage])
        first = traces[0]
        entry = _trace_dict(first)
        entry["deterministic"] = all(_same_result(first, t) for t in traces[1:])
        entry["matches_real_predict"] = pipeline.matches_predict(first, case.age, case.budget, case.location)
        entry["symptom_extraction"] = ev.symptom_extraction_scores(first.detected_symptoms, case.expected_symptoms)
        entry["classification"] = _outcome(case, first.top_specialties)
        entry["provider_coverage"] = ev.provider_coverage(case.expected_specialty, first.top_providers)
        per_case[case.case_id] = entry
    return {"per_case": per_case, "timings": timings}


def run_deployed_condition(language: str, local_per_case: dict) -> dict:
    per_case = {}
    with httpx.Client(timeout=REMOTE_TIMEOUT) as client:
        for case in CASES:
            text = case.text_en if language == "en" else case.text_fr
            remote = call_deployed_space(text, case.age, case.urgent, case.budget, case.location, client)
            if remote["ok"]:
                remote["classification"] = _outcome(case, remote["top_specialties"])
                local = local_per_case[case.case_id]
                local_trace = StageTrace(
                    text=text, urgent=case.urgent, translation=None,
                    detected_symptoms=local["detected_symptoms"],
                    top_specialties=[tuple(x) for x in local["top_specialties"]],
                    top_providers=local["top_providers"],
                )
                remote["same_as_local"] = same_as_deployed(local_trace, remote)
            per_case[case.case_id] = remote
    return {"per_case": per_case}


def run_oracle_and_urgent(pipeline: LocalPipeline, en_per_case: dict) -> dict:
    oracle, urgent_effect = {}, {}
    for case in CASES:
        top = pipeline.classify(case.expected_symptoms, case.age, case.urgent)
        entry = _outcome(case, top)
        entry["top_specialties"] = [(s, float(v)) for s, v in top]
        oracle[case.case_id] = entry

        detected = en_per_case[case.case_id]["detected_symptoms"]
        effect = {}
        for label, symptoms in (("detected_symptoms", detected), ("expected_symptoms", case.expected_symptoms)):
            off = pipeline.classify(symptoms, case.age, False)
            on = pipeline.classify(symptoms, case.age, True)
            effect[label] = {
                "top1_not_urgent": off[0][0] if off else None,
                "top1_urgent": on[0][0] if on else None,
                "top1_changes": (off[0][0] if off else None) != (on[0][0] if on else None),
            }
        urgent_effect[case.case_id] = effect
    return {"oracle": oracle, "urgent_effect": urgent_effect}


def run_optimization_checks(pipeline: LocalPipeline) -> dict:
    pf = pipeline.provider_filter
    per_specialty = {}
    shifts: Dict[str, List[dict]] = {k: [] for k in (
        "moved_to_patient_city", "cost_set_to_budget", "cost_degraded", "quality_degraded",
        "wait_degraded", "quality_improved")}

    for specialty in pipeline.specialties:
        candidates = pf.filter_by_specialty_name(specialty)
        max_cost = float(candidates["average_cost"].max())

        default_ids = ev.full_ranking(pf, specialty)
        city_ids = ev.full_ranking(pf, specialty, location=OPT_CITY)
        budget_ids = ev.full_ranking(pf, specialty, budget=OPT_BUDGET)
        by_id = candidates.set_index("ID")
        in_city = lambda ids: sum(1 for i in ids if by_id.loc[i, "location"] == OPT_CITY)  # noqa: E731
        gap = lambda ids: statistics.mean(abs(float(by_id.loc[i, "average_cost"]) - OPT_BUDGET) for i in ids)  # noqa: E731

        per_specialty[specialty] = {
            **ev.first_front_structure(pf, specialty),
            "default_top3": default_ids[:3],
            # `filter_by_specialty_name` trie les candidats par qualité
            # décroissante avant le classement : le 1er de cette liste est
            # le médecin le mieux noté de la spécialité.
            "default_top1_is_highest_quality": default_ids[0] == candidates["ID"].iloc[0],
            "default_top3_worst_on": ev.worst_on_some_criterion(pf, specialty, default_ids[:3]),
            "location": {
                "providers_in_city": in_city(default_ids),
                "top3_in_city_without_location": in_city(default_ids[:3]),
                "top3_in_city_with_location": in_city(city_ids[:3]),
                "top3_changed": default_ids[:3] != city_ids[:3],
            },
            "budget": {
                "mean_gap_to_budget_all_providers": gap(default_ids),
                "mean_gap_to_budget_top3_without_budget": gap(default_ids[:3]),
                "mean_gap_to_budget_top3_with_budget": gap(budget_ids[:3]),
                "top3_changed": default_ids[:3] != budget_ids[:3],
            },
        }

        for pid in default_ids:
            row = by_id.loc[pid]
            if row["location"] != OPT_CITY:
                shifts["moved_to_patient_city"].append(
                    ev.rank_shift(pf, specialty, pid, {"location": OPT_CITY}, location=OPT_CITY))
            if float(row["average_cost"]) != OPT_BUDGET:
                shifts["cost_set_to_budget"].append(
                    ev.rank_shift(pf, specialty, pid, {"average_cost": OPT_BUDGET}, budget=OPT_BUDGET))
            shifts["cost_degraded"].append(
                ev.rank_shift(pf, specialty, pid, {"average_cost": DEGRADED_COST_FACTOR * max_cost}))
            shifts["quality_degraded"].append(
                ev.rank_shift(pf, specialty, pid, {"quality_score": DEGRADED_QUALITY}))
            shifts["wait_degraded"].append(
                ev.rank_shift(pf, specialty, pid, {"waiting_time_days": DEGRADED_WAIT_DAYS}))
            shifts["quality_improved"].append(
                ev.rank_shift(pf, specialty, pid, {"quality_score": IMPROVED_QUALITY}))

    n = len(per_specialty)
    worst_flags = [bool(w["worst_on"]) for s in per_specialty.values() for w in s["default_top3_worst_on"]]
    summary = {
        "n_specialties": n,
        "mean_first_front_share": statistics.mean(s["first_front_share"] for s in per_specialty.values()),
        "min_first_front_share": min(s["first_front_share"] for s in per_specialty.values()),
        "mean_first_front_points_with_infinite_density": statistics.mean(
            s["first_front_points_with_infinite_density"] for s in per_specialty.values()),
        "min_first_front_points_with_infinite_density": min(
            s["first_front_points_with_infinite_density"] for s in per_specialty.values()),
        "default_top1_is_highest_quality": sum(
            1 for s in per_specialty.values() if s["default_top1_is_highest_quality"]),
        "mean_providers_in_city": statistics.mean(s["location"]["providers_in_city"] for s in per_specialty.values()),
        "top3_changed_when_location_given": sum(1 for s in per_specialty.values() if s["location"]["top3_changed"]),
        "mean_top3_in_city_without_location": statistics.mean(
            s["location"]["top3_in_city_without_location"] for s in per_specialty.values()),
        "mean_top3_in_city_with_location": statistics.mean(
            s["location"]["top3_in_city_with_location"] for s in per_specialty.values()),
        "top3_changed_when_budget_given": sum(1 for s in per_specialty.values() if s["budget"]["top3_changed"]),
        "mean_gap_to_budget_all_providers": statistics.mean(
            s["budget"]["mean_gap_to_budget_all_providers"] for s in per_specialty.values()),
        "mean_gap_to_budget_top3_without_budget": statistics.mean(
            s["budget"]["mean_gap_to_budget_top3_without_budget"] for s in per_specialty.values()),
        "mean_gap_to_budget_top3_with_budget": statistics.mean(
            s["budget"]["mean_gap_to_budget_top3_with_budget"] for s in per_specialty.values()),
        "default_top3_slots": len(worst_flags),
        "default_top3_slots_worst_on_a_criterion": sum(worst_flags),
        "specialties_with_a_worst_provider_in_default_top3": sum(
            1 for s in per_specialty.values() if any(w["worst_on"] for w in s["default_top3_worst_on"])),
    }
    return {
        "scenario": {"city": OPT_CITY, "budget": OPT_BUDGET, "degraded_cost_factor": DEGRADED_COST_FACTOR,
                     "degraded_wait_days": DEGRADED_WAIT_DAYS, "degraded_quality": DEGRADED_QUALITY,
                     "improved_quality": IMPROVED_QUALITY},
        "summary": summary,
        "single_provider_perturbations": {k: ev.summarize_rank_shifts(v) for k, v in shifts.items()},
        "per_specialty": per_specialty,
    }


# ═════════════════════════════════════════════════════════════════════════
# Affichage
# ═════════════════════════════════════════════════════════════════════════


def _pct(value) -> str:
    return f"{100 * value:.1f}%" if value is not None else "n/a"


def _num(value, spec=".3f") -> str:
    return format(value, spec) if value is not None else "n/a"


def _print_classification(title: str, block: dict) -> None:
    print(f"{title:<46} n={block['n_cases']:<3} Top-1 strict {_pct(block['top1_strict_accuracy']):>6}   "
          f"Top-1 large {_pct(block['top1_lenient_accuracy']):>6}   Top-3 {_pct(block['top3_coverage']):>6}   "
          f"sans prédiction {block['no_prediction']}")


def _print_report(report: dict) -> None:
    line = "=" * 110
    net = report["network"]
    print(line)
    print("  DÉPENDANCES RÉSEAU")
    print(line)
    print(f"Space déployé : {net['deployed_space']['responses_ok']}/{net['deployed_space']['calls']} réponses")
    print(f"Google Translate (appelé par l'extracteur, en local) : "
          f"{net['google_translate_local']['calls_ok']}/{net['google_translate_local']['calls']} appels réussis "
          f"— erreurs : {net['google_translate_local']['errors']}")
    print(f"Local identique au Space déployé (EN) : {net['local_equals_deployed_en']}")
    print()

    print(line)
    print("  CLASSIFICATION DE SPÉCIALITÉ")
    print(line)
    cls = report["classification"]
    _print_classification("EN, pipeline complet (local)", cls["en_local"])
    _print_classification("  dont cas non ambigus", cls["en_local"]["non_ambiguous_cases"])
    _print_classification("  dont cas ambigus", cls["en_local"]["ambiguous_cases"])
    _print_classification("EN, symptômes attendus (extraction parfaite)", cls["oracle"])
    _print_classification("FR, Space déployé", cls["fr_deployed"])
    _print_classification("FR, local", cls["fr_local"])
    print()
    by_branch = cls["en_local"]["by_branch"]
    print(f"Par branche (EN, pipeline complet) — exactitude branche {_pct(by_branch['accuracy'])}, "
          f"F1 macro {_num(by_branch['macro_f1'])}")
    print(f"{'branche':<10}{'support':>8}{'précision':>11}{'rappel':>9}{'F1':>8}")
    for b, m in by_branch["per_branch"].items():
        print(f"{b:<10}{m['support']:>8}{_num(m['precision']):>11}{_num(m['recall']):>9}{_num(m['f1']):>8}")
    print()
    columns = list(next(iter(by_branch["confusion_matrix"].values())).keys())
    print("Matrice de confusion par branche (ligne = attendue, colonne = prédite) :")
    print(f"{'':<6}" + "".join(f"{c[:8]:>9}" for c in columns))
    for b, row in by_branch["confusion_matrix"].items():
        print(f"{b:<6}" + "".join(f"{row[c]:>9}" for c in columns))
    print()

    nlp = report["symptom_extraction"]["en_local"]
    print(line)
    print("  EXTRACTION NLP (EN) — symptômes extraits contre symptômes attendus")
    print(line)
    print(f"précision {_num(nlp['precision'])}   rappel {_num(nlp['recall'])}   F1 {_num(nlp['f1'])}   "
          f"(TP {nlp['true_positives']}, FP {nlp['false_positives']}, FN {nlp['false_negatives']}) — "
          f"{nlp['cases_with_no_symptom_detected']} cas sans aucun symptôme extrait")
    print()

    rec = report["recommendation"]
    print(line)
    print("  RECOMMANDATION FINALE (EN)")
    print(line)
    print(f"Cas avec un Top 3 de prestataires renvoyé : {rec['cases_with_providers']}/{rec['n_cases']}")
    print(f"Spécialité attendue chez le prestataire n°1 : {_pct(rec['provider_top1_coverage'])}")
    print(f"Spécialité attendue parmi les 3 prestataires : {_pct(rec['provider_top3_coverage'])}")
    print(f"Top 3 de prestataires contenant plus d'une spécialité : {rec['cases_with_several_specialties_in_top3']}")
    print()

    print(line)
    print("  LATENCE (ms)")
    print(line)
    print(f"{'étape':<44}{'n':>5}{'moyenne':>11}{'médiane':>11}{'P95':>11}{'max':>11}")
    for stage, s in report["latency"]["local"].items():
        print(f"{stage:<44}{s['n']:>5}{s['mean_ms']:>11.1f}{s['median_ms']:>11.1f}{s['p95_ms']:>11.1f}{s['max_ms']:>11.1f}")
    s = report["latency"]["deployed_space_end_to_end"]
    if s["n"]:
        print(f"{'Space déployé, bout en bout (réseau compris)':<44}{s['n']:>5}{s['mean_ms']:>11.1f}"
              f"{s['median_ms']:>11.1f}{s['p95_ms']:>11.1f}{s['max_ms']:>11.1f}")
    print()

    print(line)
    print("  CAS AMBIGUS (EN)")
    print(line)
    for a in report["ambiguous_cases"]:
        margin = f"{a['score_margin']:.1f} pts" if a["score_margin"] is not None else "n/a"
        print(f"{a['case_id']}  attendu {a['expected']} / acceptable {a['acceptable_alternative']}  ->  "
              f"{a['top_specialties']}  écart 1re-2e : {margin}  [{a['verdict']}]")
    print()

    opt = report["optimization"]
    print(line)
    print("  COHÉRENCE DE L'OPTIMISATION (22 spécialités)")
    print(line)
    s = opt["summary"]
    print(f"Part moyenne des prestataires sur le 1er front de Pareto : {_pct(s['mean_first_front_share'])} "
          f"(minimum {_pct(s['min_first_front_share'])})")
    print(f"Points du 1er front à densité infinie (extrémités) : {s['mean_first_front_points_with_infinite_density']:.1f} "
          f"en moyenne (minimum {s['min_first_front_points_with_infinite_density']}) — le n°1 par défaut est le médecin "
          f"le mieux noté dans {s['default_top1_is_highest_quality']}/{s['n_specialties']} spécialités")
    print(f"Top 3 modifié quand la ville est fournie : {s['top3_changed_when_location_given']}/{s['n_specialties']} — "
          f"prestataires de la ville dans le Top 3 : {s['mean_top3_in_city_without_location']:.2f} -> "
          f"{s['mean_top3_in_city_with_location']:.2f} sur 3")
    print(f"Top 3 modifié quand le budget est fourni : {s['top3_changed_when_budget_given']}/{s['n_specialties']} — "
          f"écart moyen au budget : tous {s['mean_gap_to_budget_all_providers']:.1f}, "
          f"Top 3 sans budget {s['mean_gap_to_budget_top3_without_budget']:.1f}, "
          f"Top 3 avec budget {s['mean_gap_to_budget_top3_with_budget']:.1f} TND")
    print(f"Places du Top 3 par défaut occupées par le pire de sa spécialité sur un critère : "
          f"{s['default_top3_slots_worst_on_a_criterion']}/{s['default_top3_slots']}")
    print()
    print(f"{'modification d un seul prestataire':<28}{'essais':>8}{'rang amélioré':>15}{'inchangé':>10}"
          f"{'rang dégradé':>14}{'rang moyen':>18}{'dans le Top 3':>18}")
    for name, p in opt["single_provider_perturbations"].items():
        print(f"{name:<28}{p['n_trials']:>8}{p['improved']:>15}{p['unchanged']:>10}{p['worsened']:>14}"
              f"{p['mean_rank_before']:>9.1f} -> {p['mean_rank_after']:<5.1f}"
              f"{p['in_top_k_before']:>9} -> {p['in_top_k_after']:<5}")
    print()


# ═════════════════════════════════════════════════════════════════════════
# Programme principal
# ═════════════════════════════════════════════════════════════════════════


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    pipeline = LocalPipeline()
    branches = pipeline.branch_by_specialty()

    en_local = run_local_condition(pipeline, "en", TIMING_REPETITIONS)
    en_deployed = run_deployed_condition("en", en_local["per_case"])
    replay = run_oracle_and_urgent(pipeline, en_local["per_case"])
    fr_local = run_local_condition(pipeline, "fr", 1)
    fr_deployed = run_deployed_condition("fr", fr_local["per_case"])
    optimization = run_optimization_checks(pipeline)

    remote_all = list(en_deployed["per_case"].values()) + list(fr_deployed["per_case"].values())
    parity = [r.get("same_as_local") for r in en_deployed["per_case"].values()]
    translation_errors: Dict[str, int] = {}
    for call in pipeline.translation_calls:
        if not call.ok:
            translation_errors[call.error] = translation_errors.get(call.error, 0) + 1

    en_cases = en_local["per_case"]
    coverage = [en_cases[c.case_id]["provider_coverage"] for c in CASES]

    def remote_outcomes(condition: dict) -> Dict[str, dict]:
        # Un appel sans réponse du Space n'est pas un résultat du pipeline :
        # il est exclu ici et compté dans `network`.
        return {cid: r["classification"] for cid, r in condition["per_case"].items() if r["ok"]}

    ambiguous = []
    for case in CASES:
        if not case.ambiguous:
            continue
        entry = en_cases[case.case_id]
        outcome = entry["classification"]
        verdict = ("spécialité attendue" if outcome["top1_strict"] else
                   "alternative acceptable" if outcome["top1_lenient"] else
                   "aucune prédiction" if outcome["predicted"] is None else "autre spécialité")
        ambiguous.append({
            "case_id": case.case_id, "title": case.title,
            "expected": case.expected_specialty, "acceptable_alternative": case.acceptable_alternative,
            "detected_symptoms": entry["detected_symptoms"],
            "top_specialties": entry["top_specialties"], "score_margin": entry["score_margin"],
            "verdict": verdict,
            "with_expected_symptoms": replay["oracle"][case.case_id]["top_specialties"],
        })

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Code teste : clone local du Space emergency-savior-output, importe tel quel. "
            "La verite terrain (specialite et symptomes attendus) est une hypothese de test "
            "fixee avant execution, pas une verite clinique. Les modules optionnels (vision, "
            "extraction de carte d'identite) et backend/realtime ne sont pas sollicites."
        ),
        "n_cases": len(CASES),
        "timing_repetitions": TIMING_REPETITIONS,
        "network": {
            "deployed_space": {
                "url": DEPLOYED_SPACE_URL,
                "calls": len(remote_all),
                "responses_ok": sum(1 for r in remote_all if r["ok"]),
                "errors": [r["error"] for r in remote_all if not r["ok"]],
            },
            "google_translate_local": {
                "calls": len(pipeline.translation_calls),
                "calls_ok": sum(1 for c in pipeline.translation_calls if c.ok),
                "errors": translation_errors,
            },
            "local_equals_deployed_en": f"{sum(1 for p in parity if p)}/{sum(1 for p in parity if p is not None)}",
            "local_staged_equals_real_predict": all(e["matches_real_predict"] for e in en_cases.values()),
            "local_deterministic": all(e["deterministic"] for e in en_cases.values()),
        },
        "pipeline_load_seconds": pipeline.load_seconds,
        "classification": {
            "en_local": _classification_block({cid: e["classification"] for cid, e in en_cases.items()}, branches),
            "en_deployed": _classification_block(remote_outcomes(en_deployed), branches),
            "oracle": _classification_block(replay["oracle"], branches),
            "fr_local": _classification_block(
                {cid: e["classification"] for cid, e in fr_local["per_case"].items()}, branches),
            "fr_deployed": _classification_block(remote_outcomes(fr_deployed), branches),
        },
        "symptom_extraction": {
            "en_local": ev.aggregate_symptom_extraction([e["symptom_extraction"] for e in en_cases.values()]),
            "fr_local": ev.aggregate_symptom_extraction(
                [e["symptom_extraction"] for e in fr_local["per_case"].values()]),
        },
        "recommendation": {
            "n_cases": len(CASES),
            "cases_with_providers": sum(1 for c in coverage if c["n_providers"] > 0),
            "provider_top1_coverage": sum(1 for c in coverage if c["provider_top1"]) / len(CASES),
            "provider_top3_coverage": sum(1 for c in coverage if c["provider_top3"]) / len(CASES),
            "cases_with_several_specialties_in_top3": sum(1 for c in coverage if c["distinct_specialties_in_top3"] > 1),
        },
        "latency": {
            "local": {stage: ev.latency_stats(en_local["timings"][stage]) for stage in STAGES},
            "deployed_space_end_to_end": ev.latency_stats(
                [r["seconds"] for r in en_deployed["per_case"].values() if r["ok"]]),
        },
        "ambiguous_cases": ambiguous,
        "urgent_flag_effect": {
            "cases_where_top1_changes_with_detected_symptoms": [
                cid for cid, e in replay["urgent_effect"].items() if e["detected_symptoms"]["top1_changes"]],
            "cases_where_top1_changes_with_expected_symptoms": [
                cid for cid, e in replay["urgent_effect"].items() if e["expected_symptoms"]["top1_changes"]],
            "per_case": replay["urgent_effect"],
        },
        "optimization": optimization,
        "cases": {
            case.case_id: {
                "title": case.title, "text_fr": case.text_fr, "text_en": case.text_en,
                "age": case.age, "severity": case.severity.value, "urgent": case.urgent,
                "location": case.location, "budget": case.budget,
                "expected_specialty": case.expected_specialty,
                "expected_branch": branches[case.expected_specialty],
                "acceptable_alternative": case.acceptable_alternative,
                "expected_symptoms": list(case.expected_symptoms),
                "ambiguous": case.ambiguous, "rationale": case.rationale,
                "en_local": en_cases[case.case_id],
                "en_deployed": en_deployed["per_case"][case.case_id],
                "oracle": replay["oracle"][case.case_id],
                "fr_local": fr_local["per_case"][case.case_id],
                "fr_deployed": fr_deployed["per_case"][case.case_id],
            }
            for case in CASES
        },
    }

    _print_report(report)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Rapport sauvegardé dans : {REPORT_PATH}")


if __name__ == "__main__":
    main()
