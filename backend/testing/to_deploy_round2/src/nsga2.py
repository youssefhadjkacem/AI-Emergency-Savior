import numpy as np
from typing import List, Optional, Sequence

def fast_non_dominated_sort(objs: np.ndarray) -> List[List[int]]:
    """
    Implémentation vectorisée et optimisée du tri Pareto de NSGA-II (Efficacité améliorée).
    Réduit drastiquement le temps d'exécution grâce à Numpy (bypasse les boucles imbriquées).
    """
    n = objs.shape[0]
    
    # Réduction vectorielle des comparaisons (Point 2.a: Fast Non-Dominated Sorting Optimisé)
    diff = objs[:, np.newaxis, :] - objs[np.newaxis, :, :]
    less_equal = np.all(diff <= 0, axis=-1)
    strictly_less = np.any(diff < 0, axis=-1)
    
    domination_matrix = less_equal & strictly_less
    
    # Nombre de solutions qui dominent p
    n_dom = np.sum(domination_matrix, axis=0)
    
    # Solutions que p domine
    S = [np.where(domination_matrix[p, :])[0].tolist() for p in range(n)]
    
    fronts = []
    current_front = np.where(n_dom == 0)[0].tolist()
    
    while current_front:
        fronts.append(current_front)
        next_front = []
        for p in current_front:
            for q in S[p]:
                n_dom[q] -= 1
                if n_dom[q] == 0:
                    next_front.append(q)
        current_front = next_front
        
    return fronts


def knn_density_distance(objs: np.ndarray, front: List[int], k_neighbors: int = 3) -> np.ndarray:
    """
    AMÉLIORATION (Idée 1.c: Densité Améliorée) : Utilisation de la Densité KNN.
    Remplace la Crowding Distance classique. KNN mesure la distance réelle vers les plus proches voisins.
    """
    l = len(front)
    if l <= 1:
        return np.array([np.inf])
    if l <= 2:
        return np.array([np.inf, np.inf])

    k = min(k_neighbors, l - 1)
    F = objs[front, :]

    maxv = F.max(axis=0)
    minv = F.min(axis=0)
    rng = maxv - minv
    rng[rng == 0] = 1.0  
    F_norm = (F - minv) / rng

    diff = F_norm[:, np.newaxis, :] - F_norm[np.newaxis, :, :]
    distances = np.sqrt(np.sum(diff ** 2, axis=-1))
    distances.sort(axis=1)
    
    knn_dist = np.mean(distances[:, 1:k+1], axis=1)

    for m in range(F.shape[1]):
        sorted_idx = np.argsort(F[:, m])
        knn_dist[sorted_idx[0]] = np.inf
        knn_dist[sorted_idx[-1]] = np.inf

    return knn_dist


WITHIN_FRONT_COMPROMISE = "compromise"
WITHIN_FRONT_KNN_DENSITY = "knn_density"


def compromise_scores(objs: np.ndarray, weights: Optional[Sequence[float]] = None) -> np.ndarray:
    """
    Score de compromis de chaque point : moyenne ponderee de ses objectifs
    normalises entre 0 (meilleure valeur observee) et 1 (pire valeur
    observee). Tous les objectifs sont a minimiser, donc PLUS LE SCORE EST
    BAS, MEILLEUR EST LE POINT.

    La normalisation min-max se fait sur l'ENSEMBLE des candidats, pas front
    par front : un cout de 80 vaut le meme score quel que soit le front.
    Un objectif constant (tous les candidats egaux) vaut 0 pour tous.

    `weights` : un poids positif par objectif ; poids egaux par defaut. Des
    poids egaux sont un choix, pas une neutralite : chaque critere, y compris
    binaire (CNAM, teleconsultation, meme ville), pese autant que la qualite.
    """
    objs = np.asarray(objs, dtype=float)
    low, high = objs.min(axis=0), objs.max(axis=0)
    span = high - low
    span[span == 0] = 1.0
    normalized = (objs - low) / span
    w = np.ones(objs.shape[1]) if weights is None else np.asarray(weights, dtype=float)
    return normalized @ w / w.sum()


def nsga2_rank(objs: np.ndarray, within_front: str = WITHIN_FRONT_COMPROMISE,
               weights: Optional[Sequence[float]] = None) -> List[int]:
    """
    Classe les points : d'abord par front de Pareto, puis a l'interieur de
    chaque front.

    DEFAUT CORRIGE. A l'interieur d'un front, l'ancien tri utilisait la
    densite k-NN, qui recompense le fait d'etre ISOLE, et donnait une
    densite infinie aux deux extremites de chaque objectif, la meilleure
    comme la pire. Un medecin devenu le plus cher de sa specialite passait
    donc en tete de son front. Verifie par un test isole : un cardiologue
    classe 10e a son prix reel passe 3e des que son prix augmente de 20 %,
    exactement comme s'il etait a moitie prix.

    La densite a un sens dans un algorithme genetique, ou elle preserve la
    diversite d'une population d'une generation a l'autre. Ici il n'y a pas
    de generations : on classe une liste fixe pour recommander les
    meilleurs, et la diversite n'est pas un critere de qualite.

    `within_front="compromise"` (defaut) : a l'interieur d'un front, les
    points sont tries par `compromise_scores` croissant. La densite k-NN ne
    sert plus qu'a departager deux points de score rigoureusement egal.

    Propriete obtenue : degrader un point sur un objectif, toutes choses
    egales par ailleurs, ne peut JAMAIS ameliorer son rang.
      - son front ne peut pas s'ameliorer (il est domine par au moins autant
        de points qu'avant) ;
      - face a n'importe quel autre point, sa part de score sur l'objectif
        degrade augmente au moins autant que celle de l'autre.

    `within_front="knn_density"` : ancien comportement, conserve a
    l'identique pour mesurer l'avant / apres.
    """
    if len(objs) <= 1:
        return [0] if len(objs) == 1 else []

    # 1) Non Dominating Sort (Vitesse améliorée)
    fronts = fast_non_dominated_sort(objs)
    ordered = []

    if within_front == WITHIN_FRONT_KNN_DENSITY:
        # 2) Triage intra-front (ancien comportement)
        for front in fronts:
            if len(front) == 0:
                continue

            if len(front) <= 2:
                ordered.extend(front)
            else:
                # Densité KNN (Précision améliorée)
                distances = knn_density_distance(objs, front, k_neighbors=3)

                sort_indices = np.argsort(-distances)
                sorted_front = [front[i] for i in sort_indices]
                ordered.extend(sorted_front)
        return ordered

    scores = compromise_scores(objs, weights)
    for front in fronts:
        if len(front) == 0:
            continue
        density = knn_density_distance(objs, front, k_neighbors=3)
        # Score de compromis croissant ; a score egal (arrondi a 1e-12 pour
        # ignorer le bruit de virgule flottante), le point le plus isole
        # d'abord ; puis l'ordre d'entree, pour un resultat deterministe.
        keys = sorted(range(len(front)), key=lambda i: (round(float(scores[front[i]]), 12), -float(density[i]), front[i]))
        ordered.extend(front[i] for i in keys)

    return ordered
