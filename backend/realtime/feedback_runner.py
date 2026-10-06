"""
Exécution en parallèle des simulations de la boucle de rétroaction
(Phase 3). Une tâche = (seed, niveau de charge, condition, paramètres) ;
elle renvoie les métriques de `feedback_evaluation.evaluate_run`.

Chaque processus charge une fois la base des prestataires et garde le
dernier monde construit : les tâches sont regroupées par (seed, charge).
"""

from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .feedback_simulator import CONDITIONS, LOAD_LEVELS, FeedbackConfig, SystemParams, build_world, run_condition

_source = None
_world_cache: Dict[tuple, object] = {}


@dataclass(frozen=True)
class Task:
    seed: int
    load: str
    condition: str
    params: SystemParams = SystemParams()
    follow_probabilities: Tuple[float, float, float] = (0.70, 0.20, 0.10)

    def world_key(self) -> tuple:
        return (self.seed, self.load, self.follow_probabilities)


def _get_source():
    global _source
    if _source is None:
        from .adaptation import SpaceRankingSource

        _source = SpaceRankingSource()
    return _source


def world_for(task: Task):
    key = task.world_key()
    if key not in _world_cache:
        _world_cache.clear()  # un seul monde en mémoire par processus
        _world_cache[key] = build_world(
            FeedbackConfig(seed=task.seed, arrivals_per_hour=LOAD_LEVELS[task.load],
                           follow_probabilities=task.follow_probabilities),
            _get_source(),
        )
    return _world_cache[key]


def run_task(task: Task) -> dict:
    from .feedback_evaluation import evaluate_run

    log = run_condition(world_for(task), _get_source(), CONDITIONS[task.condition], task.params)
    return {
        "seed": task.seed, "load": task.load, "condition": task.condition,
        "params": asdict(task.params), "follow_probabilities": list(task.follow_probabilities),
        "metrics": evaluate_run(log),
    }


def _run_group(tasks: List[Task]) -> List[dict]:
    return [run_task(task) for task in tasks]


def run_many(tasks: List[Task], workers: Optional[int] = None) -> List[dict]:
    """Exécute les tâches, regroupées par monde, sur plusieurs processus.
    L'ordre des résultats suit celui des tâches."""
    groups: Dict[tuple, List[Task]] = {}
    for task in tasks:
        groups.setdefault(task.world_key(), []).append(task)
    # Un groupe trop long monopoliserait un processus : découpe par 8.
    chunks = [group[i:i + 8] for group in groups.values() for i in range(0, len(group), 8)]
    # Par défaut la moitié des cœurs, pour laisser la machine utilisable ;
    # FEEDBACK_WORKERS permet d'en demander plus ou moins.
    workers = workers or int(os.environ.get("FEEDBACK_WORKERS", 0)) or max(1, (os.cpu_count() or 2) // 2)
    workers = max(1, min(workers, len(chunks)))
    results: Dict[Task, dict] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for chunk, chunk_results in zip(chunks, pool.map(_run_group, chunks)):
            for task, result in zip(chunk, chunk_results):
                results[task] = result
    return [results[task] for task in tasks]
