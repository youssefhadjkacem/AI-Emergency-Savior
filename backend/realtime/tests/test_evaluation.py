"""
Tests unitaires du module d'évaluation (`evaluation.py`) — vérifie que les
formules de précision/rappel/F1 et la matrice de confusion sont justes, sur
un petit jeu de (label de vérité terrain, FilterResult) construits à la
main (pas le flux simulé complet, pour isoler le calcul lui-même).

Lancer avec :
    cd backend
    python -m pytest realtime/tests/test_evaluation.py -v
"""

from datetime import datetime, timezone

from realtime.evaluation import evaluate
from realtime.events import EventType, ProviderType, make_event
from realtime.noise_filter import EventStatus, FilterResult
from realtime.simulator import GroundTruthLabel

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _fr(status: EventStatus, ewma_weight_applied=None) -> FilterResult:
    """FilterResult minimal pour les tests : le contenu de l'event n'importe
    pas ici, seul le statut décidé par le pipeline compte."""
    dummy_event = make_event("rec-x", "prov-x", ProviderType.cabinet, EventType.availability_update, T0)
    return FilterResult(event=dummy_event, status=status, ewma_weight_applied=ewma_weight_applied)


# ═════════════════════════════════════════════════════════════════════════
# Précision / rappel / F1 par catégorie + matrice de confusion
# ═════════════════════════════════════════════════════════════════════════


def test_precision_recall_f1_by_category_hand_computed():
    """
    Jeu de 8 paires construit à la main. Valeurs attendues calculées par
    la définition classique TP/(TP+FP), TP/(TP+FN) :

      injected_duplicate    : TP=2, FP=1, FN=1 -> P=R=2/3,        F1=2/3
      injected_unconfirmed  : TP=1, FP=0, FN=0 -> P=R=F1=1.0
      injected_anomaly      : TP=1, FP=0, FN=1 -> P=1.0, R=0.5,   F1=2/3

      normal (predicted positive = status accepted) : TP=1 (item8).
      FP = tout événement non-normal accepté = item3 (dup, accepted)
           ET item7 (anomaly, accepted) -> FP=2. FN = item4 (normal,
           deduplicated) -> FN=1.
      normal : TP=1, FP=2, FN=1 -> P=1/3, R=1/2, F1=2/5=0.4
    """
    pairs = [
        (GroundTruthLabel.injected_duplicate, _fr(EventStatus.deduplicated)),        # dup TP
        (GroundTruthLabel.injected_duplicate, _fr(EventStatus.deduplicated)),        # dup TP
        (GroundTruthLabel.injected_duplicate, _fr(EventStatus.accepted)),            # dup FN
        (GroundTruthLabel.normal, _fr(EventStatus.deduplicated)),                    # dup FP / normal FN
        (GroundTruthLabel.injected_unconfirmed, _fr(EventStatus.unconfirmed)),       # unconfirmed TP
        (GroundTruthLabel.injected_anomaly, _fr(EventStatus.quarantined_anomaly)),   # anomaly TP
        (GroundTruthLabel.injected_anomaly, _fr(EventStatus.accepted)),              # anomaly FN
        (GroundTruthLabel.normal, _fr(EventStatus.accepted)),                        # normal TP
    ]

    report = evaluate(pairs)
    categories = report["categories"]

    dup = categories[GroundTruthLabel.injected_duplicate.value]
    assert (dup["true_positives"], dup["false_positives"], dup["false_negatives"]) == (2, 1, 1)
    assert dup["precision"] == 2 / 3
    assert dup["recall"] == 2 / 3
    assert abs(dup["f1"] - 2 / 3) < 1e-9

    unconfirmed = categories[GroundTruthLabel.injected_unconfirmed.value]
    assert (unconfirmed["true_positives"], unconfirmed["false_positives"], unconfirmed["false_negatives"]) == (1, 0, 0)
    assert unconfirmed["precision"] == 1.0
    assert unconfirmed["recall"] == 1.0
    assert unconfirmed["f1"] == 1.0

    anomaly = categories[GroundTruthLabel.injected_anomaly.value]
    assert (anomaly["true_positives"], anomaly["false_positives"], anomaly["false_negatives"]) == (1, 0, 1)
    assert anomaly["precision"] == 1.0
    assert anomaly["recall"] == 0.5
    assert abs(anomaly["f1"] - 2 / 3) < 1e-9

    normal = categories[GroundTruthLabel.normal.value]
    assert (normal["true_positives"], normal["false_positives"], normal["false_negatives"]) == (1, 2, 1)
    assert abs(normal["precision"] - 1 / 3) < 1e-9
    assert normal["recall"] == 0.5
    assert abs(normal["f1"] - 0.4) < 1e-9

    # injected_stale absent du jeu de test : support nul -> recall non défini (0/0).
    # precision reste calculable (0.0) car plusieurs events non-stale de ce jeu
    # tombent dans l'ensemble de statuts "positifs" élargi de injected_stale
    # (deduplicated/unconfirmed/quarantined_anomaly) sans qu'aucun ne soit stale.
    stale = categories[GroundTruthLabel.injected_stale.value]
    assert stale["support"] == 0
    assert stale["true_positives"] == 0
    assert stale["precision"] == 0.0
    assert stale["recall"] is None


def test_confusion_matrix_counts_match_inputs():
    pairs = [
        (GroundTruthLabel.injected_duplicate, _fr(EventStatus.deduplicated)),
        (GroundTruthLabel.injected_duplicate, _fr(EventStatus.accepted)),
        (GroundTruthLabel.normal, _fr(EventStatus.accepted)),
    ]
    report = evaluate(pairs)
    matrix = report["confusion_matrix"]

    assert matrix[GroundTruthLabel.injected_duplicate.value][EventStatus.deduplicated.value] == 1
    assert matrix[GroundTruthLabel.injected_duplicate.value][EventStatus.accepted.value] == 1
    assert matrix[GroundTruthLabel.normal.value][EventStatus.accepted.value] == 1
    # Toutes les autres cellules doivent rester à 0.
    total_counted = sum(sum(row.values()) for row in matrix.values())
    assert total_counted == len(pairs)


def test_precision_and_recall_undefined_when_no_predicted_positive_or_no_support():
    # Aucun événement n'est jamais prédit "deduplicated" : precision indéfinie (0/0).
    pairs = [
        (GroundTruthLabel.injected_duplicate, _fr(EventStatus.accepted)),
        (GroundTruthLabel.normal, _fr(EventStatus.accepted)),
    ]
    report = evaluate(pairs)
    dup = report["categories"][GroundTruthLabel.injected_duplicate.value]
    assert dup["true_positives"] == 0
    assert dup["false_positives"] == 0
    assert dup["precision"] is None       # 0 / (0 + 0)
    assert dup["recall"] == 0.0           # 0 / (0 + 1), bien défini (il y a du support)


# ═════════════════════════════════════════════════════════════════════════
# Métrique auxiliaire injected_stale (suppression_rate)
# ═════════════════════════════════════════════════════════════════════════


def test_stale_suppression_rate_hand_computed():
    """
    3 events injected_stale : 2 avec un poids EWMA >= seuil (0.99) donc
    "neutralisés", 1 avec un poids trop faible (contribution non négligeable).
    suppression_rate attendu = 2/3.
    """
    pairs = [
        (GroundTruthLabel.injected_stale, _fr(EventStatus.accepted, ewma_weight_applied=0.9999)),
        (GroundTruthLabel.injected_stale, _fr(EventStatus.accepted, ewma_weight_applied=0.999)),
        (GroundTruthLabel.injected_stale, _fr(EventStatus.accepted, ewma_weight_applied=0.5)),
        (GroundTruthLabel.normal, _fr(EventStatus.accepted, ewma_weight_applied=0.7)),  # ne doit pas interférer
    ]
    report = evaluate(pairs)
    stale = report["categories"][GroundTruthLabel.injected_stale.value]

    assert stale["support"] == 3
    assert stale["evaluable_count"] == 3
    assert stale["suppressed_count"] == 2
    assert abs(stale["suppression_rate"] - 2 / 3) < 1e-9


def test_stale_suppression_rate_ignores_events_without_ewma_weight():
    """Un event périmé rejeté avant même d'atteindre l'EWMA (ex. dédoublonné)
    n'a pas de ewma_weight_applied : il ne doit pas fausser suppression_rate."""
    pairs = [
        (GroundTruthLabel.injected_stale, _fr(EventStatus.deduplicated, ewma_weight_applied=None)),
        (GroundTruthLabel.injected_stale, _fr(EventStatus.accepted, ewma_weight_applied=0.995)),
    ]
    report = evaluate(pairs)
    stale = report["categories"][GroundTruthLabel.injected_stale.value]

    assert stale["support"] == 2
    assert stale["evaluable_count"] == 1
    assert stale["suppressed_count"] == 1
    assert stale["suppression_rate"] == 1.0
