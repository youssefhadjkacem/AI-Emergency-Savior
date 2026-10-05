"""
Tests des trois corrections du pipeline principal (chemin français,
négations, Top 3 diversifié), sur des exemples construits à la main.

Le code testé est celui du clone du Space `emergency-savior-output` ; tous
ces tests sont ignorés si le clone est absent. Aucun n'a besoin du réseau :
les pipelines sont créés sans service de traduction.

Lancer avec :
    cd backend
    python -m pytest testing/tests/test_pipeline_fixes.py -v
"""

import importlib.util
from pathlib import Path

import pytest

from testing.pipeline_runner import LEGACY_CONFIG, SPACE_DIR, LocalPipeline

pytestmark = pytest.mark.skipif(
    not (SPACE_DIR / "src" / "pipeline.py").exists(),
    reason="clone du Space emergency-savior-output absent",
)

OFFLINE = dict(translation_chain=())


@pytest.fixture(scope="module")
def fixed():
    """Pipeline corrigé, sans service de traduction."""
    return LocalPipeline(**OFFLINE)


@pytest.fixture(scope="module")
def legacy():
    """Comportement d'avant correction, sans service de traduction."""
    return LocalPipeline(**dict(LEGACY_CONFIG, translation_chain=()))


@pytest.fixture(scope="module")
def negation(fixed):
    import src.negation as module  # importable une fois le clone ajouté au chemin par LocalPipeline
    return module


# ═════════════════════════════════════════════════════════════════════════
# Portée de négation
# ═════════════════════════════════════════════════════════════════════════


def test_negated_words_are_masked_until_punctuation(negation):
    masked = negation.mask_negated_scopes("i have a sore throat, but no fever, no cough and no chest pain.")

    assert masked == "i have a sore throat, but no _, no _ _ no _ _."


def test_scope_is_limited_to_the_window(negation):
    """5 mots masqués après le marqueur, pas un de plus."""
    masked = negation.mask_negated_scopes("no one two three four five six seven")

    assert masked == "no _ _ _ _ _ six seven"


def test_scope_stops_at_but_and_mais(negation):
    assert negation.mask_negated_scopes("no fever but cough") == "no _ but cough"
    assert negation.mask_negated_scopes("pas de fievre mais je tousse") == "pas _ _ mais je tousse"


def test_and_or_do_not_close_the_scope(negation):
    assert negation.mask_negated_scopes("no fever or cough") == "no _ _ _"


def test_french_cues(negation):
    assert negation.mask_negated_scopes("je n ai pas de fievre.") == "je n ai pas _ _."
    assert negation.mask_negated_scopes("aucune douleur, sans fievre, ni toux") == "aucune _, sans _, ni _"


def test_french_plus_is_a_cue_only_after_ne(negation):
    assert negation.mask_negated_scopes("je n ai plus de fievre.") == "je n ai plus _ _."
    # « de plus en plus » et « plus d'une heure » ne sont pas des négations.
    assert negation.mask_negated_scopes("j ai de plus en plus mal") == "j ai de plus en plus mal"
    assert negation.mask_negated_scopes("raide plus d une heure") == "raide plus d une heure"


def test_inability_is_not_a_negation(negation):
    """« ne peut plus respirer » décrit un symptôme, pas son absence."""
    assert negation.mask_negated_scopes("il ne peut plus respirer") == "il ne peut plus respirer"
    assert negation.mask_negated_scopes("je n arrive pas a bouger le bras") == "je n arrive pas a bouger le bras"
    assert negation.mask_negated_scopes("she can not see") == "she can not see"


def test_false_cues_are_ignored(negation):
    assert negation.mask_negated_scopes("je tousse sans arret la nuit") == "je tousse sans arret la nuit"
    assert negation.mask_negated_scopes("j ai pas mal de fievre") == "j ai pas mal de fievre"


def test_english_contractions_are_cues(negation):
    assert negation.mask_negated_scopes("he doesn't have fever.") == "he doesn't _ _."


# ═════════════════════════════════════════════════════════════════════════
# Extraction : négation
# ═════════════════════════════════════════════════════════════════════════

NEGATED_EN = "I have a sore throat, but I have no fever, no cough and no chest pain."
NEGATED_FR = "J'ai mal à la gorge, mais je n'ai pas de fièvre, pas de toux et aucune douleur dans la poitrine."


def test_negated_symptoms_are_not_extracted_in_english(fixed, legacy):
    assert sorted(fixed.extractor.extract(NEGATED_EN)) == ["throat_irritation"]
    # Avant correction, les trois symptômes niés étaient extraits.
    assert {"high_fever", "cough", "chest_pain"} <= set(legacy.extractor.extract(NEGATED_EN))


def test_negated_symptoms_are_not_extracted_in_french(fixed):
    assert sorted(fixed.extractor.extract(NEGATED_FR)) == ["throat_irritation"]


def test_negation_does_not_remove_affirmed_symptoms(fixed):
    """Symptôme affirmé avant et après une négation : les deux sont gardés."""
    found = set(fixed.extractor.extract("Je tousse, je n'ai pas de fièvre, mais j'ai mal à la tête."))

    assert found == {"cough", "headache"}


def test_symptoms_phrased_negatively_survive_negation(fixed):
    """« plus d'appétit », « ne dort plus », « no smell » : la tournure est
    négative mais c'est un symptôme."""
    found = set(fixed.extractor.extract("Je n'ai plus d'appétit et je ne dors plus."))
    assert {"loss_of_appetite", "sleep_disturbance"} <= found
    assert "loss_of_smell" in fixed.extractor.extract("I have no smell since monday.")


# ═════════════════════════════════════════════════════════════════════════
# Extraction : français sans traduction, et statuts
# ═════════════════════════════════════════════════════════════════════════


def test_french_is_extracted_without_any_translation_service(fixed, legacy):
    text = "J'ai de la fièvre et je tousse beaucoup."

    report = fixed.extractor.extract_with_status(text)
    assert report["symptoms"] == ["cough", "high_fever"]
    assert report["language"] == "fr"
    assert report["status"] == "ok"

    # Avant correction : rien, et rien ne le signalait à l'appelant.
    assert legacy.extractor.extract(text) == []


def test_untranslated_text_without_symptom_is_not_reported_as_no_symptom(fixed):
    """Sans traduction et sans symptôme reconnu, le statut ne doit PAS être
    celui d'un texte compris qui ne contient aucun symptôme."""
    french = fixed.extractor.extract_with_status("Bonjour, je voudrais un renseignement s'il vous plaît.")
    english = fixed.extractor.extract_with_status("Hello, I would like some information please.")

    assert french["symptoms"] == [] and french["status"] == "no_symptom_translation_failed"
    assert english["symptoms"] == [] and english["status"] == "no_symptom_found"
    assert french["message"] and english["message"] and french["message"] != english["message"]


def test_english_text_triggers_no_translation_call(fixed):
    report = fixed.extractor.extract_with_status("I have chest pain and a cough.")

    assert report["language"] == "en"
    assert report["translation"]["status"] == "not_needed"
    assert report["status"] == "ok"


def test_mal_au_ventre_is_not_the_pediatric_belly_pain(fixed):
    found = set(fixed.extractor.extract("J'ai très mal au ventre depuis hier."))

    assert "abdominal_pain" in found
    assert "belly_pain" not in found


def test_french_lexicon_only_uses_known_symptoms_and_valid_patterns(fixed):
    import src.french_lexicon as lexicon

    vocabulary = set(fixed.vocabulary)
    unknown = sorted(set(lexicon.FRENCH_SYMPTOM_PATTERNS) - vocabulary)
    assert not unknown, f"identifiants hors vocabulaire : {unknown}"

    compiled = lexicon.compile_french_lexicon(vocabulary)  # lève une erreur si une expression est invalide
    assert len(compiled) == sum(len(p) for p in lexicon.FRENCH_SYMPTOM_PATTERNS.values())


def test_normalize_french(fixed):
    from src.french_lexicon import normalize_french

    assert normalize_french("J'ai mal à la tête, et le cœur qui s'emballe.") == \
        "j ai mal a la tete, et le coeur qui s emballe."


# ═════════════════════════════════════════════════════════════════════════
# Top 3 diversifié
# ═════════════════════════════════════════════════════════════════════════


def test_allocate_top_slots_follows_classifier_confidence(fixed):
    from src.filtering import allocate_top_slots

    assert allocate_top_slots([98.1, 0.7, 0.5]) == [3, 0, 0]     # net : une seule spécialité
    assert allocate_top_slots([67.5, 11.8, 8.8]) == [2, 1, 0]
    assert allocate_top_slots([54.9, 39.1, 1.3]) == [2, 1, 0]
    assert allocate_top_slots([42.3, 42.0, 15.7]) == [1, 1, 1]   # indécis : trois spécialités
    assert allocate_top_slots([100.0]) == [3]
    assert allocate_top_slots([]) == []


def test_allocate_top_slots_always_fills_k_and_serves_first_specialty(fixed):
    from src.filtering import allocate_top_slots

    for scores in ([34.0, 33.0, 33.0], [50.0, 50.0], [20.0, 20.0, 20.0], [10.0, 0.0, 0.0]):
        slots = allocate_top_slots(scores, 3)
        assert sum(slots) == 3
        assert slots[0] >= 1


def test_diversified_top3_keeps_the_same_first_provider(fixed):
    """Le n°1 reste celui de l'ancien Top 3 ; l'alternative arrive en 2e."""
    pf = fixed.provider_filter
    top_specialties = [("Phlebologist", 54.9), ("Gastroenterologist", 39.1), ("Infectiologist", 1.3)]

    old_top3 = pf.optimize_providers_nsga("Phlebologist", top_k=3)
    new_top3 = pf.diversified_top_providers(top_specialties, top_k=3)

    assert new_top3["ID"].iloc[0] == old_top3["ID"].iloc[0]
    assert new_top3["specialty"].tolist() == ["Phlebologist", "Gastroenterologist", "Phlebologist"]
    assert new_top3["specialty_rank"].tolist() == [1, 2, 1]
    assert new_top3["ID"].iloc[2] == old_top3["ID"].iloc[1]


def test_diversified_top3_is_unchanged_when_classification_is_clear(fixed):
    pf = fixed.provider_filter

    old_top3 = pf.optimize_providers_nsga("Cardiologist", top_k=3)
    new_top3 = pf.diversified_top_providers([("Cardiologist", 98.1), ("Internal Medicine", 0.7)], top_k=3)

    assert new_top3["ID"].tolist() == old_top3["ID"].tolist()


def test_diversified_top3_with_no_specialty_is_empty(fixed):
    assert fixed.provider_filter.diversified_top_providers([], top_k=3).empty


def test_predict_exposes_status_and_three_providers(fixed):
    result = fixed.recommender.predict("J'ai très mal à l'estomac, je vomis et j'ai la diarrhée.", age=30)

    assert result["extraction"]["status"] == "ok"
    assert len(result["top_providers"]) == 3
    assert result["top_providers"]["provider_name"].iloc[0] == \
        result["top_specialty_providers"]["provider_name"].iloc[0]


# ═════════════════════════════════════════════════════════════════════════
# Parseur du backend
# ═════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def parse():
    path = Path(__file__).resolve().parents[2] / "main.py"
    spec = importlib.util.spec_from_file_location("backend_main_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_optimization_output


def test_backend_parser_reads_status_and_top3(parse):
    raw = ("Statut extraction : ok_without_translation\n"
           "Avertissement : Traduction indisponible : symptômes extraits directement du texte français.\n\n"
           "Symptômes détectés : cough, high_fever\n\n"
           "Spécialités recommandées :\n- Internal Medicine (30.6%)\n- Pulmonologist (20.8%)\n\n"
           "Meilleur médecin : Dr. A\n\n"
           "Top 3 prestataires :\n1. Dr. A | Internal Medicine | Bizerte\n2. Dr. B | Pulmonologist | Mahdia\n"
           "3. Dr. C | Internal Medicine | Tunis\n")

    parsed = parse(raw)

    assert parsed["extraction_status"] == "ok_without_translation"
    assert parsed["extraction_warning"].startswith("Traduction indisponible")
    assert parsed["detected_symptoms"] == ["cough", "high_fever"]
    assert [s["specialty"] for s in parsed["recommended_specialties"]] == ["Internal Medicine", "Pulmonologist"]
    assert parsed["best_provider"] == "Dr. A"
    assert parsed["top_providers"] == [
        {"name": "Dr. A", "specialty": "Internal Medicine", "location": "Bizerte"},
        {"name": "Dr. B", "specialty": "Pulmonologist", "location": "Mahdia"},
        {"name": "Dr. C", "specialty": "Internal Medicine", "location": "Tunis"},
    ]


def test_backend_parser_still_reads_the_old_output_format(parse):
    """Tant que le Space déployé n'est pas mis à jour, il renvoie l'ancien format."""
    parsed = parse("Symptômes détectés : chest_pain\n\nSpécialités recommandées :\n- Cardiologist (49.8%)\n\n"
                   "Meilleur médecin : Dr. Leila Boukadida\n")

    assert parsed["best_provider"] == "Dr. Leila Boukadida"
    assert parsed["top_providers"] == []
    assert parsed["extraction_status"] == "ok"
