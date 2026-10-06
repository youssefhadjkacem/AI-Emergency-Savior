"""
Module realtime — traitement des événements temps réel (section 3.8 du papier).

Phase 1 : filtrage du bruit (dédoublonnage, confirmation, lissage EWMA,
détection d'anomalie) — `noise_filter.py`.
Phase 2 : détection de concept drift sur la disponibilité lissée
(ADWIN / Page-Hinkley) — `drift_detection.py`.
Phase 3 : adaptation performative — détection de saturation
(`saturation.py`), réaction sur le classement (`adaptation.py`) et
simulation de la boucle de rétroaction (`feedback_simulator.py`).
"""
