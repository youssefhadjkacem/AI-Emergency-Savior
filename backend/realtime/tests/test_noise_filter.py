"""
Tests unitaires du pipeline de filtrage du bruit (section 3.8 du papier).

Chaque étape (dédoublonnage, confirmation, EWMA, anomalie) est testée avec
des événements construits à la main plutôt qu'avec le simulateur, pour
vérifier des cas précis et déterministes.

Lancer avec :
    cd backend
    python -m pytest realtime/tests/test_noise_filter.py -v
"""

from datetime import datetime, timedelta, timezone

from realtime.events import EventType, ProviderType, make_event
from realtime.noise_filter import (
    DEFAULT_ANOMALY_MIN_SAMPLES,
    Deduplicator,
    EventStatus,
    NoiseFilterPipeline,
    ewma_weight,
)

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _confirmed_pipeline(pipeline: NoiseFilterPipeline, recommendation_id: str, provider_id: str, ts: datetime):
    """Fait passer recommendation_issued + patient_confirmed pour que les
    availability_update suivants ne soient pas filtrés par le filtre de confirmation."""
    pipeline.process_event(
        make_event(recommendation_id, provider_id, ProviderType.cabinet, EventType.recommendation_issued, ts)
    )
    pipeline.process_event(
        make_event(
            recommendation_id, provider_id, ProviderType.cabinet, EventType.patient_confirmed,
            ts + timedelta(seconds=1),
        )
    )


# ═════════════════════════════════════════════════════════════════════════
# a) Déduplication
# ═════════════════════════════════════════════════════════════════════════


def test_duplicate_event_is_rejected():
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-1", "prov-1", T0)

    original = make_event(
        "rec-1", "prov-1", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=10), {"available_slots": 5},
    )
    duplicate = make_event(
        "rec-1", "prov-1", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=12), {"available_slots": 5},  # même clé, 2s plus tard : retry réseau
    )

    result_original = pipeline.process_event(original)
    result_duplicate = pipeline.process_event(duplicate)

    assert result_original.status == EventStatus.accepted
    assert result_duplicate.status == EventStatus.deduplicated
    assert pipeline.deduplicator.duplicate_count == 1


def test_legitimate_update_with_different_payload_is_not_deduplicated():
    """
    Régression du bug diagnostiqué via evaluation.py (précision
    injected_duplicate ~74-77% au lieu de ~100%) : une 2e mise à jour de
    disponibilité légitime pour la MÊME recommandation/provider, arrivant
    dans la fenêtre de dédoublonnage mais avec une valeur DIFFÉRENTE, ne
    doit PAS être rejetée comme doublon — c'est une vraie nouvelle
    observation, pas un retry réseau.
    """
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-1b", "prov-1b", T0)

    first_update = make_event(
        "rec-1b", "prov-1b", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=10), {"available_slots": 5},
    )
    second_update = make_event(
        "rec-1b", "prov-1b", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=13), {"available_slots": 4},  # même clé (rec/provider/type), 3s plus tard, valeur différente
    )

    result_first = pipeline.process_event(first_update)
    result_second = pipeline.process_event(second_update)

    assert result_first.status == EventStatus.accepted
    assert result_second.status == EventStatus.accepted  # pas un doublon : contenu différent
    assert pipeline.deduplicator.duplicate_count == 0


def test_true_retry_still_deduplicated_among_legitimate_updates():
    """Vérifie que la correction ne casse pas la détection des vrais
    doublons : un retry exact (même payload) au milieu de mises à jour
    légitimes à contenu différent doit toujours être rejeté."""
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-1c", "prov-1c", T0)

    update_a = make_event(
        "rec-1c", "prov-1c", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=10), {"available_slots": 5},
    )
    retry_of_a = make_event(
        "rec-1c", "prov-1c", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=11), {"available_slots": 5},  # même valeur : vrai retry
    )
    update_b = make_event(
        "rec-1c", "prov-1c", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=13), {"available_slots": 3},  # valeur différente : nouvelle observation légitime
    )

    result_a = pipeline.process_event(update_a)
    result_retry = pipeline.process_event(retry_of_a)
    result_b = pipeline.process_event(update_b)

    assert result_a.status == EventStatus.accepted
    assert result_retry.status == EventStatus.deduplicated
    assert result_b.status == EventStatus.accepted
    assert pipeline.deduplicator.duplicate_count == 1


def test_events_outside_dedup_window_are_not_duplicates():
    dedup = Deduplicator(window_seconds=5.0)
    event_a = make_event("rec-1", "prov-1", ProviderType.cabinet, EventType.availability_update, T0, {"available_slots": 5})
    event_b = make_event(
        "rec-1", "prov-1", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=10), {"available_slots": 5},  # 10s > fenêtre de 5s
    )

    assert dedup.is_duplicate(event_a) is False
    assert dedup.is_duplicate(event_b) is False
    assert dedup.duplicate_count == 0


# ═════════════════════════════════════════════════════════════════════════
# b) Filtre de confirmation
# ═════════════════════════════════════════════════════════════════════════


def test_unconfirmed_availability_update_does_not_affect_smoothed_value():
    pipeline = NoiseFilterPipeline()

    pipeline.process_event(
        make_event("rec-2", "prov-2", ProviderType.cabinet, EventType.recommendation_issued, T0)
    )
    # Pas de patient_confirmed / patient_arrived envoyé.

    update = make_event(
        "rec-2", "prov-2", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=30), {"available_slots": 7},
    )
    result = pipeline.process_event(update)

    assert result.status == EventStatus.unconfirmed
    assert result.smoothed_availability is None
    assert pipeline.smoother.current("prov-2") is None
    assert len(pipeline.confirmation_tracker.unconfirmed_log) == 1


def test_confirmed_availability_update_does_update_smoothed_value():
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-3", "prov-3", T0)

    update = make_event(
        "rec-3", "prov-3", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=30), {"available_slots": 6},
    )
    result = pipeline.process_event(update)

    assert result.status == EventStatus.accepted
    assert result.smoothed_availability == 6.0
    assert pipeline.smoother.current("prov-3") == 6.0


# ═════════════════════════════════════════════════════════════════════════
# c) Pondération temporelle (EWMA)
# ═════════════════════════════════════════════════════════════════════════


def test_ewma_weight_decreases_with_age():
    tau = 1200.0  # valeur par défaut (20 min)
    w_recent = ewma_weight(delta_t_seconds=30, tau_seconds=tau)     # 30s d'écart
    w_old = ewma_weight(delta_t_seconds=6 * 3600, tau_seconds=tau)  # 6h d'écart

    assert 0.0 < w_old < w_recent <= 1.0
    # Une observation récente laisse l'ancien état quasi inchangé (w proche de 1) ;
    # une observation très ancienne cède presque toute la place à la nouvelle (w proche de 0).
    assert w_recent > 0.9
    assert w_old < 1e-4


def test_old_value_weighs_less_than_recent_value_in_smoothed_availability():
    """
    Scénario réaliste du simulateur : une mise à jour périmée de plusieurs
    heures arrive mélangée à des événements récents. Elle ne doit quasiment
    pas peser dans la disponibilité lissée une fois qu'une observation
    récente est appliquée.
    """
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-4", "prov-4", T0)

    stale_value = 20.0  # valeur très différente de la valeur récente ci-dessous
    recent_value = 5.0

    stale_update = make_event(
        "rec-4", "prov-4", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=5), {"available_slots": stale_value},
    )
    recent_update = make_event(
        "rec-4", "prov-4", ProviderType.cabinet, EventType.availability_update,
        T0 + timedelta(seconds=5) + timedelta(hours=6), {"available_slots": recent_value},
    )

    result_stale = pipeline.process_event(stale_update)
    result_recent = pipeline.process_event(recent_update)

    assert result_stale.smoothed_availability == stale_value  # première observation : init pure
    # Après 6h d'écart, la valeur ancienne (20) doit peser presque rien :
    # la disponibilité lissée doit être très proche de la valeur récente (5).
    assert abs(result_recent.smoothed_availability - recent_value) < 0.01
    assert result_recent.ewma_weight_applied < 1e-4  # poids de l'ancien état ≈ 0


# ═════════════════════════════════════════════════════════════════════════
# d) Détection d'anomalie (backend par défaut : robust_zscore)
# ═════════════════════════════════════════════════════════════════════════


def test_clear_outlier_is_quarantined_and_does_not_affect_smoothed_value():
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-5", "prov-5", T0)

    # Warm-up : alimente le détecteur avec une baseline stable et cohérente
    # (nécessaire pour dépasser DEFAULT_ANOMALY_MIN_SAMPLES avant de tester la détection).
    ts = T0 + timedelta(seconds=10)
    baseline_value = 10.0
    last_result = None
    for i in range(DEFAULT_ANOMALY_MIN_SAMPLES + 2):
        ts = ts + timedelta(seconds=20)
        update = make_event(
            "rec-5", "prov-5", ProviderType.cabinet, EventType.availability_update,
            ts, {"available_slots": baseline_value},
        )
        last_result = pipeline.process_event(update)

    assert last_result.status == EventStatus.accepted
    smoothed_before = pipeline.smoother.current("prov-5")
    assert smoothed_before == baseline_value

    # Pic aberrant et isolé (10 -> 0), jamais corroboré par la suite.
    ts = ts + timedelta(seconds=20)
    outlier = make_event(
        "rec-5", "prov-5", ProviderType.cabinet, EventType.availability_update,
        ts, {"available_slots": 0.0},
    )
    result_outlier = pipeline.process_event(outlier)

    assert result_outlier.status == EventStatus.quarantined_anomaly
    assert result_outlier.anomaly_score is not None
    assert result_outlier.anomaly_score >= pipeline._detector_for("prov-5").threshold
    # La disponibilité lissée n'a pas bougé : l'anomalie n'a pas été appliquée.
    assert pipeline.smoother.current("prov-5") == smoothed_before

    # Retour à la normale : ne doit pas non plus confirmer le "0" (trop loin, |10-0|>tolérance)
    ts = ts + timedelta(seconds=20)
    back_to_normal = make_event(
        "rec-5", "prov-5", ProviderType.cabinet, EventType.availability_update,
        ts, {"available_slots": baseline_value},
    )
    result_back = pipeline.process_event(back_to_normal)

    assert result_back.status == EventStatus.accepted
    assert len(pipeline.quarantine_log) == 1  # le "0" reste seul en quarantaine, jamais fusionné


def test_quarantined_anomaly_confirmed_by_coherent_follow_up_is_merged():
    pipeline = NoiseFilterPipeline()
    _confirmed_pipeline(pipeline, "rec-6", "prov-6", T0)

    ts = T0 + timedelta(seconds=10)
    baseline_value = 10.0
    for i in range(DEFAULT_ANOMALY_MIN_SAMPLES + 2):
        ts = ts + timedelta(seconds=20)
        update = make_event(
            "rec-6", "prov-6", ProviderType.cabinet, EventType.availability_update,
            ts, {"available_slots": baseline_value},
        )
        pipeline.process_event(update)

    # Premier point d'un vrai changement de régime : signalé anormal, quarantainé.
    ts = ts + timedelta(seconds=20)
    first_new_regime = make_event(
        "rec-6", "prov-6", ProviderType.cabinet, EventType.availability_update,
        ts, {"available_slots": 25.0},
    )
    result_1 = pipeline.process_event(first_new_regime)
    assert result_1.status == EventStatus.quarantined_anomaly

    # Deuxième point, proche du premier (cohérent) : confirme le changement de régime.
    ts = ts + timedelta(seconds=15)
    second_new_regime = make_event(
        "rec-6", "prov-6", ProviderType.cabinet, EventType.availability_update,
        ts, {"available_slots": 26.0},
    )
    result_2 = pipeline.process_event(second_new_regime)

    assert result_2.status == EventStatus.accepted
    assert result_2.smoothed_availability is not None
    # La quarantaine a été levée et fusionnée : plus rien en attente.
    assert pipeline._pending_quarantine.get("prov-6", []) == []
