"""
Modèle d'événement temps réel — section 3.8 du papier.

Un événement représente un fait ponctuel dans le cycle de vie d'une
recommandation (émission, confirmation par le patient, mise à jour de
disponibilité déclarée par un prestataire, etc.). C'est l'unité de base
que consomme le pipeline de filtrage (`noise_filter.py`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ProviderType(str, Enum):
    hospital = "hospital"
    cabinet = "cabinet"


class EventType(str, Enum):
    # Une recommandation (cabinet ou hôpital) vient d'être émise au patient.
    recommendation_issued = "recommendation_issued"
    # Le patient a confirmé qu'il se rend chez ce prestataire.
    patient_confirmed = "patient_confirmed"
    # Le patient a annulé / n'a pas donné suite.
    patient_cancelled = "patient_cancelled"
    # Le prestataire (ou son système) déclare un nouveau niveau de disponibilité
    # (ex. nombre de créneaux/lits libres). payload attendu : {"available_slots": <int|float>}.
    availability_update = "availability_update"
    # Le patient est physiquement arrivé chez le prestataire.
    patient_arrived = "patient_arrived"


class Event(BaseModel):
    """Structure d'un événement brut, tel qu'il arriverait sur le flux temps réel."""

    event_id: UUID = Field(default_factory=uuid4)
    recommendation_id: str
    provider_id: str
    provider_type: ProviderType
    event_type: EventType
    timestamp: datetime
    payload: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(use_enum_values=False)

    @staticmethod
    def now_utc() -> datetime:
        return datetime.now(timezone.utc)


def make_event(
    recommendation_id: str,
    provider_id: str,
    provider_type: ProviderType,
    event_type: EventType,
    timestamp: Optional[datetime] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Event:
    """Petit helper pour construire un événement à la main (tests, démo)."""
    return Event(
        recommendation_id=recommendation_id,
        provider_id=provider_id,
        provider_type=provider_type,
        event_type=event_type,
        timestamp=timestamp or Event.now_utc(),
        payload=payload or {},
    )
