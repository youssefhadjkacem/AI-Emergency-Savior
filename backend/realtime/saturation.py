"""
Détection de saturation d'un prestataire — section 3.8 du papier
("Performative Drift Handling"), Phase 3.

Entrée : la disponibilité d'un prestataire (places libres), de préférence
celle que produit le pipeline de bruit de la Phase 1 (`noise_filter.py`,
EWMA tau = 1200 s), et le suivi des recommandations émises vers lui.
Sortie : un état par prestataire (NORMAL / ALERT / SATURATED), lu par la
réaction (`adaptation.py`).

Ce module ne redéveloppe ni le filtrage (Phase 1) ni le détecteur de drift
(Phase 2) : il les branche.

── Les trois signaux et leur rôle ───────────────────────────────────────
  1. SEUIL (confirmation). Occupation estimée >= 80 % : alerte ; >= 90 % :
     saturation. C'est le seul signal qui peut déclarer un prestataire
     saturé, donc le seul qui peut l'écarter d'une recommandation.
  2. RÉSERVATIONS PROVISOIRES (suivi de la recommandation à l'arrivée). Une
     recommandation acceptée compte comme une place occupée dès qu'elle est
     émise, avant que le patient n'arrive. Sans elles, le système ne voit
     les patients qu'il a lui-même envoyés qu'une fois arrivés : c'est
     exactement le retard qui crée la boucle de rétroaction.
  3. TENDANCE (anticipation). Page-Hinkley (réglages de la Phase 2) sur la
     série de disponibilité. Un drift à la baisse met le prestataire en
     ALERT avant le seuil de 80 %, jamais en SATURATED : une tendance n'est
     pas une preuve.

Le temps est en secondes (float), sur une origine quelconque mais commune à
tous les appels.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Optional, Tuple

from .drift_detection import PageHinkleyDriftDetector
from .events import Event, EventType
from .noise_filter import DEFAULT_EWMA_TAU_SECONDS, EventStatus, NoiseFilterPipeline

ALERT_THRESHOLD = 0.80
SATURATION_THRESHOLD = 0.90
# Les deux seuils viennent du papier (section 3.8 : "80-90 %"). Ils ne sont
# pas réglés.

DEFAULT_READMISSION_MARGIN = 0.15
# Hystérésis. Un prestataire SATURATED le reste jusqu'à ce que son
# occupation estimée repasse SOUS 90 % - marge ; un prestataire en ALERT le
# reste jusqu'à 80 % - marge. Sans marge, un prestataire à 89-91 %
# changerait d'état à chaque mise à jour, et le Top 3 avec lui.
# Réglé sur les seeds 100/101/102 (`run_feedback_tuning.py`), grille
# {0.05, 0.10, 0.15, 0.20} : voir results/feedback_tuning_report.json.
# Effet faible : le taux de refus moyen va de 2,5 % (marge 0,05) à 2,2 %
# (marge 0,20). La marge sert d'abord à stabiliser le Top 3.

DEFAULT_RESERVATION_TTL_SECONDS = 90 * 60.0
# Durée de vie d'une réservation provisoire sans nouvelle du patient. Passé
# ce délai, le patient est considéré comme ne venant pas et la place est
# rendue. Trop court : la réservation expire avant l'arrivée d'un patient
# qui vient de loin, et la place est comptée libre à tort. Trop long : un
# patient qui ne viendra jamais bloque une place.
# Réglé sur les seeds 100/101/102, grille {30, 60, 90, 120} minutes. C'est
# le seul des trois paramètres réglés qui compte vraiment : taux de refus
# moyen de 4,0 % à 30 min, 2,2 % à 60, 1,7 % à 90, 1,6 % à 120. Le trajet
# simulé vers une autre ville dure jusqu'à 120 min : une réservation plus
# courte que le trajet expire avant l'arrivée. 120 min, bord de la grille,
# donne le moins de refus mais la plus forte perte de note ; la règle de
# choix (voir `run_feedback_tuning.py`) retient 90.

DRIFT_HOLD_SECONDS = 30 * 60.0
# Durée pendant laquelle un drift à la baisse signalé par Page-Hinkley
# maintient le prestataire en ALERT. Page-Hinkley se réinitialise après
# chaque détection et reste aveugle 30 points (~65 min, Phase 2) : sans
# maintien, le signal ne durerait qu'un seul point. 30 minutes = 1,5 x la
# constante de temps de l'EWMA. Valeur fixée, non réglée.

DRIFT_MIN_OCCUPANCY = 0.60
# Le signal de tendance n'est pris en compte que si l'occupation estimée
# atteint déjà 60 % : une baisse de disponibilité chez un prestataire aux
# deux tiers vide n'annonce pas une saturation. 20 points sous le seuil
# d'alerte. Valeur fixée, non réglée.

DRIFT_DIRECTION_WINDOW = 30
# Page-Hinkley est en mode "both" (Phase 2) : le sens du drift se lit sur
# la série. Baisse = la valeur courante est sous la moyenne des 30 points
# précédents (30 = `PH_MIN_INSTANCES`, la fenêtre de référence du détecteur).

RESERVATION_MIN_WEIGHT = 0.05
# Une réservation en cours d'extinction (voir `arrival_fade_tau_seconds`)
# est supprimée sous ce poids, soit 3 constantes de temps après l'arrivée.


class ProviderStatus(str, Enum):
    normal = "normal"
    alert = "alert"
    saturated = "saturated"


@dataclass
class SaturationConfig:
    alert_threshold: float = ALERT_THRESHOLD
    saturation_threshold: float = SATURATION_THRESHOLD
    readmission_margin: float = DEFAULT_READMISSION_MARGIN

    use_reservations: bool = True
    reservation_ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS
    arrival_fade_tau_seconds: float = DEFAULT_EWMA_TAU_SECONDS
    # Que devient une réservation quand le patient arrive ? Il occupe alors
    # une vraie place, que le prestataire publie. Mais la disponibilité
    # lissée n'absorbe cette place qu'avec la constante de temps de l'EWMA :
    # rendre la réservation d'un coup ferait disparaître le patient des
    # comptes pendant ~20 minutes. Le poids de la réservation décroît donc
    # en exp(-dt / tau) après l'arrivée, à la vitesse où l'EWMA prend le
    # relais. Avec une disponibilité non lissée (flux brut), mettre 0 : la
    # réservation est rendue à l'arrivée.

    use_drift_signal: bool = True
    drift_hold_seconds: float = DRIFT_HOLD_SECONDS
    drift_min_occupancy: float = DRIFT_MIN_OCCUPANCY


@dataclass
class _Reservation:
    provider_id: str
    created_at: float
    arrived_at: Optional[float] = None


@dataclass
class _ProviderTracking:
    capacity: float
    free_places: Optional[float] = None
    # Dernière disponibilité observée. None = jamais observée : le
    # prestataire est alors supposé entièrement libre (seules les
    # réservations comptent).
    saturation_latch: bool = False
    alert_latch: bool = False
    drift_until: float = -math.inf
    detector: Optional[PageHinkleyDriftDetector] = None
    recent: Deque[float] = field(default_factory=lambda: deque(maxlen=DRIFT_DIRECTION_WINDOW))
    reservations: Dict[str, _Reservation] = field(default_factory=dict)
    status: ProviderStatus = ProviderStatus.normal


class SaturationMonitor:
    """
    État de saturation de chaque prestataire.

    Occupation estimée = (capacité - places libres observées + réservations
    provisoires) / capacité.

    La capacité est une donnée de référence du prestataire (nombre de
    places), connue du système. Les places libres viennent du flux de
    disponibilité, via `observe_availability`.
    """

    def __init__(self, config: Optional[SaturationConfig] = None):
        self.config = config or SaturationConfig()
        self._providers: Dict[str, _ProviderTracking] = {}
        self._reservation_owner: Dict[str, str] = {}
        # Journaux, pour l'évaluation uniquement.
        self.transitions: List[Tuple[float, str, ProviderStatus]] = []
        self.drift_alerts: List[Tuple[float, str]] = []

    # ── Référentiel ──────────────────────────────────────────────────────

    def register_provider(self, provider_id: str, capacity: float) -> None:
        if capacity <= 0:
            raise ValueError(f"capacité invalide pour {provider_id}: {capacity}")
        detector = PageHinkleyDriftDetector() if self.config.use_drift_signal else None
        self._providers[provider_id] = _ProviderTracking(capacity=float(capacity), detector=detector)

    def __contains__(self, provider_id: str) -> bool:
        return provider_id in self._providers

    # ── Flux de disponibilité ────────────────────────────────────────────

    def observe_availability(self, provider_id: str, free_places: float, now: float) -> ProviderStatus:
        """Nouvelle valeur de disponibilité (places libres) d'un prestataire."""
        p = self._providers[provider_id]
        p.free_places = float(free_places)
        if p.detector is not None:
            drift = p.detector.update(p.free_places)
            if drift and len(p.recent) >= 2:
                falling = p.free_places < sum(p.recent) / len(p.recent)
                if falling:
                    p.drift_until = now + self.config.drift_hold_seconds
                    self.drift_alerts.append((now, provider_id))
                else:
                    # Disponibilité qui remonte : le signal d'anticipation tombe.
                    p.drift_until = -math.inf
            p.recent.append(p.free_places)
        return self._evaluate(provider_id, p, now)

    # ── Suivi des recommandations ────────────────────────────────────────

    def reserve(self, recommendation_id: str, provider_id: str, now: float) -> None:
        """Le patient a accepté la recommandation : une place provisoire."""
        if not self.config.use_reservations:
            return
        p = self._providers[provider_id]
        p.reservations[recommendation_id] = _Reservation(provider_id, created_at=now)
        self._reservation_owner[recommendation_id] = provider_id
        self._evaluate(provider_id, p, now)

    def arrived(self, recommendation_id: str, now: float) -> None:
        """Le patient est arrivé : sa place est désormais dans la
        disponibilité publiée, la réservation s'éteint (voir
        `arrival_fade_tau_seconds`)."""
        provider_id = self._reservation_owner.get(recommendation_id)
        if provider_id is None:
            return
        p = self._providers[provider_id]
        reservation = p.reservations.get(recommendation_id)
        if reservation is None:
            return
        if self.config.arrival_fade_tau_seconds > 0:
            reservation.arrived_at = now
        else:
            self._drop(p, recommendation_id)
        self._evaluate(provider_id, p, now)

    def cancel(self, recommendation_id: str, now: float) -> None:
        """Annulation, ou patient refusé à l'arrivée : la place est rendue."""
        provider_id = self._reservation_owner.get(recommendation_id)
        if provider_id is None:
            return
        p = self._providers[provider_id]
        self._drop(p, recommendation_id)
        self._evaluate(provider_id, p, now)

    def _drop(self, p: _ProviderTracking, recommendation_id: str) -> None:
        p.reservations.pop(recommendation_id, None)
        self._reservation_owner.pop(recommendation_id, None)

    def pending_load(self, provider_id: str, now: float) -> float:
        """Charge en attente (en places) : réservations non expirées, plus la
        part résiduelle des réservations dont le patient vient d'arriver."""
        return self._pending_load(self._providers[provider_id], now)

    def _pending_load(self, p: _ProviderTracking, now: float) -> float:
        if not p.reservations:
            return 0.0
        ttl = self.config.reservation_ttl_seconds
        tau = self.config.arrival_fade_tau_seconds
        load = 0.0
        expired = []
        for rec_id, r in p.reservations.items():
            if r.arrived_at is None:
                if now - r.created_at >= ttl:
                    expired.append(rec_id)  # le patient n'est pas venu
                else:
                    load += 1.0
            else:
                weight = math.exp(-(now - r.arrived_at) / tau)
                if weight < RESERVATION_MIN_WEIGHT:
                    expired.append(rec_id)
                else:
                    load += weight
        for rec_id in expired:
            self._drop(p, rec_id)
        return load

    # ── Occupation et état ───────────────────────────────────────────────

    def occupancy(self, provider_id: str, now: float) -> float:
        return self._occupancy(self._providers[provider_id], now)

    def _occupancy(self, p: _ProviderTracking, now: float) -> float:
        # Une disponibilité publiée hors de [0, capacité] (valeur aberrante
        # passée au travers) est ramenée dans l'intervalle.
        free = p.capacity if p.free_places is None else min(max(p.free_places, 0.0), p.capacity)
        return (p.capacity - free + self._pending_load(p, now)) / p.capacity

    def status(self, provider_id: str, now: float) -> ProviderStatus:
        """État courant. Réévalue l'occupation : une réservation a pu expirer
        depuis le dernier événement."""
        return self._evaluate(provider_id, self._providers[provider_id], now)

    def tick(self, now: float) -> Dict[str, ProviderStatus]:
        """Réévaluation périodique de tous les prestataires."""
        return {pid: self._evaluate(pid, p, now) for pid, p in self._providers.items()}

    def _evaluate(self, provider_id: str, p: _ProviderTracking, now: float) -> ProviderStatus:
        cfg = self.config
        occupancy = self._occupancy(p, now)

        # Deux bascules à hystérésis, indépendantes. Les seuils de sortie
        # sont arrondis : 0.8 - 0.1 vaut 0.7000000000000001 en flottant.
        if occupancy >= cfg.saturation_threshold:
            p.saturation_latch = True
        elif occupancy < round(cfg.saturation_threshold - cfg.readmission_margin, 9):
            p.saturation_latch = False
        if occupancy >= cfg.alert_threshold:
            p.alert_latch = True
        elif occupancy < round(cfg.alert_threshold - cfg.readmission_margin, 9):
            p.alert_latch = False

        trend = cfg.use_drift_signal and now < p.drift_until and occupancy >= cfg.drift_min_occupancy

        if p.saturation_latch:
            status = ProviderStatus.saturated
        elif p.alert_latch or trend:
            status = ProviderStatus.alert
        else:
            status = ProviderStatus.normal

        if status != p.status:
            p.status = status
            self.transitions.append((now, provider_id, status))
        return status


# ═════════════════════════════════════════════════════════════════════════
# Branchement sur le flux d'événements
# ═════════════════════════════════════════════════════════════════════════


class FilteredAvailabilityFeed:
    """Flux de disponibilité FILTRÉ : chaque événement passe par le
    `NoiseFilterPipeline` de la Phase 1, inchangé ; seule la disponibilité
    lissée des mises à jour acceptées atteint le moniteur."""

    def __init__(self, monitor: SaturationMonitor, pipeline: Optional[NoiseFilterPipeline] = None):
        self.monitor = monitor
        self.pipeline = pipeline or NoiseFilterPipeline()

    def process(self, event: Event, now: float) -> None:
        result = self.pipeline.process_event(event)
        if result.status == EventStatus.accepted and result.smoothed_availability is not None:
            self.monitor.observe_availability(event.provider_id, result.smoothed_availability, now)


class RawAvailabilityFeed:
    """Flux de disponibilité BRUT : la dernière valeur reçue fait foi, sans
    dédoublonnage, sans filtre de confirmation, sans contrôle d'horodatage
    ni d'anomalie, sans lissage. Sert de point de comparaison pour mesurer
    l'apport de la Phase 1."""

    def __init__(self, monitor: SaturationMonitor):
        self.monitor = monitor

    def process(self, event: Event, now: float) -> None:
        if event.event_type != EventType.availability_update:
            return
        value = event.payload.get(NoiseFilterPipeline.AVAILABILITY_PAYLOAD_KEY)
        if value is not None:
            self.monitor.observe_availability(event.provider_id, float(value), now)
