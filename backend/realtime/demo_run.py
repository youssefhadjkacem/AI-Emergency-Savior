"""
Script de démonstration : génère un flux bruité avec le simulateur, le
fait passer par le pipeline complet de filtrage, et affiche un résumé
chiffré exploitable pour la section Experiments du papier.

Lancer avec :
    cd backend
    python -m realtime.demo_run
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List

from .events import EventType
from .noise_filter import EventStatus, FilterResult, NoiseFilterPipeline
from .simulator import SimulatorConfig, generate_stream


def run_demo(config: SimulatorConfig = None) -> None:
    config = config or SimulatorConfig(n_events=1200, n_providers=6, seed=7)
    pipeline = NoiseFilterPipeline()

    status_counts: Counter = Counter()
    availability_history: Dict[str, List[float]] = defaultdict(list)
    n_raw_events = 0
    n_availability_updates_seen = 0

    for event in generate_stream(config):
        n_raw_events += 1
        if event.event_type == EventType.availability_update:
            n_availability_updates_seen += 1

        result: FilterResult = pipeline.process_event(event)
        status_counts[result.status] += 1

        if result.status == EventStatus.accepted and result.smoothed_availability is not None:
            availability_history[event.provider_id].append(result.smoothed_availability)

    n_duplicates = pipeline.deduplicator.duplicate_count
    n_unconfirmed = len(pipeline.confirmation_tracker.unconfirmed_log)
    n_quarantined = len(pipeline.quarantine_log)
    n_noise_filtered = n_duplicates + n_unconfirmed + n_quarantined

    print("=" * 70)
    print("  DÉMO — Pipeline de filtrage du bruit temps réel (section 3.8)")
    print("=" * 70)
    print(f"Configuration : {config.n_events} événements demandés, {config.n_providers} providers simulés (seed={config.seed})")
    print(f"Événements réellement générés (avec doublons injectés) : {n_raw_events}")
    print(f"  dont availability_update (avant filtrage)            : {n_availability_updates_seen}")
    print("-" * 70)
    print("Résultat du pipeline par statut :")
    for status in EventStatus:
        print(f"  {status.value:<22} : {status_counts.get(status, 0)}")
    print("-" * 70)
    print(f"Doublons rejetés (dédoublonnage)                : {n_duplicates}")
    print(f"Mises à jour non confirmées filtrées             : {n_unconfirmed}")
    print(f"Anomalies mises en quarantaine                   : {n_quarantined}")
    print(f"Total « bruit » filtré (dédup + non-confirmé + quarantaine) : {n_noise_filtered}")
    if n_raw_events:
        print(f"Proportion du flux total considérée comme bruit  : {n_noise_filtered / n_raw_events:.1%}")
    print("-" * 70)

    sample_providers = sorted(availability_history.keys())[:3]
    print(f"Évolution de la disponibilité lissée (EWMA) pour {len(sample_providers)} providers :")
    for pid in sample_providers:
        history = availability_history[pid]
        preview = ", ".join(f"{v:.2f}" for v in history[:8])
        suffix = " ..." if len(history) > 8 else ""
        print(f"  {pid} ({len(history)} maj lissées) : [{preview}{suffix}]")
        print(f"    -> valeur lissée finale : {pipeline.smoother.current(pid):.2f}")
    print("=" * 70)


if __name__ == "__main__":
    run_demo()
