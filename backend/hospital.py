"""
Module hôpitaux — autonome, à placer à côté de main.py.

Contient tout ce qu'il faut :
  - NSGA-II générique (tri non-dominé + crowding distance)
  - HospitalFilter (filtrage + optimisation multi-critères sur le JSON de test)
  - should_fallback_to_hospital (décide si on bascule cabinet -> hôpital)
  - Le router FastAPI avec les 2 endpoints /api/hospitals/...

Dans main.py, il suffit d'ajouter :

    from hospital import router as hospital_router
    app.include_router(hospital_router)

Rien d'autre ne change dans main.py.
"""

import json
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────────────────────────────────
# NSGA-II générique
# ─────────────────────────────────────────────────────────────────────────
# Toutes les colonnes de la matrice d'objectifs sont supposées À MINIMISER.
# Pour maximiser un critère, on le multiplie par -1 avant de l'empiler.


def _dominates(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all(a <= b) and np.any(a < b))


def _fast_non_dominated_sort(objs: np.ndarray) -> List[List[int]]:
    n = objs.shape[0]
    dominated_by = [[] for _ in range(n)]
    domination_count = [0] * n
    fronts: List[List[int]] = [[]]

    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            if _dominates(objs[p], objs[q]):
                dominated_by[p].append(q)
            elif _dominates(objs[q], objs[p]):
                domination_count[p] += 1
        if domination_count[p] == 0:
            fronts[0].append(p)

    i = 0
    while fronts[i]:
        next_front = []
        for p in fronts[i]:
            for q in dominated_by[p]:
                domination_count[q] -= 1
                if domination_count[q] == 0:
                    next_front.append(q)
        i += 1
        fronts.append(next_front)

    fronts.pop()
    return fronts


def _crowding_distance(objs: np.ndarray, front: List[int]) -> dict:
    distance = {i: 0.0 for i in front}
    if len(front) <= 2:
        for i in front:
            distance[i] = float("inf")
        return distance

    num_objectives = objs.shape[1]
    for m in range(num_objectives):
        front_sorted = sorted(front, key=lambda i: objs[i][m])
        distance[front_sorted[0]] = float("inf")
        distance[front_sorted[-1]] = float("inf")

        obj_min = objs[front_sorted[0]][m]
        obj_max = objs[front_sorted[-1]][m]
        if obj_max - obj_min == 0:
            continue

        for k in range(1, len(front_sorted) - 1):
            prev_val = objs[front_sorted[k - 1]][m]
            next_val = objs[front_sorted[k + 1]][m]
            distance[front_sorted[k]] += (next_val - prev_val) / (obj_max - obj_min)

    return distance


def nsga2_rank(objs: np.ndarray) -> List[int]:
    objs = np.asarray(objs, dtype=float)
    fronts = _fast_non_dominated_sort(objs)

    ranked: List[int] = []
    for front in fronts:
        distances = _crowding_distance(objs, front)
        front_sorted = sorted(front, key=lambda i: -distances[i])
        ranked.extend(front_sorted)

    return ranked


# ─────────────────────────────────────────────────────────────────────────
# HospitalFilter
# ─────────────────────────────────────────────────────────────────────────

HOSPITALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hospitals_test.json")


class HospitalFilter:
    REQUIRED_COLUMNS = [
        "hospital_name", "specialty", "location", "quality_score",
        "average_cost", "waiting_time_days", "available_beds",
        "accepts_cnam", "has_emergency",
    ]

    def __init__(self, hospitals_file: str):
        self.is_ready = False
        self.df_hospitals: Optional[pd.DataFrame] = None

        try:
            with open(hospitals_file, "r", encoding="utf-8") as f:
                records = json.load(f)

            self.df_hospitals = pd.DataFrame(records)

            missing = [c for c in self.REQUIRED_COLUMNS if c not in self.df_hospitals.columns]
            if missing:
                raise ValueError(f"Colonnes manquantes dans le dataset hôpitaux : {missing}")

            self.is_ready = True
        except Exception as e:
            print(f"[HospitalFilter] Erreur de chargement : {e}")
            self.df_hospitals = None
            self.is_ready = False

    def filter_by_specialty_name(
        self,
        specialty_name: str,
        sort_by: str = "quality_score",
        ascending: bool = False,
        top_n: Optional[int] = None,
    ) -> pd.DataFrame:
        if not self.is_ready or self.df_hospitals is None:
            return pd.DataFrame()

        mask = self.df_hospitals["specialty"].str.lower() == specialty_name.lower()
        filtered = self.df_hospitals[mask].copy()

        if filtered.empty:
            return pd.DataFrame()

        if sort_by in filtered.columns:
            filtered = filtered.sort_values(by=sort_by, ascending=ascending)

        if top_n is not None:
            filtered = filtered.head(top_n)

        return filtered

    def get_top_hospitals(
        self, specialties: List[str], top_n: int = 5, sort_by: str = "quality_score"
    ) -> Dict[str, pd.DataFrame]:
        results = {}
        for spec in specialties:
            filtered = self.filter_by_specialty_name(spec, sort_by=sort_by, ascending=False, top_n=top_n)
            if not filtered.empty:
                results[spec] = filtered
        return results

    def optimize_hospitals_nsga(
        self,
        specialty_name: str,
        top_k: int = 3,
        budget: Optional[float] = None,
        location: Optional[str] = None,
        is_urgent: bool = False,
    ) -> pd.DataFrame:
        """
        7 critères :
          1. Qualité              (max)
          2. Coût                 (min, ou distance au budget)
          3. Délai                (min)
          4. Lits disponibles     (max)  <- statique pour l'instant
          5. Localisation         (min distance, 0 si même ville)
          6. CNAM                 (max)
          7. Service d'urgence    (max, pondéré x2 si is_urgent)
        """
        filtered = self.filter_by_specialty_name(specialty_name)
        if filtered.empty:
            return pd.DataFrame()

        df_opt = filtered.copy()
        n = len(df_opt)

        obj_quality = -df_opt["quality_score"].fillna(0).values

        costs = df_opt["average_cost"].fillna(0).values
        obj_cost = np.abs(costs - budget) if budget is not None else costs

        obj_wait = df_opt["waiting_time_days"].fillna(30).values
        obj_beds = -df_opt["available_beds"].fillna(0).values

        if location is not None:
            obj_loc = np.where(df_opt["location"].str.lower() == location.lower(), 0, 1)
        else:
            obj_loc = np.zeros(n)

        obj_cnam = -df_opt["accepts_cnam"].fillna(0).values

        emergency_weight = 2.0 if is_urgent else 1.0
        obj_emergency = -df_opt["has_emergency"].fillna(0).values * emergency_weight

        objs = np.column_stack([
            obj_quality, obj_cost, obj_wait, obj_beds, obj_loc, obj_cnam, obj_emergency,
        ])

        try:
            ranked_indices = nsga2_rank(objs)
            df_opt = df_opt.iloc[ranked_indices]
        except Exception as e:
            print(f"[HospitalFilter] Erreur NSGA-II : {e}")

        return df_opt.head(top_k)


hospital_filter = HospitalFilter(HOSPITALS_FILE)


# ─────────────────────────────────────────────────────────────────────────
# Fallback : cabinet -> hôpital
# ─────────────────────────────────────────────────────────────────────────


def should_fallback_to_hospital(
    optimize_result: Optional[dict],
    is_urgent: bool = False,
) -> Tuple[bool, str]:
    """
    Basé sur la sortie RÉELLE de /optimize ou /analyze-full :
        {"detected_symptoms": [...], "recommended_specialties": [...], "best_provider": ... }

    LIMITE CONNUE : pas encore de waiting_time_days exposé par /optimize,
    donc le critère "urgence + délai trop long" n'est pas encore évaluable.
    """
    if not optimize_result:
        return True, "Aucune réponse du module cabinet"

    if not optimize_result.get("best_provider"):
        return True, "Aucun médecin/cabinet recommandé par le système"

    if not optimize_result.get("recommended_specialties"):
        return True, "Aucune spécialité identifiée par le système"

    return False, "Cabinet disponible"


# ─────────────────────────────────────────────────────────────────────────
# Schémas Pydantic
# ─────────────────────────────────────────────────────────────────────────


class HospitalRecommendRequest(BaseModel):
    specialty: Optional[str] = Field(None, description="Spécialité recherchée")
    is_urgent: bool = False
    budget: Optional[float] = None
    location: Optional[str] = None
    top_k: int = 3


class OptimizeResult(BaseModel):
    detected_symptoms: List[str] = []
    recommended_specialties: List[dict] = []
    best_provider: Optional[str] = None
    raw_output: Optional[str] = None


class CombinedRecommendRequest(HospitalRecommendRequest):
    cabinet_optimize_result: Optional[OptimizeResult] = None


class HospitalResult(BaseModel):
    hospital_name: str
    specialty: str
    location: str
    quality_score: float
    average_cost: float
    waiting_time_days: float
    available_beds: int
    accepts_cnam: bool
    has_emergency: bool


class HospitalRecommendResponse(BaseModel):
    fallback_triggered: bool
    reason: str
    hospitals: List[HospitalResult]


# ─────────────────────────────────────────────────────────────────────────
# Router FastAPI
# ─────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/hospitals", tags=["hospitals"])


def _build_response(ranked: pd.DataFrame, fallback_triggered: bool, reason: str) -> HospitalRecommendResponse:
    hospitals = [
        HospitalResult(
            hospital_name=row["hospital_name"],
            specialty=row["specialty"],
            location=row["location"],
            quality_score=row["quality_score"],
            average_cost=row["average_cost"],
            waiting_time_days=row["waiting_time_days"],
            available_beds=int(row["available_beds"]),
            accepts_cnam=bool(row["accepts_cnam"]),
            has_emergency=bool(row["has_emergency"]),
        )
        for _, row in ranked.iterrows()
    ]
    return HospitalRecommendResponse(fallback_triggered=fallback_triggered, reason=reason, hospitals=hospitals)


@router.post("/recommend", response_model=HospitalRecommendResponse)
def recommend_hospitals(payload: HospitalRecommendRequest):
    """Appel direct (tests manuels) : la spécialité est obligatoire ici."""
    if not payload.specialty:
        raise HTTPException(400, "Le champ 'specialty' est requis pour un appel direct.")

    ranked = hospital_filter.optimize_hospitals_nsga(
        payload.specialty,
        top_k=payload.top_k,
        budget=payload.budget,
        location=payload.location,
        is_urgent=payload.is_urgent,
    )
    return _build_response(ranked, fallback_triggered=True, reason="Appel direct au module hôpitaux")


@router.post("/recommend-with-fallback", response_model=HospitalRecommendResponse)
def recommend_with_fallback(payload: CombinedRecommendRequest):
    """
    À appeler juste après /optimize ou /analyze-full : passe leur réponse
    telle quelle dans `cabinet_optimize_result`.
    """
    optimize_dict = payload.cabinet_optimize_result.dict() if payload.cabinet_optimize_result else None
    fallback, reason = should_fallback_to_hospital(optimize_dict, is_urgent=payload.is_urgent)

    if not fallback:
        return HospitalRecommendResponse(fallback_triggered=False, reason=reason, hospitals=[])

    specialty = payload.specialty
    if not specialty and optimize_dict and optimize_dict.get("recommended_specialties"):
        specialty = optimize_dict["recommended_specialties"][0].get("specialty")
    if not specialty:
        specialty = "General Practitioner"

    ranked = hospital_filter.optimize_hospitals_nsga(
        specialty,
        top_k=payload.top_k,
        budget=payload.budget,
        location=payload.location,
        is_urgent=payload.is_urgent,
    )
    return _build_response(ranked, fallback_triggered=True, reason=reason)