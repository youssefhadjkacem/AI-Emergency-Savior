"""
Évaluation des corrections (français, négations, Top 3) contre le Space
DÉPLOYÉ, et comparaison avec les résultats locaux.

Ce script n'exécute PAS le pipeline en local. Il :
  1. envoie chaque cas au Space en ligne `emergency-savior-output`, par le
     même protocole que `backend/main.py`, et lit la réponse avec le parseur
     de `backend/main.py` ;
  2. recalcule les métriques avec `pipeline_evaluation.py` ;
  3. compare, cas par cas, à ce que le code local avait produit — lu dans
     `results/fix_evaluation_report.json` (configuration `after_default`),
     sans le recalculer.

Jeux de cas, identiques à `run_fix_evaluation.py` :
    en       29 cas, texte anglais
    fr       29 cas, texte français
    heldout  24 cas français de contrôle

Ce que la comparaison peut voir : le Space n'expose que les symptômes, les
3 spécialités avec leur score à une décimale, les 3 prestataires et le
statut d'extraction. La comparaison porte sur ces quatre éléments.

Lancer avec :
    cd backend
    python -m testing.run_deployed_evaluation
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import httpx

from . import pipeline_evaluation as ev
from .heldout_cases_fr import HELDOUT_CASES
from .pipeline_cases import CASES, PipelineCase
from .pipeline_runner import DEPLOYED_SPACE_URL, REMOTE_TIMEOUT, call_deployed_space

SECONDS_BETWEEN_CALLS = 1.0
# Le Space tourne sur une instance partagée gratuite : une pause d'une
# seconde entre deux appels (82 appels, moins de 3 minutes au total) évite
# de le charger. La pause n'entre pas dans la latence mesurée.

MAX_ATTEMPTS = 2
# Un appel en échec réseau est retenté une fois ; les deux tentatives sont
# comptées dans le rapport.

DATASETS = {"en": (CASES, "en"), "fr": (CASES, "fr"), "heldout": (HELDOUT_CASES, "fr")}
LOCAL_CONFIG = "after_default"  # pipeline corrigé tel que livré, dans fix_evaluation_report.json

RESULTS_DIR = Path(__file__).resolve().parent / "results"
LOCAL_REPORT_PATH = RESULTS_DIR / "fix_evaluation_report.json"
REPORT_PATH = RESULTS_DIR / "deployed_evaluation_report.json"


def _call(case: PipelineCase, text: str, client: httpx.Client) -> dict:
    result, attempts = None, 0
    for attempts in range(1, MAX_ATTEMPTS + 1):
        result = call_deployed_space(text, case.age, case.urgent, case.budget, case.location, client)
        if result["ok"]:
            break
        time.sleep(SECONDS_BETWEEN_CALLS)
    result["attempts"] = attempts
    return result


def _differences(remote: dict, local: dict) -> List[str]:
    """Éléments qui diffèrent entre la réponse du Space et le résultat local."""
    diffs = []
    if sorted(remote["detected_symptoms"]) != sorted(local["detected_symptoms"]):
        diffs.append("symptoms")
    remote_specs = [(s, round(v, 1)) for s, v in remote["top_specialties"]]
    local_specs = [(s, float(f"{v:.1f}")) for s, v in local["top_specialties"]]  # même arrondi que app.py
    if remote_specs != local_specs:
        diffs.append("specialties")
    if remote["top_providers"] != local["top_providers"]:
        diffs.append("providers")
    if remote["extraction_status"] != local["extraction_status"]:
        diffs.append("extraction_status")
    return diffs


def evaluate_dataset(cases: List[PipelineCase], language: str, local_cases: Dict[str, dict],
                     client: httpx.Client) -> dict:
    per_case, outcomes, extraction, coverage, latencies = {}, [], [], [], []
    for case in cases:
        text = case.text_en if language == "en" else case.text_fr
        remote = _call(case, text, client)
        time.sleep(SECONDS_BETWEEN_CALLS)
        entry = {"expected_specialty": case.expected_specialty, "ok": remote["ok"],
                 "attempts": remote["attempts"], "seconds": remote["seconds"]}
        if not remote["ok"]:
            entry["error"] = remote["error"]
            per_case[case.case_id] = entry
            continue

        providers = [{"name": p["name"], "specialty": p["specialty"], "location": p["location"]}
                     for p in remote["top_providers"]]
        remote["top_providers"] = providers
        outcome = ev.classification_outcome(case.expected_specialty, case.acceptable_alternative,
                                            [s for s, _ in remote["top_specialties"]])
        scores = ev.symptom_extraction_scores(remote["detected_symptoms"], case.expected_symptoms)
        cover = ev.provider_coverage(case.expected_specialty, providers)
        outcomes.append(outcome)
        extraction.append(scores)
        coverage.append(cover)
        latencies.append(remote["seconds"])
        entry.update({
            "detected_symptoms": remote["detected_symptoms"],
            "spurious_symptoms": scores["spurious_symptoms"],
            "missed_symptoms": scores["missed_symptoms"],
            "top_specialties": remote["top_specialties"],
            "top_providers": providers,
            "extraction_status": remote["extraction_status"],
            "differs_from_local": _differences(remote, local_cases[case.case_id]),
            "local_predicted": local_cases[case.case_id]["predicted"],
            **outcome, **cover,
        })
        per_case[case.case_id] = entry

    n_ok = len(outcomes)
    summary = ev.classification_summary(outcomes)
    summary["calls"] = len(cases)
    summary["responses_ok"] = n_ok
    summary["calls_retried"] = sum(1 for e in per_case.values() if e["attempts"] > 1)
    summary["symptom_extraction"] = ev.aggregate_symptom_extraction(extraction)
    summary["provider_top1_coverage"] = (sum(1 for c in coverage if c["provider_top1"]) / n_ok) if n_ok else None
    summary["provider_top3_coverage"] = (sum(1 for c in coverage if c["provider_top3"]) / n_ok) if n_ok else None
    summary["responses_with_three_providers"] = sum(1 for c in coverage if c["n_providers"] == 3)
    summary["extraction_statuses"] = dict(Counter(e["extraction_status"] for e in per_case.values() if e["ok"]))
    summary["latency"] = ev.latency_stats(latencies)
    summary["cases_identical_to_local"] = sum(1 for e in per_case.values() if e["ok"] and not e["differs_from_local"])
    summary["cases_differing_from_local"] = {cid: e["differs_from_local"] for cid, e in per_case.items()
                                             if e["ok"] and e["differs_from_local"]}
    return {"summary": summary, "per_case": per_case}


def _pct(v) -> str:
    return f"{100 * v:.1f}%" if v is not None else "n/a"


def _num(v) -> str:
    return f"{v:.3f}" if v is not None else "n/a"


def main() -> None:
    local = json.loads(LOCAL_REPORT_PATH.read_text(encoding="utf-8"))
    local_summary = local["summary"][LOCAL_CONFIG]
    local_cases = local["per_case"][LOCAL_CONFIG]

    started = time.perf_counter()
    with httpx.Client(timeout=REMOTE_TIMEOUT) as client:
        results = {name: evaluate_dataset(cases, language, local_cases[name], client)
                   for name, (cases, language) in DATASETS.items()}
    total_seconds = time.perf_counter() - started

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Toutes les mesures viennent du Space deploye ; le pipeline n'a pas ete execute en local. "
            f"Reference locale : fix_evaluation_report.json, configuration '{LOCAL_CONFIG}' "
            f"(genere le {local['generated_at']})."
        ),
        "space_url": DEPLOYED_SPACE_URL,
        "seconds_between_calls": SECONDS_BETWEEN_CALLS,
        "total_seconds": total_seconds,
        "summary": {name: r["summary"] for name, r in results.items()},
        "local_reference": {name: local_summary[name] for name in DATASETS},
        "per_case": {name: r["per_case"] for name, r in results.items()},
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    titles = {"en": "ANGLAIS — 29 cas", "fr": "FRANÇAIS — 29 cas d'origine", "heldout": "FRANÇAIS — 24 cas de contrôle"}
    rows = [
        ("Top-1 strict", lambda s: _pct(s["top1_strict_accuracy"])),
        ("Top-1 large", lambda s: _pct(s["top1_lenient_accuracy"])),
        ("Top-3 spécialités", lambda s: _pct(s["top3_coverage"])),
        ("Prestataire n°1", lambda s: _pct(s["provider_top1_coverage"])),
        ("Parmi les 3 prestataires", lambda s: _pct(s["provider_top3_coverage"])),
        ("Extraction : précision", lambda s: _num(s["symptom_extraction"]["precision"])),
        ("Extraction : rappel", lambda s: _num(s["symptom_extraction"]["recall"])),
        ("Sans prédiction", lambda s: str(s["no_prediction"])),
    ]
    for name, title in titles.items():
        deployed, reference = results[name]["summary"], local_summary[name]
        print("=" * 70)
        print(f"  {title}")
        print("=" * 70)
        print(f"{'mesure':<28}{'local':>12}{'Space déployé':>18}")
        for label, get in rows:
            print(f"{label:<28}{get(reference):>12}{get(deployed):>18}")
        lat = deployed["latency"]
        print(f"Réponses : {deployed['responses_ok']}/{deployed['calls']} (appels retentés : {deployed['calls_retried']}) — "
              f"3 prestataires renvoyés : {deployed['responses_with_three_providers']}")
        if lat["n"]:
            print(f"Latence : moyenne {lat['mean_ms']:.0f} ms, médiane {lat['median_ms']:.0f} ms, "
                  f"P95 {lat['p95_ms']:.0f} ms, max {lat['max_ms']:.0f} ms")
        print(f"Statuts d'extraction : {deployed['extraction_statuses']}")
        print(f"Cas identiques au local : {deployed['cases_identical_to_local']}/{deployed['responses_ok']} — "
              f"différents : {deployed['cases_differing_from_local'] or 'aucun'}")
        print()
    print(f"Durée totale : {total_seconds:.0f} s — rapport : {REPORT_PATH}")


if __name__ == "__main__":
    main()
