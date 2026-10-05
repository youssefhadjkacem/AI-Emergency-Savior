"""
Module realtime — traitement des événements temps réel (section 3.8 du papier).

Phase 1 : filtrage du bruit (dédoublonnage, confirmation, lissage EWMA,
détection d'anomalie) — `noise_filter.py`.
Phase 2 : détection de concept drift sur la disponibilité lissée
(ADWIN / Page-Hinkley) — `drift_detection.py`.
La logique de saturation (réaction au drift) viendra dans une étape suivante.
"""
