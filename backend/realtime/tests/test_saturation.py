"""
Tests unitaires de la détection de saturation (`saturation.py`), sur des
séquences construites à la main : la réponse attendue est connue d'avance.

    cd backend
    python -m pytest realtime/tests/test_saturation.py -v
"""

import math
from datetime import datetime, timedelta, timezone

import pytest

from realtime.events import EventType, ProviderType, make_event
from realtime.saturation import (
    FilteredAvailabilityFeed,
    ProviderStatus,
    RawAvailabilityFeed,
    SaturationConfig,
    SaturationMonitor,
)

NORMAL, ALERT, SATURATED = ProviderStatus.normal, ProviderStatus.alert, ProviderStatus.saturated


def monitor_with(capacity=100, **config):
    """Un prestataire "p" de 100 places par défaut : une place = 1 point
    d'occupation. Signal de tendance coupé sauf demande contraire."""
    config.setdefault("use_drift_signal", False)
    config.setdefault("readmission_margin", 0.10)
    monitor = SaturationMonitor(SaturationConfig(**config))
    monitor.register_provider("p", capacity)
    return monitor


def observe_occupancy(monitor, occupancy_percent, now, capacity=100):
    return monitor.observe_availability("p", capacity - occupancy_percent, now)


# ═════════════════════════════════════════════════════════════════════════
# Seuils et hystérésis
# ═════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("occupancy, expected", [
    (0, NORMAL), (79, NORMAL), (80, ALERT), (89, ALERT), (90, SATURATED), (100, SATURATED),
])
def test_thresholds_80_and_90(occupancy, expected):
    assert observe_occupancy(monitor_with(), occupancy, now=0.0) == expected


def test_saturated_provider_does_not_oscillate_around_90():
    """Occupation qui hésite autour de 90 % : avec une marge de 10 points,
    le prestataire reste saturé jusqu'à repasser sous 80 %."""
    monitor = monitor_with(readmission_margin=0.10)
    sequence = [91, 89, 91, 88, 92, 85, 90, 81, 80]

    states = [observe_occupancy(monitor, occ, now=60.0 * i) for i, occ in enumerate(sequence)]

    assert states == [SATURATED] * len(sequence)
    assert [s for _, _, s in monitor.transitions] == [SATURATED]  # une seule transition


def test_without_margin_the_same_sequence_oscillates():
    """Contre-épreuve : sans hystérésis, la même séquence change d'état à
    presque chaque point. C'est ce que la marge évite."""
    monitor = monitor_with(readmission_margin=0.0)
    for i, occ in enumerate([91, 89, 91, 88, 92, 85, 90, 81, 80]):
        observe_occupancy(monitor, occ, now=60.0 * i)

    assert len(monitor.transitions) >= 6


def test_readmission_happens_below_threshold_minus_margin():
    monitor = monitor_with(readmission_margin=0.10)
    observe_occupancy(monitor, 95, 0.0)

    assert observe_occupancy(monitor, 80, 60.0) == SATURATED   # 80 n'est pas < 80
    assert observe_occupancy(monitor, 79, 120.0) == ALERT      # sorti de saturation, encore en alerte
    assert observe_occupancy(monitor, 70, 180.0) == ALERT      # 70 n'est pas < 70
    assert observe_occupancy(monitor, 69, 240.0) == NORMAL


# ═════════════════════════════════════════════════════════════════════════
# Réservations provisoires
# ═════════════════════════════════════════════════════════════════════════


def test_reservation_counts_as_load_as_soon_as_issued():
    """10 places, 4 libres (60 %). Deux recommandations acceptées : 80 %,
    alerte. Une troisième : 90 %, saturé — avant qu'aucun patient n'arrive."""
    monitor = monitor_with(capacity=10)
    monitor.observe_availability("p", 4, 0.0)
    assert monitor.status("p", 0.0) == NORMAL

    monitor.reserve("r1", "p", 10.0)
    monitor.reserve("r2", "p", 20.0)
    assert monitor.occupancy("p", 20.0) == pytest.approx(0.8)
    assert monitor.status("p", 20.0) == ALERT

    monitor.reserve("r3", "p", 30.0)
    assert monitor.status("p", 30.0) == SATURATED


def test_reservation_expires_when_the_patient_never_arrives():
    ttl = 3600.0
    monitor = monitor_with(capacity=10, reservation_ttl_seconds=ttl)
    monitor.observe_availability("p", 5, 0.0)
    monitor.reserve("r1", "p", 0.0)
    monitor.reserve("r2", "p", 600.0)

    assert monitor.pending_load("p", ttl - 1) == 2.0
    assert monitor.pending_load("p", ttl) == 1.0          # r1 a expiré
    assert monitor.pending_load("p", 600.0 + ttl) == 0.0  # r2 aussi
    assert monitor.occupancy("p", 600.0 + ttl) == pytest.approx(0.5)


def test_expiry_alone_brings_the_provider_back():
    """Aucun événement après la réservation : c'est la simple lecture de
    l'état, plus tard, qui constate l'expiration."""
    monitor = monitor_with(capacity=10, reservation_ttl_seconds=1800.0)
    monitor.observe_availability("p", 4, 0.0)
    for k in range(3):
        monitor.reserve(f"r{k}", "p", 0.0)
    assert monitor.status("p", 1.0) == SATURATED

    assert monitor.status("p", 1800.0) == NORMAL  # 60 % : sous 80 % - 10


def test_reservation_fades_after_arrival_at_the_ewma_rate():
    """À l'arrivée, le patient passe dans la disponibilité publiée, que
    l'EWMA n'absorbe qu'en ~tau : la réservation décroît à la même vitesse."""
    tau = 1200.0
    monitor = monitor_with(capacity=10, arrival_fade_tau_seconds=tau)
    monitor.reserve("r1", "p", 0.0)
    monitor.arrived("r1", 600.0)

    assert monitor.pending_load("p", 600.0) == pytest.approx(1.0)
    assert monitor.pending_load("p", 600.0 + tau) == pytest.approx(math.exp(-1))
    assert monitor.pending_load("p", 600.0 + 4 * tau) == 0.0   # sous 5 % : supprimée


def test_reservation_is_released_at_arrival_without_smoothing():
    monitor = monitor_with(capacity=10, arrival_fade_tau_seconds=0.0)
    monitor.reserve("r1", "p", 0.0)
    monitor.arrived("r1", 600.0)

    assert monitor.pending_load("p", 600.0) == 0.0


def test_cancel_releases_the_place_immediately():
    monitor = monitor_with(capacity=10)
    monitor.reserve("r1", "p", 0.0)
    monitor.cancel("r1", 5.0)

    assert monitor.pending_load("p", 5.0) == 0.0
    monitor.arrived("r1", 6.0)  # sans effet, et sans erreur
    monitor.cancel("inconnue", 7.0)


def test_reservations_can_be_switched_off():
    monitor = monitor_with(capacity=10, use_reservations=False)
    monitor.observe_availability("p", 2, 0.0)
    for k in range(5):
        monitor.reserve(f"r{k}", "p", 1.0)

    assert monitor.occupancy("p", 1.0) == pytest.approx(0.8)


def test_provider_never_observed_is_assumed_free():
    monitor = monitor_with(capacity=4)
    assert monitor.occupancy("p", 0.0) == 0.0
    monitor.reserve("r1", "p", 0.0)
    assert monitor.occupancy("p", 0.0) == pytest.approx(0.25)


def test_availability_outside_capacity_is_clipped():
    monitor = monitor_with(capacity=10)
    monitor.observe_availability("p", 22, 0.0)   # pic aberrant : 22 places libres sur 10
    assert monitor.occupancy("p", 0.0) == 0.0
    monitor.observe_availability("p", -3, 1.0)
    assert monitor.occupancy("p", 1.0) == 1.0


# ═════════════════════════════════════════════════════════════════════════
# Signal de tendance (Page-Hinkley, réglages de la Phase 2)
# ═════════════════════════════════════════════════════════════════════════

STEP = 120.0  # ~1 point lissé toutes les 2 minutes, comme en Phase 2


def feed_series(monitor, series):
    return [monitor.observe_availability("p", value, STEP * i) for i, value in enumerate(series)]


def test_falling_availability_raises_an_alert_before_the_80_threshold():
    """10 places : 8 libres (20 %), puis 3 libres (70 %). Sous le seuil
    d'alerte, mais la tendance est nette : alerte par anticipation."""
    monitor = monitor_with(capacity=10, use_drift_signal=True)

    states = feed_series(monitor, [8.0] * 60 + [3.0] * 20)

    assert set(states[:60]) == {NORMAL}
    assert ALERT in states[60:]
    assert SATURATED not in states          # une tendance ne déclare jamais la saturation
    assert len(monitor.drift_alerts) == 1


def test_same_series_without_drift_signal_stays_normal():
    monitor = monitor_with(capacity=10, use_drift_signal=False)

    assert set(feed_series(monitor, [8.0] * 60 + [3.0] * 20)) == {NORMAL}


def test_trend_alert_is_held_then_dropped():
    monitor = monitor_with(capacity=10, use_drift_signal=True, drift_hold_seconds=1800.0)
    feed_series(monitor, [8.0] * 60 + [3.0] * 20)
    alert_time, _ = monitor.drift_alerts[0]

    assert monitor.status("p", alert_time + 1799.0) == ALERT
    assert monitor.status("p", alert_time + 1800.0) == NORMAL  # 70 % : sous le seuil d'alerte


def test_trend_is_ignored_when_the_provider_is_mostly_empty():
    """20 places : 18 libres puis 13 libres (35 %). Page-Hinkley signale la
    baisse, mais l'occupation est sous 60 % : pas d'alerte."""
    monitor = monitor_with(capacity=20, use_drift_signal=True)

    states = feed_series(monitor, [18.0] * 60 + [13.0] * 20)

    assert len(monitor.drift_alerts) == 1
    assert set(states) == {NORMAL}


def test_rising_availability_is_not_an_alert():
    monitor = monitor_with(capacity=10, use_drift_signal=True)

    states = feed_series(monitor, [3.0] * 60 + [8.0] * 20)

    assert monitor.drift_alerts == []
    assert set(states) == {NORMAL}


# ═════════════════════════════════════════════════════════════════════════
# Branchement sur le flux d'événements
# ═════════════════════════════════════════════════════════════════════════

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


def event(kind, rec_id, seconds, slots=None):
    payload = {"available_slots": slots} if slots is not None else None
    return make_event(rec_id, "p", ProviderType.cabinet, kind, T0 + timedelta(seconds=seconds), payload)


def test_filtered_feed_ignores_an_unconfirmed_update_and_raw_feed_does_not():
    filtered, raw = monitor_with(capacity=10), monitor_with(capacity=10)
    feeds = [FilteredAvailabilityFeed(filtered), RawAvailabilityFeed(raw)]
    stream = [
        event(EventType.recommendation_issued, "c1", 0),
        event(EventType.patient_confirmed, "c1", 10),
        event(EventType.availability_update, "c1", 60, slots=6),      # confirmée
        event(EventType.recommendation_issued, "c2", 70),
        event(EventType.availability_update, "c2", 120, slots=0),     # jamais confirmée
    ]
    for e in stream:
        for feed in feeds:
            feed.process(e, (e.timestamp - T0).total_seconds())

    assert filtered.occupancy("p", 120.0) == pytest.approx(0.4)   # la valeur 0 n'est pas passée
    assert raw.occupancy("p", 120.0) == pytest.approx(1.0)
    assert raw.status("p", 120.0) == SATURATED
