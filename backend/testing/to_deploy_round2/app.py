import gradio as gr
from src.pipeline import MedicalRecommender

# Initialisation globale (important pour performance)
recommender = MedicalRecommender()
recommender.load_data()

def predict(text, age, urgent, budget, location):
    if not text:
        return "Veuillez entrer une description des symptômes."

    try:
        result = recommender.predict(
            text,
            age=age if age else None,
            urgent=urgent,
            budget=budget if budget else None,
            location=location if location else None
        )
    except Exception as e:
        return f"Erreur : {e}"

    output = ""

    # Statut de l'extraction : affiché dès qu'il y a quelque chose à signaler
    # (traduction indisponible, aucun symptôme reconnu). La ligne
    # "Statut extraction" est lue par backend/main.py.
    extraction = result.get('extraction') or {}
    if extraction.get('status') and extraction['status'] != 'ok':
        output += f"Statut extraction : {extraction['status']}\n"
        output += f"Avertissement : {extraction.get('message', '')}\n\n"

    # Gravité estimée (LOW / MEDIUM / HIGH / CRITICAL / UNKNOWN). La ligne
    # "Gravité estimée" est lue par backend/main.py.
    severity = result.get('severity')
    if severity:
        output += f"Gravité estimée : {severity['level']}\n\n"

    # Symptômes
    if result['detected_symptoms']:
        output += f"Symptômes détectés : {', '.join(result['detected_symptoms'])}\n\n"

    # Spécialités
    output += "Spécialités recommandées :\n"
    for spec, score in result['recommendations']:
        output += f"- {spec} ({score:.1f}%)\n"

    # Médecin
    nsga_top = result.get('top_specialty_providers')
    if nsga_top is not None and not nsga_top.empty:
        best = nsga_top.iloc[0]
        output += f"\nMeilleur médecin : {best.get('provider_name')}\n"

    # Top 3 complet (avant : seul le premier nom était affiché)
    top3 = result.get('top_providers')
    if top3 is not None and not top3.empty:
        output += "\nTop 3 prestataires :\n"
        for rank, (_, row) in enumerate(top3.iterrows(), 1):
            output += f"{rank}. {row.get('provider_name')} | {row.get('specialty')} | {row.get('location')}\n"

    return output

# Interface
iface = gr.Interface(
    fn=predict,
    inputs=[
        gr.Textbox(label="Symptômes"),
        gr.Number(label="Âge"),
        gr.Checkbox(label="Urgence"),
        gr.Number(label="Budget (€)"),
        gr.Textbox(label="Localisation")
    ],
    outputs="text",
    title="Medical Recommender (NSGA-II)",
    description="Recommandation intelligente de spécialités et médecins"
)

iface.launch()