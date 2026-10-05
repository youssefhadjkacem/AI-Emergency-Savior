import logging
import pandas as pd
import os
from src.config import SPECIALIST_FILE, TARGET_COL, K2_PROVIDERS_FILE
from src.preprocessing import normalize_text
from src.extraction import DEFAULT_TRANSLATION_CHAIN, SymptomExtractor
from src.k1_brain import K1Brain
from src.filtering import ProviderFilter
from src.nsga2 import WITHIN_FRONT_COMPROMISE, WITHIN_FRONT_KNN_DENSITY
from src.severity import estimate_severity

logger = logging.getLogger(__name__)


class MedicalRecommender:
    def __init__(self, use_french_lexicon: bool = True, handle_negation: bool = True,
                 translation_chain=DEFAULT_TRANSLATION_CHAIN, diversify_top3: bool = True,
                 translate_french: str = "fallback", monotonic_ranking: bool = True,
                 use_severity: bool = True):
        # Les trois corrections (français, négation, Top 3 diversifié) sont
        # actives par défaut ; les interrupteurs servent à mesurer l'effet de
        # chacune (voir backend/testing).
        self.use_french_lexicon = use_french_lexicon
        self.handle_negation = handle_negation
        self.translation_chain = tuple(translation_chain)
        self.diversify_top3 = diversify_top3
        self.translate_french = translate_french
        # monotonic_ranking : tri des prestataires par score de compromis à
        #   l'intérieur d'un front de Pareto (False = ancien tri par densité).
        # use_severity : gravité LOW/MEDIUM/HIGH/CRITICAL estimée depuis le
        #   texte (False = ancienne règle "urgent -> x2 cardiologie").
        self.monotonic_ranking = monotonic_ranking
        self.use_severity = use_severity
        self.df = None
        self.extractor = None
        self.scorer = None
        self.provider_filter = None
        self.is_ready = False

    def load_data(self):
        try:
            # Instantiate K1Brain which loads the intelligence (RF weights, IDF, Tree)
            self.scorer = K1Brain()
            
            # The full list of symptoms is available via the IDF weights extracted in K1Brain
            symptom_cols = list(self.scorer.idf_weights.keys())
            self.extractor = SymptomExtractor(
                symptom_cols, use_french_lexicon=self.use_french_lexicon,
                handle_negation=self.handle_negation, translation_chain=self.translation_chain,
                translate_french=self.translate_french,
            )

            # Charger les prestataires
            if os.path.exists(K2_PROVIDERS_FILE):
                self.provider_filter = ProviderFilter(K2_PROVIDERS_FILE, SPECIALIST_FILE)
                self.provider_filter.ranking_mode = (
                    WITHIN_FRONT_COMPROMISE if self.monotonic_ranking else WITHIN_FRONT_KNN_DENSITY
                )
            
            self.is_ready = True
        except Exception:
            # Un échec de chargement ne doit pas passer inaperçu.
            logger.exception("Initialisation du MedicalRecommender échouée")
            self.is_ready = False

    def predict(self, text: str, age: int = None, urgent: bool = False, top_n: int = 3,
                budget: float = None, location: str = None,
                weight_quality: float = 0.5, weight_cost: float = 0.3, weight_proximity: float = 0.2):
        if not self.is_ready:
            return {"error": "System not initialized."}

        # 1. Extraction intelligente (gère la négation et la langue)
        extraction = self.extractor.extract_with_status(text)
        detected_symptoms = list(extraction["symptoms"])
        
        # 2. Scoring avancé (gère la spécificité et le profil patient)
        # On passe l'âge et l'urgence au scorer
        severity = None
        if self.use_severity:
            severity = estimate_severity(text, detected_symptoms, age=age, urgent=urgent)
        scores = self.scorer.score(detected_symptoms, patient_age=age, is_urgent=urgent, severity=severity)
        # La gravité influence aussi le classement des prestataires : délai
        # et proximité pèsent davantage pour un cas HIGH ou CRITICAL
        # (filtering.URGENCY_WEIGHT).
        severity_level = severity["level"] if severity else None
        
        # 3. Formatage des résultats
        top_results = self.scorer.get_top_n(scores, top_n)
        
        # 4. Filtrage des prestataires (PHASE 2)
        providers_by_specialty = {}
        top_specialty_providers = None
        top_providers = None

        if self.provider_filter:
            specialties = [spec for spec, _ in top_results]
            
            providers_by_specialty = self.provider_filter.get_top_providers(
                specialties,
                top_n=5,
                sort_by='quality_score',
                budget=budget,
                location=location,
                weight_quality=weight_quality,
                weight_cost=weight_cost,
                weight_proximity=weight_proximity
            )
            # Also compute top providers for the top specialty using NSGA2
            if len(top_results) > 0:
                top_spec = top_results[0][0]
                try:
                    top_specialty_providers = self.provider_filter.optimize_providers_nsga(
                        top_spec, top_k=3, budget=budget, location=location, severity_level=severity_level
                    )
                except Exception:
                    top_specialty_providers = None

                # Top 3 final : tiré de plusieurs spécialités quand la
                # classification est indécise (voir
                # ProviderFilter.diversified_top_providers), sinon identique
                # à l'ancien Top 3.
                top_providers = top_specialty_providers
                if self.diversify_top3:
                    try:
                        top_providers = self.provider_filter.diversified_top_providers(
                            top_results, top_k=3, budget=budget, location=location,
                            severity_level=severity_level
                        )
                    except Exception:
                        logger.exception("Diversification du Top 3 échouée : Top 3 de la première spécialité conservé")
        
        return {
            "input_text": text,
            "detected_symptoms": detected_symptoms,
            # Statut de l'extraction : distingue "aucun symptôme trouvé" de
            # "texte non analysé faute de traduction".
            "extraction": {k: extraction[k] for k in ("status", "message", "language", "translation")},
            "recommendations": top_results,
            "patient_context": {"age": age, "urgent": urgent, "budget": budget, "location": location,
                                "weights": {"quality": weight_quality, "cost": weight_cost, "proximity": weight_proximity}},
            "providers": providers_by_specialty,
            "top_specialty_providers": top_specialty_providers,
            "top_providers": top_providers,
            # Gravité estimée : {"level", "score", "reasons", "driving_symptoms"},
            # ou None si l'estimation est désactivée.
            "severity": severity
        }
    
    def get_providers_for_specialty(self, specialty: str, top_n: int = 10, sort_by: str = 'quality_score'):
        """
        Récupère les meilleurs prestataires pour une spécialité donnée.
        
        Args:
            specialty: La spécialité recherchée
            top_n: Nombre de prestataires à retourner
            sort_by: Critère de tri ('quality_score', 'waiting_time_days', 'average_cost')
            
        Returns:
            DataFrame avec les prestataires filtrés
        """
        if not self.provider_filter:
            return pd.DataFrame()
        
        return self.provider_filter.filter_by_specialty_name(
            specialty, top_n=top_n, sort_by=sort_by, ascending=(sort_by != 'quality_score')
        )