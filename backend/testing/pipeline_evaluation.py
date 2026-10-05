"""
Métriques d'évaluation du pipeline principal (entrée patient -> Top 3).

Ce module ne fait que du calcul : il reçoit des résultats déjà produits par
`pipeline_runner.py` (ou construits à la main dans les tests) et les
compare à la vérité terrain de `pipeline_cases.py`. Quatre familles :

  - extraction NLP        : précision / rappel des symptômes extraits ;
  - classification        : exactitude, F1 par branche, matrice de confusion
                            par branche, couverture Top-1 / Top-3 ;
  - latence               : moyenne et P95 par étape ;
  - optimisation          : le classement des prestataires réagit-il aux
                            contraintes, et dans le bon sens ?

── Lecture des métriques de classification ──────────────────────────────
Trois niveaux d'exigence, du plus strict au plus large :

    top1_strict   : la 1re spécialité prédite est `expected_specialty` ;
    top1_lenient  : idem, ou `acceptable_alternative` pour un cas ambigu ;
    top3          : `expected_specialty` figure dans les 3 spécialités renvoyées.

Un cas pour lequel le pipeline ne renvoie aucune spécialité est compté
comme une erreur (classe prédite `NO_PREDICTION`), pas écarté.

── Couverture du Top 3 de prestataires ───────────────────────────────────
`optimize_providers_nsga` ne classe que les prestataires de la PREMIÈRE
spécialité prédite. Les 3 prestataires recommandés ont donc tous la même
spécialité : "la spécialité attendue apparaît dans le Top 3 des
prestataires" équivaut par construction à "elle est en position 1". Les
deux taux sont calculés séparément à partir des prestataires réellement
renvoyés, pour le constater plutôt que le supposer.
"""

from __future__ import annotations

import copy
import statistics
from typing import Dict, Iterable, List, Optional, Sequence

import numpy as np

NO_PREDICTION = "NO_PREDICTION"


# ═════════════════════════════════════════════════════════════════════════
# Extraction NLP
# ═════════════════════════════════════════════════════════════════════════


def _prf(tp: int, fp: int, fn: int):
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def symptom_extraction_scores(detected: Iterable[str], expected: Iterable[str]) -> dict:
    """Compare les symptômes extraits aux symptômes attendus d'UN cas."""
    detected, expected = set(detected), set(expected)
    tp, fp, fn = len(detected & expected), len(detected - expected), len(expected - detected)
    precision, recall, f1 = _prf(tp, fp, fn)
    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "spurious_symptoms": sorted(detected - expected),
        "missed_symptoms": sorted(expected - detected),
    }


def aggregate_symptom_extraction(per_case: Sequence[dict]) -> dict:
    """Micro-moyenne : les symptômes de tous les cas sont comptés ensemble."""
    tp = sum(c["true_positives"] for c in per_case)
    fp = sum(c["false_positives"] for c in per_case)
    fn = sum(c["false_negatives"] for c in per_case)
    precision, recall, f1 = _prf(tp, fp, fn)
    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "cases_with_no_symptom_detected": sum(1 for c in per_case if c["true_positives"] + c["false_positives"] == 0),
    }


# ═════════════════════════════════════════════════════════════════════════
# Classification de spécialité
# ═════════════════════════════════════════════════════════════════════════


def classification_outcome(expected: str, acceptable_alternative: Optional[str],
                           top_specialties: Sequence[str]) -> dict:
    """Verdict d'UN cas à partir des spécialités prédites, dans l'ordre."""
    predicted = top_specialties[0] if top_specialties else None
    return {
        "predicted": predicted,
        "top1_strict": predicted == expected,
        "top1_lenient": predicted is not None and predicted in (expected, acceptable_alternative),
        "top3": expected in list(top_specialties)[:3],
    }


def branch_metrics(pairs: Sequence[tuple], branch_by_specialty: Dict[str, str]) -> dict:
    """`pairs` : [(spécialité attendue, spécialité prédite ou None)].

    Retourne, au niveau BRANCHE (B1..B7) : matrice de confusion
    [branche attendue][branche prédite], précision / rappel / F1 par
    branche, F1 macro et exactitude."""
    branches = sorted(set(branch_by_specialty.values()))
    columns = branches + [NO_PREDICTION]
    matrix = {b: {c: 0 for c in columns} for b in branches}
    for expected, predicted in pairs:
        predicted_branch = branch_by_specialty.get(predicted, NO_PREDICTION) if predicted else NO_PREDICTION
        matrix[branch_by_specialty[expected]][predicted_branch] += 1

    per_branch = {}
    for b in branches:
        tp = matrix[b][b]
        fn = sum(matrix[b].values()) - tp
        fp = sum(matrix[other][b] for other in branches if other != b)
        precision, recall, f1 = _prf(tp, fp, fn)
        per_branch[b] = {"support": tp + fn, "precision": precision, "recall": recall, "f1": f1}

    # F1 macro sur les branches présentes dans le jeu de test ; un F1 non
    # défini (branche jamais prédite ni correcte) compte pour 0.
    present = [b for b in branches if per_branch[b]["support"] > 0]
    macro_f1 = statistics.mean((per_branch[b]["f1"] or 0.0) for b in present) if present else None
    correct = sum(matrix[b][b] for b in branches)
    return {
        "confusion_matrix": matrix,
        "per_branch": per_branch,
        "macro_f1": macro_f1,
        "accuracy": correct / len(pairs) if pairs else None,
    }


def classification_summary(outcomes: Sequence[dict]) -> dict:
    """Taux agrégés à partir des verdicts de `classification_outcome`."""
    n = len(outcomes)
    rate = lambda key: (sum(1 for o in outcomes if o[key]) / n) if n else None  # noqa: E731
    return {
        "n_cases": n,
        "top1_strict_accuracy": rate("top1_strict"),
        "top1_lenient_accuracy": rate("top1_lenient"),
        "top3_coverage": rate("top3"),
        "no_prediction": sum(1 for o in outcomes if o["predicted"] is None),
    }


def provider_coverage(expected: str, top_providers: Sequence[dict]) -> dict:
    """La spécialité attendue apparaît-elle parmi les prestataires recommandés ?"""
    specialties = [p["specialty"] for p in top_providers]
    return {
        "n_providers": len(specialties),
        "provider_top1": bool(specialties) and specialties[0] == expected,
        "provider_top3": expected in specialties[:3],
        "distinct_specialties_in_top3": len(set(specialties[:3])),
    }


# ═════════════════════════════════════════════════════════════════════════
# Latence
# ═════════════════════════════════════════════════════════════════════════


def latency_stats(samples_seconds: Sequence[float]) -> dict:
    """Moyenne, médiane et P95 en millisecondes (P95 par interpolation
    linéaire, `numpy.percentile`)."""
    if not samples_seconds:
        return {"n": 0, "mean_ms": None, "median_ms": None, "p95_ms": None, "max_ms": None}
    ms = np.asarray(samples_seconds, dtype=float) * 1000.0
    return {
        "n": int(ms.size),
        "mean_ms": float(ms.mean()),
        "median_ms": float(np.median(ms)),
        "p95_ms": float(np.percentile(ms, 95)),
        "max_ms": float(ms.max()),
    }


# ═════════════════════════════════════════════════════════════════════════
# Cohérence de l'optimisation multi-objectifs
# ═════════════════════════════════════════════════════════════════════════
#
# Ces tests appellent le vrai `ProviderFilter.optimize_providers_nsga` avec
# `top_k` assez grand pour obtenir le classement COMPLET d'une spécialité,
# sur la vraie base K2 ou sur une copie où UN SEUL champ d'UN SEUL
# prestataire a été modifié.

CONTINUOUS_CRITERIA = {
    # colonne : sens souhaitable (+1 = plus grand est mieux, -1 = plus petit est mieux)
    "quality_score": +1,
    "average_cost": -1,
    "waiting_time_days": -1,
    "available_slots": +1,
}


def full_ranking(provider_filter, specialty: str, budget: Optional[float] = None,
                 location: Optional[str] = None) -> List[str]:
    """Identifiants des prestataires d'une spécialité, du 1er au dernier."""
    ranked = provider_filter.optimize_providers_nsga(specialty, top_k=10**6, budget=budget, location=location)
    return ranked["ID"].tolist()


def with_modified_provider(provider_filter, provider_id: str, **changes):
    """Copie du filtre dans laquelle un prestataire a des champs modifiés."""
    modified = copy.copy(provider_filter)
    modified.df_providers = provider_filter.df_providers.copy()
    mask = modified.df_providers["ID"] == provider_id
    for column, value in changes.items():
        modified.df_providers.loc[mask, column] = value
    return modified


def rank_shift(provider_filter, specialty: str, provider_id: str, changes: dict,
               budget: Optional[float] = None, location: Optional[str] = None) -> dict:
    """Rang (1 = meilleur) d'un prestataire avant et après modification."""
    before = full_ranking(provider_filter, specialty, budget, location).index(provider_id) + 1
    modified = with_modified_provider(provider_filter, provider_id, **changes)
    after = full_ranking(modified, specialty, budget, location).index(provider_id) + 1
    return {"provider_id": provider_id, "rank_before": before, "rank_after": after}


def summarize_rank_shifts(shifts: Sequence[dict], top_k: int = 3) -> dict:
    """Compte les prestataires dont le rang s'améliore (rang plus petit),
    ne bouge pas ou se dégrade après une modification."""
    n = len(shifts)
    improved = sum(1 for s in shifts if s["rank_after"] < s["rank_before"])
    worsened = sum(1 for s in shifts if s["rank_after"] > s["rank_before"])
    return {
        "n_trials": n,
        "improved": improved,
        "unchanged": n - improved - worsened,
        "worsened": worsened,
        "improved_rate": improved / n if n else None,
        "worsened_rate": worsened / n if n else None,
        "in_top_k_before": sum(1 for s in shifts if s["rank_before"] <= top_k),
        "in_top_k_after": sum(1 for s in shifts if s["rank_after"] <= top_k),
        "mean_rank_before": statistics.mean(s["rank_before"] for s in shifts) if n else None,
        "mean_rank_after": statistics.mean(s["rank_after"] for s in shifts) if n else None,
    }


def first_front_structure(provider_filter, specialty: str) -> dict:
    """Structure du premier front de Pareto d'une spécialité, avec les 7
    objectifs du pipeline (sans budget ni ville) :

      - part des prestataires sur ce front ;
      - nombre de points du front dont la densité k-NN vaut +infini. Le code
        du Space attribue +infini aux DEUX extrémités de chaque objectif (le
        meilleur et le pire) : ces points passent devant tous les autres.
    """
    from src.nsga2 import fast_non_dominated_sort, knn_density_distance  # code du Space

    df = provider_filter.filter_by_specialty_name(specialty)
    objs = np.column_stack([
        -df["quality_score"].fillna(0).values,
        df["average_cost"].fillna(0).values,
        df["waiting_time_days"].fillna(30).values,
        -df["available_slots"].fillna(0).values,
        np.zeros(len(df)),
        -df["accepts_cnam"].fillna(0).values,
        -df["teleconsultation"].fillna(0).values,
    ])
    fronts = fast_non_dominated_sort(objs)
    density = knn_density_distance(objs, fronts[0], k_neighbors=3)
    return {
        "n_providers": len(df),
        "first_front_size": len(fronts[0]),
        "n_fronts": len(fronts),
        "first_front_share": len(fronts[0]) / len(df),
        "first_front_points_with_infinite_density": int(np.isinf(density).sum()),
    }


def worst_on_some_criterion(provider_filter, specialty: str, provider_ids: Sequence[str]) -> List[dict]:
    """Pour chaque prestataire donné, les critères continus sur lesquels il
    est le PIRE (ou à égalité avec le pire) de sa spécialité."""
    df = provider_filter.filter_by_specialty_name(specialty)
    out = []
    for pid in provider_ids:
        row = df[df["ID"] == pid].iloc[0]
        worst = []
        for column, direction in CONTINUOUS_CRITERIA.items():
            worst_value = df[column].min() if direction > 0 else df[column].max()
            if row[column] == worst_value:
                worst.append(column)
        out.append({"provider_id": pid, "worst_on": worst})
    return out
