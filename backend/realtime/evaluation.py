"""
Évaluation quantitative du pipeline de filtrage de bruit (section 3.8,
section Experiments du papier) — clôture l'étape "filtrage" avant de passer
à la détection de drift/saturation.

Le simulateur (`simulator.py`) connaît la vérité terrain de chaque
événement (`GroundTruthLabel`) ; le pipeline (`noise_filter.py`), lui, ne la
voit jamais — il ne reçoit que l'`Event` nu. Ce module recolle les deux
après coup pour mesurer précision / rappel / F1 par catégorie de bruit et
produire une matrice de confusion globale.

── Correspondance catégorie -> statut(s) "positifs" ─────────────────────────
Trois catégories ont une règle de rejet DÉDIÉE et déterministe ou quasi
déterministe dans le pipeline, donc un statut cible unique et sans ambiguïté :

    injected_duplicate    -> deduplicated
    injected_unconfirmed  -> unconfirmed
    injected_anomaly      -> quarantined_anomaly
    normal                -> accepted

`injected_stale` N'A PAS de statut dédié : la péremption n'est pas rejetée
par une règle explicite dans `noise_filter.py`, elle est seulement
atténuée par la décroissance temporelle de l'EWMA (`ewma_weight`). On
calcule quand même une précision/rappel "au sens large" (predicted positive
= n'importe quel statut autre que `accepted`) pour rester honnête sur ce que
le pipeline fait RÉELLEMENT avec ces événements — et on complète avec une
métrique auxiliaire (`suppression_rate`) qui mesure ce qui compte vraiment
pour cette catégorie : est-ce que sa contribution à la disponibilité lissée
a été effectivement neutralisée, même sans rejet explicite.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

from .noise_filter import EventStatus, FilterResult, NoiseFilterPipeline
from .simulator import GroundTruthLabel, LabeledEvent

# "Statuts positifs" attendus pour chaque catégorie de vérité terrain.
# Un ensemble (pas une valeur unique) pour pouvoir traiter `injected_stale`
# de la même façon générique que les autres, même si son ensemble est
# volontairement large (voir docstring du module).
POSITIVE_STATUSES: Dict[GroundTruthLabel, frozenset] = {
    GroundTruthLabel.normal: frozenset({EventStatus.accepted}),
    GroundTruthLabel.injected_duplicate: frozenset({EventStatus.deduplicated}),
    GroundTruthLabel.injected_unconfirmed: frozenset({EventStatus.unconfirmed}),
    GroundTruthLabel.injected_anomaly: frozenset({EventStatus.quarantined_anomaly}),
    # Pas de statut dédié : "positif" = tout ce qui n'est pas `accepted`.
    # Voir note dans `evaluate()` — cette mesure est fournie par honnêteté
    # méthodologique, mais `suppression_rate` est la métrique pertinente
    # pour cette catégorie précise.
    GroundTruthLabel.injected_stale: frozenset(
        {EventStatus.deduplicated, EventStatus.unconfirmed, EventStatus.quarantined_anomaly}
    ),
}

STALE_NOTE = (
    "Pas de statut de rejet dédié dans le pipeline actuel : la péremption est gérée "
    "implicitement par la décroissance temporelle de l'EWMA (poids ewma_weight_applied), "
    "pas par une règle de rejet explicite. La précision/le rappel ci-dessus utilisent "
    "'statut != accepted' comme proxy de détection, ce qui est peu informatif ici "
    "(un event périmé accepté n'est pas forcément un échec : sa CONTRIBUTION peut avoir "
    "été neutralisée). Voir 'suppression_rate' pour la métrique pertinente."
)

STALE_SUPPRESSION_WEIGHT_THRESHOLD = 0.99
# Un événement périmé est considéré "correctement neutralisé" si le poids
# appliqué à l'ANCIEN état lissé (ewma_weight_applied, renvoyé par
# EwmaSmoother.update) est >= ce seuil, c.-à-d. que sa propre contribution
# à la disponibilité lissée était quasi nulle (< 1%). Seuil choisi à 0.99
# car c'est déjà le cas dès qu'un event périmé de quelques heures est suivi
# d'une observation récente avec tau=20 min par défaut (voir noise_filter.py).


def _prf(tp: int, fp: int, fn: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else None
    recall = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is None or recall is None or (precision + recall) == 0:
        f1 = None
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def build_confusion_matrix(
    pairs: Iterable[Tuple[GroundTruthLabel, EventStatus]]
) -> Dict[str, Dict[str, int]]:
    """matrix[label_vérité_terrain][statut_décidé_par_le_pipeline] = compte."""
    matrix: Dict[str, Dict[str, int]] = {
        gt.value: {status.value: 0 for status in EventStatus} for gt in GroundTruthLabel
    }
    for gt_label, status in pairs:
        matrix[gt_label.value][status.value] += 1
    return matrix


def _stale_suppression_stats(pairs: List[Tuple[GroundTruthLabel, FilterResult]]) -> dict:
    stale_results = [fr for gt, fr in pairs if gt == GroundTruthLabel.injected_stale]
    evaluable = [fr for fr in stale_results if fr.ewma_weight_applied is not None]
    suppressed = sum(1 for fr in evaluable if fr.ewma_weight_applied >= STALE_SUPPRESSION_WEIGHT_THRESHOLD)
    return {
        "suppression_rate": (suppressed / len(evaluable)) if evaluable else None,
        "suppressed_count": suppressed,
        "evaluable_count": len(evaluable),
        # evaluable_count < support si certains events périmés ont été
        # rejetés à un stade antérieur (dédup/confirmation) et n'ont donc
        # jamais atteint le lissage EWMA (pas de ewma_weight_applied).
    }


def evaluate(pairs: List[Tuple[GroundTruthLabel, FilterResult]]) -> dict:
    """
    `pairs` : liste de (label de vérité terrain, résultat du pipeline) dans
    l'ordre où les événements ont été traités — typiquement produite par
    `run_pipeline_and_evaluate()`.

    Retourne un rapport structuré (sérialisable JSON) :
        {
          "confusion_matrix": {label -> {status -> count}},
          "categories": {label -> {support, true_positives, false_positives,
                                    false_negatives, precision, recall, f1,
                                    [note, suppression_rate, ...] si injected_stale},
        }
    """
    confusion = build_confusion_matrix((gt, fr.status) for gt, fr in pairs)

    categories: Dict[str, dict] = {}
    for gt_label, positive_statuses in POSITIVE_STATUSES.items():
        support = sum(1 for gt, _ in pairs if gt == gt_label)
        tp = sum(1 for gt, fr in pairs if gt == gt_label and fr.status in positive_statuses)
        fp = sum(1 for gt, fr in pairs if gt != gt_label and fr.status in positive_statuses)
        fn = sum(1 for gt, fr in pairs if gt == gt_label and fr.status not in positive_statuses)
        precision, recall, f1 = _prf(tp, fp, fn)

        entry = {
            "support": support,
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
        if gt_label == GroundTruthLabel.injected_stale:
            entry["note"] = STALE_NOTE
            entry.update(_stale_suppression_stats(pairs))

        categories[gt_label.value] = entry

    return {"confusion_matrix": confusion, "categories": categories}


def run_pipeline_and_evaluate(
    pipeline: NoiseFilterPipeline, labeled_events: Iterable[LabeledEvent]
) -> dict:
    """
    Fait passer chaque événement étiqueté dans le pipeline — SANS jamais lui
    transmettre le label — puis évalue le résultat. C'est le point d'entrée
    typique pour un run complet (voir `run_evaluation.py`).
    """
    pairs: List[Tuple[GroundTruthLabel, FilterResult]] = []
    for labeled in labeled_events:
        result = pipeline.process_event(labeled.event)  # le pipeline ne voit jamais ground_truth_label
        pairs.append((labeled.ground_truth_label, result))
    return evaluate(pairs)
