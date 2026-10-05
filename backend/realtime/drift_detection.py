"""
Détection de concept drift sur la disponibilité lissée — section 3.8 du
papier, Phase 2. Deux détecteurs River comparés : ADWIN et Page-Hinkley.

Entrée : la série de disponibilité lissée (EWMA) d'UN provider, produite par
le pipeline de la Phase 1 (`drift_simulator.build_smoothed_series` en
simulation). Sortie : les index de la série où un drift est signalé.

Périmètre : détection uniquement. Aucune réaction (seuil de saturation
80-90%, bascule de recommandation) — c'est l'étape suivante.

Chaque détecteur est un wrapper à état, utilisable de deux façons :
  - en ligne   : `update(value) -> bool`, un point à la fois (usage cible
                 en production, branché derrière le pipeline de filtrage) ;
  - hors ligne : `detect(series) -> List[int]`, qui repart d'un détecteur
                 neuf et rejoue la série point par point (évaluation).
Un provider = une instance = un état indépendant, comme pour
`ProviderAnomalyDetector` en Phase 1.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable, Dict, List, Sequence

from river import drift

# ═════════════════════════════════════════════════════════════════════════
# ADWIN (Bifet & Gavaldà, 2007)
# ═════════════════════════════════════════════════════════════════════════
#
# Principe : fenêtre glissante de taille adaptative. À chaque vérification,
# ADWIN cherche une coupure de la fenêtre en deux sous-fenêtres dont les
# moyennes diffèrent plus qu'un seuil statistique dépendant de `delta` ;
# si elle existe, la partie ancienne est abandonnée et un drift est signalé.

# ── Valeurs de départ et réglage ─────────────────────────────────────────
# Point de départ : les défauts River (`RIVER_DEFAULT_PARAMS` plus bas),
# c.-à-d. delta=0.002 (valeur de l'article d'origine) et clock=32. Évalués
# sur les seeds de réglage 100/101/102 (`run_drift_tuning.py`, résultats
# dans results/drift_tuning_report.json), ils sont inutilisables ici : au
# moins une fausse alerte sur 88% des séries stables, F1 épisode 0.50
# (progressif) / 0.34 (brusque). Deux causes, une par paramètre.

ADWIN_DELTA = 1e-12
# Niveau de confiance du test : borne sur la probabilité de couper la
# fenêtre à tort — borne qui suppose des points INDÉPENDANTS. Or la série
# analysée sort d'une EWMA : chaque point contient ~90% du précédent, donc
# la série "garde le cap" sur des dizaines de points par simple inertie, et
# ADWIN prend ces ondulations pour des changements de moyenne. Le delta
# nominal sous-estime massivement le taux réel de fausses alertes : il faut
# descendre à 1e-10 pour ne plus en avoir sur les séries stables.
# Grille {2e-3, 1e-4, 1e-6, 1e-8, 1e-10, 1e-12, 1e-15, 1e-20} : 1e-12
# maximise le F1 épisode moyen (0.992 progressif / 1.000 brusque, 0 série
# stable en fausse alerte). Au-delà (1e-15), ADWIN devient trop lent pour
# signaler un drift brusque dans sa fenêtre d'épisode (F1 brusque 0.78).
# delta n'a donc plus ici de sens probabiliste : c'est un simple réglage
# de sensibilité, à re-régler si tau (EWMA) ou la cadence des mises à jour
# changent.

ADWIN_CLOCK = 1
# Fréquence du test (tous les `clock` points). Le défaut River (32) est
# pensé pour des flux de millions de points ; ici 32 points lissés ≈ 70 min
# et la fenêtre d'épisode d'un drift brusque fait ~33 points : un test sur
# deux arrive trop tard. À delta égal (1e-12), clock=32 fait chuter le F1
# brusque de 1.000 à 0.283. Avec clock=1 le test est fait à chaque point ;
# le coût est négligeable à ce volume.

ADWIN_MIN_WINDOW_LENGTH = 5
ADWIN_GRACE_PERIOD = 10
# Défauts River conservés (hors grille) : taille minimale d'une
# sous-fenêtre pour être comparée (5) et nombre de points avant le premier
# test (10).


class AdwinDriftDetector:
    name = "adwin"

    def __init__(
        self,
        delta: float = ADWIN_DELTA,
        clock: int = ADWIN_CLOCK,
        min_window_length: int = ADWIN_MIN_WINDOW_LENGTH,
        grace_period: int = ADWIN_GRACE_PERIOD,
    ):
        self.params = {
            "delta": delta,
            "clock": clock,
            "min_window_length": min_window_length,
            "grace_period": grace_period,
        }
        self._detector = drift.ADWIN(**self.params)

    def update(self, value: float) -> bool:
        self._detector.update(float(value))
        return self._detector.drift_detected

    def detect(self, series: Sequence[float]) -> List[int]:
        return _replay(type(self)(**self.params), series)


# ═════════════════════════════════════════════════════════════════════════
# Page-Hinkley (Page, 1954)
# ═════════════════════════════════════════════════════════════════════════
#
# Principe : test séquentiel de type CUSUM. On cumule l'écart de chaque
# point à la moyenne courante de la série, diminué d'une tolérance `delta` ;
# un drift est signalé quand ce cumul s'éloigne de son extremum de plus de
# `threshold`. River réinitialise le détecteur après chaque détection.

# ── Valeurs de départ et réglage ─────────────────────────────────────────
# Point de départ : les défauts River (delta=0.005, threshold=50). Sur les
# seeds de réglage : fausse alerte sur 55% des séries stables, F1 épisode
# 0.63 (progressif) / 0.65 (brusque). Cause : delta=0.005 suppose une série
# normalisée dans [0, 1], alors que la nôtre est en places (0-15).

PH_MIN_INSTANCES = 30
# Nombre de points avant tout signalement (défaut River, hors grille).
# 30 points lissés ≈ 65 min : le temps d'estimer une moyenne de référence
# pour un provider avant de juger ses écarts. Conséquence : après chaque
# détection (River réinitialise le détecteur), Page-Hinkley est aveugle
# pendant 30 points.

PH_DELTA = 1.5
# Amplitude de variation tolérée, en places : un écart à la moyenne
# courante inférieur à delta ne s'accumule pas. L'écart-type de la série
# lissée en régime stable est ~0.5 place ; delta=1.5 ≈ 3 écarts-types, donc
# les ondulations lentes de l'EWMA ne s'accumulent presque jamais.
# Grille {0.005, 0.1, 0.25, 0.5, 1.0, 1.5} x threshold {5, 10, 20, 35, 50,
# 80} : (1.5, 10) est la seule des 3 configurations à F1 = 1.000 sur les
# deux profils à avoir un délai court (55 points progressif / 11 brusque,
# contre 73 / 23 pour la suivante).
# RÉSERVE IMPORTANTE : 1.5 est le BORD de la grille, et ce n'est pas un
# hasard — tous les drifts simulés font 4 à 6 places, donc plus delta est
# grand, mieux il sépare. La grille n'a volontairement pas été étendue
# au-delà : delta fixe aussi le plus petit drift détectable. Un drift
# durable de moins de ~1.5 place est invisible pour cette configuration
# (voir le test d'amplitude réduite dans le rapport d'évaluation).

PH_THRESHOLD = 10.0
# Seuil lambda sur le cumul des écarts (en places x points). Une fois la
# série lissée descendue de ~5 places, chaque point ajoute ~3.5 au cumul
# (5 - delta) : le seuil est franchi en ~3 points. Un seuil bas suffit
# parce que delta filtre déjà le bruit en amont ; le défaut River (50) avec
# le même delta retarde la détection brusque de 11 à 26 points et fait
# sortir des drifts brusques de leur fenêtre d'épisode (F1 0.92).

PH_ALPHA = 0.9999
# Facteur d'oubli du cumul (défaut River, quasi sans oubli, hors grille).

PH_MODE = "both"
# Détection dans les deux sens. La saturation correspond à une BAISSE de
# disponibilité, mais un retour à la normale est aussi un changement de
# régime que l'étape suivante devra connaître ; le sens du drift se lit de
# toute façon sur la série elle-même.


class PageHinkleyDriftDetector:
    name = "page_hinkley"

    def __init__(
        self,
        min_instances: int = PH_MIN_INSTANCES,
        delta: float = PH_DELTA,
        threshold: float = PH_THRESHOLD,
        alpha: float = PH_ALPHA,
        mode: str = PH_MODE,
    ):
        self.params = {
            "min_instances": min_instances,
            "delta": delta,
            "threshold": threshold,
            "alpha": alpha,
            "mode": mode,
        }
        self._detector = drift.PageHinkley(**self.params)

    def update(self, value: float) -> bool:
        self._detector.update(float(value))
        return self._detector.drift_detected

    def detect(self, series: Sequence[float]) -> List[int]:
        return _replay(type(self)(**self.params), series)


class ParallelDriftDetector:
    """
    ADWIN et Page-Hinkley en parallèle sur la même série : un drift est
    signalé dès que L'UN des deux le signale (union des signalements).
    Chaque détecteur garde son propre état et ses propres hyperparamètres ;
    rien n'est re-réglé pour la combinaison. Évalué dans
    `run_drift_evaluation.py` pour décider si les deux valent mieux qu'un.
    """

    name = "adwin_or_page_hinkley"

    def __init__(self):
        self.params = {"adwin": AdwinDriftDetector().params, "page_hinkley": PageHinkleyDriftDetector().params}
        self._detectors = [AdwinDriftDetector(), PageHinkleyDriftDetector()]

    def update(self, value: float) -> bool:
        # Pas de court-circuit : les deux détecteurs doivent voir chaque point.
        return any([d.update(value) for d in self._detectors])

    def detect(self, series: Sequence[float]) -> List[int]:
        return _replay(type(self)(), series)


def _replay(detector, series: Sequence[float]) -> List[int]:
    return [i for i, value in enumerate(series) if detector.update(value)]


class DriftAlgorithm(str, Enum):
    adwin = "adwin"
    page_hinkley = "page_hinkley"


# Défauts de River 0.26, gardés comme point de comparaison ("valeurs de
# départ") dans le réglage et dans le rapport d'évaluation.
RIVER_DEFAULT_PARAMS: Dict[DriftAlgorithm, dict] = {
    DriftAlgorithm.adwin: {"delta": 0.002, "clock": 32},
    DriftAlgorithm.page_hinkley: {"delta": 0.005, "threshold": 50.0},
}

DETECTOR_FACTORY: Dict[DriftAlgorithm, Callable[..., object]] = {
    DriftAlgorithm.adwin: AdwinDriftDetector,
    DriftAlgorithm.page_hinkley: PageHinkleyDriftDetector,
}


def detect_drift(series: Sequence[float], algorithm: DriftAlgorithm, **params) -> List[int]:
    """Index de `series` où `algorithm` signale un drift (détecteur neuf,
    hyperparamètres par défaut du module sauf surcharge via `params`)."""
    return DETECTOR_FACTORY[algorithm](**params).detect(series)
