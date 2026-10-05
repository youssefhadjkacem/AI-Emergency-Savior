import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional

from src.nsga2 import WITHIN_FRONT_COMPROMISE, nsga2_rank

# Ordre des 7 objectifs dans la matrice passée à `nsga2_rank`.
OBJECTIVES = ("quality", "cost", "wait", "slots", "location", "cnam", "teleconsultation")

QUALITY_WEIGHT = 3.0
# Poids de la NOTE du prestataire dans le score de compromis ; les six autres
# critères pèsent 1 (hors gravité, voir URGENCY_WEIGHT).
# C'est un CHOIX DE CONCEPTION ASSUMÉ, pas une valeur optimisée : on donne
# la priorité clinique à la qualité du prestataire sur le coût et le délai.
# Pourquoi il faut un poids : avec des poids égaux, la note pèse autant
# qu'un critère binaire (CNAM, téléconsultation, même ville), qui vaut
# toujours 0 ou 1 en entier alors que la note varie peu d'un médecin à
# l'autre. Mesuré dans backend/testing (round2_report.md) : note moyenne du
# Top 3 de 6,98 sur 10 à poids égaux (47e centile de la spécialité), 7,60 à
# x2, 8,30 à x3 (75e centile), 8,82 à x5. x3 a été retenu par décision du
# projet comme compromis entre note, coût et délai. Aucune donnée d'issue
# clinique ne permet de le calibrer.
# La monotonie du classement (dégrader un critère n'améliore jamais le
# rang) tient pour tout jeu de poids positifs, donc aussi pour celui-ci.

URGENCY_WEIGHT = {"HIGH": 2.0, "CRITICAL": 3.0}
# Poids donné au DÉLAI de rendez-vous et à la PROXIMITÉ dans le score de
# compromis quand la gravité estimée est HIGH ou CRITICAL (1.0 sinon, comme
# tous les autres critères). Un cas grave a d'abord besoin d'un médecin
# proche et disponible vite ; la qualité, le coût et le reste continuent de
# compter, avec leur poids normal. 2 et 3 sont des valeurs simples et
# croissantes, pas des valeurs calibrées : la base K2 ne contient aucune
# donnée d'issue clinique qui permettrait de les ajuster.
# Ces poids n'agissent qu'À L'INTÉRIEUR d'un front de Pareto : ils ne
# peuvent pas faire passer un médecin dominé devant celui qui le domine.


def objective_weights(severity_level: Optional[str]) -> List[float]:
    """Poids des 7 objectifs pour le score de compromis, selon la gravité."""
    urgency = URGENCY_WEIGHT.get(severity_level or "", 1.0)
    weights = {name: 1.0 for name in OBJECTIVES}
    weights["quality"] = QUALITY_WEIGHT
    weights["wait"] = weights["location"] = urgency
    return [weights[name] for name in OBJECTIVES]

def allocate_top_slots(scores: List[float], k: int = 3) -> List[int]:
    """
    Répartit `k` places de recommandation entre des spécialités, en
    proportion de leur score de classification (méthode du plus fort reste).

    `scores` : scores des spécialités, de la mieux classée à la moins bien
    classée. Retourne le nombre de places de chacune, dans le même ordre.

    Règle :
      1. chaque spécialité reçoit la partie entière de `k x score / total` ;
      2. les places restantes vont aux plus grands restes (à égalité, à la
         spécialité la mieux classée) ;
      3. la première spécialité a toujours au moins une place.

    Exemples avec k = 3 :
      [98.1, 0.7, 0.5]   -> [3, 0, 0]   classification nette : rien ne change
      [67.5, 11.8, 8.8]  -> [2, 1, 0]
      [54.9, 39.1, 1.3]  -> [2, 1, 0]
      [42.3, 42.0, 15.7] -> [1, 1, 1]   classification indécise : 3 spécialités

    Pourquoi cette règle : la diversité du Top 3 suit l'incertitude du
    classifieur. Quand il est sûr de lui, le patient reçoit trois médecins
    de la même spécialité, comme avant. Quand deux spécialités sont proches,
    les deux sont proposées, ce qui laisse une chance de rattraper une
    erreur de classification sans diluer une bonne réponse.
    """
    if not scores or k <= 0:
        return [0] * len(scores)
    total = float(sum(scores))
    if total <= 0:
        return [k] + [0] * (len(scores) - 1)

    quotas = [k * s / total for s in scores]
    slots = [int(q) for q in quotas]
    by_remainder = sorted(range(len(scores)), key=lambda i: (-(quotas[i] - slots[i]), i))
    for i in by_remainder[: k - sum(slots)]:
        slots[i] += 1

    if slots[0] == 0:  # impossible si scores est trié, garde-fou sinon
        donor = max(range(len(slots)), key=lambda i: slots[i])
        slots[donor] -= 1
        slots[0] += 1
    return slots


class ProviderFilter:
    """
    Filtre les prestataires selon la nouvelle base K2 et K3 (7 features).
    """

    def __init__(self, providers_file: str, specialist_file: str):
        try:
            # On ignore la première ligne qui contient le titre de la base K2
            self.df_providers = pd.read_excel(providers_file, sheet_name='Médecins', skiprows=1)
            
            # Renommer les colonnes pour que l'ancien code ne casse pas, tout en gardant l'info
            column_mapping = {
                'Nom complet': 'provider_name',
                'Spécialité': 'specialty',
                'Ville': 'location',
                'Score qualité': 'quality_score',
                'Coût consult. (TND)': 'average_cost',
                'Délai RDV (jours)': 'waiting_time_days',
                'Téléconsult.': 'teleconsultation',
                'Créneaux/sem.': 'available_slots',
                'CNAM': 'accepts_cnam'
            }
            self.df_providers.rename(columns=column_mapping, inplace=True)
            
            # Conversion des valeurs textuelles (Oui/Non) en booléens
            for col in ['teleconsultation', 'accepts_cnam']:
                if col in self.df_providers.columns:
                    self.df_providers[col] = self.df_providers[col].apply(
                        lambda x: 1 if str(x).strip().lower() == 'oui' else 0
                    )

            self.is_ready = True
            # "compromise" (défaut) ou "knn_density" (ancien tri, gardé pour
            # mesurer l'avant / après) — voir nsga2.nsga2_rank.
            self.ranking_mode = WITHIN_FRONT_COMPROMISE

        except Exception as e:
            self.df_providers = None
            self.is_ready = False

    def filter_by_specialty_name(self, specialty_name: str, sort_by: str = 'quality_score', ascending: bool = False, top_n: Optional[int] = None) -> pd.DataFrame:
        if not self.is_ready or self.df_providers is None:
            return pd.DataFrame()
            
        # Filtre exact (les noms correspondent exactement car K1 et K2 sont alignés)
        mask = self.df_providers['specialty'].str.lower() == specialty_name.lower()
        filtered = self.df_providers[mask].copy()
        
        if filtered.empty:
            return pd.DataFrame()
            
        if sort_by in filtered.columns:
            filtered = filtered.sort_values(by=sort_by, ascending=ascending)
            
        if top_n is not None:
            filtered = filtered.head(top_n)
            
        return filtered

    def get_top_providers(self, specialties: List[str], top_n: int = 5, sort_by: str = 'quality_score', budget: float = None, location: str = None, weight_quality: float = 0.5, weight_cost: float = 0.3, weight_proximity: float = 0.2) -> Dict[str, pd.DataFrame]:
        results = {}
        for spec in specialties:
            filtered = self.filter_by_specialty_name(spec, sort_by=sort_by, ascending=False, top_n=top_n)
            if not filtered.empty:
                results[spec] = filtered
        return results

    def optimize_providers_nsga(self, specialty_name: str, top_k: int = 3, budget: float = None, location: str = None,
                                severity_level: Optional[str] = None) -> pd.DataFrame:
        """
        K3/K4/K5 Optimisation NSGA-II sur les 7 paramètres du médecin.
        1. Qualité (à maximiser)
        2. Coût (à minimiser ou optimiser autour du budget)
        3. Délai RDV (à minimiser)
        4. Créneaux (à maximiser)
        5. Distance / Localisation (à minimiser)
        6. CNAM (à maximiser)
        7. Téléconsultation (à maximiser)
        """
        filtered = self.filter_by_specialty_name(specialty_name)
        if filtered.empty:
            return pd.DataFrame()
            
        df_opt = filtered.copy()
        n = len(df_opt)
        
        # NSGA-II dans nsga2.py fait une minimisation !
        # Donc tout ce qu'on veut MAXIMISER, on doit le rendre NÉGATIF.
        
        # 1. Quality (MAX -> *-1)
        obj_quality = -df_opt['quality_score'].fillna(0).values
        
        # 2. Cost (MIN -> tel quel, ou distance au budget)
        costs = df_opt['average_cost'].fillna(0).values
        if budget is not None:
            # On minimise la différence absolue (excès)
            obj_cost = np.abs(costs - budget)
        else:
            obj_cost = costs
            
        # 3. Wait time (MIN -> tel quel)
        obj_wait = df_opt['waiting_time_days'].fillna(30).values
        
        # 4. Slots (MAX -> *-1)
        obj_slots = -df_opt['available_slots'].fillna(0).values
        
        # 5. Location (MIN distance -> mock simple si location textuelle)
        if location is not None:
            # Si même ville = distance 0, sinon distance 1
            obj_loc = np.where(df_opt['location'].str.lower() == location.lower(), 0, 1)
        else:
            obj_loc = np.zeros(n)
            
        # 6. CNAM (MAX -> *-1)
        obj_cnam = -df_opt['accepts_cnam'].fillna(0).values
        
        # 7. Téléconsult (MAX -> *-1)
        obj_tele = -df_opt['teleconsultation'].fillna(0).values
        
        # Matrice d'objectifs (N, 7)
        objs = np.column_stack([
            obj_quality,
            obj_cost,
            obj_wait,
            obj_slots,
            obj_loc,
            obj_cnam,
            obj_tele
        ])
        
        try:
            ranked_indices = nsga2_rank(objs, within_front=self.ranking_mode,
                                        weights=objective_weights(severity_level))
            # Reordonner le dataframe
            df_opt = df_opt.iloc[ranked_indices]
            return df_opt.head(top_k)
        except Exception as e:
            print(f"Erreur NSGA-II : {e}")
            return df_opt.head(top_k)

    def diversified_top_providers(self, top_specialties: List[Tuple[str, float]], top_k: int = 3,
                                  budget: float = None, location: str = None,
                                  severity_level: Optional[str] = None) -> pd.DataFrame:
        """
        Top `top_k` de prestataires tiré de PLUSIEURS spécialités.

        Avant : les `top_k` prestataires venaient tous de la première
        spécialité prédite. Une erreur de classification rendait donc les
        trois recommandations fausses, même quand la bonne spécialité était
        classée deuxième.

        Maintenant :
          1. les places sont réparties entre les spécialités renvoyées par
             la classification, en proportion de leur score
             (`allocate_top_slots`) ;
          2. dans chaque spécialité, les prestataires sont pris dans l'ordre
             de `optimize_providers_nsga`, qui n'est pas modifié ;
          3. l'ordre final alterne les spécialités : le meilleur de la 1re,
             le meilleur de la 2e, puis les suivants. Le n°1 reste donc
             exactement le même qu'avant, et l'alternative éventuelle
             apparaît dès la 2e position.

        Deux colonnes sont ajoutées : `specialty_rank` (1 = première
        spécialité prédite) et `specialty_score` (score de classification).
        """
        if not top_specialties:
            return pd.DataFrame()

        ranked = {}
        for spec, _ in top_specialties:
            ranked[spec] = self.optimize_providers_nsga(spec, top_k=top_k, budget=budget, location=location,
                                                        severity_level=severity_level)

        slots = allocate_top_slots([score for _, score in top_specialties], top_k)
        # Une spécialité sans assez de prestataires rend ses places à la
        # spécialité la mieux classée qui en a encore.
        for i, (spec, _) in enumerate(top_specialties):
            surplus = slots[i] - len(ranked[spec])
            if surplus > 0:
                slots[i] -= surplus
                for j, (other, _) in enumerate(top_specialties):
                    spare = len(ranked[other]) - slots[j]
                    take = min(surplus, max(0, spare)) if j != i else 0
                    slots[j] += take
                    surplus -= take

        rows = []
        for depth in range(top_k):
            for i, (spec, score) in enumerate(top_specialties):
                if depth < slots[i]:
                    row = ranked[spec].iloc[depth].copy()
                    row["specialty_rank"] = i + 1
                    row["specialty_score"] = score
                    rows.append(row)
        return pd.DataFrame(rows).head(top_k) if rows else pd.DataFrame()

