"""
Simulateur d'événements temps réel — sert à prototyper et tester le pipeline
de filtrage (`noise_filter.py`) sans dépendre d'un flux réel côté prestataires.

Le flux généré alterne des cycles "recommandation -> confirmation ->
mises à jour de disponibilité" par prestataire, et injecte volontairement
4 catégories de bruit décrites dans la section 3.8 du papier :

  1. doublons          : le même événement (recommendation_id, provider_id,
                          event_type) renvoyé 2-3 fois à quelques secondes
                          d'intervalle (retry réseau simulé).
  2. non-confirmés      : une recommandation émise mais jamais suivie de
                          patient_confirmed / patient_arrived.
  3. valeurs périmées   : un availability_update dont le timestamp est vieux
                          de plusieurs heures, mélangé aux événements récents.
  4. valeurs aberrantes : un pic ponctuel et incohérent de disponibilité
                          (ex. 10 -> 0 -> 8 créneaux en quelques dizaines
                          de secondes) sur un cycle donné.

Tout le reste (majorité des cycles) est un flux "normal" cohérent :
dérive lente et bornée de la disponibilité déclarée. Au sein d'un même
cycle, deux mises à jour légitimes consécutives ont TOUJOURS une valeur
différente (jamais de palier) — cela reflète la réalité (la disponibilité
change réellement entre deux observations) et rend les vrais doublons
(catégorie 1, même payload) distinguables des mises à jour légitimes
rapprochées par leur seul contenu — voir `Deduplicator._key` dans
`noise_filter.py`, dont la clé inclut désormais une signature du payload.

── Vérité terrain (pour l'évaluation, `evaluation.py`) ─────────────────────
Chaque événement généré est accompagné d'un `GroundTruthLabel` indiquant
POURQUOI il a été injecté (ou `normal` s'il ne l'a pas été), via
`generate_labeled_stream()`. Ce label n'existe QUE dans cette paire
(event, label) produite par le simulateur : `generate_stream()` — celui que
consomme le pipeline — n'expose que l'`Event` nu, exactement comme un flux
réel où personne ne sait à l'avance qu'un événement est du bruit.

Quand plusieurs conditions d'injection se chevauchent sur un même
availability_update (ex. un pic aberrant qui tombe aussi sur une
recommandation jamais confirmée), le label retenu suit la même priorité que
l'ordre de traitement réel du pipeline (dédoublonnage -> confirmation ->
anomalie -> décroissance EWMA) : duplicate > unconfirmed > anomaly > stale.
C'est la condition qui détermine effectivement ce que le pipeline va faire
de l'événement.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator, List, Optional, Union

from .events import Event, EventType, ProviderType, make_event


class GroundTruthLabel(str, Enum):
    normal = "normal"
    injected_duplicate = "injected_duplicate"
    injected_unconfirmed = "injected_unconfirmed"
    injected_stale = "injected_stale"
    injected_anomaly = "injected_anomaly"


@dataclass
class LabeledEvent:
    event: Event
    ground_truth_label: GroundTruthLabel


@dataclass
class SimulatorConfig:
    n_events: int = 500
    n_providers: int = 5
    provider_type: ProviderType = ProviderType.cabinet

    start_time: Optional[datetime] = None
    # Écart moyen (en secondes) entre deux événements "logiques" successifs ;
    # la valeur réelle est tirée aléatoirement autour de cette moyenne.
    time_step_seconds: float = 8.0

    # ── Proportions de bruit injecté (0.0 - 1.0) ─────────────────────────────
    duplicate_rate: float = 0.12       # d'événements normaux rejoués en double
    unconfirmed_rate: float = 0.35     # de recommandations jamais confirmées
    stale_rate: float = 0.05           # d'availability_update horodatés dans le passé
    stale_min_hours: float = 2.0
    stale_max_hours: float = 6.0
    outlier_rate: float = 0.06         # de cycles provider avec un pic aberrant

    baseline_slots_min: int = 4
    baseline_slots_max: int = 15

    seed: int = 42


def _provider_ids(config: SimulatorConfig) -> list:
    return [f"prov-{i:03d}" for i in range(config.n_providers)]


def generate_labeled_stream(config: Optional[SimulatorConfig] = None) -> Iterator[LabeledEvent]:
    """Générateur Python paresseux : produit (événement, label de vérité
    terrain) dans l'ordre chronologique. C'est la source de vérité complète ;
    `generate_stream()` en dérive la vue "aveugle" consommée par le pipeline."""
    config = config or SimulatorConfig()
    rng = random.Random(config.seed)
    clock = config.start_time or datetime.now(timezone.utc)

    provider_ids = _provider_ids(config)
    baseline = {pid: rng.randint(config.baseline_slots_min, config.baseline_slots_max) for pid in provider_ids}

    emitted = 0
    rec_counter = 0

    def advance_clock() -> None:
        nonlocal clock
        clock = clock + timedelta(seconds=config.time_step_seconds * rng.uniform(0.4, 1.8))

    def emit_with_duplicates(event: Event, base_label: GroundTruthLabel) -> Iterator[LabeledEvent]:
        """Émet l'événement avec son label "de fond", puis avec probabilité
        `duplicate_rate`, le renvoie 1-2 fois de plus à 0.5-4s d'intervalle
        (même clé de dédoublonnage, event_id différent — simule un retry
        réseau côté émetteur). Les copies renvoyées sont étiquetées
        `injected_duplicate`, quel que soit le label de l'original : c'est
        la déduplication, pas leur contenu, qui est censée les intercepter."""
        yield LabeledEvent(event, base_label)
        if rng.random() < config.duplicate_rate:
            for _ in range(rng.choice([1, 2])):
                retry = make_event(
                    recommendation_id=event.recommendation_id,
                    provider_id=event.provider_id,
                    provider_type=event.provider_type,
                    event_type=event.event_type,
                    timestamp=event.timestamp + timedelta(seconds=rng.uniform(0.5, 4.0)),
                    payload=dict(event.payload),
                )
                yield LabeledEvent(retry, GroundTruthLabel.injected_duplicate)

    while emitted < config.n_events:
        pid = provider_ids[emitted % config.n_providers]

        # 1. Nouvelle recommandation ------------------------------------------------
        rec_counter += 1
        rec_id = f"rec-{rec_counter:05d}"
        advance_clock()
        for le in emit_with_duplicates(
            make_event(rec_id, pid, config.provider_type, EventType.recommendation_issued, clock),
            GroundTruthLabel.normal,
        ):
            yield le
            emitted += 1
            if emitted >= config.n_events:
                return

        # 2. Confirmation, ou bruit "non confirmé" -----------------------------------
        confirmed = rng.random() >= config.unconfirmed_rate
        if confirmed:
            advance_clock()
            outcome = rng.choice([EventType.patient_confirmed, EventType.patient_arrived])
            for le in emit_with_duplicates(
                make_event(rec_id, pid, config.provider_type, outcome, clock), GroundTruthLabel.normal
            ):
                yield le
                emitted += 1
                if emitted >= config.n_events:
                    return
        elif rng.random() < 0.3:
            # Une partie des non-confirmés est explicitement annulée, le reste
            # reste simplement "en attente" (bruit réaliste : abandon silencieux).
            advance_clock()
            cancel = make_event(rec_id, pid, config.provider_type, EventType.patient_cancelled, clock)
            for le in emit_with_duplicates(cancel, GroundTruthLabel.normal):
                yield le
                emitted += 1
                if emitted >= config.n_events:
                    return

        # 3. Mises à jour de disponibilité rattachées à cette recommandation --------
        n_updates = rng.randint(1, 3)
        is_outlier_cycle = rng.random() < config.outlier_rate
        prev_value_in_cycle: Optional[int] = None  # voir garde anti-collision ci-dessous

        for k in range(n_updates):
            advance_clock()

            is_outlier_point = is_outlier_cycle and k == 1 and n_updates >= 2
            if is_outlier_point:
                # Valeur aberrante ponctuelle et incohérente (ex. chute brutale
                # non corroborée par la tendance récente), puis retour à la normale
                # au tour suivant.
                spike_down = baseline[pid] > 0
                value = 0 if spike_down else baseline[pid] + 10
            else:
                # Dérive lente et cohérente autour de la baseline du provider.
                # Pas de palier (0 explicitement exclu) : au sein d'un même
                # cycle, deux mises à jour légitimes successives doivent avoir
                # un contenu réellement différent (voir Deduplicator._key
                # dans noise_filter.py, qui inclut désormais une signature du
                # payload — sans ce pas garanti non-nul, une disponibilité
                # inchangée par coïncidence entre deux updates légitimes du
                # même cycle serait indiscernable d'un vrai retry).
                baseline[pid] = max(0, baseline[pid] + rng.choice([-1, 1]))
                value = baseline[pid]
                if prev_value_in_cycle is not None and value == prev_value_in_cycle:
                    # Cas limite : baseline déjà à 0 et step=-1 (plafonné par
                    # max(0, ...)) laisserait la valeur inchangée. On force un
                    # écart réel plutôt que de risquer une fausse collision.
                    baseline[pid] = value + 1
                    value = baseline[pid]

            prev_value_in_cycle = value
            ts = clock
            is_stale_point = rng.random() < config.stale_rate
            if is_stale_point:
                # Mise à jour périmée : timestamp vieux de plusieurs heures,
                # mais insérée dans le flux au même moment que les événements récents.
                ts = clock - timedelta(hours=rng.uniform(config.stale_min_hours, config.stale_max_hours))

            # Priorité de label = ordre de traitement réel du pipeline : une
            # recommandation non confirmée bloque tout avant même que la valeur
            # ne soit examinée ; un pic aberrant est détecté sur la VALEUR
            # (indépendamment du timestamp) avant que la péremption ne joue.
            if not confirmed:
                label = GroundTruthLabel.injected_unconfirmed
            elif is_outlier_point:
                label = GroundTruthLabel.injected_anomaly
            elif is_stale_point:
                label = GroundTruthLabel.injected_stale
            else:
                label = GroundTruthLabel.normal

            update = make_event(
                rec_id, pid, config.provider_type, EventType.availability_update,
                ts, {"available_slots": value},
            )
            for le in emit_with_duplicates(update, label):
                yield le
                emitted += 1
                if emitted >= config.n_events:
                    return


def generate_stream(config: Optional[SimulatorConfig] = None) -> Iterator[Event]:
    """Vue "aveugle" du flux, SANS le label de vérité terrain — c'est ce que
    consomme le pipeline de filtrage (`NoiseFilterPipeline.process_event`),
    exactement comme un flux réel où l'origine du bruit n'est pas connue
    à l'avance."""
    for labeled in generate_labeled_stream(config):
        yield labeled.event


def write_jsonl(path: Union[str, Path], config: Optional[SimulatorConfig] = None) -> int:
    """Écrit le flux généré (sans labels) dans un fichier JSONL (un événement
    par ligne), pour pouvoir rejouer exactement le même flux plus tard.
    Retourne le nombre d'événements écrits."""
    path = Path(path)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for event in generate_stream(config):
            f.write(event.model_dump_json() + "\n")
            count += 1
    return count


def read_jsonl(path: Union[str, Path]) -> Iterator[Event]:
    """Relit un flux précédemment écrit par `write_jsonl`, événement par événement."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield Event.model_validate_json(line)
