# Second round de corrections : classement des prestataires et gravité

Deux chantiers séparés, chacun avec son diagnostic, sa correction et sa mesure avant / après. Tout est local : rien n'est déployé sur le Space.

Chiffres issus de `round2_evaluation_report.json`. Pour les reproduire, depuis `backend/` :

```
python -m testing.run_round2_evaluation
python -m pytest testing realtime
```

> **Mise à jour.** Les chiffres de classement de ce rapport sont ceux des poids égaux. Depuis, un poids de 3 sur la note a été retenu et `backend/hospital.py` a été corrigé : les chiffres définitifs sont dans `round2_final_report.md`. Le diagnostic, la logique de gravité et ses résultats restent valables tels quels.

## 1. Conclusion

**Chantier 1 — le défaut est corrigé, avec une contrepartie à arbitrer.** Rendre un médecin dix fois plus cher n'améliore plus jamais son rang (0 essai sur 504, contre 144 avant). Mais le nouveau tri donne le même poids aux sept critères, et les médecins recommandés ont maintenant une note moyenne (6,98 sur 10) au lieu d'une note élevée (8,78).

**Chantier 2 — la gravité existe, elle est correcte en français et faible en anglais.** Sur 24 cas annotés, le niveau estimé est exact dans 67 % des cas et à un niveau près dans 83 % ; en français, 75 % et 100 %. En anglais, 3 cas graves sur 12 sont sous-estimés ou non évalués, parce que l'extracteur anglais ne reconnaît pas leurs symptômes.

**Rien n'est cassé.** Top-1 et Top-3 sont identiques à ceux du rapport précédent sur les trois jeux de cas. Les 105 tests passent.

Deux résultats décevants sont à connaître avant de citer ces chiffres :

- le bonus de gravité sur la spécialité ne change aucune classification dans nos jeux de test : son utilité n'est pas démontrée ;
- pour un cas grave, le classement privilégie bien les médecins de la ville du patient, mais ne réduit pas le délai de rendez-vous.

## 2. Chantier 1 — classement des prestataires

### 2.1 Diagnostic

L'hypothèse est confirmée, avec une précision : la dominance de Pareto est respectée ; le défaut est dans le tri à l'intérieur d'un front.

`knn_density_distance` attribue une densité infinie aux deux extrémités de chaque objectif :

```python
knn_dist[sorted_idx[0]] = np.inf    # la meilleure valeur
knn_dist[sorted_idx[-1]] = np.inf   # la pire valeur
```

et `nsga2_rank` place les densités les plus fortes en tête du front. Devenir le pire sur un critère fait donc monter.

Test isolé, sur le cardiologue D00033 (115 TND, classé 10e), en ne changeant que son prix :

| Prix | × 0,5 | réel | × 1,2 | × 1,5 | × 2 | × 10 |
|---|---|---|---|---|---|---|
| Rang avant correction | 3 | 10 | 3 | 3 | 3 | 3 |
| Densité dans son front | infinie | 0,51 | infinie | infinie | infinie | infinie |

À + 20 %, il devient le plus cher de sa spécialité et passe de la 10e à la 3e place, exactement comme s'il était à moitié prix. Un autre médecin (D00041), lui, recule de front quand son prix monte : là où un médecin en domine un autre, le tri est correct.

La densité a un sens dans un algorithme génétique, où elle préserve la diversité d'une population entre deux générations. Ici il n'y a pas de générations : on classe une liste fixe, et être isolé n'est pas une qualité.

### 2.2 Correction

Dans `nsga2.py`. Le tri par fronts de Pareto ne change pas. À l'intérieur d'un front, les médecins sont triés par un **score de compromis** :

1. chaque objectif est ramené entre 0 (meilleure valeur parmi les candidats) et 1 (pire valeur) ;
2. le score est la moyenne de ces sept valeurs ; le plus bas est le meilleur ;
3. la densité k-NN ne sert plus qu'à départager deux scores rigoureusement égaux.

Propriété obtenue, vérifiée par test : dégrader un médecin sur un critère, toutes choses égales par ailleurs, ne peut pas améliorer son rang. Son front ne peut pas s'améliorer, et face à tout autre médecin son score sur le critère dégradé augmente.

L'ancien tri reste disponible (`within_front="knn_density"`) pour mesurer l'avant / après.

### 2.3 Résultats

Même méthode que le rapport précédent : un seul attribut d'un seul médecin est modifié, sur les 504 médecins des 22 spécialités. La gravité est désactivée des deux côtés.

**Dégradations — le rang ne doit pas s'améliorer.**

| Modification | Essais | Rang amélioré, avant | Rang amélioré, après |
|---|---|---|---|
| Coût multiplié par 10 | 504 | 144 (28,6 %) | **0** |
| Délai porté à 365 jours | 504 | 214 (42,5 %) | **0** |
| Note mise à zéro | 504 | 65 (12,9 %) | **0** |
| Déplacé hors de la ville du patient | 87 | 4 (4,6 %) | **0** |

Balayage de prix (× 1,2 à × 10) : avant, 175 médecins sur 504 gagnaient au moins un rang quelque part quand leur prix montait ; après, aucun.

**Améliorations — le rang doit s'améliorer.**

| Modification | Essais | Rang amélioré, avant | Rang amélioré, après |
|---|---|---|---|
| Déplacé dans la ville du patient | 417 | 254 (60,9 %) | 397 (95,2 %) |
| Coût ramené au budget | 461 | 311 (67,5 %) | 387 (83,9 %) |
| Note portée au maximum | 504 | 482 (95,6 %) | 437 (86,7 %) |

La ville et le budget comptent nettement plus qu'avant. Les essais où le rang ne s'améliore pas sont des rangs inchangés, jamais des reculs.

### 2.4 Effets de bord

**La note ne décide plus du Top 3.** C'est l'effet le plus important.

| Top 3 par défaut, moyenne des 22 spécialités | Avant | Après | Moyenne de la base |
|---|---|---|---|
| Note sur 10 | 8,78 | 6,98 | 7,28 |
| Centile de note dans la spécialité | 87 | 47 | — |
| Coût | 102 TND | 84 TND | 88 TND |
| Délai de rendez-vous | 36,1 j | 27,5 j | 32,8 j |
| Accepte la CNAM | 77 % | 98 % | 75 % |
| Téléconsultation | 58 % | 97 % | 42 % |
| Le n°1 est le mieux noté de sa spécialité | 22 sur 22 | 0 sur 22 | — |
| Places tenues par le pire de sa spécialité sur un critère | 29 sur 66 | 5 sur 66 | — |

Avant, le Top 3 était en pratique « les mieux notés », chers et lents. Après, c'est « les moins chers, les plus rapides, conventionnés et en téléconsultation », avec une note un peu sous la moyenne. La cause : les sept critères ont le même poids, et trois d'entre eux sont binaires (CNAM, téléconsultation, même ville), donc valent toujours 0 ou 1 en entier, alors que la note varie peu d'un médecin à l'autre.

Ce n'est pas un bug mais un choix de pondération, et c'est à toi de le faire. Pour l'éclairer, voici ce que donne un poids plus fort sur la note, sans rien changer d'autre (la propriété de monotonie tient pour tout jeu de poids positifs) :

| Poids de la note | Note du Top 3 | Centile | Coût | Délai | CNAM | Téléconsultation |
|---|---|---|---|---|---|---|
| × 1 (réglage livré) | 6,98 | 47 | 84 TND | 27,5 j | 98 % | 97 % |
| × 2 | 7,60 | 60 | 90 TND | 29,2 j | 98 % | 97 % |
| × 3 | 8,30 | 75 | 96 TND | 31,4 j | 97 % | 89 % |
| × 5 | 8,82 | 87 | 100 TND | 33,4 j | 94 % | 74 % |

**Autres effets.**

- Un médecin dont la note passe au maximum n'est plus automatiquement premier (dans le Top 3 pour 158 essais sur 504, contre 504 avant).
- Fournir un budget ne rapproche toujours pas le Top 3 de ce budget en moyenne (écart de 18,0 TND avec ou sans). Le Top 3 est simplement moins cher qu'avant dans tous les cas (écart de 32 TND avant correction).
- Un coût multiplié par 10 écrase la normalisation : les écarts de prix entre les autres médecins ne comptent presque plus. Cela ne concerne que ce test ; dans la base réelle, les prix vont de 50 à 150 TND.

## 3. Chantier 2 — gravité

### 3.1 Diagnostic

Un point de l'énoncé est à corriger : **il n'y a aucune détection du mot « urgent » dans le texte**. `urgent` est un booléen fourni par l'appelant — la case à cocher du Space, le paramètre de `/optimize` — et la seule règle qui l'utilise est dans `K1Brain.score` :

```python
if is_urgent and 'Cardiologist' in spec:
    score_final *= 2.0
```

Recherche dans tout le code du Space : aucune autre logique de gravité. La « sévérité » mentionnée dans `k1_brain.py` est fixée à 1 en commentaire. Dans `/analyze-full`, `urgent` ne peut devenir vrai que par le module vision (optionnel) ou si l'âge estimé vaut `"senior"`, valeur que le Space speech ne renvoie jamais. Sur le chemin principal, il était donc toujours faux.

### 3.2 Logique de gravité

Nouveau module `src/severity.py`, dans le Space. C'est un système de règles, qui renvoie avec chaque estimation la liste des indices qui l'ont produite.

**Score de départ, selon le symptôme le plus grave extrait.**

| Palier | Score | Exemples |
|---|---|---|
| Critique (10 symptômes) | 6 | déficit d'un côté du corps, paralysie faciale, confusion, coma, idées suicidaires, vomissement de sang, cyanose |
| Élevé (37 symptômes) | 4 | douleur thoracique, essoufflement, crachats de sang, convulsions, perte de vision, sang dans les urines, ictère, colique néphrétique |
| Modéré (44 symptômes) | 2 | fièvre, vomissements, douleur abdominale, palpitations, vertiges |
| Symptômes bénins seulement | 1 | — |

**Relèvement à 6 au moins.**

- Trois combinaisons : douleur thoracique avec sueurs, irradiation au bras, oppression, essoufflement, rythme irrégulier ou syncope ; essoufflement avec cyanose ou œdème ; raideur de nuque avec fièvre.
- Formulations d'urgence vitale lues directement dans le texte, même si aucun symptôme n'a été extrait : « ne respire plus », « inconscient », « crise cardiaque », « veut en finir », et leurs équivalents anglais.

**Modificateurs, un point chacun.** Plus un : au moins deux symptômes du palier élevé ; intensité exprimée (« atroce », « unbearable ») ; début brutal (« d'un seul coup », « suddenly ») ; appel à l'aide (« ambulance », « au secours ») ; âge de moins de 5 ans ou d'au moins 75 ans. Moins un : ancienneté (« depuis des mois », « for years »).

**Seuils.** 6 et plus : CRITICAL. 4 ou 5 : HIGH. 2 ou 3 : MEDIUM. 1 ou moins : LOW.

Les seuils suivent les scores de départ : un symptôme du palier élevé suffit pour HIGH, et il faut deux modificateurs pour qu'un symptôme modéré y arrive.

**Deux règles particulières.**

- `UNKNOWN` quand il n'y a ni symptôme ni indice : l'absence d'information n'est pas une preuve de bénignité.
- `urgent=True` fourni par l'appelant devient un plancher à HIGH : un opérateur qui signale une urgence n'est jamais contredit à la baisse.

Le lexique français et la détection de négation existants sont réutilisés tels quels : les symptômes arrivent de l'extracteur, et les indices textuels sont cherchés dans le texte dont les négations sont masquées.

### 3.3 Intégration

**Dans le score de spécialité.** La règle « urgent → cardiologie × 2 » est supprimée. Pour un cas HIGH ou CRITICAL, toute spécialité responsable d'un des symptômes qui font la gravité reçoit un bonus (× 1,25 pour HIGH, × 1,5 pour CRITICAL). « Responsable » signifie que le symptôme figure dans ses symptômes clés ou exclusifs de l'arbre K1, ou dans son profil. La cardiologie en bénéficie pour une douleur thoracique, la neurologie pour un déficit, etc.

**Dans le classement des prestataires.** Décision de conception, gardée simple : pour un cas HIGH ou CRITICAL, le délai de rendez-vous et la proximité pèsent 2 ou 3 fois plus dans le score de compromis. Ces poids n'agissent qu'à l'intérieur d'un front de Pareto. C'est le seul point de contact entre les deux chantiers.

Aucune de ces valeurs (1,25, 1,5, 2, 3) n'est calibrée : la base ne contient aucune donnée d'issue clinique.

### 3.4 Qualité de l'estimation

24 cas annotés à la main après le gel des règles (`severity_cases.py`), 6 par niveau, moitié en français, moitié en anglais. Les 29 cas d'origine, dont la gravité avait été annotée lors du premier test, servent de second jeu. Évaluation avec `urgent` faux partout : on mesure ce que le texte seul permet.

| Jeu | Cas | Exact | À un niveau près | Sous-estimés | Surestimés | Non évalués |
|---|---|---|---|---|---|---|
| 24 cas de gravité | 24 | 66,7 % | 83,3 % | 3 | 2 | 3 |
| — en français | 12 | 75,0 % | 100 % | 1 | 2 | 0 |
| — en anglais | 12 | 58,3 % | 66,7 % | 2 | 0 | 3 |
| 29 cas d'origine, français | 29 | 65,5 % | 96,6 % | 0 | 10 | 0 |
| 29 cas d'origine, anglais | 29 | 44,8 % | 72,4 % | 7 | 3 | 6 |

**Matrice de confusion, 24 cas** (ligne : attendu ; colonne : estimé).

| | LOW | MEDIUM | HIGH | CRITICAL | Non évalué |
|---|---|---|---|---|---|
| LOW | 5 | 0 | 0 | 0 | 1 |
| MEDIUM | 1 | 5 | 0 | 0 | 0 |
| HIGH | 0 | 1 | 1 | 2 | 2 |
| CRITICAL | 0 | 1 | 0 | 5 | 0 |

**Les 8 erreurs, une par une.**

| Cas | Langue | Attendu | Estimé | Cause |
|---|---|---|---|---|
| S24 | anglais | CRITICAL | MEDIUM | « vomiting blood » et « confused » non extraits ; seul `vomiting` reste. L'erreur la plus grave du jeu |
| S14 | anglais | HIGH | MEDIUM | « lost the vision » non extrait comme perte de vision |
| S16 | anglais | HIGH | non évalué | hallucinations et délire décrits sans les mots du vocabulaire |
| S18 | anglais | HIGH | non évalué | « skin and eyes turned yellow », « stools are black » non extraits |
| S04 | anglais | LOW | non évalué | « knee is a little stiff » non extrait |
| S11 | français | MEDIUM | LOW | entorse : seul `painful_walking` extrait, classé bénin |
| S15 | français | HIGH | CRITICAL | deux symptômes élevés et « atroce » : 4 + 1 + 1 |
| S17 | français | HIGH | CRITICAL | convulsion fébrile comptée deux fois, plus l'âge de 2 ans |

Lecture :

- **En anglais, toutes les erreurs viennent de l'extraction**, pas des règles : le symptôme grave n'arrive jamais jusqu'à l'estimation. C'est la même limite que dans les rapports précédents (rappel de 0,27).
- **En français, le système surestime.** Aucune sous-estimation sur les 29 cas d'origine, mais 10 surestimations, dont 5 cas HIGH classés CRITICAL. Les modificateurs s'additionnent trop facilement au-dessus d'un symptôme élevé.
- **Le niveau HIGH est mal isolé** : 1 cas sur 6 correctement classé, les autres partant au-dessus ou n'étant pas évalués.

Les règles n'ont pas été retouchées après ces résultats.

### 3.5 Effet sur le reste du pipeline

Classement monotone actif des deux côtés ; seule la gravité change.

| Jeu | Top-1 avant → après | Top-3 avant → après | Parmi les 3 prestataires | Cas dont le Top-1 change |
|---|---|---|---|---|
| Anglais (29) | 44,8 % → 44,8 % | 58,6 % → 58,6 % | 58,6 % → 58,6 % | aucun |
| Français (29) | 82,8 % → 82,8 % | 100 % → 100 % | 100 % → 100 % | aucun |
| Contrôle français (24) | 83,3 % → 83,3 % | 100 % → 100 % | 100 % → 100 % | aucun |

Ces valeurs sont celles de `fix_report.md` et `deployed_report.md` : rien n'est dégradé. Mais rien n'est amélioré non plus : sur 82 cas, le bonus de gravité ne fait basculer aucune spécialité. L'ancienne règle et la nouvelle donnent les mêmes prédictions sur ces jeux. Le remplacement est donc plus défendable sur le principe, sans gain mesuré.

**Effet sur le classement**, patient à Tunis, moyenne des 22 spécialités :

| Gravité transmise | Médecins de Tunis dans le Top 3 | Délai moyen | Note moyenne |
|---|---|---|---|
| MEDIUM | 1,41 sur 3 | 28,5 j | 7,02 |
| HIGH | 2,18 sur 3 | 28,7 j | 6,87 |
| CRITICAL | 2,32 sur 3 | 29,0 j | 6,87 |

La proximité réagit nettement. Le délai, non : il augmente même d'une demi-journée, parce que le critère « même ville », binaire, l'emporte sur les écarts de délai, qui sont faibles d'un médecin à l'autre.

**Exemple, cas C04** (suspicion d'infarctus, 58 ans, Tunis) :

| | Avant ce round | Après |
|---|---|---|
| Gravité | — | CRITICAL (douleur thoracique avec signes associés) |
| Top 3 | Monastir, Sousse, Tunis | Tunis, Tunis, Tunis |
| Délais | 46, 40 et 36 jours | 38, 36 et 33 jours |
| Notes | 10,0 ; 9,2 ; 9,0 | 8,1 ; 9,0 ; 6,3 |

## 4. Limites restantes

1. **La pondération du classement est à décider.** Poids égaux par défaut, avec la conséquence sur la note décrite en 2.4.
2. **Le budget fourni par le patient reste sans effet visible** sur l'écart moyen du Top 3 au budget.
3. **`backend/hospital.py` a le même défaut d'origine.** Sa copie du classement utilise une distance de crowding qui met aussi les deux extrémités en tête. Elle n'a pas été touchée : hors du périmètre de ce round.
4. **La gravité en anglais dépend d'un extracteur qui rate les trois quarts des symptômes.** Un des trois cas critiques en anglais y est classé MEDIUM.
5. **La gravité en français surestime**, surtout de HIGH vers CRITICAL.
6. **Les paliers de symptômes et tous les poids sont posés à la main**, sans clinicien ni données.
7. **Le bonus de gravité sur la spécialité n'a pas d'effet mesuré.**
8. **Un cas grave n'obtient pas un rendez-vous plus rapide.** Plus largement, la base K2 décrit des consultations à 16-48 jours : aucun classement ne peut y trouver une prise en charge en urgence.
9. **Auteur unique.** Les 24 cas de gravité ont été écrits après le gel des règles, mais par la même personne.

## 5. À déployer plus tard

Rien n'a été poussé. Dans le clone local du Space : `src/severity.py` (nouveau), `src/nsga2.py`, `src/filtering.py`, `src/k1_brain.py`, `src/pipeline.py`, `app.py` (modifiés). Côté projet : `backend/main.py` lit la nouvelle ligne « Gravité estimée » et reste compatible avec le Space actuel.

Tant que ce n'est pas déployé, `run_deployed_evaluation.py` continuera de trouver le Space identique à la référence du round précédent.

## 6. Résumé pour les sections Experiments et Limitations

**Classement des prestataires.** L'évaluation initiale avait montré que le classement n'était pas monotone : multiplier par dix le coût d'un prestataire améliorait son rang dans 28,6 % des essais. Nous en avons identifié la cause : à l'intérieur d'un front de Pareto, le tri par densité k-NN attribuait une densité infinie aux deux extrémités de chaque objectif, la meilleure comme la pire. Nous avons remplacé ce tri par un score de compromis (moyenne des objectifs normalisés), la densité ne servant plus qu'à départager les égalités. Sur 504 prestataires et 22 spécialités, plus aucune dégradation d'un critère n'améliore le rang (coût × 10 : 28,6 % → 0 % ; délai porté à un an : 42,5 % → 0 %), et les modifications favorables sont mieux récompensées (rapprochement géographique : 60,9 % → 95,2 % ; coût ramené au budget : 67,5 % → 83,9 %).

**Gravité.** Le système ne disposait d'aucune estimation de gravité : un indicateur binaire fourni par l'appelant doublait le score d'une seule spécialité. Nous l'avons remplacé par une estimation à quatre niveaux, fondée sur des règles explicites : palier du symptôme le plus grave extrait, combinaisons de symptômes, formulations d'urgence vitale, et modificateurs (intensité, soudaineté, appel à l'aide, âge, ancienneté), en réutilisant le lexique et la détection de négation. Le niveau estimé favorise les spécialités responsables des symptômes graves et accroît le poids de la proximité et du délai dans le classement.

| Mesure | Résultat |
|---|---|
| Rang amélioré après un coût × 10 | 28,6 % → 0 % |
| Rang amélioré après rapprochement géographique | 60,9 % → 95,2 % |
| Gravité, 24 cas : exactitude ; à un niveau près | 66,7 % ; 83,3 % |
| Gravité, français (12 cas) : exactitude ; à un niveau près | 75,0 % ; 100 % |
| Gravité, anglais (12 cas) : exactitude ; à un niveau près | 58,3 % ; 66,7 % |
| Top-1 spécialité, français / anglais | inchangé (83,3 % / 44,8 %) |

**Limites.** Le score de compromis pondère également sept critères dont trois binaires ; la note moyenne des prestataires recommandés passe du 87e au 47e centile, et la pondération reste à arbitrer. L'estimation de gravité repose sur des paliers et des poids fixés à la main, sans validation clinique. Elle hérite des faiblesses de l'extraction : en anglais, un des trois cas critiques est classé modéré. En français, elle surestime (dix cas sur vingt-neuf, aucun sous-estimé). Le bonus de gravité ne modifie aucune classification de spécialité dans nos jeux de test, et la priorité donnée aux cas graves se traduit par des prestataires plus proches mais pas plus rapidement disponibles, la base ne contenant que des consultations sur rendez-vous.
