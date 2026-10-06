Tableaux générés par `run_feedback_evaluation.py`. Moyenne ± écart-type entre les seeds 7, 1 et 42.

### Charge faible (50 patients/heure)

| Métrique | A — Statique | B — Seuil seul | C — Système complet | D — Complet sur flux brut | E — Complet sans réservations | F — Complet sans Page-Hinkley |
|---|---|---|---|---|---|---|
| Patients envoyés vers un prestataire saturé (%) | 1,2 ± 0,2 | 1,1 ± 0,3 | 1,0 ± 0,2 | 0,9 ± 0,2 | 1,1 ± 0,3 | 1,0 ± 0,2 |
| Patients refusés faute de place (%) | 1,1 ± 0,3 | 0,9 ± 0,4 | 0,7 ± 0,2 | 0,6 ± 0,2 | 0,9 ± 0,4 | 0,7 ± 0,2 |
| Occupation moyenne (%) | 30,4 ± 0,4 | 30,4 ± 0,4 | 30,4 ± 0,3 | 30,4 ± 0,4 | 30,4 ± 0,4 | 30,4 ± 0,3 |
| Occupation moyenne du prestataire le plus chargé (%) | 62,2 ± 4,2 | 62,2 ± 4,2 | 62,1 ± 4,1 | 62,1 ± 4,1 | 62,2 ± 4,2 | 62,1 ± 4,1 |
| Temps-prestataire à 90 % ou plus (%) | 1,35 ± 0,18 | 1,35 ± 0,18 | 1,33 ± 0,18 | 1,35 ± 0,19 | 1,35 ± 0,18 | 1,33 ± 0,18 |
| Gini des patients reçus, par spécialité | 0,865 ± 0,003 | 0,864 ± 0,003 | 0,852 ± 0,003 | 0,851 ± 0,004 | 0,863 ± 0,003 | 0,852 ± 0,003 |
| Écart-type de l'occupation, par spécialité (points) | 10,6 ± 0,5 | 10,5 ± 0,5 | 10,5 ± 0,5 | 10,5 ± 0,5 | 10,5 ± 0,5 | 10,5 ± 0,5 |
| Part des patients chez les 3 plus sollicités (%) | 85,5 ± 0,6 | 85,3 ± 1,1 | 82,9 ± 0,7 | 82,6 ± 0,8 | 85,2 ± 1,0 | 82,9 ± 1,0 |
| Top 1 modifié (%) | 0,0 ± 0,0 | 1,8 ± 1,4 | 9,1 ± 1,9 | 11,2 ± 0,8 | 2,3 ± 1,5 | 8,7 ± 2,3 |
| Top 3 modifié (%) | 0,0 ± 0,0 | 2,7 ± 0,7 | 11,9 ± 2,2 | 19,5 ± 3,2 | 3,5 ± 0,1 | 11,8 ± 2,5 |
| Variation de la note du n°1 (sur 10) | +0,00 ± 0,00 | -0,01 ± 0,01 | -0,05 ± 0,02 | -0,04 ± 0,03 | -0,01 ± 0,01 | -0,05 ± 0,02 |
| Variation du coût du n°1 (TND) | +0,0 ± 0,0 | +0,1 ± 0,2 | +0,2 ± 0,4 | +0,2 ± 0,3 | +0,1 ± 0,2 | +0,2 ± 0,4 |
| Variation du délai du n°1 (jours) | +0,00 ± 0,00 | -0,06 ± 0,04 | -0,08 ± 0,05 | -0,05 ± 0,09 | -0,07 ± 0,03 | -0,10 ± 0,06 |
| Variation du n°1 dans la ville du patient (points) | +0,0 ± 0,0 | -0,1 ± 0,2 | +1,2 ± 0,9 | +0,3 ± 0,4 | -0,1 ± 0,1 | +1,2 ± 1,1 |
| Épisodes de saturation réelle | — | 235 ± 37 | 232 ± 39 | 234 ± 40 | 235 ± 37 | 233 ± 39 |
| Épisodes détectés (état saturé) (%) | — | 19,3 ± 0,9 | 24,4 ± 1,9 | 99,9 ± 0,2 | 19,3 ± 0,9 | 24,5 ± 2,1 |
| Épisodes anticipés (délai ≤ 0) (%) | — | 4,5 ± 0,5 | 7,5 ± 2,3 | 46,7 ± 4,8 | 4,5 ± 0,5 | 7,6 ± 2,5 |
| Délai de détection médian (min) | — | +14,0 ± 4,0 | +9,7 ± 5,6 | +0,7 ± 0,6 | +14,0 ± 4,0 | +9,5 ± 5,8 |
| Délai de détection moyen (min) | — | +13,2 ± 1,2 | +9,3 ± 2,8 | -1,5 ± 0,4 | +13,2 ± 1,2 | +9,2 ± 3,0 |
| Déclarations de saturation | — | 41 ± 6 | 66 ± 11 | 2382 ± 42 | 41 ± 6 | 67 ± 11 |
| Déclarations à tort (%) | — | 7,2 ± 1,5 | 21,3 ± 4,1 | 83,7 ± 1,8 | 7,2 ± 1,5 | 21,9 ± 4,6 |
| … dont sans patient en route (%) | — | 7,2 ± 1,5 | 8,4 ± 1,7 | 82,3 ± 1,9 | 7,2 ± 1,5 | 7,7 ± 2,5 |
| Alertes Page-Hinkley | — | 0 ± 0 | 306 ± 8 | 732 ± 26 | 307 ± 10 | 0 ± 0 |
| Alertes suivies d'une saturation réelle (%) | — | — | 18,5 ± 3,2 | 16,9 ± 2,2 | 18,5 ± 3,8 | — |

### Charge moyenne (200 patients/heure)

| Métrique | A — Statique | B — Seuil seul | C — Système complet | D — Complet sur flux brut | E — Complet sans réservations | F — Complet sans Page-Hinkley |
|---|---|---|---|---|---|---|
| Patients envoyés vers un prestataire saturé (%) | 9,7 ± 0,6 | 8,2 ± 0,7 | 2,3 ± 0,1 | 2,0 ± 0,2 | 8,3 ± 0,7 | 2,4 ± 0,1 |
| Patients refusés faute de place (%) | 5,9 ± 0,9 | 4,8 ± 0,7 | 1,4 ± 0,2 | 1,3 ± 0,3 | 4,9 ± 0,6 | 1,4 ± 0,1 |
| Occupation moyenne (%) | 32,5 ± 0,4 | 32,6 ± 0,4 | 32,9 ± 0,3 | 32,8 ± 0,3 | 32,6 ± 0,4 | 32,9 ± 0,3 |
| Occupation moyenne du prestataire le plus chargé (%) | 74,6 ± 1,5 | 71,9 ± 1,7 | 64,4 ± 3,3 | 65,2 ± 3,8 | 71,9 ± 1,7 | 64,4 ± 3,3 |
| Temps-prestataire à 90 % ou plus (%) | 2,14 ± 0,15 | 2,09 ± 0,17 | 1,79 ± 0,18 | 1,81 ± 0,17 | 2,09 ± 0,18 | 1,78 ± 0,17 |
| Gini des patients reçus, par spécialité | 0,849 ± 0,002 | 0,833 ± 0,005 | 0,756 ± 0,002 | 0,761 ± 0,005 | 0,832 ± 0,006 | 0,756 ± 0,002 |
| Écart-type de l'occupation, par spécialité (points) | 12,2 ± 0,5 | 11,9 ± 0,5 | 11,2 ± 0,5 | 11,2 ± 0,5 | 11,9 ± 0,5 | 11,2 ± 0,5 |
| Part des patients chez les 3 plus sollicités (%) | 83,1 ± 0,4 | 80,7 ± 0,7 | 65,7 ± 0,3 | 67,5 ± 1,1 | 80,6 ± 0,7 | 65,7 ± 0,1 |
| Top 1 modifié (%) | 0,0 ± 0,0 | 10,2 ± 1,0 | 42,2 ± 1,8 | 40,6 ± 1,2 | 10,8 ± 1,0 | 42,2 ± 1,7 |
| Top 3 modifié (%) | 0,0 ± 0,0 | 12,9 ± 1,6 | 65,1 ± 2,8 | 64,4 ± 2,6 | 14,2 ± 1,6 | 64,9 ± 2,2 |
| Variation de la note du n°1 (sur 10) | +0,00 ± 0,00 | -0,05 ± 0,02 | -0,29 ± 0,04 | -0,27 ± 0,04 | -0,05 ± 0,02 | -0,30 ± 0,04 |
| Variation du coût du n°1 (TND) | +0,0 ± 0,0 | +0,1 ± 0,4 | -1,4 ± 0,6 | -1,3 ± 0,5 | +0,1 ± 0,3 | -1,5 ± 0,6 |
| Variation du délai du n°1 (jours) | +0,00 ± 0,00 | -0,10 ± 0,07 | -0,19 ± 0,17 | -0,14 ± 0,18 | -0,10 ± 0,06 | -0,22 ± 0,18 |
| Variation du n°1 dans la ville du patient (points) | +0,0 ± 0,0 | +1,3 ± 0,6 | +4,3 ± 0,9 | +3,4 ± 0,5 | +1,4 ± 0,6 | +4,4 ± 0,8 |
| Épisodes de saturation réelle | — | 369 ± 44 | 318 ± 31 | 325 ± 37 | 369 ± 46 | 317 ± 31 |
| Épisodes détectés (état saturé) (%) | — | 23,9 ± 2,5 | 46,0 ± 3,0 | 99,9 ± 0,2 | 23,6 ± 2,4 | 45,9 ± 3,5 |
| Épisodes anticipés (délai ≤ 0) (%) | — | 6,6 ± 1,2 | 29,5 ± 2,2 | 61,9 ± 4,1 | 6,7 ± 1,2 | 28,9 ± 2,1 |
| Délai de détection médian (min) | — | +10,0 ± 1,8 | -10,0 ± 0,0 | -1,0 ± 1,7 | +10,2 ± 2,3 | -9,8 ± 0,3 |
| Délai de détection moyen (min) | — | +10,1 ± 1,2 | -0,5 ± 0,4 | -3,7 ± 0,4 | +10,1 ± 1,3 | -0,3 ± 0,2 |
| Déclarations de saturation | — | 71 ± 6 | 336 ± 25 | 2836 ± 38 | 70 ± 6 | 335 ± 20 |
| Déclarations à tort (%) | — | 3,7 ± 1,3 | 64,1 ± 1,9 | 79,6 ± 1,7 | 3,8 ± 1,4 | 63,9 ± 2,7 |
| … dont sans patient en route (%) | — | 3,7 ± 1,3 | 15,3 ± 1,8 | 67,3 ± 1,5 | 3,8 ± 1,4 | 15,1 ± 1,5 |
| Alertes Page-Hinkley | — | 0 ± 0 | 350 ± 5 | 782 ± 24 | 341 ± 8 | 0 ± 0 |
| Alertes suivies d'une saturation réelle (%) | — | — | 24,4 ± 3,1 | 21,9 ± 1,3 | 24,9 ± 2,8 | — |

### Charge forte (600 patients/heure)

| Métrique | A — Statique | B — Seuil seul | C — Système complet | D — Complet sur flux brut | E — Complet sans réservations | F — Complet sans Page-Hinkley |
|---|---|---|---|---|---|---|
| Patients envoyés vers un prestataire saturé (%) | 37,0 ± 0,8 | 28,4 ± 1,0 | 3,2 ± 0,2 | 3,7 ± 0,1 | 28,1 ± 1,0 | 3,3 ± 0,1 |
| Patients refusés faute de place (%) | 26,0 ± 0,3 | 19,7 ± 0,5 | 2,1 ± 0,1 | 2,5 ± 0,3 | 19,3 ± 0,6 | 2,1 ± 0,1 |
| Occupation moyenne (%) | 36,2 ± 0,3 | 37,0 ± 0,3 | 39,8 ± 0,4 | 39,6 ± 0,4 | 37,1 ± 0,3 | 39,8 ± 0,3 |
| Occupation moyenne du prestataire le plus chargé (%) | 89,0 ± 0,8 | 79,3 ± 2,3 | 67,2 ± 2,5 | 69,3 ± 4,1 | 78,8 ± 1,3 | 66,8 ± 3,2 |
| Temps-prestataire à 90 % ou plus (%) | 4,99 ± 0,26 | 4,88 ± 0,15 | 3,32 ± 0,06 | 3,49 ± 0,09 | 4,88 ± 0,10 | 3,34 ± 0,10 |
| Gini des patients reçus, par spécialité | 0,841 ± 0,001 | 0,780 ± 0,007 | 0,483 ± 0,011 | 0,538 ± 0,005 | 0,780 ± 0,007 | 0,482 ± 0,010 |
| Écart-type de l'occupation, par spécialité (points) | 16,0 ± 0,4 | 15,0 ± 0,3 | 11,4 ± 0,1 | 12,5 ± 0,1 | 15,0 ± 0,3 | 11,5 ± 0,1 |
| Part des patients chez les 3 plus sollicités (%) | 82,6 ± 0,1 | 71,9 ± 1,4 | 33,3 ± 0,9 | 36,8 ± 0,7 | 71,9 ± 1,4 | 33,3 ± 1,0 |
| Top 1 modifié (%) | 0,0 ± 0,0 | 31,4 ± 1,5 | 75,9 ± 0,7 | 72,4 ± 0,5 | 31,7 ± 1,5 | 75,8 ± 0,5 |
| Top 3 modifié (%) | 0,0 ± 0,0 | 54,7 ± 1,7 | 93,3 ± 0,1 | 92,8 ± 0,3 | 54,8 ± 1,8 | 93,2 ± 0,2 |
| Variation de la note du n°1 (sur 10) | +0,00 ± 0,00 | -0,17 ± 0,03 | -0,72 ± 0,02 | -0,63 ± 0,01 | -0,17 ± 0,04 | -0,71 ± 0,02 |
| Variation du coût du n°1 (TND) | +0,0 ± 0,0 | -0,5 ± 0,5 | -4,8 ± 0,3 | -4,1 ± 0,3 | -0,5 ± 0,6 | -4,8 ± 0,3 |
| Variation du délai du n°1 (jours) | +0,00 ± 0,00 | +0,02 ± 0,05 | -0,07 ± 0,05 | -0,04 ± 0,09 | +0,04 ± 0,06 | -0,06 ± 0,00 |
| Variation du n°1 dans la ville du patient (points) | +0,0 ± 0,0 | +2,6 ± 0,6 | +3,4 ± 0,5 | +3,3 ± 0,5 | +2,8 ± 0,8 | +3,3 ± 0,5 |
| Épisodes de saturation réelle | — | 813 ± 31 | 622 ± 12 | 654 ± 43 | 818 ± 26 | 626 ± 22 |
| Épisodes détectés (état saturé) (%) | — | 42,6 ± 0,6 | 78,1 ± 1,0 | 100,0 ± 0,1 | 41,9 ± 1,2 | 77,7 ± 0,9 |
| Épisodes anticipés (délai ≤ 0) (%) | — | 23,1 ± 0,7 | 66,9 ± 1,0 | 84,1 ± 2,1 | 22,4 ± 1,3 | 66,4 ± 0,6 |
| Délai de détection médian (min) | — | -6,8 ± 0,3 | -10,0 ± 0,0 | -10,0 ± 0,0 | -6,7 ± 1,2 | -10,0 ± 0,0 |
| Délai de détection moyen (min) | — | +2,9 ± 0,9 | -6,5 ± 0,4 | -6,9 ± 0,1 | +2,9 ± 0,9 | -6,5 ± 0,2 |
| Déclarations de saturation | — | 174 ± 8 | 1588 ± 33 | 4457 ± 46 | 175 ± 7 | 1593 ± 41 |
| Déclarations à tort (%) | — | 1,5 ± 0,6 | 80,6 ± 0,5 | 74,8 ± 0,7 | 1,5 ± 0,6 | 80,8 ± 0,6 |
| … dont sans patient en route (%) | — | 1,5 ± 0,6 | 27,4 ± 0,4 | 36,4 ± 0,4 | 1,5 ± 0,6 | 27,3 ± 0,5 |
| Alertes Page-Hinkley | — | 0 ± 0 | 477 ± 2 | 930 ± 17 | 411 ± 5 | 0 ± 0 |
| Alertes suivies d'une saturation réelle (%) | — | — | 31,4 ± 1,6 | 34,4 ± 2,3 | 38,6 ± 1,3 | — |

### Contrôles de sécurité (somme sur les 3 seeds et les 3 charges)

| Condition | Patients | Spécialité conservée | Patients CRITICAL | CRITICAL détournés hors de leur ville | Réponses « tous saturés » | Top 3 gardant un prestataire saturé |
|---|---|---|---|---|---|---|
| A — Statique | 25742 | 100 % | 1296 | 0 | 0 | 0 |
| B — Seuil seul | 25742 | 100 % | 1296 | 0 | 0 | 30 |
| C — Système complet | 25742 | 100 % | 1296 | 0 | 0 | 211 |
| D — Complet sur flux brut | 25742 | 100 % | 1296 | 0 | 0 | 181 |
| E — Complet sans réservations | 25742 | 100 % | 1296 | 0 | 0 | 32 |
| F — Complet sans Page-Hinkley | 25742 | 100 % | 1296 | 0 | 0 | 207 |

### Sensibilité au suivi des recommandations (charge forte)

| Probabilités n°1 / n°2 / n°3 | Condition | Envoyés vers un saturé (%) | Refusés (%) | Part chez les 3 plus sollicités (%) | Variation de la note du n°1 |
|---|---|---|---|---|---|
| 0,50 / 0,30 / 0,20 | A — Statique | 30,6 ± 1,7 | 20,2 ± 0,7 | 81,0 ± 0,7 | +0,00 ± 0,00 |
| 0,50 / 0,30 / 0,20 | C — Système complet | 3,2 ± 0,1 | 2,2 ± 0,1 | 32,9 ± 0,8 | -0,64 ± 0,01 |
| 0,70 / 0,20 / 0,10 (défaut) | A — Statique | 37,0 ± 0,8 | 26,0 ± 0,3 | 82,6 ± 0,1 | +0,00 ± 0,00 |
| 0,70 / 0,20 / 0,10 (défaut) | C — Système complet | 3,2 ± 0,2 | 2,1 ± 0,1 | 33,3 ± 0,9 | -0,72 ± 0,02 |
| 0,90 / 0,07 / 0,03 | A — Statique | 45,8 ± 0,4 | 33,4 ± 0,5 | 85,8 ± 0,4 | +0,00 ± 0,00 |
| 0,90 / 0,07 / 0,03 | C — Système complet | 3,3 ± 0,2 | 2,2 ± 0,1 | 33,7 ± 0,9 | -0,76 ± 0,02 |
