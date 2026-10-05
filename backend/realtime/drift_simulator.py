"""
Simulateur de concept drift — section 3.8 du papier, étape "détection de
drift" (Phase 2). Complète `simulator.py` (Phase 1), qu'il ne remplace pas.

Pourquoi un module séparé plutôt qu'une extension de `simulator.py` : dans
le simulateur de la Phase 1, la disponibilité "normale" d'un provider est
une marche aléatoire (+/-1 à chaque mise à jour). C'est suffisant pour
évaluer le filtrage du bruit, mais une marche aléatoire n'a PAS de moyenne
stable : elle dérive d'elle-même, donc il n'existerait aucun cas de
"non-drift" propre pour mesurer les faux positifs. Ici, la disponibilité
déclarée est tirée autour d'une moyenne latente CONNUE, mu(k), qui est soit
constante (provider stable), soit modifiée par un drift injecté.

── Ce qui est généré ────────────────────────────────────────────────────
Pour chaque provider, un flux d'événements bruts de même nature que celui
de la Phase 1 (recommandation -> confirmation -> availability_update), avec
les 4 mêmes catégories de bruit et, par défaut, les mêmes taux
(`SimulatorConfig`) : doublons, non-confirmés, valeurs périmées, pics
aberrants. Ce flux brut passe ensuite par le `NoiseFilterPipeline` de la
Phase 1, INCHANGÉ (`build_smoothed_series`) : la série sur laquelle on
détecte le drift est la disponibilité lissée (EWMA) qui en sort, pas le
flux brut. Aucune logique de nettoyage n'est redéveloppée ici.

Trois profils de moyenne latente (`DriftType`) :
  - none    : mu constante — fluctuations normales, aucune tendance.
  - gradual : mu passe linéairement de `pre` à `post` sur
              `gradual_transition_updates` mises à jour brutes (saturation
              qui s'installe progressivement).
  - abrupt  : même transition, mais sur `abrupt_transition_updates` mises à
              jour seulement (bascule quasi immédiate).

── Vérité terrain ───────────────────────────────────────────────────────
`DriftGroundTruth` (par provider) et `raw_update_index` (par event_id) ne
vivent QUE dans le `DriftScenario` renvoyé par le simulateur. Les
détecteurs (`drift_detection.py`) ne reçoivent qu'une liste de flottants ;
seule l'évaluation (`drift_evaluation.py`) recolle les deux après coup —
même principe que `GroundTruthLabel` en Phase 1.

L'instant du drift est défini sur l'axe des mises à jour BRUTES du provider
(`drift_start_update`), puis traduit en index dans la série lissée par
`SmoothedSeries.index_of_raw_update` : c'est le premier point lissé dont la
mise à jour d'origine est >= `drift_start_update`. La traduction est
nécessaire car le pipeline rejette une partie des mises à jour (non
confirmées, doublons, quarantaine) : les deux axes ne coïncident pas.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID

from .events import Event, EventType, ProviderType, make_event
from .noise_filter import EventStatus, NoiseFilterPipeline
from .simulator import SimulatorConfig

_PHASE1_DEFAULTS = SimulatorConfig()

# Instant de départ fixe (et non `datetime.now()`) : deux exécutions avec le
# même seed produisent exactement le même flux, timestamps compris.
DEFAULT_START_TIME = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)


class DriftType(str, Enum):
    none = "none"
    gradual = "gradual"
    abrupt = "abrupt"


@dataclass
class DriftSimulatorConfig:
    drift_type: DriftType = DriftType.gradual
    n_drift_providers: int = 20
    n_stable_providers: int = 20
    # Autant de providers stables que de providers driftés : le taux de faux
    # positifs est mesuré sur un échantillon de même taille que le rappel.
    provider_type: ProviderType = ProviderType.hospital

    updates_per_provider: int = 600
    # Mises à jour BRUTES par provider. Après le filtre de confirmation
    # (35% de recommandations non confirmées), il reste ~380 points lissés
    # par provider : assez pour ~200 points de régime stable avant le drift
    # et ~100 après la fin d'un drift progressif.

    update_interval_seconds: float = 90.0
    # Écart moyen entre deux mises à jour d'un même provider. Avec
    # tau = 20 min (Phase 1) et ~35% de mises à jour écartées, l'écart
    # médian mesuré entre deux points ACCEPTÉS est de 129 s, soit un poids
    # EWMA w = exp(-129/1200) ≈ 0.90 : chaque nouveau point pèse ~10% et la
    # série lissée a une mémoire d'environ 10 points. Ce choix fixe l'échelle
    # de temps de toute l'étude : 1 point lissé ≈ 2 min.

    # ── Moyenne latente ──────────────────────────────────────────────────
    pre_mean_min: float = 7.0
    pre_mean_max: float = 10.0
    drop_min: float = 4.0
    drop_max: float = 6.0
    # Drift = baisse de 4 à 6 places depuis une moyenne de 7-10, soit le
    # cas "8 -> 3" du cahier des charges avec un peu de diversité entre
    # providers. Toujours une BAISSE : c'est la saturation qui s'installe.
    stable_mean_min: float = 2.0
    stable_mean_max: float = 10.0
    # Les providers stables couvrent à la fois les niveaux "avant" et
    # "après" drift : un détecteur ne doit pas être évalué uniquement sur
    # des séries stables confortables (moyenne haute, loin du plancher 0).
    noise_std: float = 1.5
    # Écart-type du bruit d'observation autour de mu (places), avant
    # arrondi à l'entier et plancher à 0 : fluctuation normale d'un service
    # dont quelques places se libèrent/s'occupent entre deux déclarations.

    # ── Position et durée du drift (en mises à jour brutes) ────────────────
    drift_start_min: int = 240
    drift_start_max: int = 360
    # Début tiré au hasard par provider dans [40%, 60%] de la série : un
    # détecteur ne peut pas "apprendre" une position fixe, et il reste
    # toujours une longue phase stable avant (mesure des fausses alertes
    # pré-drift) et assez de points après.
    gradual_transition_updates: int = 150
    # ~100 points lissés ≈ 3 h 30 : une saturation qui s'installe sur une
    # demi-journée de garde.
    abrupt_transition_updates: int = 5
    # ~3 points lissés ≈ 7 min : afflux soudain (accident, fermeture d'un
    # service voisin).

    # ── Bruit de la Phase 1 : mêmes taux que `SimulatorConfig` ─────────────
    duplicate_rate: float = _PHASE1_DEFAULTS.duplicate_rate
    unconfirmed_rate: float = _PHASE1_DEFAULTS.unconfirmed_rate
    stale_rate: float = _PHASE1_DEFAULTS.stale_rate
    stale_min_hours: float = _PHASE1_DEFAULTS.stale_min_hours
    stale_max_hours: float = _PHASE1_DEFAULTS.stale_max_hours
    outlier_rate: float = _PHASE1_DEFAULTS.outlier_rate

    start_time: datetime = DEFAULT_START_TIME
    seed: int = 42


@dataclass
class DriftGroundTruth:
    """Vérité terrain d'un provider. `drift_start_update` et
    `drift_end_update` sont exprimés en index de mise à jour BRUTE."""

    provider_id: str
    drift_type: DriftType
    pre_mean: float
    post_mean: float
    drift_start_update: Optional[int] = None
    drift_end_update: Optional[int] = None

    @property
    def has_drift(self) -> bool:
        return self.drift_type != DriftType.none


@dataclass
class DriftScenario:
    config: DriftSimulatorConfig
    # Flux aveugle, dans l'ordre d'arrivée : c'est tout ce que voit le pipeline.
    events: List[Event]
    # ── Vérité terrain (jamais transmise au pipeline ni aux détecteurs) ────
    ground_truth: Dict[str, DriftGroundTruth]
    raw_update_index: Dict[UUID, int]


def latent_mean(gt: DriftGroundTruth, update_index: int) -> float:
    """Moyenne latente mu(k) du provider à la k-ième mise à jour brute."""
    if not gt.has_drift or update_index < gt.drift_start_update:
        return gt.pre_mean
    if update_index >= gt.drift_end_update:
        return gt.post_mean
    progress = (update_index - gt.drift_start_update) / (gt.drift_end_update - gt.drift_start_update)
    return gt.pre_mean + progress * (gt.post_mean - gt.pre_mean)


def _draw_ground_truth(config: DriftSimulatorConfig, rng: random.Random) -> Dict[str, DriftGroundTruth]:
    truth: Dict[str, DriftGroundTruth] = {}
    n_total = config.n_drift_providers + config.n_stable_providers
    # Les providers driftés ne sont pas regroupés en tête de liste : l'ordre
    # est mélangé pour qu'aucune information ne fuite par le provider_id.
    is_drifted = [True] * config.n_drift_providers + [False] * config.n_stable_providers
    rng.shuffle(is_drifted)

    transition = (
        config.gradual_transition_updates
        if config.drift_type == DriftType.gradual
        else config.abrupt_transition_updates
    )
    for i in range(n_total):
        pid = f"prov-{i:03d}"
        if is_drifted[i] and config.drift_type != DriftType.none:
            pre = rng.uniform(config.pre_mean_min, config.pre_mean_max)
            start = rng.randint(config.drift_start_min, config.drift_start_max)
            truth[pid] = DriftGroundTruth(
                provider_id=pid,
                drift_type=config.drift_type,
                pre_mean=pre,
                post_mean=pre - rng.uniform(config.drop_min, config.drop_max),
                drift_start_update=start,
                drift_end_update=start + transition,
            )
        else:
            mean = rng.uniform(config.stable_mean_min, config.stable_mean_max)
            truth[pid] = DriftGroundTruth(provider_id=pid, drift_type=DriftType.none, pre_mean=mean, post_mean=mean)
    return truth


@dataclass
class _Arrival:
    arrival_time: datetime
    event: Event
    raw_update: Optional[int] = None


def _provider_arrivals(
    gt: DriftGroundTruth, config: DriftSimulatorConfig, rng: random.Random
) -> List[_Arrival]:
    """Flux d'un provider : cycles recommandation -> issue -> 1 à 3 mises à
    jour de disponibilité, avec le bruit de la Phase 1 injecté."""
    arrivals: List[_Arrival] = []
    clock = config.start_time + timedelta(seconds=rng.uniform(0, config.update_interval_seconds))
    stale_lag_updates = lambda hours: int(hours * 3600 / config.update_interval_seconds)  # noqa: E731

    def push(event: Event, at: datetime, raw_update: Optional[int] = None) -> None:
        arrivals.append(_Arrival(at, event, raw_update))
        if rng.random() < config.duplicate_rate:
            # Retry réseau : même contenu, quelques secondes plus tard.
            for _ in range(rng.choice([1, 2])):
                delay = timedelta(seconds=rng.uniform(0.5, 4.0))
                retry = make_event(
                    event.recommendation_id, event.provider_id, event.provider_type,
                    event.event_type, event.timestamp + delay, dict(event.payload),
                )
                arrivals.append(_Arrival(at + delay, retry, raw_update))

    k = 0
    rec_counter = 0
    while k < config.updates_per_provider:
        rec_counter += 1
        rec_id = f"{gt.provider_id}-rec-{rec_counter:05d}"
        push(make_event(rec_id, gt.provider_id, config.provider_type, EventType.recommendation_issued, clock), clock)

        confirmed = rng.random() >= config.unconfirmed_rate
        if confirmed:
            clock += timedelta(seconds=rng.uniform(5.0, 30.0))
            outcome = rng.choice([EventType.patient_confirmed, EventType.patient_arrived])
            push(make_event(rec_id, gt.provider_id, config.provider_type, outcome, clock), clock)

        n_updates = rng.randint(1, 3)
        is_outlier_cycle = rng.random() < config.outlier_rate
        for j in range(n_updates):
            if k >= config.updates_per_provider:
                break
            clock += timedelta(seconds=config.update_interval_seconds * rng.uniform(0.4, 1.8))
            mu = latent_mean(gt, k)
            timestamp = clock

            if is_outlier_cycle and j == 1:
                # Pic ponctuel incohérent, comme en Phase 1 : chute à 0, ou
                # bond de +10 si le provider est déjà proche de 0.
                value = 0 if mu >= 5 else int(round(mu)) + 10
            elif rng.random() < config.stale_rate:
                # Mise à jour périmée : horodatée plusieurs heures en
                # arrière, et portant la disponibilité de CE moment-là.
                hours = rng.uniform(config.stale_min_hours, config.stale_max_hours)
                timestamp = clock - timedelta(hours=hours)
                old_mu = latent_mean(gt, max(0, k - stale_lag_updates(hours)))
                value = max(0, int(round(rng.gauss(old_mu, config.noise_std))))
            else:
                value = max(0, int(round(rng.gauss(mu, config.noise_std))))

            update = make_event(
                rec_id, gt.provider_id, config.provider_type, EventType.availability_update,
                timestamp, {"available_slots": value},
            )
            push(update, clock, raw_update=k)
            k += 1

        clock += timedelta(seconds=rng.uniform(5.0, 30.0))
    return arrivals


def generate_drift_scenario(config: Optional[DriftSimulatorConfig] = None) -> DriftScenario:
    """Génère le flux brut de tous les providers (entrelacés dans l'ordre
    d'arrivée) et, séparément, la vérité terrain correspondante."""
    config = config or DriftSimulatorConfig()
    rng = random.Random(config.seed)
    truth = _draw_ground_truth(config, rng)

    arrivals: List[_Arrival] = []
    for pid in sorted(truth):
        # Un générateur dérivé par provider : ajouter ou retirer un provider
        # ne décale pas les tirages des autres.
        provider_rng = random.Random(rng.getrandbits(64))
        arrivals.extend(_provider_arrivals(truth[pid], config, provider_rng))
    arrivals.sort(key=lambda a: a.arrival_time)

    return DriftScenario(
        config=config,
        events=[a.event for a in arrivals],
        ground_truth=truth,
        raw_update_index={a.event.event_id: a.raw_update for a in arrivals if a.raw_update is not None},
    )


# ═════════════════════════════════════════════════════════════════════════
# Passage par le pipeline de la Phase 1 -> séries de disponibilité lissée
# ═════════════════════════════════════════════════════════════════════════


@dataclass
class SmoothedSeries:
    """Disponibilité lissée d'un provider, dans l'ordre d'arrivée.
    `values` est la seule chose transmise aux détecteurs de drift."""

    provider_id: str
    values: List[float] = field(default_factory=list)
    timestamps: List[datetime] = field(default_factory=list)
    event_ids: List[UUID] = field(default_factory=list)

    def index_of_raw_update(self, raw_update: int, raw_update_index: Dict[UUID, int]) -> Optional[int]:
        """Index du premier point lissé issu d'une mise à jour brute
        >= `raw_update` (None si la série s'arrête avant)."""
        for i, event_id in enumerate(self.event_ids):
            if raw_update_index[event_id] >= raw_update:
                return i
        return None


def build_smoothed_series(
    events: List[Event], pipeline: Optional[NoiseFilterPipeline] = None
) -> Dict[str, SmoothedSeries]:
    """Fait passer le flux brut par le `NoiseFilterPipeline` de la Phase 1
    (réglages par défaut, inchangés) et collecte, par provider, la
    disponibilité lissée après chaque mise à jour acceptée."""
    pipeline = pipeline or NoiseFilterPipeline()
    series: Dict[str, SmoothedSeries] = {}
    for event in events:
        result = pipeline.process_event(event)
        if result.status != EventStatus.accepted or result.smoothed_availability is None:
            continue
        s = series.setdefault(event.provider_id, SmoothedSeries(provider_id=event.provider_id))
        s.values.append(result.smoothed_availability)
        s.timestamps.append(event.timestamp)
        s.event_ids.append(event.event_id)
    return series


def simulate_smoothed_series(config: DriftSimulatorConfig):
    """Raccourci : génère un scénario et renvoie (scénario, séries lissées
    par provider) après passage par le pipeline de la Phase 1."""
    scenario = generate_drift_scenario(config)
    return scenario, build_smoothed_series(scenario.events)
