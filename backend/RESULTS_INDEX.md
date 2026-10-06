# Index des résultats — AI Emergency Savior

Un chiffre par ligne, avec le fichier dont il vient et la réserve à citer avec lui. Les chemins sont relatifs à `backend/`. Chaque valeur a été relue dans le fichier indiqué le 6 octobre 2026. Sauf mention contraire, « ± » est l'écart-type entre les seeds 7, 1 et 42.

Les valeurs marquées **non vérifié** sont listées en section 9.

## 1. Filtrage de bruit (Phase 1)

Source unique : `realtime/results/evaluation_report.json` (`reports_by_seed`). Seeds 7, 1, 42 ; 1 200 événements et 6 prestataires simulés par seed. Il n'existe pas de rapport rédigé pour cette phase : les moyennes ci-dessous sont calculées à partir des trois valeurs par seed du fichier.

| Étape | Chiffre à citer | Valeurs par seed (7 / 1 / 42) | Support par seed |
|---|---|---|---|
| Déduplication | Précision 0,994 ± 0,001 ; rappel 1,000 | P 0,994 / 0,994 / 0,994 | 180 / 179 / 155 doublons |
| Filtre de confirmation | Précision 1,000 ; rappel 1,000 | identiques | 203 / 211 / 180 |
| Péremption | Contribution neutralisée pour 100 % des valeurs périmées évaluables (`suppression_rate`) | 17 sur 17 / 22 sur 22 / 22 sur 22 | 18 / 23 / 23 |
| Anomalies (z-score robuste) | Précision 0,542 ± 0,102 ; rappel 0,523 ± 0,092 ; F1 0,529 ± 0,085 | P 0,429 / 0,625 / 0,571 ; R 0,500 / 0,625 / 0,444 | 6 / 8 / 9 pics |
| Événements normaux acceptés | Rappel 0,996 ± 0,001 | 0,995 / 0,996 / 0,996 | 793 / 779 / 833 |

Avant correction (`realtime/results/evaluation_report_v0.json`, seed 7) : précision de la déduplication 0,770 ; précision des anomalies 0,045 avec HalfSpaceTrees.

**Réserves.**
- Anomalies : 6 à 9 vrais pics par seed, donc chaque événement pèse 11 à 17 points. Résultat fragile, à ne pas présenter comme fiable.
- Péremption : il n'y a pas de règle de rejet. La précision et le rappel du fichier pour cette catégorie (0,003 et 0,048) utilisent un substitut que le fichier lui-même déclare peu informatif ; seul `suppression_rate` est pertinent.
- Les hyperparamètres de l'anomalie ont été réglés sur ces mêmes seeds 7/1/42 (commentaire dans `realtime/noise_filter.py:235-277`) : pas de seeds de réglage séparés pour la Phase 1.
- Flux simulé par nous.

## 2. Détection de concept drift (Phase 2)

Source : `realtime/results/drift_report.md` (chiffres issus de `drift_evaluation_report.json` ; réglage dans `drift_tuning_report.json`, seeds 100/101/102). Évaluation sur les seeds 7, 1, 42 : 60 épisodes et 60 séries stables par profil. Drift de 4 à 6 places. 1 point ≈ 2 min.

| | F1 progressif | F1 brusque | Séries stables en fausse alerte | Délai progressif (points) | Délai brusque (points) |
|---|---|---|---|---|---|
| ADWIN (`delta = 1e-12`, `clock = 1`) | 1,000 ± 0,000 | 0,983 ± 0,029 | 0 sur 60 | 61,1 ± 1,2 | 24,3 ± 0,4 |
| Page-Hinkley (`delta = 1.5`, `threshold = 10`), retenu | 0,984 ± 0,027 | 0,992 ± 0,014 | 1 sur 60 | 53,7 ± 0,7 | 10,8 ± 0,8 |

| Autre chiffre | Valeur |
|---|---|
| Défauts River, F1 | ADWIN 0,522 (progressif) et 0,357 (brusque) ; Page-Hinkley 0,615 et 0,634 |
| Défauts River, séries stables en fausse alerte | ADWIN 49 sur 60 ; Page-Hinkley 32 sur 60 |
| Drift réduit à 2-3 places, F1 | ADWIN 1,000 ± 0,000 et 0,233 ± 0,076 ; Page-Hinkley 0,886 ± 0,057 et 0,726 ± 0,116 |
| Délai brusque en minutes | Page-Hinkley ≈ 23 min, ADWIN ≈ 52 min |

**Réserves.**
- Les écarts de F1 entre algorithmes sont plus petits que leur écart-type entre seeds ; seul le délai les départage.
- Les F1 proches de 1 reflètent un drift simulé net (effet plafond).
- Le `delta` de Page-Hinkley est au bord de sa grille ; un drift de moins de 1,5 place environ est invisible.
- Pas de saisonnalité, un seul épisode de baisse par série.

## 3. Pipeline de bout en bout, avant correction

Source : `testing/results/pipeline_report.md` (chiffres issus de `pipeline_evaluation_report.json`, copie dans `pipeline_evaluation_report_before_fixes.json`). 29 cas écrits à la main (`testing/pipeline_cases.py`).

| Mesure | Valeur |
|---|---|
| Anglais, Top-1 strict | 44,8 % (13 sur 29) |
| Anglais, Top-1 large | 51,7 % (15) |
| Anglais, Top-3 | 58,6 % (17) |
| Anglais, cas sans prédiction | 8 sur 29 |
| Anglais, classifieur alimenté avec les symptômes attendus | 89,7 % (26) en Top-1 strict ; 100 % en Top-1 large et Top-3 |
| Anglais, exactitude par branche ; F1 macro | 58,6 % ; 0,661 |
| Extraction anglaise | Précision 0,755 ; rappel 0,274 ; F1 0,402 |
| Français, Space déployé | 3,4 % (1 sur 29) ; 26 cas sans prédiction |
| Latence | 103 ms en moyenne en local (P95 152 ms) ; 644 ms via le Space (P95 734 ms) |
| Classement : rang amélioré quand le coût est multiplié par 10 | 29 % des essais |

**Réserves.**
- Vérité terrain fixée par l'auteur des tests, sur le bon sens médical et l'arbre K1 : ce n'est pas une vérité clinique.
- Un seul cas par spécialité : on ne peut pas conclure sur une spécialité donnée.
- Google Translate refusait tous les appels pendant le test (erreur 429, 174 sur 174).

## 4. Corrections : lexique français, négations, Top 3

Source : `testing/results/fix_report.md` (chiffres issus de `fix_evaluation_report.json`).

| Jeu | Mesure | Avant | Après |
|---|---|---|---|
| **Contrôle français, 24 cas** (à retenir) | Top-1 | 4,2 % | **83,3 %** |
| | Top-3 spécialités | 4,2 % | 100 % |
| | Spécialité attendue parmi les 3 prestataires | 4,2 % | 100 % |
| | Extraction, précision ; rappel | 1,000 ; 0,011 | 0,939 ; 0,697 |
| | Cas sans prédiction | 23 | 0 |
| Français, 29 cas d'origine | Top-1 | 3,4 % | 82,8 % |
| | Extraction, précision ; rappel | 0,667 ; 0,014 | 0,928 ; 0,973 |
| Anglais, 29 cas | Top-1 ; Top-3 | 44,8 % ; 58,6 % | 44,8 % ; 58,6 % |
| | Spécialité attendue parmi les 3 prestataires | 44,8 % | 58,6 % |
| | Extraction, précision ; rappel | 0,755 ; 0,274 | 0,800 ; 0,274 |
| | Cas sans prédiction | 8 | 8 |

| Effet isolé | Valeur |
|---|---|
| Négations, précision d'extraction | Anglais 0,755 → 0,800 ; français 0,911 → 0,928 ; contrôle 0,899 → 0,939 |
| Négations, coût | 2 vrais symptômes perdus sur les 29 cas français (rappel 0,986 → 0,973) ; 9 symptômes niés ne sont plus extraits |
| Couverture du lexique | 271 des 300 identifiants de symptômes |
| Traduction seule (MyMemory), extracteur d'origine, français 29 cas | 34,5 % — **non vérifié** dans un fichier de données (section 9) |
| Latence locale sans traduction | Anglais 85 ms (P95 155 ms) ; français 19 ms (P95 42 ms) — **non vérifié** dans un fichier de données (section 9) |

**Réserves.**
- Les jeux de test sont du même auteur que le lexique. Les 29 cas d'origine étaient connus pendant sa rédaction : leur rappel de 0,973 est optimiste. Le jeu de contrôle a été écrit après le gel du lexique.
- Sur le jeu de contrôle, 30 % des symptômes ne sont pas reconnus.
- L'anglais n'est pas réparé : Top-1 à 44,8 %, rappel à 0,274, 8 cas sans prédiction.

## 5. Round 2 : classement corrigé, poids ×3 sur la note, gravité

Source : `testing/results/round2_final_report.md` (chiffres issus de `round2_evaluation_report.json` ; poids égaux dans `round2_evaluation_report_equal_weights.json` ; diagnostic dans `round2_report.md`).

**Classement.** Un seul attribut d'un seul médecin est modifié, sur les 504 médecins des 22 spécialités.

| Modification | Essais | Rang amélioré, avant | Rang amélioré, après |
|---|---|---|---|
| Coût multiplié par 10 | 504 | 144 (28,6 %) | 0 |
| Délai porté à 365 jours | 504 | 214 (42,5 %) | 0 |
| Note mise à zéro | 504 | 65 (12,9 %) | 0 |
| Déplacé hors de la ville du patient | 87 | 4 (4,6 %) | 0 |
| Déplacé dans la ville du patient (doit s'améliorer) | 417 | 254 (60,9 %) | 401 (96,2 %) |
| Coût ramené au budget (doit s'améliorer) | 461 | 311 (67,5 %) | 368 (79,8 %) |
| Note portée au maximum (doit s'améliorer) | 504 | 482 (95,6 %) | 474 (94,0 %) |

| Top 3 par défaut, moyenne des 22 spécialités | Ancien tri | Poids égaux | Poids 3 (retenu) | Base entière |
|---|---|---|---|---|
| Note sur 10 | 8,78 | 6,98 | 8,30 | 7,28 |
| Centile de note dans la spécialité | 87 | 47 | 75 | — |
| Coût | 102 TND | 84 TND | 96 TND | 88 TND |
| Délai de rendez-vous | 36,1 j | 27,5 j | 31,4 j | 32,8 j |

Module hôpitaux (`hospital.py`, 136 lignes de `hospitals_test.json`) : coût multiplié par 10 améliorant le rang dans 16 essais sur 136 (11,8 %) avant, 0 après.

**Gravité** (règles, `severity.py` du Space).

| Jeu | Cas | Exact | À un niveau près | Sous-estimés | Surestimés | Non évalués |
|---|---|---|---|---|---|---|
| 24 cas de gravité | 24 | 66,7 % | 83,3 % | 3 | 2 | 3 |
| — français | 12 | 75,0 % | 100 % | 1 | 2 | 0 |
| — anglais | 12 | 58,3 % | 66,7 % | 2 | 0 | 3 |
| 29 cas d'origine, français | 29 | 65,5 % | 96,6 % | 0 | 10 | 0 |
| 29 cas d'origine, anglais | 29 | 44,8 % | 72,4 % | 7 | 3 | 6 |

Top-1 et Top-3 du pipeline complet : identiques à la section 4, aucun cas ne change de spécialité.

**Réserves.**
- Le poids 3 est un choix de conception, pas une valeur calibrée : aucune donnée d'issue clinique.
- Le budget fourni par le patient reste presque sans effet (écart au budget du Top 3 : 26,4 TND sans budget, 26,0 avec).
- La gravité est annotée par l'auteur des règles. En anglais elle dépend d'un extracteur qui manque les trois quarts des symptômes.
- Le bonus de gravité sur la spécialité ne change aucune classification dans les jeux de test.
- `hospital.py` est testé sur un jeu de test, pas sur des données réelles d'hôpitaux.

## 6. Adaptation performative (Phase 3)

Source : `realtime/results/feedback_tables.md` et `feedback_report.md` (chiffres issus de `feedback_evaluation_report.json` ; réglage dans `feedback_tuning_report.json`, seeds 100/101/102). Seeds 7, 1, 42 ; 25 742 patients simulés par condition, toutes charges réunies. Figures : `realtime/results/feedback_fig1_occupancy.png`, `feedback_fig2_saturated_rate.png`.

Conditions : A statique ; B seuil seul ; C système complet ; D complet sur flux brut ; E complet sans réservations ; F complet sans Page-Hinkley.

| Mesure | Charge | A | B | C | D | F |
|---|---|---|---|---|---|---|
| Patients envoyés vers un prestataire saturé (%) | Faible (50/h) | 1,2 ± 0,2 | 1,1 ± 0,3 | 1,0 ± 0,2 | 0,9 ± 0,2 | 1,0 ± 0,2 |
| | Moyenne (200/h) | 9,7 ± 0,6 | 8,2 ± 0,7 | 2,3 ± 0,1 | 2,0 ± 0,2 | 2,4 ± 0,1 |
| | Forte (600/h) | 37,0 ± 0,8 | 28,4 ± 1,0 | 3,2 ± 0,2 | 3,7 ± 0,1 | 3,3 ± 0,1 |
| Patients refusés faute de place (%) | Faible | 1,1 ± 0,3 | 0,9 ± 0,4 | 0,7 ± 0,2 | 0,6 ± 0,2 | 0,7 ± 0,2 |
| | Moyenne | 5,9 ± 0,9 | 4,8 ± 0,7 | 1,4 ± 0,2 | 1,3 ± 0,3 | 1,4 ± 0,1 |
| | Forte | 26,0 ± 0,3 | 19,7 ± 0,5 | 2,1 ± 0,1 | 2,5 ± 0,3 | 2,1 ± 0,1 |
| Gini des patients reçus, par spécialité | Forte | 0,841 ± 0,001 | 0,780 ± 0,007 | 0,483 ± 0,011 | 0,538 ± 0,005 | 0,482 ± 0,010 |
| Part des patients chez les 3 prestataires les plus sollicités (%) | Faible | 85,5 ± 0,6 | 85,3 ± 1,1 | 82,9 ± 0,7 | 82,6 ± 0,8 | 82,9 ± 1,0 |
| | Moyenne | 83,1 ± 0,4 | 80,7 ± 0,7 | 65,7 ± 0,3 | 67,5 ± 1,1 | 65,7 ± 0,1 |
| | Forte | 82,6 ± 0,1 | 71,9 ± 1,4 | 33,3 ± 0,9 | 36,8 ± 0,7 | 33,3 ± 1,0 |
| Occupation moyenne de tous les prestataires (%) | Forte | 36,2 ± 0,3 | 37,0 ± 0,3 | 39,8 ± 0,4 | 39,6 ± 0,4 | 39,8 ± 0,3 |

**Coût de l'adaptation, condition C par rapport à A.**

| Pour le prestataire n°1 | Faible | Moyenne | Forte |
|---|---|---|---|
| Recommandations modifiées (%) | 9,1 ± 1,9 | 42,2 ± 1,8 | 75,9 ± 0,7 |
| Note sur 10 | −0,05 ± 0,02 | −0,29 ± 0,04 | −0,72 ± 0,02 |
| Coût (TND) | +0,2 ± 0,4 | −1,4 ± 0,6 | −4,8 ± 0,3 |
| Délai de rendez-vous (jours) | −0,08 ± 0,05 | −0,19 ± 0,17 | −0,07 ± 0,05 |
| Dans la ville du patient (points) | +1,2 ± 0,9 | +4,3 ± 0,9 | +3,4 ± 0,5 |

| Autre chiffre | Valeur |
|---|---|
| Spécialité recommandée identique à A | 100 % dans les conditions B à F |
| Cas CRITICAL détournés hors de leur ville | 0 sur 1 296, dans toutes les conditions |
| Épisodes de saturation réelle détectés, C | 24,4 ± 1,9 % (faible) ; 46,0 ± 3,0 % (moyenne) ; 78,1 ± 1,0 % (forte) |
| Déclarations de saturation par journée, charge faible | C : 66 ± 11 ; D : 2 382 ± 42 |
| Déclarations « à tort », C, charge forte | 80,6 ± 0,5 % ; 27,4 ± 0,4 % une fois retirées celles fondées sur des patients en route |
| Sensibilité au suivi (refusés, charge forte, A puis C) | 0,50/0,30/0,20 : 20,2 ± 0,7 % et 2,2 ± 0,1 % ; 0,90/0,07/0,03 : 33,4 ± 0,5 % et 2,2 ± 0,1 % |
| Paramètres réglés | Réservation 90 min ; marge d'hystérésis 0,15 ; pénalité d'alerte 0,02 |
| Figure 1 (seed 7, charge forte, allergologue D00013, 10 places) | Temps à 90 % ou plus : 68,5 % en A, 6,3 % en C ; patients reçus : 150 en A, 22 en C |

**Réserves.**
- Simulateur écrit par nous. Capacité (créneaux par semaine ÷ 5), taux d'arrivée, suivi des recommandations, durées et répartition de la gravité sont des hypothèses, sans donnée réelle.
- Les trois niveaux de charge ont été choisis pour produire trois régimes de saturation en condition statique.
- Les réservations supposent que le système connaît le prestataire choisi par le patient ; sans cela on retombe sur B.
- Page-Hinkley n'apporte rien de mesurable (C et F identiques à l'écart-type près).
- Le taux de déclarations « à tort » est gonflé par la boucle elle-même et ne doit pas être cité seul.
- Ce round démontre un mécanisme, pas une efficacité en conditions réelles.

## 7. Évaluation en production

Source : `testing/results/deployed_report.md`. Round 1 : sections 1 à 6, chiffres dans `deployed_evaluation_report_round1.json` (5 octobre 2026). Round 2 : section 7, chiffres dans `deployed_evaluation_report.json` et `deployed_round2_check.json` (6 octobre 2026). Space `youssef0081/emergency-savior-output`, 82 appels par évaluation.

| Mesure | Round 1 | Round 2 |
|---|---|---|
| Réponses obtenues | 82 sur 82 | 82 sur 82 |
| Top-1 strict : anglais ; français ; contrôle FR | 44,8 % ; 82,8 % ; 83,3 % | 44,8 % ; 82,8 % ; 83,3 % |
| Top-3 spécialités : anglais ; français ; contrôle FR | 58,6 % ; 100 % ; 100 % | 58,6 % ; 100 % ; 100 % |
| Réponses identiques au code local du même round | 82 sur 82 (symptômes, spécialités, prestataires, statut) | 82 sur 82 pour les trois prestataires |
| Gravité renvoyée, 24 cas | — | Identique au local 24 sur 24 ; exacte 16 sur 24 (66,7 %) |
| Latence moyenne : anglais ; français ; contrôle FR | 415 ms ; 411 ms ; 458 ms | 326 ms ; 290 ms ; 226 ms |
| Latence P95 : anglais ; français ; contrôle FR | 480 ms ; 510 ms ; 658 ms | 356 ms ; 229 ms ; 246 ms |

**Réserves.**
- Une seule exécution par round, depuis une seule machine, Space déjà réveillé ; les deux rounds ont été mesurés à un jour d'écart. La différence de latence n'est pas une comparaison contrôlée.
- L'identité entre local et production prouve que le code déployé est le bon, rien de plus sur la qualité du lexique ou des règles.
- `deployed_round2_check.json` vient d'un script ponctuel non versionné.
- La monotonie du classement n'est pas testable contre le Space ; elle repose sur l'identité du code.
- L'adaptation performative (section 6) n'est pas déployée.

## 8. Écarts entre le papier et le code

Je n'ai pas eu le texte du papier. La colonne « Le papier décrit » reprend `testing/results/pipeline_report.md` (section 2) et les énoncés de travail des rounds successifs ; elle est à confronter au manuscrit. La colonne « Le code fait » décrit l'état actuel.

Les fichiers `src/…` sont ceux du Space `emergency-savior-output`. Une copie identique de `nsga2.py`, `filtering.py`, `k1_brain.py`, `pipeline.py`, `severity.py` et `app.py` se trouve dans `testing/to_deploy_round2/` ; `extraction.py`, `negation.py` et `french_lexicon.py` ne sont que dans le Space.

| Le papier décrit | Le code fait | Fichier et ligne |
|---|---|---|
| Random Forest à poids figés | Aucun modèle n'est chargé ni exécuté. Les importances sont lues dans une feuille Excel ; un symptôme sans importance reçoit 0,001. La « probabilité RF » est une somme de importance × IDF × coefficient de profil | `src/k1_brain.py:28-32` (lecture), `:97-112` (calcul) |
| Score final 0,60 × arbre + 0,40 × RF | Conforme | `src/k1_brain.py:166` |
| Score d'arbre pondéré par la sévérité | Sévérité fixée à 1 dans le score d'arbre | `src/k1_brain.py:82-87` |
| Extraction d'entités médicales | Dictionnaire de synonymes, expressions régulières et correspondance floue en anglais ; lexique d'expressions régulières en français (271 symptômes sur 300) | `src/extraction.py:263-309`, `src/french_lexicon.py:60` |
| Gestion de la négation | Masquage de portée de 5 mots, ajouté au round 1 | `src/negation.py:33`, `:103` |
| Traduction vers l'anglais | Secours seulement, pour un texte français où le lexique ne trouve rien ; Google puis MyMemory | `src/extraction.py:42-51` |
| Optimisation sur distance, coût, adéquation de spécialité | 7 objectifs : note, coût, délai, créneaux, ville, CNAM, téléconsultation. L'adéquation de spécialité est un filtre, pas un objectif | `src/filtering.py:8`, `:158-219` |
| Distance | 0 si même ville (égalité de chaînes), 1 sinon | `src/filtering.py:200` |
| NSGA-II+ | Pas de population, de générations, de croisement ni de mutation. Un tri non dominé, puis un tri par score de compromis pondéré dans chaque front | `src/nsga2.py:102-170` |
| Densité k-NN améliorée | Ne sert plus qu'à départager deux scores égaux | `src/nsga2.py:163-168` |
| (poids des objectifs) | Poids 3 sur la note, 1 sur les autres ; 2 ou 3 sur délai et ville si HIGH ou CRITICAL | `src/filtering.py:10`, `:26`, `:38-44` |
| Top 3 prestataires | Top 3 réparti entre spécialités selon le score de classification, affiché par le Space | `src/filtering.py:46`, `:231` ; `app.py:55-59` |
| Niveaux de gravité LOW à CRITICAL | Estimation par règles sur les symptômes et le texte, ajoutée au round 2 ; ni modèle ni apprentissage | `src/severity.py:76-166`, `:190`, `:200` |
| Gravité issue de la voix ou de la vision | `urgent` vaut vrai si le module vision renvoie HIGH ou CRITICAL ou si le groupe d'âge est `"senior"` ; le rapport de bout en bout indique que le Space vocal renvoie `"elderly"` | `main.py:344-345`, `:357` |
| Étape vocale (Whisper) | Appelée par le backend, jamais mesurée : toutes les évaluations partent d'un texte | `main.py:36`, `:238`, `:324` |
| Module vision | Optionnel, jamais évalué | `main.py:37`, `:351` |
| Filtrage de bruit, drift, saturation (section 3.8) | Implémentés et testés en simulation ; aucun import de `realtime` dans le backend | `main.py` (aucune occurrence de `realtime`) |
| Seuil de saturation 80-90 % | 0,80 (alerte) et 0,90 (saturation) | `realtime/saturation.py:44-45` |
| Capacité des prestataires | Absente de la base ; hypothèse créneaux par semaine ÷ 5 | `realtime/feedback_simulator.py:71` |
| Détecteur de drift | Page-Hinkley retenu, ADWIN comparé ; sans effet mesurable dans la réaction | `realtime/drift_detection.py:45`, `:124`, `:140` |
| Recommandation d'hôpitaux | Même classement corrigé, routes actives, non appelées par le frontend d'après `round2_final_report.md` | `hospital.py:96`, `:119`, `:176` |

## 9. Valeurs non vérifiées

| Valeur | Pourquoi |
|---|---|
| Ce que le papier affirme, ligne par ligne (section 8) | Le manuscrit ne m'a pas été fourni |
| 34,5 % en français avec la traduction MyMemory seule, et les autres chiffres de la « 1re exécution » | Présents dans `fix_report.md` (section 4.2), tirés de la sortie console ; le fichier JSON enregistré est celui de la seconde exécution |
| Latence locale 85 ms et 19 ms | Présente dans `fix_report.md` (section 4.4), « mesure faite à part, hors du fichier JSON » |
| « 80 symptômes sur 300 ont une importance » | Présent dans `pipeline_report.md` ; non recompté aujourd'hui dans `Model_Results.xlsx` |
| `"elderly"` renvoyé par le Space vocal | Relevé à la lecture du code dans `pipeline_report.md` ; le Space vocal n'a jamais été exécuté |
| Performance de l'étape vocale et du module vision | Jamais mesurée |
| Répartition des 211 Top 3 gardant un prestataire saturé entre la règle CRITICAL et le manque d'alternatives | Non mesurée (`feedback_report.md`, section 6) |
