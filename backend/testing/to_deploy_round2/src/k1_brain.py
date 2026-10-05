import pandas as pd
from typing import List, Dict, Tuple
from collections import defaultdict
import os
import re

from src.config import SPECIALIST_FILE, MODEL_RESULTS_FILE, K1_TREE_FILE
from src.preprocessing import normalize_text

class K1Brain:
    def __init__(self):
        """
        Initialise le Cerveau K1 qui combine le Score de l'Arbre Hiérarchique 
        et la Probabilité Random Forest.
        """
        self._load_knowledge()

    def _normalize_symptom_name(self, name: str) -> str:
        if not isinstance(name, str):
            return ""
        # Convert to lowercase and replace spaces/hyphens with underscores
        norm = name.lower().strip()
        norm = re.sub(r'[\s\-]+', '_', norm)
        return norm

    def _load_knowledge(self):
        # 1. Load Feature Importance (RF)
        df_rf = pd.read_excel(MODEL_RESULTS_FILE, sheet_name='Feature_Importance', skiprows=1)
        self.rf_weights = {}
        for _, row in df_rf.iterrows():
            sym = self._normalize_symptom_name(row['Symptôme'])
            self.rf_weights[sym] = float(row['Importance RF']) if pd.notna(row['Importance RF']) else 0.001

        # 2. Load IDF Weights
        df_idf = pd.read_excel(SPECIALIST_FILE, sheet_name='IDF_Weights', skiprows=1)
        self.idf_weights = {}
        for _, row in df_idf.iterrows():
            sym = self._normalize_symptom_name(row['Symptôme'])
            self.idf_weights[sym] = float(row['Score IDF']) if pd.notna(row['Score IDF']) else 1.0

        # 3. Load Specialist Profiles
        df_profiles = pd.read_excel(SPECIALIST_FILE, sheet_name='Profils_Specialistes', skiprows=1)
        self.specialties = df_profiles['Spécialiste'].dropna().unique().tolist()
        
        self.spec_profiles = {}
        for _, row in df_profiles.iterrows():
            spec = row['Spécialiste']
            if pd.isna(spec): continue
            
            def parse_symptoms(val):
                if pd.isna(val): return []
                return [self._normalize_symptom_name(s.strip()) for s in str(val).split(',')]
            
            self.spec_profiles[spec] = {
                'core': parse_symptoms(row.get('Symptômes CORE (discriminants)', '')),
                'secondary': parse_symptoms(row.get('Symptômes SECONDAIRES', '')),
                'rare': parse_symptoms(row.get('Symptômes RARES', ''))
            }

        # 4. Load Architecture Arbre
        df_tree = pd.read_excel(K1_TREE_FILE, sheet_name='Architecture_Arbre', skiprows=1)
        self.tree_rules = defaultdict(lambda: {'exclusifs': set(), 'cles': set()})
        
        for _, row in df_tree.iterrows():
            spec = row['Spécialiste (feuille)']
            if pd.isna(spec): continue
            
            def parse_rules(val):
                if pd.isna(val) or val == 'N/A' or val == 'Aucun': return set()
                return set([self._normalize_symptom_name(s.strip()) for s in str(val).split(',')])
            
            self.tree_rules[spec]['cles'].update(parse_rules(row.get('Symptômes clés', '')))
            self.tree_rules[spec]['exclusifs'].update(parse_rules(row.get('Symptômes exclusifs', '')))

    def _compute_arbre_score(self, symptoms: List[str], spec: str) -> float:
        """
        Calcule le Score_arbre pour une spécialité donnée (max 1.0).
        """
        score = 0.0
        rules = self.tree_rules.get(spec, {'exclusifs': set(), 'cles': set()})
        
        # We assume severity = 1.0 for detected symptoms
        for sym in symptoms:
            if sym in rules['exclusifs']:
                score += 3.0  # ×3.0 × sévérité
            elif sym in rules['cles']:
                score += 1.5  # ×1.5 × sévérité
                
        # Normalisation
        max_possible = max(1.0, 3.0 * len(rules['exclusifs']) + 1.5 * len(rules['cles']))
        return min(1.0, score / max_possible) if max_possible > 0 else 0.0

    def _compute_rf_prob(self, symptoms: List[str], spec: str) -> float:
        """
        Estime la probabilité RF pour une spécialité (max 1.0).
        """
        profile = self.spec_profiles.get(spec, {'core': [], 'secondary': [], 'rare': []})
        rf_score = 0.0
        
        for sym in symptoms:
            rf_weight = self.rf_weights.get(sym, 0.001)
            idf_weight = self.idf_weights.get(sym, 1.0)
            
            # Combine RF Importance + IDF to emphasize highly discriminant symptoms
            base_score = rf_weight * idf_weight
            
            if sym in profile['core']:
                rf_score += base_score * 2.0
            elif sym in profile['secondary']:
                rf_score += base_score * 1.0
            elif sym in profile['rare']:
                rf_score += base_score * 0.5
                
        return rf_score

    SEVERITY_BOOST = {"HIGH": 1.25, "CRITICAL": 1.5}
    # Multiplicateur appliqué, pour un cas HIGH ou CRITICAL, aux spécialités
    # RESPONSABLES des symptômes qui font la gravité (voir `_owns`). Il
    # remplace l'ancien « urgent -> x2 cardiologie », qui favorisait une
    # seule spécialité sans regarder les symptômes.
    # Valeurs volontairement plus faibles que l'ancien x2 : le bonus doit
    # départager des spécialités proches en faveur de celle qui traite le
    # signe grave, pas renverser une classification nette. 1.25 et 1.5 sont
    # des valeurs simples et croissantes, non calibrées sur des données.

    def _owns(self, spec: str, symptom: str) -> bool:
        """La spécialité est-elle responsable de ce symptôme ? Oui s'il fait
        partie de ses symptômes clés ou exclusifs dans l'arbre K1, ou de ses
        symptômes CORE dans son profil clinique."""
        rules = self.tree_rules.get(spec, {'exclusifs': set(), 'cles': set()})
        profile = self.spec_profiles.get(spec, {'core': []})
        return symptom in rules['exclusifs'] or symptom in rules['cles'] or symptom in profile['core']

    def score(self, symptoms: List[str], patient_age: int = None, is_urgent: bool = False,
              severity: dict = None) -> Dict[str, float]:
        """
        Calcule le score final : 0.60 * Score_arbre + 0.40 * Prob_RF

        `severity` : résultat de `severity.estimate_severity`. S'il est
        fourni, il remplace la règle « urgent -> x2 cardiologie » : pour un
        cas HIGH ou CRITICAL, toute spécialité responsable d'un des
        symptômes qui font la gravité reçoit `SEVERITY_BOOST`. La
        cardiologie en bénéficie quand la gravité vient d'une douleur
        thoracique, la neurologie quand elle vient d'un déficit, etc.
        Sans `severity`, l'ancien comportement est conservé à l'identique.
        """
        normalized_symptoms = [self._normalize_symptom_name(s) for s in symptoms]
        
        raw_rf_scores = {}
        arbre_scores = {}
        
        for spec in self.specialties:
            arbre_scores[spec] = self._compute_arbre_score(normalized_symptoms, spec)
            raw_rf_scores[spec] = self._compute_rf_prob(normalized_symptoms, spec)
            
        # Normalize RF scores to probabilities (sum = 1)
        total_rf = sum(raw_rf_scores.values())
        rf_probs = {}
        for spec in self.specialties:
            rf_probs[spec] = raw_rf_scores[spec] / total_rf if total_rf > 0 else 0.0

        # Combine
        final_scores = {}
        for spec in self.specialties:
            # Formule finale K1
            score_final = (0.60 * arbre_scores[spec]) + (0.40 * rf_probs[spec])
            
            # Ajustements clinique (similaires à l'ancien moteur)
            if patient_age and patient_age < 16 and 'Pediatrician' in spec:
                score_final *= 1.5
            if severity is None:
                if is_urgent and 'Cardiologist' in spec:
                    score_final *= 2.0
            else:
                boost = self.SEVERITY_BOOST.get(severity.get("level"), 1.0)
                if boost != 1.0 and any(self._owns(spec, s) for s in severity.get("driving_symptoms", [])):
                    score_final *= boost
                
            final_scores[spec] = score_final
            
        # Convert to percentage
        total_final = sum(final_scores.values())
        if total_final > 0:
            final_scores_pct = {k: round((v / total_final) * 100, 2) for k, v in final_scores.items()}
        else:
            final_scores_pct = {k: 0.0 for k in self.specialties}
            
        # Filtre les scores nuls
        return {k: v for k, v in final_scores_pct.items() if v > 0}

    def get_top_n(self, scores: Dict[str, float], n: int = 3) -> List[Tuple[str, float]]:
        """Retourne le Top N des spécialités triées par pertinence."""
        sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return sorted_scores[:n]
