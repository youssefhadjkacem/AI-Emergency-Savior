"""
Mesure avant / après des trois corrections du pipeline principal :
chemin français, négations, Top 3 diversifié.

Même méthodologie que `run_pipeline_evaluation.py` : mêmes 29 cas, même
vérité terrain (`pipeline_cases.py`), mêmes métriques
(`pipeline_evaluation.py`). Les corrections sont activées une à une :

    before             comportement d'avant correction (`LEGACY_CONFIG`)
    +french            + lexique français, statut d'extraction explicite
    +french+negation   + détection de portée de négation
    after              + Top 3 diversifié (= pipeline corrigé complet)

Ces quatre configurations tournent SANS service de traduction (sauf
`before`, qui garde Google Translate comme à l'origine) : elles sont
reproductibles hors ligne et isolent l'effet du lexique.

Deux configurations supplémentaires dépendent du réseau et sont exécutées
une seule fois, avec ce que les services veulent bien renvoyer ce jour-là :

    translation_only   extraction d'origine + chaîne Google -> MyMemory.
                       Sert au diagnostic : si le français remonte au niveau
                       de l'anglais dès que la traduction répond, la panne
                       de traduction est bien la cause.
    after+translation  pipeline corrigé + traduction systématique du
                       français (union lexique + traduction).
    after_default      pipeline corrigé tel que livré : réglages par défaut
                       de `MedicalRecommender`, traduction du français
                       seulement si le lexique ne trouve rien.

Chaque configuration est évaluée sur :
    en       les 29 cas, texte anglais
    fr       les 29 cas, texte français
    heldout  24 cas français écrits après le gel du lexique
             (`heldout_cases_fr.py`) — la mesure à retenir pour le lexique,
             les 29 cas d'origine ayant été vus pendant sa rédaction.

Le Space déployé est aussi interrogé en français, pour constater l'état de
la production tant que les corrections n'y sont pas poussées.

Lancer avec :
    cd backend
    python -m testing.run_fix_evaluation
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import httpx

from . import pipeline_evaluation as ev
from .heldout_cases_fr import HELDOUT_CASES
from .pipeline_cases import CASES, PipelineCase
from .pipeline_runner import (LEGACY_CONFIG, REMOTE_TIMEOUT, SPACE_DIR, LocalPipeline, call_deployed_space)

OFFLINE = ()  # aucune chaîne de traduction
LIVE_CHAIN = ("google", "mymemory")

CONFIGS: Dict[str, dict] = {
    "before": dict(LEGACY_CONFIG),
    "+french": dict(use_french_lexicon=True, handle_negation=False, translation_chain=OFFLINE, diversify_top3=False),
    "+french+negation": dict(use_french_lexicon=True, handle_negation=True, translation_chain=OFFLINE,
                             diversify_top3=False),
    "after": dict(use_french_lexicon=True, handle_negation=True, translation_chain=OFFLINE, diversify_top3=True),
    "translation_only": dict(use_french_lexicon=False, handle_negation=False, translation_chain=LIVE_CHAIN,
                             diversify_top3=False),
    "after+translation": dict(use_french_lexicon=True, handle_negation=True, translation_chain=LIVE_CHAIN,
                              diversify_top3=True, translate_french="always"),
    "after_default": {},
}
DATASETS = {"en": (CASES, "en"), "fr": (CASES, "fr"), "heldout": (HELDOUT_CASES, "fr")}

# Les 8 cas pour lesquels le rapport précédent ne renvoyait aucune prédiction (en anglais).
PREVIOUS_NO_PREDICTION = ["C02", "C03", "C13", "C15", "C18", "C20", "A05", "R02"]

RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPORT_PATH = RESULTS_DIR / "fix_evaluation_report.json"


def evaluate(pipeline: LocalPipeline, cases: List[PipelineCase], language: str, branches: Dict[str, str]) -> dict:
    per_case, outcomes, extraction, coverage = {}, [], [], []
    for case in cases:
        text = case.text_en if language == "en" else case.text_fr
        trace = pipeline.run(text, case.age, case.urgent, case.budget, case.location)
        outcome = ev.classification_outcome(case.expected_specialty, case.acceptable_alternative,
                                            [s for s, _ in trace.top_specialties])
        scores = ev.symptom_extraction_scores(trace.detected_symptoms, case.expected_symptoms)
        cover = ev.provider_coverage(case.expected_specialty, trace.top_providers)
        outcomes.append(outcome)
        extraction.append(scores)
        coverage.append(cover)
        per_case[case.case_id] = {
            "expected_specialty": case.expected_specialty,
            "detected_symptoms": trace.detected_symptoms,
            "spurious_symptoms": scores["spurious_symptoms"],
            "missed_symptoms": scores["missed_symptoms"],
            "top_specialties": trace.top_specialties,
            "top_providers": [{"name": p["provider_name"], "specialty": p["specialty"], "location": p["location"]}
                              for p in trace.top_providers],
            "extraction_status": trace.extraction_status,
            "language": trace.language,
            "translation": ({"ok": trace.translation.ok, "provider": trace.translation.provider,
                             "error": trace.translation.error, "output": trace.translation.output}
                            if trace.translation else None),
            "matches_real_predict": pipeline.matches_predict(trace, case.age, case.budget, case.location),
            **outcome, **cover,
        }

    n = len(cases)
    summary = ev.classification_summary(outcomes)
    summary["branch_accuracy"] = ev.branch_metrics(
        [(c.expected_specialty, o["predicted"]) for c, o in zip(cases, outcomes)], branches)["accuracy"]
    summary["symptom_extraction"] = ev.aggregate_symptom_extraction(extraction)
    summary["provider_top1_coverage"] = sum(1 for c in coverage if c["provider_top1"]) / n
    summary["provider_top3_coverage"] = sum(1 for c in coverage if c["provider_top3"]) / n
    summary["cases_with_several_specialties_in_top3"] = sum(1 for c in coverage if c["distinct_specialties_in_top3"] > 1)
    summary["extraction_statuses"] = dict(Counter(e["extraction_status"] for e in per_case.values()))
    translations = [e["translation"] for e in per_case.values() if e["translation"]]
    summary["translation_calls"] = len(translations)
    summary["translation_ok_by_provider"] = dict(Counter(t["provider"] for t in translations if t["ok"]))
    summary["staged_equals_real_predict"] = all(e["matches_real_predict"] for e in per_case.values())
    return {"summary": summary, "per_case": per_case}


def _row(label: str, s: dict) -> str:
    x = s["symptom_extraction"]
    fmt = lambda v: f"{100 * v:5.1f}%" if v is not None else "  n/a "  # noqa: E731
    num = lambda v: f"{v:.3f}" if v is not None else " n/a "  # noqa: E731
    return (f"{label:<20}{fmt(s['top1_strict_accuracy']):>8}{fmt(s['top1_lenient_accuracy']):>8}"
            f"{fmt(s['top3_coverage']):>8}{fmt(s['provider_top1_coverage']):>10}{fmt(s['provider_top3_coverage']):>10}"
            f"{num(x['precision']):>8}{num(x['recall']):>8}{s['no_prediction']:>7}")


def main() -> None:
    logging.disable(logging.CRITICAL)  # les échecs de traduction sont comptés dans le rapport, pas affichés
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Dict[str, dict]] = {}
    branches = None
    for name, options in CONFIGS.items():
        pipeline = LocalPipeline(**options)
        branches = branches or pipeline.branch_by_specialty()
        results[name] = {dataset: evaluate(pipeline, cases, language, branches)
                         for dataset, (cases, language) in DATASETS.items()}

    # État de la production : le Space déployé, en français.
    deployed = {}
    with httpx.Client(timeout=REMOTE_TIMEOUT) as client:
        for case in CASES:
            r = call_deployed_space(case.text_fr, case.age, case.urgent, case.budget, case.location, client)
            if r["ok"]:
                r.update(ev.classification_outcome(case.expected_specialty, case.acceptable_alternative,
                                                   [s for s, _ in r["top_specialties"]]))
            deployed[case.case_id] = r
    deployed_ok = [r for r in deployed.values() if r["ok"]]
    deployed_summary = {
        "responses_ok": f"{len(deployed_ok)}/{len(deployed)}",
        "top1_strict": sum(1 for r in deployed_ok if r["top1_strict"]),
        "no_symptom_detected": sum(1 for r in deployed_ok if not r["detected_symptoms"]),
        "responses_listing_three_providers": sum(1 for r in deployed_ok if "Top 3 prestataires" in r["raw_output"]),
    }

    tracked = {
        cid: {f"{config}/{dataset}": results[config][dataset]["per_case"][cid]["predicted"]
              for config in ("before", "after") for dataset in ("en", "fr")}
        for cid in PREVIOUS_NO_PREDICTION
    }
    for cid, entry in tracked.items():
        entry["expected"] = results["after"]["en"]["per_case"][cid]["expected_specialty"]
        entry["missed_symptoms_en_after"] = results["after"]["en"]["per_case"][cid]["missed_symptoms"]

    lexicon = (SPACE_DIR / "src" / "french_lexicon.py").read_bytes()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Corrections evaluees dans le clone local du Space ; le Space deploye n'est pas modifie. "
            "'fr' = les 29 cas d'origine, vus pendant la redaction du lexique francais (resultat optimiste). "
            "'heldout' = 24 cas francais ecrits apres le gel du lexique, jamais utilises pour le modifier."
        ),
        "french_lexicon_sha256": hashlib.sha256(lexicon).hexdigest(),
        "configs": {k: {a: list(b) if isinstance(b, tuple) else b for a, b in v.items()} for k, v in CONFIGS.items()},
        "summary": {c: {d: results[c][d]["summary"] for d in DATASETS} for c in CONFIGS},
        "deployed_space_french": deployed_summary,
        "previous_no_prediction_cases": tracked,
        "per_case": {c: {d: results[c][d]["per_case"] for d in DATASETS} for c in CONFIGS},
        "deployed_space_french_per_case": deployed,
    }

    header = (f"{'configuration':<20}{'Top-1':>8}{'Top-1 lg':>8}{'Top-3':>8}{'prest. n°1':>10}{'prest. 3':>10}"
              f"{'préc.':>8}{'rappel':>8}{'aucune':>7}")
    titles = {"en": "ANGLAIS — 29 cas", "fr": "FRANÇAIS — 29 cas d'origine (vus pendant la rédaction du lexique)",
              "heldout": "FRANÇAIS — 24 cas de contrôle (écrits après le gel du lexique)"}
    for dataset, title in titles.items():
        print("=" * len(header))
        print(f"  {title}")
        print("=" * len(header))
        print(header)
        for config in CONFIGS:
            print(_row(config, results[config][dataset]["summary"]))
        print()
    print("Top-1 / Top-3 : spécialité attendue en 1re position / parmi les 3 spécialités renvoyées.")
    print("prest. n°1 / prest. 3 : spécialité attendue chez le 1er prestataire / parmi les 3 prestataires.")
    print()
    for config in ("translation_only", "after+translation"):
        s = results[config]["fr"]["summary"]
        print(f"Traductions réussies ({config}, 29 cas FR) : {s['translation_ok_by_provider']} "
              f"sur {s['translation_calls']} appels")
    print(f"Space déployé, français : {deployed_summary}")
    print()
    print("Suivi des 8 cas sans prédiction du rapport précédent :")
    for cid, entry in tracked.items():
        print(f"  {cid} attendu {entry['expected']:<18} EN avant {entry['before/en']} -> après {entry['after/en']}"
              f" | FR avant {entry['before/fr']} -> après {entry['after/fr']}")

    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\nRapport sauvegardé dans : {REPORT_PATH}")


if __name__ == "__main__":
    main()
