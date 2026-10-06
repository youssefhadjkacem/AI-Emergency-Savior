"""
Simulateur de la boucle de rétroaction — section 3.8 du papier, Phase 3.

Ce que le simulateur met en scène : le système recommande des prestataires,
les patients y vont, ces prestataires se remplissent, et leur disponibilité
— que le système observe ensuite — a changé PARCE QUE le système les a
recommandés. C'est la boucle "performative" du titre du papier.

── Le monde simulé (vérité terrain) ─────────────────────────────────────
  - Prestataires : ceux de la base K2 (`K2_Medical_Providers.xlsx`), avec
    leur spécialité et leur ville. La base ne contient PAS de capacité :
    voir `capacity_from_weekly_slots`.
  - Charge de fond : chaque prestataire reçoit des patients qui ne passent
    pas par le système (processus de Poisson), identiques dans toutes les
    conditions comparées.
  - Patients du système : arrivent selon un processus de Poisson, reçoivent
    un Top 3, choisissent un prestataire, se déplacent, puis occupent une
    place pendant une durée de service — ou sont refusés s'il n'y en a plus.
  - L'occupation réelle de chaque prestataire est la VÉRITÉ TERRAIN. Elle
    n'est jamais transmise au système.

── Ce que voit le système ───────────────────────────────────────────────
  - Le flux de disponibilité publié par chaque prestataire : son nombre de
    places libres réel, recouvert du bruit de la Phase 1 (doublons, mises à
    jour non confirmées, valeurs périmées, valeurs aberrantes), aux mêmes
    taux que `SimulatorConfig` et avec la même structure de cycles que
    `drift_simulator._provider_arrivals`.
  - Le choix de chaque patient (place provisoire), son arrivée ou son refus.

── Reproductibilité ─────────────────────────────────────────────────────
Tout l'aléa est tiré AVANT la simulation (`build_world`) : patients, charge
de fond, calendrier et bruit des publications. Les conditions comparées
rejouent donc exactement les mêmes patients, avec les mêmes tirages ; seule
la recommandation change.
"""

from __future__ import annotations

import bisect
import heapq
import math
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .adaptation import DEFAULT_ALERT_PENALTY, AdaptiveRanker, Candidate, SpaceRankingSource
from .drift_simulator import DEFAULT_START_TIME
from .events import EventType, ProviderType, make_event
from .noise_filter import DEFAULT_EWMA_TAU_SECONDS
from .saturation import (
    DEFAULT_READMISSION_MARGIN,
    DEFAULT_RESERVATION_TTL_SECONDS,
    FilteredAvailabilityFeed,
    ProviderStatus,
    RawAvailabilityFeed,
    SaturationConfig,
    SaturationMonitor,
)
from .simulator import SimulatorConfig

_PHASE1_DEFAULTS = SimulatorConfig()

# ═════════════════════════════════════════════════════════════════════════
# Hypothèses du monde simulé
# ═════════════════════════════════════════════════════════════════════════

WORKING_DAYS_PER_WEEK = 5


def capacity_from_weekly_slots(weekly_slots: float) -> int:
    """
    HYPOTHÈSE DE CAPACITÉ. La base K2 ne donne pas de capacité : elle donne
    `Créneaux/sem.` (15 à 60), un délai de rendez-vous et un nombre de lits
    par établissement, mais rien qui dise combien de patients un médecin
    peut prendre en même temps.

    Capacité (places simultanées) = créneaux par semaine / 5 jours ouvrés,
    arrondi, soit 3 à 12 places. Elle est donc déterministe, tirée de la
    base, et identique pour toutes les conditions et toutes les seeds.
    L'ordre de grandeur (3-12) est celui des disponibilités des Phases 1 et
    2 (4 à 15 places).
    """
    return max(1, int(round(float(weekly_slots) / WORKING_DAYS_PER_WEEK)))


LOAD_LEVELS: Dict[str, float] = {"low": 50.0, "medium": 200.0, "high": 600.0}
# Patients du système par heure, toutes spécialités confondues. Fixés sur
# les seeds de réglage (100/101/102) à partir de la condition statique, pour
# couvrir trois régimes : 1 %, 8 % et 36 % des patients envoyés vers un
# prestataire saturé. Rapportés à la capacité totale (3 821 places, service
# d'une heure), ils restent faibles — 16 % à forte charge : c'est la
# concentration des recommandations, pas le volume, qui sature.

SEVERITY_DISTRIBUTION: Dict[str, float] = {"LOW": 0.30, "MEDIUM": 0.45, "HIGH": 0.20, "CRITICAL": 0.05}
# Hypothèse : une minorité de cas graves. Aucune donnée réelle de
# répartition n'est disponible.


@dataclass
class FeedbackConfig:
    seed: int = 42
    arrivals_per_hour: float = LOAD_LEVELS["medium"]

    warmup_hours: float = 3.0
    # Avant le premier patient du système : le temps que la charge de fond
    # atteigne son régime et que les filtres et détecteurs aient leurs
    # premières valeurs (Page-Hinkley attend 30 points, ~65 min).
    duration_hours: float = 10.0
    # Une journée de consultation, 08:00-18:00.

    specialties: Optional[Tuple[str, ...]] = None  # None = les 22 spécialités

    # ── Patients ─────────────────────────────────────────────────────────
    follow_probabilities: Tuple[float, float, float] = (0.70, 0.20, 0.10)
    # Suivi des recommandations : le patient choisit le n°1 avec une
    # probabilité de 0,70, le n°2 avec 0,20, le n°3 avec 0,10. Hypothèse :
    # la première position d'une liste attire la majorité des choix. La
    # sensibilité à ce paramètre est mesurée dans l'évaluation (0,50 et 0,90).
    no_show_rate: float = 0.10
    # Part des patients qui acceptent une recommandation et ne viennent pas.
    travel_same_city_minutes: Tuple[float, float] = (10.0, 40.0)
    travel_other_city_minutes: Tuple[float, float] = (40.0, 120.0)
    # Délai entre la recommandation et l'arrivée, loi uniforme. La base ne
    # donne que la ville des médecins, pas de distance : deux cas seulement.
    service_mean_minutes: float = 60.0
    service_sigma: float = 0.5
    # Durée d'occupation d'une place (attente + consultation), loi
    # log-normale de moyenne 60 min et de paramètre de forme 0,5 (médiane
    # 53 min, 95e centile ~2 h).
    severity_distribution: Dict[str, float] = field(default_factory=lambda: dict(SEVERITY_DISTRIBUTION))

    # ── Charge de fond ───────────────────────────────────────────────────
    background_utilisation: Tuple[float, float] = (0.15, 0.45)
    # Occupation moyenne visée par les patients hors système, tirée par
    # prestataire dans cet intervalle (loi uniforme). Elle fait qu'un
    # prestataire n'est pas vide quand le système ne lui envoie personne.

    # ── Publication de la disponibilité (bruit de la Phase 1) ────────────
    update_interval_seconds: float = 90.0  # comme en Phase 2
    duplicate_rate: float = _PHASE1_DEFAULTS.duplicate_rate
    unconfirmed_rate: float = _PHASE1_DEFAULTS.unconfirmed_rate
    stale_rate: float = _PHASE1_DEFAULTS.stale_rate
    stale_min_hours: float = _PHASE1_DEFAULTS.stale_min_hours
    stale_max_hours: float = _PHASE1_DEFAULTS.stale_max_hours
    outlier_rate: float = _PHASE1_DEFAULTS.outlier_rate

    sample_interval_seconds: float = 60.0
    # Pas d'échantillonnage de l'occupation réelle et de l'état du système,
    # et période de réévaluation des états (`SaturationMonitor.tick`).

    @property
    def warmup_seconds(self) -> float:
        return self.warmup_hours * 3600.0

    @property
    def end_seconds(self) -> float:
        return (self.warmup_hours + self.duration_hours) * 3600.0


# ═════════════════════════════════════════════════════════════════════════
# Monde pré-tiré
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Patient:
    patient_id: int
    request_time: float
    specialty: str
    city: str
    severity: str
    choice_draw: float        # uniforme [0, 1) : rang choisi dans le Top 3
    travel_same_city: float   # secondes
    travel_other_city: float
    no_show: bool
    service_seconds: float


# Types d'événements de publication.
_PUB_ISSUED, _PUB_OUTCOME_CONFIRMED, _PUB_OUTCOME_ARRIVED, _PUB_UPDATE = 0, 1, 2, 3
_PUB_EVENT_TYPE = {
    _PUB_ISSUED: EventType.recommendation_issued,
    _PUB_OUTCOME_CONFIRMED: EventType.patient_confirmed,
    _PUB_OUTCOME_ARRIVED: EventType.patient_arrived,
    _PUB_UPDATE: EventType.availability_update,
}
_MODE_NORMAL, _MODE_OUTLIER, _MODE_STALE = 0, 1, 2

# Types d'événements de la simulation.
_EV_BACKGROUND, _EV_DEPARTURE, _EV_PUBLICATION, _EV_REQUEST, _EV_ARRIVAL, _EV_SAMPLE = range(6)


@dataclass
class World:
    config: FeedbackConfig
    provider_ids: List[str]
    capacity: List[int]
    specialty: List[str]
    city: List[str]
    patients: List[Patient]
    static_events: List[tuple]   # triés par date : fond, publications, demandes, échantillons
    n_publications: int

    def index(self) -> Dict[str, int]:
        return {pid: i for i, pid in enumerate(self.provider_ids)}


def _lognormal_seconds(rng: random.Random, mean_minutes: float, sigma: float) -> float:
    mu = math.log(mean_minutes * 60.0) - sigma * sigma / 2.0
    return rng.lognormvariate(mu, sigma)


def _publication_schedule(provider_index: int, provider_id: str, config: FeedbackConfig,
                          rng: random.Random, next_entry_id: List[int]) -> List[tuple]:
    """Calendrier des publications d'un prestataire : cycles recommandation
    -> issue -> 1 à 3 mises à jour, avec le bruit de la Phase 1 (même
    structure que `drift_simulator._provider_arrivals`). Seules les VALEURS
    manquent : elles dépendent de l'occupation réelle au moment de l'envoi
    et sont calculées pendant la simulation."""
    entries: List[tuple] = []
    clock = rng.uniform(0, config.update_interval_seconds)

    def push(kind: int, rec_id: str, at: float, mode: int = _MODE_NORMAL, stale_seconds: float = 0.0) -> None:
        entry_id = next_entry_id[0]
        next_entry_id[0] += 1
        # (date de réception, prestataire, type, rec_id, mode, ancienneté, id, id de l'original, retard du renvoi)
        entries.append((at, provider_index, kind, rec_id, mode, stale_seconds, entry_id, -1, 0.0))
        if rng.random() < config.duplicate_rate:
            for _ in range(rng.choice([1, 2])):  # retry réseau : même contenu
                delay = rng.uniform(0.5, 4.0)
                dup_id = next_entry_id[0]
                next_entry_id[0] += 1
                entries.append((at + delay, provider_index, kind, rec_id, mode, stale_seconds, dup_id, entry_id, delay))

    rec_counter = 0
    while clock < config.end_seconds:
        rec_counter += 1
        rec_id = f"{provider_id}-cyc-{rec_counter:05d}"
        push(_PUB_ISSUED, rec_id, clock)
        if rng.random() >= config.unconfirmed_rate:
            clock += rng.uniform(5.0, 30.0)
            push(rng.choice([_PUB_OUTCOME_CONFIRMED, _PUB_OUTCOME_ARRIVED]), rec_id, clock)
        n_updates = rng.randint(1, 3)
        is_outlier_cycle = rng.random() < config.outlier_rate
        for j in range(n_updates):
            clock += config.update_interval_seconds * rng.uniform(0.4, 1.8)
            if clock >= config.end_seconds:
                break
            if is_outlier_cycle and j == 1:
                push(_PUB_UPDATE, rec_id, clock, _MODE_OUTLIER)
            elif rng.random() < config.stale_rate:
                hours = rng.uniform(config.stale_min_hours, config.stale_max_hours)
                push(_PUB_UPDATE, rec_id, clock, _MODE_STALE, hours * 3600.0)
            else:
                push(_PUB_UPDATE, rec_id, clock)
        clock += rng.uniform(5.0, 30.0)
    return entries


def build_world(config: FeedbackConfig, source: SpaceRankingSource) -> World:
    """Tire tout l'aléa d'une simulation. Deux appels avec la même
    configuration renvoient le même monde."""
    rng = random.Random(config.seed)
    providers = source.providers
    specialties = list(config.specialties) if config.specialties else source.specialties()
    providers = providers[providers["specialty"].isin(specialties)].sort_values("ID")

    provider_ids = providers["ID"].tolist()
    capacity = [capacity_from_weekly_slots(s) for s in providers["available_slots"]]
    specialty = providers["specialty"].tolist()
    city = [str(c) for c in providers["location"]]

    # Un générateur dérivé par usage : changer le taux d'arrivée ne décale
    # ni la charge de fond ni le bruit des publications.
    patient_rng = random.Random(rng.getrandbits(64))
    background_rng = random.Random(rng.getrandbits(64))
    publication_rng = random.Random(rng.getrandbits(64))

    # ── Patients du système ──────────────────────────────────────────────
    # Ville : proportionnelle au nombre de médecins de la ville dans la base
    # (substitut de la population ; Tunis ~17 %). Spécialité : uniforme.
    city_counts = providers["location"].astype(str).value_counts().sort_index()
    cities, city_weights = city_counts.index.tolist(), city_counts.values.tolist()
    severities, severity_weights = zip(*sorted(config.severity_distribution.items()))

    patients: List[Patient] = []
    t = config.warmup_seconds
    rate_per_second = config.arrivals_per_hour / 3600.0
    while True:
        t += patient_rng.expovariate(rate_per_second)
        if t >= config.end_seconds:
            break
        patients.append(Patient(
            patient_id=len(patients),
            request_time=t,
            specialty=patient_rng.choice(specialties),
            city=patient_rng.choices(cities, city_weights)[0],
            severity=patient_rng.choices(severities, severity_weights)[0],
            choice_draw=patient_rng.random(),
            travel_same_city=60.0 * patient_rng.uniform(*config.travel_same_city_minutes),
            travel_other_city=60.0 * patient_rng.uniform(*config.travel_other_city_minutes),
            no_show=patient_rng.random() < config.no_show_rate,
            service_seconds=_lognormal_seconds(patient_rng, config.service_mean_minutes, config.service_sigma),
        ))

    events: List[tuple] = []
    seq = 0

    # ── Charge de fond ───────────────────────────────────────────────────
    mean_service = config.service_mean_minutes * 60.0
    for i in range(len(provider_ids)):
        provider_rng = random.Random(background_rng.getrandbits(64))
        utilisation = provider_rng.uniform(*config.background_utilisation)
        rate = utilisation * capacity[i] / mean_service
        t = 0.0
        while True:
            t += provider_rng.expovariate(rate)
            if t >= config.end_seconds:
                break
            service = _lognormal_seconds(provider_rng, config.service_mean_minutes, config.service_sigma)
            events.append((t, seq, _EV_BACKGROUND, i, service))
            seq += 1

    # ── Publications ─────────────────────────────────────────────────────
    next_entry_id = [0]
    for i, pid in enumerate(provider_ids):
        provider_rng = random.Random(publication_rng.getrandbits(64))
        for entry in _publication_schedule(i, pid, config, provider_rng, next_entry_id):
            events.append((entry[0], seq, _EV_PUBLICATION, entry, None))
            seq += 1

    for patient in patients:
        events.append((patient.request_time, seq, _EV_REQUEST, patient, None))
        seq += 1

    t = config.warmup_seconds
    while t < config.end_seconds:
        events.append((t, seq, _EV_SAMPLE, None, None))
        seq += 1
        t += config.sample_interval_seconds

    events.sort(key=lambda e: (e[0], e[1]))
    return World(config, provider_ids, capacity, specialty, city, patients, events, next_entry_id[0])


# ═════════════════════════════════════════════════════════════════════════
# Conditions comparées
# ═════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Condition:
    name: str
    label: str
    adaptive: bool = True
    feed: Optional[str] = "filtered"   # "filtered" (Phase 1), "raw", ou None
    use_reservations: bool = True
    use_drift_signal: bool = True


CONDITIONS: Dict[str, Condition] = {
    "A": Condition("A", "Statique", adaptive=False, feed=None, use_reservations=False, use_drift_signal=False),
    "B": Condition("B", "Seuil seul", use_reservations=False, use_drift_signal=False),
    "C": Condition("C", "Système complet"),
    "D": Condition("D", "Complet sur flux brut", feed="raw"),
    "E": Condition("E", "Complet sans réservations", use_reservations=False),
    "F": Condition("F", "Complet sans Page-Hinkley", use_drift_signal=False),
}


@dataclass(frozen=True)
class SystemParams:
    """Les trois paramètres réglés sur les seeds 100/101/102."""

    readmission_margin: float = DEFAULT_READMISSION_MARGIN
    reservation_ttl_seconds: float = DEFAULT_RESERVATION_TTL_SECONDS
    alert_penalty: float = DEFAULT_ALERT_PENALTY


# ═════════════════════════════════════════════════════════════════════════
# Journal d'une simulation
# ═════════════════════════════════════════════════════════════════════════


@dataclass
class PatientRecord:
    patient: Patient
    base_top: Tuple[Candidate, ...]      # Top 3 du classement statique
    top: Tuple[Candidate, ...]           # Top 3 effectivement recommandé
    chosen: Candidate
    chosen_rank: int
    all_saturated: bool
    saturated_in_top: int                # prestataires saturés (selon le système) laissés dans le Top 3
    outcome: str = "pending"             # served / refused / no_show / pending (fin de simulation)
    arrival_time: Optional[float] = None
    occupancy_at_arrival: Optional[float] = None   # occupation RÉELLE du prestataire choisi, avant ce patient


STATUS_CODE = {ProviderStatus.normal: 0, ProviderStatus.alert: 1, ProviderStatus.saturated: 2}


@dataclass
class RunLog:
    world: World
    condition: Condition
    params: SystemParams
    records: List[PatientRecord]
    sample_times: List[float]
    occupied: List[List[int]]            # [échantillon][prestataire] places réellement occupées
    en_route: List[List[int]]            # patients du système en chemin qui arriveront
    system_status: List[List[int]]       # état vu par le système (vide en condition A)
    drift_alerts: List[Tuple[float, str]]


def run_condition(world: World, source: SpaceRankingSource, condition: Condition,
                  params: Optional[SystemParams] = None) -> RunLog:
    """Rejoue le monde `world` sous une condition."""
    config = world.config
    params = params or SystemParams()
    n = len(world.provider_ids)
    index = world.index()
    capacity = world.capacity

    # ── Le système ───────────────────────────────────────────────────────
    monitor = None
    feed = None
    if condition.adaptive:
        monitor = SaturationMonitor(SaturationConfig(
            readmission_margin=params.readmission_margin,
            use_reservations=condition.use_reservations,
            reservation_ttl_seconds=params.reservation_ttl_seconds,
            # Flux brut : pas de lissage, donc pas d'extinction progressive.
            arrival_fade_tau_seconds=DEFAULT_EWMA_TAU_SECONDS if condition.feed == "filtered" else 0.0,
            use_drift_signal=condition.use_drift_signal,
        ))
        for pid, cap in zip(world.provider_ids, capacity):
            monitor.register_provider(pid, cap)
        feed = FilteredAvailabilityFeed(monitor) if condition.feed == "filtered" else RawAvailabilityFeed(monitor)
    ranker = AdaptiveRanker(source, monitor, alert_penalty=params.alert_penalty)
    static_ranker = AdaptiveRanker(source, None)

    # ── La vérité terrain ────────────────────────────────────────────────
    occupied = [0] * n
    en_route = [0] * n
    history_t: List[List[float]] = [[0.0] for _ in range(n)]   # pour les valeurs périmées
    history_free: List[List[int]] = [[capacity[i]] for i in range(n)]
    published_values: Dict[int, float] = {}

    def set_occupied(i: int, value: int, now: float) -> None:
        occupied[i] = value
        history_t[i].append(now)
        history_free[i].append(capacity[i] - value)

    def free_at(i: int, when: float) -> int:
        return history_free[i][max(0, bisect.bisect_right(history_t[i], when) - 1)]

    records: List[PatientRecord] = []
    log = RunLog(world, condition, params, records, [], [], [], [], [])
    dynamic: List[tuple] = []
    seq = len(world.static_events)
    p1, p2, _ = config.follow_probabilities

    static = world.static_events
    cursor = 0
    while cursor < len(static) or dynamic:
        if dynamic and (cursor >= len(static) or dynamic[0][0] <= static[cursor][0]):
            now, _, kind, a, b = heapq.heappop(dynamic)
        else:
            now, _, kind, a, b = static[cursor]
            cursor += 1
        if now >= config.end_seconds:
            break

        if kind == _EV_PUBLICATION:
            if feed is None:
                continue
            _, i, pub_kind, rec_id, mode, stale_seconds, entry_id, original_id, delay = a
            payload = None
            timestamp = now - stale_seconds
            if pub_kind == _PUB_UPDATE:
                if original_id >= 0:
                    value = published_values[original_id]
                else:
                    free = capacity[i] - occupied[i]
                    if mode == _MODE_OUTLIER:
                        # Comme en Phase 2 : chute à 0, ou bond de +10 près de 0.
                        value = 0 if free >= 5 else free + 10
                    elif mode == _MODE_STALE:
                        # Horodatée dans le passé, avec la disponibilité d'alors.
                        value = free_at(i, timestamp)
                    else:
                        value = free
                    published_values[entry_id] = value
                payload = {"available_slots": value}
            event = make_event(
                rec_id, world.provider_ids[i], ProviderType.cabinet, _PUB_EVENT_TYPE[pub_kind],
                DEFAULT_START_TIME + timedelta(seconds=timestamp), payload,
            )
            feed.process(event, now)

        elif kind == _EV_BACKGROUND:
            if occupied[a] < capacity[a]:   # sinon le patient hors système va ailleurs
                set_occupied(a, occupied[a] + 1, now)
                heapq.heappush(dynamic, (now + b, seq, _EV_DEPARTURE, a, None))
                seq += 1

        elif kind == _EV_DEPARTURE:
            set_occupied(a, occupied[a] - 1, now)

        elif kind == _EV_REQUEST:
            patient: Patient = a
            base = static_ranker.recommend(patient.specialty, now, patient.city, patient.severity)
            result = ranker.recommend(patient.specialty, now, patient.city, patient.severity)
            top = tuple(result.top(3))
            rank = 0 if patient.choice_draw < p1 else (1 if patient.choice_draw < p1 + p2 else 2)
            rank = min(rank, len(top) - 1)
            chosen = top[rank]
            record = PatientRecord(patient, tuple(base.top(3)), top, chosen, rank,
                                   result.all_saturated, len(result.saturated_in_top(3)))
            records.append(record)
            rec_id = f"pat-{patient.patient_id:06d}"
            if monitor is not None:
                monitor.reserve(rec_id, chosen.provider_id, now)
            if patient.no_show:
                record.outcome = "no_show"
            else:
                same_city = chosen.city.strip().lower() == patient.city.strip().lower()
                travel = patient.travel_same_city if same_city else patient.travel_other_city
                en_route[index[chosen.provider_id]] += 1
                heapq.heappush(dynamic, (now + travel, seq, _EV_ARRIVAL, record, rec_id))
                seq += 1

        elif kind == _EV_ARRIVAL:
            record: PatientRecord = a
            i = index[record.chosen.provider_id]
            en_route[i] -= 1
            record.arrival_time = now
            record.occupancy_at_arrival = occupied[i] / capacity[i]
            if occupied[i] < capacity[i]:
                record.outcome = "served"
                set_occupied(i, occupied[i] + 1, now)
                heapq.heappush(dynamic, (now + record.patient.service_seconds, seq, _EV_DEPARTURE, i, None))
                seq += 1
                if monitor is not None:
                    monitor.arrived(b, now)
            else:
                record.outcome = "refused"
                if monitor is not None:
                    monitor.cancel(b, now)

        elif kind == _EV_SAMPLE:
            log.sample_times.append(now)
            log.occupied.append(list(occupied))
            log.en_route.append(list(en_route))
            if monitor is not None:
                statuses = monitor.tick(now)
                log.system_status.append([STATUS_CODE[statuses[pid]] for pid in world.provider_ids])

    if monitor is not None:
        log.drift_alerts = list(monitor.drift_alerts)
    return log
