"""
Serveur minimal pour tester UNIQUEMENT le module hôpitaux,
sans dépendre des HF Spaces (audio/CV/optimisation).

Lancer avec :
    uvicorn test_hospital_only:app --reload

Puis ouvrir : http://127.0.0.1:8000/docs
"""

from fastapi import FastAPI

from hospital import router as hospital_router

app = FastAPI(title="Test - Module Hôpitaux")
app.include_router(hospital_router)