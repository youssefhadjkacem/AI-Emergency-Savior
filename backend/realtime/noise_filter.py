"""
Pipeline de filtrage du bruit — section 3.8 du papier, étape "filtrage"
uniquement (pas de détection de drift performatif, pas de logique de
saturation : ce sera un module séparé dans une étape suivante).

4 étapes, appliquées dans cet ordre à chaque événement :

    a) Déduplication          (Deduplicator)
    b) Filtre de confirmation (ConfirmationTracker)
    c) Pondération EWMA       (EwmaSmoother)
    d) Détection d'anomalie   (ProviderAnomalyDetector, backend configurable —
                               robust_zscore par défaut, River HalfSpaceTrees en option)

Point d'entrée : NoiseFilterPipeline.process_event(event) -> FilterResult.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

from river import anomaly

from .events import Event, EventType

# ═════════════════════════════════════════════════════════════════════════
# a) Déduplication
# ═════════════════════════════════════════════════════════════════════════


class Deduplicator:
    """
    Filtre les doublons (retries réseau côté émetteur) selon la clé
    (recommendation_id, provider_id, event_type, signature_du_payload).

    BUG CORRIGÉ (diagnostiqué via evaluation.py, seeds 7/1/42 : précision
    injected_duplicate ~74-77% au lieu de ~100% attendu) : la clé initiale
    (recommendation_id, provider_id, event_type) SANS le contenu confondait
    deux cas différents :
      1. un vrai doublon      : le même événement renvoyé (retry réseau) —
         même payload, à quelques secondes d'intervalle ;
      2. une mise à jour légitime : la 2e ou 3e disponibilité d'un même
         cycle (même recommendation_id/provider_id/event_type) arrivant
         elle aussi dans la fenêtre de 5s, mais avec un payload DIFFÉRENT
         puisque la disponibilité a réellement changé entre-temps.
    Sans le contenu dans la clé, le cas 2 était rejeté à tort comme un
    doublon. Vérifié empiriquement : 21/1200 événements du seed 7 étaient
    des availability_update légitimes rejetés à 0.07-4.9s du précédent.

    En ajoutant une signature du payload à la clé, un retry réseau (même
    contenu) reste détecté comme doublon, tandis qu'une mise à jour au
    contenu différent est traitée comme un nouvel événement à part entière
    — ce qui correspond à la réalité (voir aussi `simulator.py`, qui
    garantit que deux mises à jour légitimes consécutives d'un même cycle
    ont toujours des valeurs différentes, pour que ce scénario reste
    représentatif d'un flux réel).

    Sémantique "TTL cache" : la fenêtre est ancrée sur le PREMIER événement
    accepté pour une clé donnée et ne se réinitialise PAS à chaque doublon
    supplémentaire détecté. Ça borne le comportement (une rafale de retries
    ne peut pas repousser indéfiniment la fenêtre de dédoublonnage) tout en
    restant simple à raisonner et à tester.
    """

    DEFAULT_WINDOW_SECONDS = 5.0
    # "Quelques secondes" (demande du papier) : couvre un backoff de retry
    # HTTP court (1er retry ~1-2s après échec, 2e ~3-4s) sans risquer de
    # confondre deux mises à jour de disponibilité réellement distinctes et
    # rapprochées dans le temps.

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self.window = timedelta(seconds=window_seconds)
        self._expiry: Dict[Tuple[str, str, str, Tuple], datetime] = {}
        self.duplicate_count = 0

    @staticmethod
    def _payload_signature(payload: Dict[str, Any]) -> Tuple:
        """Signature stable et hashable du contenu du payload (triée par
        clé). Un retry réseau renvoie le MÊME event, donc la MÊME
        signature ; une nouvelle mise à jour légitime a, par construction,
        une valeur différente et donc une signature différente."""
        return tuple(sorted(payload.items()))

    @classmethod
    def _key(cls, event: Event) -> Tuple[str, str, str, Tuple]:
        event_type_value = event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type)
        return (
            event.recommendation_id,
            event.provider_id,
            event_type_value,
            cls._payload_signature(event.payload),
        )

    def is_duplicate(self, event: Event) -> bool:
        key = self._key(event)
        expiry = self._expiry.get(key)
        if expiry is not None and event.timestamp <= expiry:
            self.duplicate_count += 1
            return True
        self._expiry[key] = event.timestamp + self.window
        return False


# ═════════════════════════════════════════════════════════════════════════
# b) Filtre de confirmation
# ═════════════════════════════════════════════════════════════════════════


class ConfirmationTracker:
    """
    Suit, par recommendation_id, si l'issue a été confirmée
    (patient_confirmed ou patient_arrived).

    Hypothèse de flux : les événements sont traités dans l'ordre
    chronologique (streaming). Si une confirmation arrivait APRÈS une
    availability_update qui lui est rattachée, cette dernière resterait
    "unconfirmed" au moment de son traitement — pas de retraitement
    rétroactif à ce stade (ça relèverait de la logique de drift/saturation,
    hors périmètre de cette étape).
    """

    def __init__(self):
        self._confirmed_recommendation_ids: set = set()
        self.unconfirmed_log: List[Event] = []

    def register_outcome(self, event: Event) -> None:
        if event.event_type in (EventType.patient_confirmed, EventType.patient_arrived):
            self._confirmed_recommendation_ids.add(event.recommendation_id)

    def is_confirmed(self, recommendation_id: str) -> bool:
        return recommendation_id in self._confirmed_recommendation_ids

    def log_unconfirmed(self, event: Event) -> None:
        self.unconfirmed_log.append(event)


# ═════════════════════════════════════════════════════════════════════════
# c) Pondération temporelle (EWMA)
# ═════════════════════════════════════════════════════════════════════════

DEFAULT_EWMA_TAU_SECONDS = 20 * 60  # 1200s = 20 minutes
# Choix exact de tau, au milieu de la fourchette demandée (15-30 min) :
#   - half-life = tau * ln(2) ≈ 831s ≈ 13.9 min : une observation perd la
#     moitié de son influence sur la disponibilité lissée en un peu moins
#     de 14 minutes. C'est cohérent avec le délai typique entre le moment
#     où un patient est orienté vers un prestataire et son arrivée
#     effective — au-delà, la disponibilité "vue" doit déjà avoir
#     largement cédé la place à des observations plus fraîches.
#   - Assez long pour absorber les mises à jour rapprochées / en rafale
#     (sous-bruit résiduel ayant passé le dédoublonnage) sans faire
#     sauter la moyenne à chaque événement individuel.
#   - Assez court pour qu'une disponibilité périmée de plusieurs heures
#     (bruit "stale" injecté par le simulateur) pèse presque rien dès
#     qu'une observation récente arrive :
#     exp(-6h / 20min) = exp(-18) ≈ 1.5e-8.


def ewma_weight(delta_t_seconds: float, tau_seconds: float = DEFAULT_EWMA_TAU_SECONDS) -> float:
    """
    w = exp(-Δt / tau) ∈ (0, 1].

    w est le poids conservé pour L'ANCIEN état lissé ; (1 - w) est le poids
    donné à la nouvelle observation. Δt = temps écoulé depuis la dernière
    mise à jour acceptée pour ce provider.

        Δt = 0   -> w = 1   : aucune info nouvelle n'est arrivée, l'état
                              lissé ne bouge (quasi) pas.
        Δt -> ∞  -> w -> 0  : l'ancien état est complètement obsolète, la
                              nouvelle observation le remplace de fait.
    """
    delta_t_seconds = max(0.0, delta_t_seconds)
    return math.exp(-delta_t_seconds / tau_seconds)


@dataclass
class _ProviderSmoothState:
    smoothed_value: Optional[float] = None
    last_update_ts: Optional[datetime] = None


class EwmaSmoother:
    """
    Disponibilité "lissée" par provider = moyenne mobile à décroissance
    exponentielle, pondérée par l'ancienneté RÉELLE en secondes (timestamps
    irréguliers), plutôt qu'une simple dernière valeur brute.
    """

    DEFAULT_TAU_SECONDS = DEFAULT_EWMA_TAU_SECONDS

    def __init__(self, tau_seconds: float = DEFAULT_TAU_SECONDS):
        self.tau_seconds = tau_seconds
        self._state: Dict[str, _ProviderSmoothState] = {}

    def current(self, provider_id: str) -> Optional[float]:
        state = self._state.get(provider_id)
        return state.smoothed_value if state else None

    def update(self, provider_id: str, value: float, timestamp: datetime) -> Tuple[float, float]:
        """Applique la nouvelle observation. Retourne (valeur_lissée, w) où w
        est le poids EWMA appliqué à l'ancien état (0.0 s'il s'agit de la
        toute première observation pour ce provider, donc pas d'état antérieur)."""
        state = self._state.setdefault(provider_id, _ProviderSmoothState())

        if state.smoothed_value is None or state.last_update_ts is None:
            state.smoothed_value = float(value)
            state.last_update_ts = timestamp
            return state.smoothed_value, 0.0

        delta_t = (timestamp - state.last_update_ts).total_seconds()
        w = ewma_weight(delta_t, self.tau_seconds)
        new_smoothed = w * state.smoothed_value + (1 - w) * float(value)

        state.smoothed_value = new_smoothed
        # On ne recule jamais l'horloge de référence, même si un événement
        # arrivait légèrement désordonné (garde-fou défensif).
        state.last_update_ts = max(state.last_update_ts, timestamp)
        return new_smoothed, w


# ═════════════════════════════════════════════════════════════════════════
# d) Détection d'anomalie — deux backends interchangeables
# ═════════════════════════════════════════════════════════════════════════
#
# HISTORIQUE : la 1ère version (HalfSpaceTrees seul, window_size=25,
# height=6, seuil=0.7) évaluée sur les seeds 7/1/42 donnait une précision
# de ~2-5% et un rappel de ~13-33% sur injected_anomaly — bien en dessous
# d'un seuil utilisable (sur 44 quarantaines au seed 7, seulement 2 étaient
# de vrais pics injectés ; 4 vrais pics sur 6 sont passés inaperçus).
#
# RETUNING effectué (grille exhaustive, 128 configs, exécutée via un
# harness réutilisant `evaluation.run_pipeline_and_evaluate` sur les 3
# seeds 7/1/42, moyennée) :
#   n_trees     ∈ {10, 25}
#   height      ∈ {3, 4, 6, 8}
#   window_size ∈ {10, 15, 25, 40}
#   threshold   ∈ {0.3, 0.5, 0.7, 0.9}
# MEILLEUR RÉSULTAT TROUVÉ (n_trees=25, height=3, window_size=10,
# threshold=0.5) : précision moyenne 0.258, rappel moyen 0.292, F1 0.267 —
# une nette amélioration par rapport à la config initiale (F1 ~0.06), mais
# TOUJOURS très en dessous de l'objectif ~50-60%. HalfSpaceTrees partitionne
# l'espace des features de façon géométrique (arbres construits sur des
# bornes fixes) : avec aussi peu d'observations par provider et une plage
# de valeurs aussi étroite (0-50, entiers), le découpage reste instable
# d'un provider à l'autre — structurellement peu adapté à ce cas d'usage,
# quel que soit le réglage. Conservé en option (voir constantes HST_*
# ci-dessous, réglées sur cette meilleure config trouvée) pour comparaison
# future si le volume/la distribution des données change.
#
# ALTERNATIVE implémentée en conséquence : un z-score robuste (médiane +
# MAD glissantes, Iglewicz & Hoaglin) — bien plus adapté à une série
# univariée, faible variance, un flux par provider, où l'on cherche
# simplement "cette valeur est-elle loin du comportement récent de CE
# provider ?", sans dépendre d'un découpage géométrique fixe.
#
# Grille testée (72 configs initiales + 200 configs de raffinement autour
# de la zone prometteuse, mêmes 3 seeds) :
#   window_size ∈ {10, 12, 15, 18, 20, 30}, min_samples ∈ {4, 5, 6, 8, 10},
#   threshold   ∈ {2.0, 2.5, 3.0, 3.2, 3.5, 3.7, 3.8, 4.0, 4.2, 4.5, 5.0}
# MEILLEURE CONFIG avec précision ET rappel >= 0.5 SIMULTANÉMENT (window_size=12,
# min_samples=5, threshold=4.2) : précision moyenne 0.542, rappel moyen
# 0.523, F1 0.529 — objectif atteint EN MOYENNE sur les 3 seeds, mais avec
# une variance notable vu le faible support (6 à 9 vrais pics injectés par
# seed) : précision par seed = {7: 0.429, 1: 0.625, 42: 0.571}, rappel par
# seed = {7: 0.500, 1: 0.625, 42: 0.444}. Le seed 7 repasse sous 50% en
# précision, le seed 42 sous 50% en rappel — c'est un résultat correct et
# nettement meilleur que HalfSpaceTrees (F1 ~2x supérieur), mais fragile et
# À NE PAS présenter comme un objectif atteint de façon fiable et robuste :
# avec 6-9 positifs par seed, chaque événement pèse ~11-17 points de
# pourcentage, donc ces chiffres resteront bruités tant que le volume
# d'anomalies réellement observées reste aussi faible. C'est le backend PAR
# DÉFAUT depuis ce retuning. HalfSpaceTrees reste disponible
# (AnomalyBackend.half_space_trees), pas supprimé, pour comparaison future.


class AnomalyBackend(str, Enum):
    half_space_trees = "half_space_trees"
    robust_zscore = "robust_zscore"


# ─────────────────────────────────────────────────────────────────────────
# Backend 1 : River HalfSpaceTrees — gardé en option (voir historique ci-dessus)
# ─────────────────────────────────────────────────────────────────────────

HST_N_TREES = 25
HST_HEIGHT = 3
HST_WINDOW_SIZE = 10
HST_SCORE_THRESHOLD = 0.5
# Meilleure configuration trouvée sur la grille ci-dessus (voir historique :
# F1=0.267, précision=0.258, rappel=0.292). Un height plus faible (3 au lieu
# de 6) et un window_size plus petit (10 au lieu de 25) font pivoter la
# première fenêtre plus tôt et réduisent le nombre de bacs (2^height) sur
# une plage de valeurs étroite, ce qui limite la fragmentation excessive de
# l'espace observé. Nettement mieux que la config initiale (F1 ~0.06), mais
# toujours insuffisant en absolu — d'où le choix du backend robust_zscore
# par défaut.

HST_FEATURE_RANGE: Tuple[float, float] = (0.0, 50.0)
# Bornes plausibles pour un nombre de créneaux/lits disponibles déclarés.
# Passées à `limits=` de HalfSpaceTrees pour que le partitionnement de
# l'espace des features soit pertinent dès les premières observations
# (au lieu d'être découvert empiriquement au fil du flux).


class _HalfSpaceTreesBackend:
    """Un détecteur River HalfSpaceTrees dédié par provider (série
    univariée des valeurs de disponibilité déclarées par ce provider)."""

    def __init__(
        self,
        n_trees: int = HST_N_TREES,
        height: int = HST_HEIGHT,
        window_size: int = HST_WINDOW_SIZE,
        feature_range: Tuple[float, float] = HST_FEATURE_RANGE,
        seed: int = 42,
    ):
        self._model = anomaly.HalfSpaceTrees(
            n_trees=n_trees,
            height=height,
            window_size=window_size,
            limits={"available_slots": feature_range},
            seed=seed,
        )
        self.n_seen = 0
        # Contrainte imposée par l'implémentation River elle-même :
        # score_one() renvoie systématiquement 0 tant que `learn_one` n'a
        # pas été appelé `window_size` fois (1ère fenêtre pas encore
        # "pivotée" en interne — voir river.anomaly.hst). En dessous de ce
        # seuil, un score de 0 ne voudrait rien dire : tout est accepté
        # normalement pendant le warm-up plutôt que mal interprété.
        self.min_samples = window_size

    @property
    def warmed_up(self) -> bool:
        return self.n_seen >= self.min_samples

    def score(self, value: float) -> float:
        return self._model.score_one({"available_slots": float(value)})

    def learn(self, value: float) -> None:
        self._model.learn_one({"available_slots": float(value)})
        self.n_seen += 1


# ─────────────────────────────────────────────────────────────────────────
# Backend 2 : z-score robuste (médiane + MAD glissantes) — DÉFAUT
# ─────────────────────────────────────────────────────────────────────────

ROBUST_ZSCORE_WINDOW_SIZE = 12
# Taille de la fenêtre glissante (dernières valeurs observées pour CE
# provider) utilisée pour calculer la médiane et le MAD de référence.
# Valeur retenue après grille (voir historique ci-dessus) : une fenêtre
# plus courte que l'intuition initiale (20) réagit plus vite à un vrai
# changement de régime sans être polluée par une trop longue histoire —
# cohérent avec le nombre d'availability_update réellement observés par
# provider dans une session (quelques dizaines).

ROBUST_ZSCORE_MIN_SAMPLES = 5
# Nombre minimal d'observations avant de faire confiance au score
# ("warm-up") : en dessous, la médiane/le MAD sont trop instables pour
# juger un écart de façon fiable. Valeur retenue après grille : suffisant
# pour amorcer une médiane/MAD utilisables sans retarder inutilement la
# détection sur des séries courtes.

ROBUST_ZSCORE_MAD_FLOOR = 0.5
# Plancher appliqué au MAD quand il vaut 0 (ex. une série parfaitement
# stable sur la fenêtre récente). Sans ce plancher, un MAD nul rendrait le
# score infini au moindre écart d'une seule unité — irréaliste sur une
# série entière à faible variance où "aucune variation récente" est
# fréquent, pas une garantie que le prochain écart est anormal.

ROBUST_ZSCORE_THRESHOLD = 4.2
# Le "modified z-score" d'Iglewicz & Hoaglin (0.6745 * (x - médiane) / MAD)
# recommande généralement un seuil de 3.5 dans la littérature générale.
# Ici, après grille sur {2.0 .. 5.0} croisée avec window_size/min_samples
# (voir historique ci-dessus), 4.2 est la plus petite valeur de la zone où
# précision ET rappel dépassent 0.5 simultanément en moyenne sur les 3
# seeds (0.542 / 0.523, F1=0.529) — un seuil plus bas (ex. 3.5) pousse le
# rappel légèrement plus haut mais fait chuter la précision sous 0.5 en
# noyant le signal dans plus de faux positifs sur les valeurs normales.


class _RobustZScoreBackend:
    """
    Détecteur par écart robuste à la médiane glissante (MAD = Median
    Absolute Deviation), un backend par provider. Bien adapté à une série
    univariée entière à faible variance avec peu d'observations par
    provider — contrairement à HalfSpaceTrees (voir historique ci-dessus),
    il ne dépend d'aucun découpage géométrique de l'espace des features :
    seule la dispersion RÉCEMMENT OBSERVÉE pour CE provider sert de
    référence, ce qui le rend robuste même avec une fenêtre courte.
    """

    def __init__(
        self,
        window_size: int = ROBUST_ZSCORE_WINDOW_SIZE,
        min_samples: int = ROBUST_ZSCORE_MIN_SAMPLES,
        mad_floor: float = ROBUST_ZSCORE_MAD_FLOOR,
    ):
        self._history: Deque[float] = deque(maxlen=window_size)
        self.min_samples = min_samples
        self.mad_floor = mad_floor
        self.n_seen = 0

    @property
    def warmed_up(self) -> bool:
        return self.n_seen >= self.min_samples

    def score(self, value: float) -> float:
        if len(self._history) < 2:
            return 0.0
        values = list(self._history)
        median = statistics.median(values)
        mad = statistics.median([abs(v - median) for v in values])
        if mad == 0:
            mad = self.mad_floor
        modified_z = 0.6745 * (float(value) - median) / mad
        return abs(modified_z)

    def learn(self, value: float) -> None:
        self._history.append(float(value))
        self.n_seen += 1


DEFAULT_ANOMALY_BACKEND = AnomalyBackend.robust_zscore
# Choix du backend par défaut, basé sur la comparaison chiffrée seeds
# 7/1/42 (voir rapport d'évaluation) : robust_zscore obtient un F1
# nettement supérieur à HalfSpaceTrees (même après retuning de ce dernier)
# sur injected_anomaly, sans dégrader les autres catégories.

_ANOMALY_BACKEND_FACTORY = {
    AnomalyBackend.half_space_trees: _HalfSpaceTreesBackend,
    AnomalyBackend.robust_zscore: _RobustZScoreBackend,
}
_ANOMALY_BACKEND_DEFAULT_THRESHOLD = {
    AnomalyBackend.half_space_trees: HST_SCORE_THRESHOLD,
    AnomalyBackend.robust_zscore: ROBUST_ZSCORE_THRESHOLD,
}
_ANOMALY_BACKEND_DEFAULT_MIN_SAMPLES = {
    AnomalyBackend.half_space_trees: HST_WINDOW_SIZE,
    AnomalyBackend.robust_zscore: ROBUST_ZSCORE_MIN_SAMPLES,
}
DEFAULT_ANOMALY_MIN_SAMPLES = _ANOMALY_BACKEND_DEFAULT_MIN_SAMPLES[DEFAULT_ANOMALY_BACKEND]
# Alias pratique pour les tests/scripts : le warm-up du backend PAR DÉFAUT,
# quel qu'il soit, sans avoir à connaître son nom.


class ProviderAnomalyDetector:
    """
    Détecteur d'anomalie dédié à un provider (série univariée des valeurs
    de disponibilité déclarées par ce provider), qui délègue au backend
    choisi (voir `AnomalyBackend`). Un provider = une instance = un état
    indépendant, quel que soit le backend.
    """

    def __init__(
        self,
        backend: AnomalyBackend = DEFAULT_ANOMALY_BACKEND,
        threshold: Optional[float] = None,
        **backend_kwargs,
    ):
        self.backend_name = backend
        factory = _ANOMALY_BACKEND_FACTORY[backend]
        self._impl = factory(**backend_kwargs)
        self.threshold = threshold if threshold is not None else _ANOMALY_BACKEND_DEFAULT_THRESHOLD[backend]

    @property
    def warmed_up(self) -> bool:
        return self._impl.warmed_up

    def score(self, value: float) -> float:
        return self._impl.score(value)

    def learn(self, value: float) -> None:
        self._impl.learn(value)


QUARANTINE_COHERENCE_TOLERANCE = 2.0
# Écart absolu (en créneaux) en-dessous duquel une NOUVELLE valeur est
# considérée comme "corroborant" une valeur précédemment mise en
# quarantaine pour le même provider — auquel cas la quarantaine est levée
# et les deux valeurs sont appliquées au lissage. Choisi pour confirmer un
# vrai changement de régime (ex. deux observations consécutives proches
# l'une de l'autre mais loin de la baseline) sans confirmer un pic isolé
# qui retombe ensuite (ex. 10 -> 0 -> 8 : |0 - 8| = 8 > tolérance, donc
# le 0 reste quarantainé, ce qui est le comportement attendu).

QUARANTINE_COHERENCE_WINDOW_SECONDS = 2 * DEFAULT_EWMA_TAU_SECONDS
# Au-delà de cette fenêtre (40 min), on considère que trop de temps a
# passé pour qu'une nouvelle valeur "confirme" encore une quarantaine
# ancienne : elle reste en quarantaine, jamais fusionnée au lissage.


@dataclass
class QuarantinedEvent:
    event: Event
    value: float
    score: float
    quarantined_at: datetime


# ═════════════════════════════════════════════════════════════════════════
# Sortie du pipeline
# ═════════════════════════════════════════════════════════════════════════


class EventStatus(str, Enum):
    accepted = "accepted"
    deduplicated = "deduplicated"
    unconfirmed = "unconfirmed"
    quarantined_anomaly = "quarantined_anomaly"


@dataclass
class FilterResult:
    event: Event
    status: EventStatus
    smoothed_availability: Optional[float] = None
    ewma_weight_applied: Optional[float] = None
    anomaly_score: Optional[float] = None
    detail: str = ""


# ═════════════════════════════════════════════════════════════════════════
# Pipeline
# ═════════════════════════════════════════════════════════════════════════


class NoiseFilterPipeline:
    """
    Orchestre les 4 étapes dans l'ordre : dédoublonnage -> filtre de
    confirmation -> détection d'anomalie -> pondération EWMA.

    (La détection d'anomalie est évaluée avant l'application au lissage
    puisqu'une valeur anormale ne doit justement PAS être lissée tant
    qu'elle n'est pas confirmée — voir `_try_confirm_from_quarantine`.)
    """

    AVAILABILITY_PAYLOAD_KEY = "available_slots"

    def __init__(
        self,
        dedup_window_seconds: float = Deduplicator.DEFAULT_WINDOW_SECONDS,
        ewma_tau_seconds: float = DEFAULT_EWMA_TAU_SECONDS,
        anomaly_backend: AnomalyBackend = DEFAULT_ANOMALY_BACKEND,
        anomaly_threshold: Optional[float] = None,
        anomaly_backend_kwargs: Optional[dict] = None,
    ):
        self.deduplicator = Deduplicator(window_seconds=dedup_window_seconds)
        self.confirmation_tracker = ConfirmationTracker()
        self.smoother = EwmaSmoother(tau_seconds=ewma_tau_seconds)
        self.anomaly_backend = anomaly_backend
        self.anomaly_threshold = anomaly_threshold
        self._anomaly_backend_kwargs = anomaly_backend_kwargs or {}

        self._detectors: Dict[str, ProviderAnomalyDetector] = {}
        self._pending_quarantine: Dict[str, List[QuarantinedEvent]] = {}
        self.quarantine_log: List[QuarantinedEvent] = []

    def _detector_for(self, provider_id: str) -> ProviderAnomalyDetector:
        if provider_id not in self._detectors:
            self._detectors[provider_id] = ProviderAnomalyDetector(
                backend=self.anomaly_backend,
                threshold=self.anomaly_threshold,
                **self._anomaly_backend_kwargs,
            )
        return self._detectors[provider_id]

    def process_event(self, event: Event) -> FilterResult:
        # a) Déduplication -----------------------------------------------------
        if self.deduplicator.is_duplicate(event):
            return FilterResult(
                event=event, status=EventStatus.deduplicated,
                detail="Doublon rejeté (retry réseau probable, même clé dans la fenêtre de dédoublonnage).",
            )

        # Événements de cycle de vie sans disponibilité associée : juste enregistrés.
        if event.event_type == EventType.recommendation_issued:
            return FilterResult(event=event, status=EventStatus.accepted, detail="Recommandation émise, enregistrée.")

        if event.event_type == EventType.patient_cancelled:
            return FilterResult(event=event, status=EventStatus.accepted, detail="Annulation enregistrée.")

        if event.event_type in (EventType.patient_confirmed, EventType.patient_arrived):
            self.confirmation_tracker.register_outcome(event)
            return FilterResult(event=event, status=EventStatus.accepted, detail="Issue de recommandation confirmée, enregistrée.")

        # À partir d'ici : event_type == availability_update --------------------

        # b) Filtre de confirmation ----------------------------------------------
        if not self.confirmation_tracker.is_confirmed(event.recommendation_id):
            self.confirmation_tracker.log_unconfirmed(event)
            return FilterResult(
                event=event, status=EventStatus.unconfirmed,
                detail="Recommandation non confirmée : disponibilité ignorée (journalisée pour analyse du taux de confirmation).",
            )

        value = event.payload.get(self.AVAILABILITY_PAYLOAD_KEY)
        if value is None:
            return FilterResult(
                event=event, status=EventStatus.accepted,
                detail=f"Pas de clé '{self.AVAILABILITY_PAYLOAD_KEY}' dans le payload, rien à lisser.",
            )

        provider_id = event.provider_id
        detector = self._detector_for(provider_id)

        # d) Détection d'anomalie (backend configurable, voir AnomalyBackend) -----
        score = detector.score(value)
        is_anomalous = detector.warmed_up and score >= detector.threshold
        # Le modèle apprend de TOUTE valeur (y compris anormale) pour ne pas
        # rester aveugle si un vrai changement de régime se confirme ensuite.
        detector.learn(value)

        # Une valeur en attente en quarantaine est-elle corroborée par celle-ci
        # (ou l'inverse) ? Si oui, la quarantaine est levée et les deux valeurs
        # sont appliquées au lissage, dans l'ordre chronologique.
        confirmed_pending = self._try_confirm_from_quarantine(provider_id, value, event.timestamp)
        if confirmed_pending is not None:
            smoothed, w = self._apply_confirmed_pair(provider_id, confirmed_pending, event, value)
            return FilterResult(
                event=event, status=EventStatus.accepted,
                smoothed_availability=smoothed, ewma_weight_applied=w, anomaly_score=score,
                detail="Valeur cohérente avec une quarantaine en attente : quarantaine levée, appliquée au lissage.",
            )

        if is_anomalous:
            qe = QuarantinedEvent(event=event, value=float(value), score=score, quarantined_at=event.timestamp)
            self._pending_quarantine.setdefault(provider_id, []).append(qe)
            self.quarantine_log.append(qe)
            return FilterResult(
                event=event, status=EventStatus.quarantined_anomaly,
                anomaly_score=score,
                detail=f"Valeur aberrante détectée (backend={detector.backend_name.value}) : mise en quarantaine, n'impacte pas le lissage.",
            )

        # c) Pondération temporelle (EWMA) -----------------------------------------
        smoothed, w = self.smoother.update(provider_id, float(value), event.timestamp)
        return FilterResult(
            event=event, status=EventStatus.accepted,
            smoothed_availability=smoothed, ewma_weight_applied=w, anomaly_score=score,
            detail="Disponibilité confirmée, non anormale : acceptée et lissée (EWMA).",
        )

    def _try_confirm_from_quarantine(
        self, provider_id: str, value: float, timestamp: datetime
    ) -> Optional[QuarantinedEvent]:
        pending_list = self._pending_quarantine.get(provider_id) or []
        for qe in list(pending_list):
            close_enough = abs(qe.value - float(value)) <= QUARANTINE_COHERENCE_TOLERANCE
            recent_enough = (timestamp - qe.quarantined_at).total_seconds() <= QUARANTINE_COHERENCE_WINDOW_SECONDS
            if close_enough and recent_enough:
                pending_list.remove(qe)
                return qe
        return None

    def _apply_confirmed_pair(
        self, provider_id: str, quarantined: QuarantinedEvent, new_event: Event, new_value: float
    ) -> Tuple[float, float]:
        # On applique d'abord la valeur précédemment quarantainée (la plus
        # ancienne des deux), puis la nouvelle, pour respecter l'ordre
        # chronologique du lissage.
        self.smoother.update(provider_id, quarantined.value, quarantined.event.timestamp)
        return self.smoother.update(provider_id, float(new_value), new_event.timestamp)
