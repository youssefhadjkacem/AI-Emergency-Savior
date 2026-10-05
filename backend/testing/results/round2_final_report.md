# Second round, version finale : poids 3 sur la note et correction de `hospital.py`

Complète `round2_report.md`, qui décrit le diagnostic et la logique des deux chantiers. Ce rapport donne les chiffres définitifs, avec le poids retenu, et le traitement de `backend/hospital.py`.

Chiffres issus de `round2_evaluation_report.json`. Les résultats à poids égaux du rapport précédent sont conservés dans `round2_evaluation_report_equal_weights.json`. Pour reproduire, depuis `backend/` :

```
python -m testing.run_round2_evaluation
python -m pytest testing realtime
```

## 1. Conclusion

- **Poids 3 sur la note : appliqué et documenté** comme un choix de conception assumé, dans le Space (`src/filtering.py`) et dans `backend/hospital.py`.
- **Le défaut de classement reste à 0 %** avec ce poids, sur les trois tests isolés et sur le balayage de prix.
- **`hospital.py` est utilisé, avait le même défaut, et est corrigé** : de 16 hôpitaux sur 136 qui montaient avec un coût décuplé à 0.
- **Top-1 et Top-3 n'ont pas bougé**, en français comme en anglais.
- **Rien n'est déployé sur le Space.** Les six fichiers à déposer sont dans `backend/testing/to_deploy_round2/`.

Les 111 tests passent (105 existants, 6 nouveaux).

## 2. Poids 3 sur la note

Dans le score de compromis, la note du prestataire pèse 3 et les six autres critères pèsent 1. Pour un cas HIGH ou CRITICAL, le délai et la proximité pèsent en plus 2 ou 3.

**Statut de cette valeur.** C'est un paramètre choisi, pas un résultat d'optimisation : il exprime une priorité clinique donnée à la qualité du prestataire sur le coût et le délai. La base ne contient aucune donnée d'issue clinique qui permettrait de le calibrer. Il est documenté ainsi en commentaire dans le code, et doit être présenté ainsi dans le papier.

**Pourquoi un poids est nécessaire.** À poids égaux, la note compte autant qu'un critère binaire (CNAM, téléconsultation, même ville), qui vaut toujours 0 ou 1 en entier, alors que la note varie peu d'un médecin à l'autre.

| Top 3 par défaut, moyenne des 22 spécialités | Ancien tri | Poids égaux | **Poids 3 (retenu)** | Moyenne de la base |
|---|---|---|---|---|
| Note sur 10 | 8,78 | 6,98 | **8,30** | 7,28 |
| Centile de note dans la spécialité | 87 | 47 | **75** | — |
| Coût | 102 TND | 84 TND | **96 TND** | 88 TND |
| Délai de rendez-vous | 36,1 j | 27,5 j | **31,4 j** | 32,8 j |
| Accepte la CNAM | 77 % | 98 % | **97 %** | 75 % |
| Téléconsultation | 58 % | 97 % | **89 %** | 42 % |
| Le n°1 est le mieux noté de sa spécialité | 22 sur 22 | 0 sur 22 | **4 sur 22** | — |
| Places tenues par le pire de sa spécialité sur un critère | 29 sur 66 | 5 sur 66 | **7 sur 66** | — |

Avec le poids 3, le Top 3 retrouve une note élevée tout en restant moins cher, plus rapide et plus souvent conventionné que l'ancien tri.

## 3. Classement des prestataires, chiffres définitifs

Un seul attribut d'un seul médecin est modifié, sur les 504 médecins des 22 spécialités ; gravité désactivée des deux côtés.

**Dégradations — le rang ne doit pas s'améliorer.**

| Modification | Essais | Rang amélioré, avant | Rang amélioré, après |
|---|---|---|---|
| Coût multiplié par 10 | 504 | 144 (28,6 %) | **0** |
| Délai porté à 365 jours | 504 | 214 (42,5 %) | **0** |
| Note mise à zéro | 504 | 65 (12,9 %) | **0** |
| Déplacé hors de la ville du patient | 87 | 4 (4,6 %) | **0** |

Balayage de prix (× 1,2 à × 10) : 175 médecins sur 504 gagnaient un rang quelque part avant ; 0 après.

**Améliorations — le rang doit s'améliorer.**

| Modification | Essais | Avant | Après |
|---|---|---|---|
| Déplacé dans la ville du patient | 417 | 254 (60,9 %) | 401 (96,2 %) |
| Coût ramené au budget | 461 | 311 (67,5 %) | 368 (79,8 %) |
| Note portée au maximum | 504 | 482 (95,6 %) | 474 (94,0 %) |

Dans les essais restants, le rang est inchangé ; il ne recule jamais.

**Ce qui reste imparfait.**

- Un médecin au coût décuplé figure encore dans le Top 3 dans 15 essais sur 504, et avec un délai d'un an dans 12. Son rang n'a pas progressé : il était déjà très bien placé, et sa note pèse désormais assez pour l'y maintenir. C'est la contrepartie directe du poids 3.
- Fournir un budget ne rapproche presque pas le Top 3 de ce budget en moyenne (écart de 26,4 TND sans budget, 26,0 avec).

## 4. `backend/hospital.py`

**Est-il utilisé ? Oui.** `backend/main.py` l'importe et monte son routeur (`app.include_router(hospital_router)`), ce qui expose `/api/hospitals/recommend` et `/api/hospitals/recommend-with-fallback`. `backend/test_hospital_only.py` le monte aussi. Le frontend ne l'appelle pas encore, mais les routes sont actives dès que le backend tourne.

**Avait-il le défaut ? Oui.** Il contient sa propre copie du classement, avec une crowding distance qui vaut l'infini aux deux extrémités de chaque critère. Test isolé sur les 136 lignes de `hospitals_test.json` (16 spécialités), un seul attribut d'un seul hôpital dégradé :

| Modification | Essais | Rang amélioré, avant | Rang amélioré, après |
|---|---|---|---|
| Coût multiplié par 10 | 136 | 16 (11,8 %) | **0** |
| Délai porté à 365 jours | 136 | 29 (21,3 %) | **0** |
| Note mise à zéro | 136 | 11 (8,1 %) | **0** |
| Aucun lit disponible | 136 | 28 (20,6 %) | **0** |

**Correction appliquée.** La même que dans le Space : tri par score de compromis à l'intérieur d'un front de Pareto, poids 3 sur la note, la crowding distance ne départageant plus que les égalités. L'ancien tri reste disponible (`ranking_mode = "crowding"`) pour la mesure.

**Un second défaut trouvé au passage.** Le paramètre `is_urgent` multipliait par 2 la colonne du critère « service d'urgence ». Cela n'avait aucun effet : un tri de Pareto et une crowding distance sont insensibles à l'échelle d'un critère. Il est devenu un poids de 2 dans le score de compromis, et change maintenant réellement le classement, ce qu'un test vérifie.

`hospital.py` ne fait pas partie du Space : il est livré par le dépôt GitHub, pas par le dépôt manuel.

## 5. Gravité et reste du pipeline

Le poids de la note n'intervient pas dans l'estimation de la gravité : ces chiffres sont ceux de `round2_report.md`, revérifiés.

| Jeu | Cas | Exact | À un niveau près | Sous-estimés | Surestimés | Non évalués |
|---|---|---|---|---|---|---|
| 24 cas de gravité | 24 | 66,7 % | 83,3 % | 3 | 2 | 3 |
| — en français | 12 | 75,0 % | 100 % | 1 | 2 | 0 |
| — en anglais | 12 | 58,3 % | 66,7 % | 2 | 0 | 3 |
| 29 cas d'origine, français | 29 | 65,5 % | 96,6 % | 0 | 10 | 0 |
| 29 cas d'origine, anglais | 29 | 44,8 % | 72,4 % | 7 | 3 | 6 |

**Top-1 et Top-3, pipeline complet avec le poids définitif.**

| Jeu | Top-1 | Top-3 spécialités | Spécialité attendue parmi les 3 prestataires |
|---|---|---|---|
| Anglais (29) | 44,8 % | 58,6 % | 58,6 % |
| Français (29) | 82,8 % | 100 % | 100 % |
| Contrôle français (24) | 83,3 % | 100 % | 100 % |

Identiques à `fix_report.md` et `deployed_report.md` : aucune régression. Aucun cas ne change de spécialité.

**Effet de la gravité sur le classement**, patient à Tunis, moyenne des 22 spécialités :

| Gravité | Médecins de Tunis dans le Top 3 | Délai moyen | Note moyenne | Coût moyen |
|---|---|---|---|---|
| MEDIUM | 1,45 sur 3 | 31,6 j | 8,23 | 96 TND |
| HIGH | 2,09 sur 3 | 31,0 j | 7,81 | 93 TND |
| CRITICAL | 2,36 sur 3 | 30,1 j | 7,37 | 88 TND |

Pour un cas grave, le classement échange de la note contre de la proximité : presque un médecin local de plus dans le Top 3, pour 0,9 point de note en moins. Le délai baisse cette fois dans le bon sens, mais d'un jour et demi seulement : la base ne contient que des consultations à 16-48 jours.

## 6. Limites restantes

1. Le poids 3 est un choix, pas une valeur calibrée.
2. Le budget fourni par le patient reste presque sans effet sur le Top 3.
3. La gravité en anglais dépend d'un extracteur qui rate les trois quarts des symptômes ; un des trois cas critiques en anglais est classé MEDIUM.
4. La gravité en français surestime (10 cas sur 29, aucun sous-estimé).
5. Le bonus de gravité sur la spécialité ne change aucune classification dans les jeux de test.
6. `hospital.py` est testé sur un jeu de 136 lignes de test, pas sur des données réelles d'hôpitaux.

## 7. Déploiement à faire à la main

Dossier prêt : `backend/testing/to_deploy_round2/`, avec l'arborescence du Space et un `LISEZMOI.md`.

| Fichier | État |
|---|---|
| `src/severity.py` | nouveau |
| `src/nsga2.py` | modifié |
| `src/filtering.py` | modifié |
| `src/k1_brain.py` | modifié |
| `src/pipeline.py` | modifié |
| `app.py` | modifié |

Les six doivent être déposés ensemble. Tant que ce n'est pas fait, la production reste à l'état du round précédent : pas de gravité, ancien classement.

## 8. Résumé pour le papier (mise à jour)

Les paragraphes de `round2_report.md`, section 6, restent valables avec trois changements.

- Dans le score de compromis, la note du prestataire reçoit un poids de 3 et les autres critères un poids de 1. Ce poids est un paramètre de conception, exprimant la priorité donnée à la qualité du prestataire ; il n'est pas issu d'une optimisation.
- Avec ce poids, la note moyenne des prestataires recommandés est au 75e centile de leur spécialité (87e avec l'ancien tri, 47e à poids égaux), pour un coût et un délai moyens inférieurs à ceux de l'ancien tri (96 contre 102 TND ; 31,4 contre 36,1 jours).
- Les taux d'amélioration du rang deviennent : rapprochement géographique 60,9 % → 96,2 % ; coût ramené au budget 67,5 % → 79,8 % ; toutes les dégradations testées restent à 0 %. La même correction appliquée au module hôpitaux ramène de 11,8 % à 0 % les cas où un coût décuplé améliorait le rang.
