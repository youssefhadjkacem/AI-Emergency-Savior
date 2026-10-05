"""
Exécution instrumentée du pipeline principal, étape par étape.

Le code testé est le VRAI code du Space Hugging Face `emergency-savior-output`
(clone local dans `spaces_src/emergency-savior-output/`), importé tel quel :
rien n'est réimplémenté ici. Deux façons de l'exécuter :

  - en local (`LocalPipeline`) : mêmes classes que le Space
    (`SymptomExtractor`, `K1Brain`, `ProviderFilter`), appelées une à une
    pour chronométrer chaque étape et garder les sorties intermédiaires ;
  - à distance (`call_deployed_space`) : appel HTTP du Space déployé, par le
    même protocole Gradio que `backend/main.py`, et lecture de la réponse
    avec le parseur de `backend/main.py`. C'est le chemin de production.

Correspondance entre les 5 étapes du papier et le code réel :

    1. Entrée patient    -> texte (transcript simulé) + âge, urgence, budget, ville
    2. NLP               -> SymptomExtractor.extract(text)
    3. Classification    -> K1Brain.score(...) puis get_top_n(..., 3)
    4. Optimisation      -> ProviderFilter.optimize_providers_nsga(top_spec, top_k=3, ...)
    5. Recommandation    -> les 3 lignes renvoyées par l'étape 4

── Instrumentation ───────────────────────────────────────────────────────
`SymptomExtractor.extract_with_status` renvoie, avec les symptômes, la
langue détectée, le statut d'extraction et le compte rendu de l'appel de
traduction (service, durée, erreurs). Le harnais ne fait que le lire : il
n'intercepte ni ne remplace rien.

Les corrections du pipeline (lexique français, négation, Top 3 diversifié,
chaîne de traduction) se règlent par les arguments de `LocalPipeline`, qui
sont ceux de `MedicalRecommender`. `LEGACY_CONFIG` les désactive toutes et
reproduit le comportement d'avant correction.

Le découpage en étapes reproduit le corps de `MedicalRecommender.predict`.
Pour garantir qu'il n'en diverge pas, `LocalPipeline.matches_predict`
compare son résultat à celui du vrai `predict` sur chaque cas.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import httpx
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
SPACE_DIR = Path(os.environ.get("EMERGENCY_SAVIOR_OUTPUT_SRC", REPO_ROOT / "spaces_src" / "emergency-savior-output"))

# URL du Space déployé : la même que `HF_OPT_URL` dans backend/main.py.
DEPLOYED_SPACE_URL = "https://youssef0081-emergency-savior-output.hf.space/gradio_api/call/predict"
REMOTE_TIMEOUT = httpx.Timeout(120.0, connect=30.0)

TOP_N_SPECIALTIES = 3
TOP_K_PROVIDERS = 3
# Valeurs de `MedicalRecommender.predict` (top_n=3) et de son appel à
# `optimize_providers_nsga` (top_k=3).

PROVIDER_FIELDS = ["ID", "provider_name", "specialty", "location", "quality_score", "average_cost",
                   "waiting_time_days", "available_slots", "accepts_cnam", "teleconsultation"]


# Comportement d'avant correction : pas de lexique français, pas de gestion
# de la négation, Google Translate seul, Top 3 tiré de la 1re spécialité.
LEGACY_CONFIG = dict(use_french_lexicon=False, handle_negation=False,
                     translation_chain=("google",), diversify_top3=False)


@dataclass
class TranslationCall:
    seconds: float
    ok: bool
    error: Optional[str] = None
    output: Optional[str] = None
    provider: Optional[str] = None


@dataclass
class StageTrace:
    """Sorties intermédiaires et durées (secondes) d'une exécution locale."""

    text: str
    urgent: bool
    translation: Optional[TranslationCall]
    detected_symptoms: List[str]
    # Top 3 des spécialités, [(spécialité, score en %)], dans l'ordre.
    top_specialties: List[tuple]
    # Top 3 des prestataires, dans l'ordre du classement.
    top_providers: List[dict]
    timings: Dict[str, float] = field(default_factory=dict)
    # "ok", "ok_without_translation", "no_symptom_found" ou
    # "no_symptom_translation_failed" (constantes STATUS_* de l'extracteur).
    extraction_status: Optional[str] = None
    language: Optional[str] = None

    @property
    def predicted_specialty(self) -> Optional[str]:
        return self.top_specialties[0][0] if self.top_specialties else None

    @property
    def score_margin(self) -> Optional[float]:
        """Écart de score (points de %) entre la 1re et la 2e spécialité."""
        if len(self.top_specialties) < 2:
            return None
        return self.top_specialties[0][1] - self.top_specialties[1][1]


def _provider_rows(df: Optional[pd.DataFrame]) -> List[dict]:
    if df is None or df.empty:
        return []
    rows = []
    for _, row in df.iterrows():
        rows.append({k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in PROVIDER_FIELDS})
    return rows


class LocalPipeline:
    """Charge le code du Space et l'exécute étape par étape."""

    def __init__(self, space_dir: Path = SPACE_DIR, **recommender_options):
        if not (space_dir / "src" / "pipeline.py").exists():
            raise FileNotFoundError(
                f"Code du Space introuvable dans {space_dir}. Cloner le Space "
                "emergency-savior-output ou définir EMERGENCY_SAVIOR_OUTPUT_SRC."
            )
        self.space_dir = space_dir
        sys.dont_write_bytecode = True  # ne pas écrire de __pycache__ dans le clone du Space
        if str(space_dir) not in sys.path:
            sys.path.insert(0, str(space_dir))

        from src.pipeline import MedicalRecommender

        self.options = recommender_options
        self.translation_calls: List[TranslationCall] = []

        load_start = time.perf_counter()
        self.recommender = MedicalRecommender(**recommender_options)
        with contextlib.redirect_stdout(io.StringIO()):
            self.recommender.load_data()
        self.load_seconds = time.perf_counter() - load_start
        if not self.recommender.is_ready:
            raise RuntimeError("MedicalRecommender.load_data() a échoué (is_ready=False).")

        self.extractor = self.recommender.extractor
        self.scorer = self.recommender.scorer
        self.provider_filter = self.recommender.provider_filter

    # ── Connaissances exposées à l'évaluation ───────────────────────────────

    @property
    def vocabulary(self) -> List[str]:
        return list(self.scorer.idf_weights.keys())

    @property
    def specialties(self) -> List[str]:
        return list(self.scorer.specialties)

    def branch_by_specialty(self) -> Dict[str, str]:
        """Branche (B1..B7) de chaque spécialité, lue dans l'arbre K1."""
        from src.config import K1_TREE_FILE

        tree = pd.read_excel(K1_TREE_FILE, sheet_name="Architecture_Arbre", skiprows=1)
        return {row["Spécialiste (feuille)"]: row["Branche"] for _, row in tree.iterrows()
                if pd.notna(row["Spécialiste (feuille)"])}

    # ── Étapes ───────────────────────────────────────────────────────────────

    def classify(self, symptoms: Sequence[str], age: Optional[int], urgent: bool) -> List[tuple]:
        scores = self.scorer.score(list(symptoms), patient_age=age, is_urgent=urgent)
        return self.scorer.get_top_n(scores, TOP_N_SPECIALTIES)

    def run(self, text: str, age: Optional[int], urgent: bool, budget: Optional[float],
            location: Optional[str]) -> StageTrace:
        timings: Dict[str, float] = {}
        total_start = time.perf_counter()

        # Étape 2 — NLP (traduction éventuelle + extraction)
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            extraction = self.extractor.extract_with_status(text)
        nlp_total = time.perf_counter() - start
        detected = list(extraction["symptoms"])
        report = extraction["translation"]
        translation = None
        if report["status"] != "not_needed":  # un appel à un service de traduction a été tenté
            translation = TranslationCall(
                seconds=report["seconds"], ok=report["status"] == "ok",
                error=", ".join(f"{k}: {v.split(':')[0]}" for k, v in report["errors"].items()) or None,
                output=report.get("text"), provider=report.get("provider"),
            )
            self.translation_calls.append(translation)
        timings["nlp_translation_call"] = translation.seconds if translation else 0.0
        timings["nlp_extraction"] = nlp_total - timings["nlp_translation_call"]

        # Étape 3 — Classification de spécialité
        start = time.perf_counter()
        top_specialties = self.classify(detected, age, urgent)
        timings["classification"] = time.perf_counter() - start

        # Étape 4 — Optimisation multi-objectifs (comme dans predict : liste des
        # prestataires des 3 spécialités, puis NSGA sur la première)
        start = time.perf_counter()
        top_providers_df = None
        with contextlib.redirect_stdout(io.StringIO()):
            self.provider_filter.get_top_providers(
                [s for s, _ in top_specialties], top_n=5, sort_by="quality_score",
                budget=budget, location=location,
            )
            if top_specialties:
                top_providers_df = self.provider_filter.optimize_providers_nsga(
                    top_specialties[0][0], top_k=TOP_K_PROVIDERS, budget=budget, location=location
                )
                if self.recommender.diversify_top3:
                    top_providers_df = self.provider_filter.diversified_top_providers(
                        top_specialties, top_k=TOP_K_PROVIDERS, budget=budget, location=location
                    )
        timings["optimization"] = time.perf_counter() - start

        # Étape 5 — Recommandation finale (mise en forme du Top 3)
        start = time.perf_counter()
        top_providers = _provider_rows(top_providers_df)
        timings["recommendation"] = time.perf_counter() - start

        timings["end_to_end"] = time.perf_counter() - total_start
        timings["end_to_end_without_translation_call"] = timings["end_to_end"] - timings["nlp_translation_call"]

        return StageTrace(
            text=text, urgent=urgent, translation=translation,
            detected_symptoms=sorted(detected),
            top_specialties=[(s, float(v)) for s, v in top_specialties],
            top_providers=top_providers, timings=timings,
            extraction_status=extraction["status"], language=extraction["language"],
        )

    def matches_predict(self, trace: StageTrace, age: Optional[int], budget: Optional[float],
                        location: Optional[str]) -> bool:
        """Le découpage en étapes donne-t-il le même résultat que le vrai
        `MedicalRecommender.predict` sur la même entrée ?"""
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.recommender.predict(trace.text, age=age, urgent=trace.urgent,
                                              budget=budget, location=location)
        return (
            sorted(result["detected_symptoms"]) == trace.detected_symptoms
            and [(s, float(v)) for s, v in result["recommendations"]] == trace.top_specialties
            and _provider_rows(result["top_providers"]) == trace.top_providers
        )


# ═════════════════════════════════════════════════════════════════════════
# Space déployé (conditions réelles)
# ═════════════════════════════════════════════════════════════════════════


_backend_main = None


def _parse_optimization_output(raw_text: str) -> dict:
    """Parseur de production : `parse_optimization_output` de backend/main.py.

    Chargé par chemin de fichier et non par `import main` : le clone du
    Space contient lui aussi un `main.py`, placé avant dans `sys.path`."""
    global _backend_main
    if _backend_main is None:
        backend_dir = Path(__file__).resolve().parents[1]
        if str(backend_dir) not in sys.path:
            sys.path.append(str(backend_dir))  # backend/main.py importe `hospital`
        spec = importlib.util.spec_from_file_location("backend_main", backend_dir / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # backend/main.py règle le logging sur INFO à l'import : sans ceci,
        # httpx écrit une ligne par requête.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        _backend_main = module
    return _backend_main.parse_optimization_output(raw_text)


def call_deployed_space(text: str, age: Optional[int], urgent: bool, budget: Optional[float],
                        location: Optional[str], client: Optional[httpx.Client] = None) -> dict:
    """Appelle le Space déployé comme le fait `/optimize` dans backend/main.py
    (mêmes valeurs par défaut : âge 0, budget 0 et ville vide si absents).

    Retourne toujours un dict avec `ok` ; en cas d'échec réseau, `ok=False`
    et `error` — l'appelant décide quoi en faire, rien n'est simulé."""
    payload = [text, age if age is not None else 0, urgent, budget if budget is not None else 0, location or ""]
    owns_client = client is None
    client = client or httpx.Client(timeout=REMOTE_TIMEOUT)
    start = time.perf_counter()
    try:
        submit = client.post(DEPLOYED_SPACE_URL, json={"data": payload})
        submit.raise_for_status()
        event_id = submit.json()["event_id"]
        stream = client.get(f"{DEPLOYED_SPACE_URL}/{event_id}")
        stream.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return {"ok": False, "seconds": time.perf_counter() - start, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if owns_client:
            client.close()
    seconds = time.perf_counter() - start

    # Seuls les échecs réseau ci-dessus sont rapportés comme `ok=False` : une
    # erreur de lecture de la réponse est un bug et doit remonter.
    data = []
    for line in stream.text.splitlines():
        if line.strip().startswith("data:"):
            try:
                data = json.loads(line.strip()[5:].strip())
            except json.JSONDecodeError:
                pass
    raw_text = data[0] if data else ""
    parsed = _parse_optimization_output(raw_text)
    return {
        "ok": True,
        "seconds": seconds,
        "detected_symptoms": sorted(parsed["detected_symptoms"]),
        "top_specialties": [(s["specialty"], s["score"]) for s in parsed["recommended_specialties"]],
        "best_provider": parsed["best_provider"],
        # Présents seulement si le Space déployé contient les corrections ;
        # sinon liste vide et statut "ok" (valeurs par défaut du parseur).
        "top_providers": parsed["top_providers"],
        "extraction_status": parsed["extraction_status"],
        "raw_output": raw_text,
    }


def same_as_deployed(trace: StageTrace, remote: dict) -> Optional[bool]:
    """Le résultat local est-il identique à celui du Space déployé ?
    Le Space n'affiche les scores qu'à une décimale et seulement le nom du
    premier prestataire : la comparaison porte sur ce qu'il expose.
    None si le Space n'a pas répondu."""
    if not remote.get("ok"):
        return None
    local_specs = [(s, float(f"{v:.1f}")) for s, v in trace.top_specialties]  # même arrondi que app.py
    remote_specs = [(s, round(v, 1)) for s, v in remote["top_specialties"]]
    local_best = trace.top_providers[0]["provider_name"] if trace.top_providers else None
    return (
        trace.detected_symptoms == remote["detected_symptoms"]
        and local_specs == remote_specs
        and local_best == remote["best_provider"]
    )
