"""
Réaction à la saturation — section 3.8 du papier, Phase 3.

Reclasse les prestataires d'UNE spécialité à partir de leur état de
saturation (`saturation.py`). Le classement de départ est celui du Space
(`src/filtering.py`, `src/nsga2.py`), qui n'est pas modifié.

── Règle ────────────────────────────────────────────────────────────────
Le classement du Space trie par (front de Pareto, score de compromis). La
réaction garde cette clé et y ajoute l'état du prestataire :

  - SATURATED : placé derrière tous les prestataires non saturés de la
    spécialité. Il ne revient dans le Top 3 que s'il reste moins de trois
    prestataires non saturés.
  - ALERT : son score de compromis est augmenté de `alert_penalty` (un score
    plus haut est moins bon). Il recule à l'intérieur de son front, sans
    être écarté.
  - NORMAL : inchangé.

── Garanties ────────────────────────────────────────────────────────────
  1. Sans prestataire en alerte ni saturé, l'ordre est EXACTEMENT celui du
     Space (la clé se réduit à la sienne).
  2. La réaction ne voit que les prestataires d'une spécialité : elle ne
     peut pas changer la spécialité recommandée.
  3. Si tous les prestataires sont saturés, la liste n'est pas vide : c'est
     l'ordre du Space, avec `all_saturated = True` et un message.
  4. Cas CRITICAL avec une ville connue : la réaction ne permute les
     prestataires qu'à l'intérieur de leur groupe (ville du patient /
     autres villes). Les positions tenues par un prestataire de la ville du
     patient le restent. Un patient en état critique n'est donc jamais
     détourné vers une autre ville du fait de la saturation ; si tous les
     prestataires de sa ville sont saturés, ils sont conservés et signalés
     (`saturated_in_top`). La base ne connaît la distance que sous la forme
     "même ville ou non" : c'est la seule notion de "plus lointain"
     disponible.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .saturation import ProviderStatus

DEFAULT_ALERT_PENALTY = 0.02
# Pénalité ajoutée au score de compromis d'un prestataire en ALERT. Le score
# est une moyenne pondérée d'objectifs normalisés entre 0 et 1.
# Réglé sur les seeds 100/101/102 (`run_feedback_tuning.py`), grille
# {0.02, 0.05, 0.10, 0.20}. Effet faible : taux de refus moyen de 2,5 %
# (0,02) à 2,2 % (0,20), au prix d'une note un peu plus basse. La règle de
# choix retient la plus petite, 0,02 (bord bas de la grille).

CRITICAL_LEVEL = "CRITICAL"


@dataclass(frozen=True)
class Candidate:
    """Un prestataire dans le classement de départ d'une spécialité."""

    provider_id: str
    specialty: str
    city: str
    front: int          # front de Pareto (0 = non dominé)
    score: float        # score de compromis du Space (plus bas = meilleur)
    base_rank: int      # rang dans le classement du Space (0 = premier)
    quality: float = 0.0
    cost: float = 0.0
    wait_days: float = 0.0
    name: str = ""


@dataclass
class RerankResult:
    order: List[Candidate]
    statuses: Dict[str, ProviderStatus]
    all_saturated: bool = False
    message: str = ""

    def top(self, k: int = 3) -> List[Candidate]:
        return self.order[:k]

    def saturated_in_top(self, k: int = 3) -> List[str]:
        """Prestataires saturés présents dans le Top k (faute de mieux)."""
        return [c.provider_id for c in self.order[:k] if self.statuses.get(c.provider_id) == ProviderStatus.saturated]


def _same_city(candidate: Candidate, city: Optional[str]) -> bool:
    return bool(city) and candidate.city.strip().lower() == city.strip().lower()


def rerank(
    candidates: Sequence[Candidate],
    statuses: Mapping[str, ProviderStatus],
    severity_level: Optional[str] = None,
    patient_city: Optional[str] = None,
    alert_penalty: float = DEFAULT_ALERT_PENALTY,
) -> RerankResult:
    """
    Reclasse `candidates` (dans l'ordre du Space) selon `statuses`.
    Un prestataire absent de `statuses` est NORMAL.
    """
    base = sorted(candidates, key=lambda c: c.base_rank)
    state = {c.provider_id: statuses.get(c.provider_id, ProviderStatus.normal) for c in base}

    if base and all(s == ProviderStatus.saturated for s in state.values()):
        return RerankResult(
            order=list(base), statuses=state, all_saturated=True,
            message="Tous les prestataires de cette spécialité sont saturés : classement habituel conservé.",
        )

    def key(c: Candidate) -> Tuple[bool, int, float, int]:
        s = state[c.provider_id]
        penalty = alert_penalty if s == ProviderStatus.alert else 0.0
        # Même arrondi que `nsga2_rank` ; le rang de départ départage les
        # égalités, ce qui rend l'ordre identique à celui du Space quand
        # aucun état n'intervient.
        return (s == ProviderStatus.saturated, c.front, round(c.score, 12) + penalty, c.base_rank)

    if severity_level == CRITICAL_LEVEL and patient_city:
        # Règle CRITICAL : permutation à l'intérieur de chaque groupe, les
        # positions de chaque groupe sont conservées.
        local = iter(sorted((c for c in base if _same_city(c, patient_city)), key=key))
        other = iter(sorted((c for c in base if not _same_city(c, patient_city)), key=key))
        order = [next(local) if _same_city(c, patient_city) else next(other) for c in base]
    else:
        order = sorted(base, key=key)

    message = ""
    kept = [c.provider_id for c in order[:3] if state[c.provider_id] == ProviderStatus.saturated]
    if kept:
        message = "Prestataire(s) saturé(s) conservé(s) faute d'alternative : " + ", ".join(kept)
    return RerankResult(order=order, statuses=state, message=message)


# ═════════════════════════════════════════════════════════════════════════
# Pont vers le classement du Space (code local, non modifié)
# ═════════════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parents[2]
SPACE_DIR = Path(os.environ.get("EMERGENCY_SAVIOR_OUTPUT_SRC", REPO_ROOT / "spaces_src" / "emergency-savior-output"))


class RankingDivergence(RuntimeError):
    """Le front et le score recalculés ici ne redonnent pas l'ordre du Space."""


class SpaceRankingSource:
    """
    Classement COMPLET d'une spécialité par le code du Space, avec le front
    et le score de compromis de chaque prestataire.

    Pourquoi ce pont : le Space déployé ne renvoie que trois noms, en texte,
    et `optimize_providers_nsga` ne renvoie ni front ni score. Or la
    réaction a besoin des deux, et de plus de trois candidats.

    L'ORDRE vient de `ProviderFilter.optimize_providers_nsga` lui-même. Le
    front et le score sont recalculés ici avec les fonctions du Space
    (`fast_non_dominated_sort`, `compromise_scores`, `objective_weights`) ;
    seule la construction de la matrice d'objectifs est recopiée de
    `filtering.py`. Chaque classement est vérifié à la construction : si le
    recalcul ne redonne pas l'ordre du Space, `RankingDivergence` est levée.
    """

    def __init__(self, space_dir: Path = SPACE_DIR):
        if not (space_dir / "src" / "filtering.py").exists():
            raise FileNotFoundError(
                f"Code du Space introuvable dans {space_dir}. Cloner le Space "
                "emergency-savior-output ou définir EMERGENCY_SAVIOR_OUTPUT_SRC."
            )
        sys.dont_write_bytecode = True  # pas de __pycache__ dans le clone du Space
        if str(space_dir) not in sys.path:
            # En fin de chemin : le Space contient aussi un `main.py`.
            sys.path.append(str(space_dir))
        from src.filtering import ProviderFilter

        self.provider_filter = ProviderFilter(
            str(space_dir / "K2_Medical_Providers.xlsx"), str(space_dir / "Specialist_Enhanced.xlsx")
        )
        if not self.provider_filter.is_ready:
            raise RuntimeError("Base des prestataires illisible.")
        self._cache: Dict[Tuple[str, Optional[str], Optional[str], Optional[float]], List[Candidate]] = {}

    @property
    def providers(self):
        """Table des prestataires (une ligne par médecin, lignes vides exclues)."""
        df = self.provider_filter.df_providers
        return df[df["specialty"].notna()]

    def specialties(self) -> List[str]:
        return sorted(self.providers["specialty"].unique())

    def ranking(self, specialty: str, location: Optional[str] = None, severity_level: Optional[str] = None,
                budget: Optional[float] = None) -> List[Candidate]:
        """Tous les prestataires de `specialty`, dans l'ordre du Space."""
        # Seuls HIGH et CRITICAL changent les poids (filtering.URGENCY_WEIGHT).
        weight_class = severity_level if severity_level in ("HIGH", "CRITICAL") else None
        cache_key = (specialty, location, weight_class, budget)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._build(specialty, location, weight_class, budget)
        return self._cache[cache_key]

    def _build(self, specialty, location, severity_level, budget) -> List[Candidate]:
        import numpy as np
        from src.filtering import objective_weights
        from src.nsga2 import compromise_scores, fast_non_dominated_sort

        pf = self.provider_filter
        ordered = pf.optimize_providers_nsga(specialty, top_k=10**9, budget=budget, location=location,
                                             severity_level=severity_level)
        if ordered.empty:
            return []

        # Matrice d'objectifs : recopie de `optimize_providers_nsga`, sur les
        # lignes dans l'ordre d'origine (la normalisation n'en dépend pas).
        df = pf.filter_by_specialty_name(specialty)
        costs = df["average_cost"].fillna(0).values
        objs = np.column_stack([
            -df["quality_score"].fillna(0).values,
            np.abs(costs - budget) if budget is not None else costs,
            df["waiting_time_days"].fillna(30).values,
            -df["available_slots"].fillna(0).values,
            np.where(df["location"].str.lower() == location.lower(), 0, 1) if location is not None else np.zeros(len(df)),
            -df["accepts_cnam"].fillna(0).values,
            -df["teleconsultation"].fillna(0).values,
        ])
        scores = compromise_scores(objs, objective_weights(severity_level))
        front_of = {}
        for front_index, members in enumerate(fast_non_dominated_sort(objs)):
            for position in members:
                front_of[position] = front_index
        by_id = {pid: (front_of[i], float(scores[i])) for i, pid in enumerate(df["ID"])}

        candidates = []
        for rank, (_, row) in enumerate(ordered.iterrows()):
            front, score = by_id[row["ID"]]
            candidates.append(Candidate(
                provider_id=row["ID"], specialty=row["specialty"], city=str(row["location"]),
                front=front, score=score, base_rank=rank,
                quality=float(row["quality_score"]), cost=float(row["average_cost"]),
                wait_days=float(row["waiting_time_days"]), name=str(row["provider_name"]),
            ))
        keys = [(c.front, round(c.score, 12)) for c in candidates]
        if keys != sorted(keys):
            raise RankingDivergence(f"{specialty} / {location} / {severity_level} / {budget}")
        return candidates


class AdaptiveRanker:
    """Classement du Space + réaction à la saturation, pour une spécialité."""

    def __init__(self, source: SpaceRankingSource, monitor=None, alert_penalty: float = DEFAULT_ALERT_PENALTY):
        self.source = source
        self.monitor = monitor  # SaturationMonitor, ou None : classement statique
        self.alert_penalty = alert_penalty

    def recommend(self, specialty: str, now: float, location: Optional[str] = None,
                  severity_level: Optional[str] = None, budget: Optional[float] = None) -> RerankResult:
        base = self.source.ranking(specialty, location, severity_level, budget)
        if self.monitor is None:
            return RerankResult(order=list(base), statuses={})
        statuses = {c.provider_id: self.monitor.status(c.provider_id, now) for c in base
                    if c.provider_id in self.monitor}
        return rerank(base, statuses, severity_level, location, self.alert_penalty)
