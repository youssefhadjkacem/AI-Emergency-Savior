# Adaptation performative : saturation, réaction et boucle de rétroaction (Phase 3)

Section 3.8 du papier, « Performative Drift Handling ». Les Phases 1 (filtrage du bruit) et 2 (détection de drift) sont réutilisées telles quelles.

Chiffres issus de `feedback_evaluation_report.json` (seeds 7, 1, 42) ; tableaux complets dans `feedback_tables.md` ; réglage dans `feedback_tuning_report.json` (seeds 100, 101, 102). Pour reproduire, depuis `backend/` :

```
python -m realtime.run_feedback_tuning        # ~15 min
python -m realtime.run_feedback_evaluation    # ~3 min, écrit les tableaux et les deux figures
python -m pytest testing realtime             # 177 tests
```

## 1. Conclusion

- **L'hypothèse de concentration est confirmée.** Avec le classement statique, 83 à 86 % des patients d'une spécialité vont chez les 3 mêmes prestataires, sur 18 à 27 disponibles.
- **Cette concentration sature ces prestataires alors que le système est loin d'être plein.** À forte charge, l'occupation moyenne est de 36 %, mais 37 % des patients arrivent chez un prestataire saturé et 26 % sont refusés faute de place.
- **Le système complet ramène ces taux à 3,2 % et 2,1 %.** Le coût est réel : le prestataire n°1 change pour 76 % des patients, et sa note moyenne baisse de 0,72 point sur 10.
- **Presque tout le gain vient des réservations provisoires.** Le seuil seul, sur la disponibilité observée, laisse 28 % de patients envoyés vers un prestataire saturé.
- **Le signal Page-Hinkley n'apporte rien de mesurable.** Avec ou sans lui, les résultats sont identiques à l'écart-type près.
- **Le filtrage de bruit de la Phase 1 change peu les résultats pour les patients.** Le flux brut fait jeu égal à charge faible et moyenne, un peu moins bien à forte charge ; il rend surtout l'état des prestataires beaucoup moins stable.
- **Les contrôles de sécurité tiennent sur 25 742 patients simulés** : spécialité conservée à 100 %, aucun cas CRITICAL détourné hors de sa ville.

Ce round démontre un mécanisme dans un simulateur que nous avons écrit. Il ne mesure pas une efficacité en conditions réelles.

## 2. Ce que l'exploration a montré

**Le Space ne peut pas servir à la réaction en l'état.** Il est sans état et renvoie du texte : la gravité, trois spécialités et trois noms de prestataires. `optimize_providers_nsga` accepte un `top_k` quelconque, mais ne renvoie ni front de Pareto ni score, et `app.py` n'expose que le Top 3. La simulation utilise donc le code local du Space, non modifié, à travers un pont (`adaptation.SpaceRankingSource`).

**La base ne contient pas de capacité.** `K2_Medical_Providers.xlsx` donne par médecin `Créneaux/sem.` (15 à 60) et `Délai RDV (jours)` (16 à 48), plus une feuille de créneaux sur 14 jours et un nombre de lits par établissement. Rien ne dit combien de patients un médecin peut prendre en même temps.

**La distance n'existe que sous la forme « même ville ou non ».** Les médecins n'ont pas de coordonnées dans le classement.

## 3. Ce qui a été construit

Tout est dans `backend/realtime/`. Le Space n'est pas modifié.

| Fichier | Rôle |
|---|---|
| `saturation.py` | État de chaque prestataire : NORMAL, ALERT, SATURATED |
| `adaptation.py` | Reclassement des prestataires d'une spécialité selon leur état |
| `feedback_simulator.py` | Monde simulé, patients, publication bruitée, conditions comparées |
| `feedback_evaluation.py` | Métriques contre l'état réel |
| `run_feedback_tuning.py`, `run_feedback_evaluation.py` | Réglage et évaluation |

### Détection de saturation

Occupation estimée = (capacité − places libres observées + réservations provisoires) / capacité.

| Signal | Rôle | Règle |
|---|---|---|
| Seuil | Confirmation | Alerte à 80 %, saturation à 90 % (valeurs du papier, non réglées) |
| Hystérésis | Stabilité | Sortie de l'état 15 points sous le seuil d'entrée |
| Réservations provisoires | Suivi de la recommandation à l'arrivée | Une place dès l'acceptation ; expire après 90 min sans arrivée |
| Page-Hinkley (Phase 2, delta 1,5, seuil 10) | Anticipation | Une baisse de disponibilité met en ALERT pendant 30 min si l'occupation dépasse 60 % ; ne déclare jamais la saturation |

À l'arrivée du patient, sa réservation ne disparaît pas d'un coup : son poids décroît avec la constante de temps de l'EWMA (1 200 s). Le patient est alors compté par la disponibilité publiée, que le lissage n'absorbe qu'à cette vitesse.

### Réaction

Le Space trie par (front de Pareto, score de compromis). La réaction garde cette clé et y ajoute l'état :

- **SATURATED** : placé derrière tous les prestataires non saturés de la spécialité.
- **ALERT** : score de compromis augmenté de 0,02 ; le prestataire recule dans son front.
- **NORMAL** : inchangé.

Quatre garanties, chacune couverte par un test :

1. Sans alerte ni saturation, le Top 3 est exactement celui du Space (vérifié sur les 22 spécialités et 7 contextes).
2. La réaction ne voit que les prestataires d'une spécialité : elle ne peut pas changer la spécialité.
3. Si tous sont saturés, la réponse garde le classement habituel et le signale (`all_saturated`), au lieu d'une liste vide.
4. **Règle CRITICAL.** Pour un cas CRITICAL dont la ville est connue, la réaction ne permute les prestataires qu'à l'intérieur de leur groupe (ville du patient, autres villes). Un patient en état critique n'est jamais détourné vers une autre ville par la saturation. Si tous les prestataires de sa ville sont saturés, ils sont conservés et signalés.

## 4. Simulateur et hypothèses

Le simulateur rejoue une journée de 10 heures (08:00-18:00) après 3 heures de mise en régime. L'occupation réelle de chaque prestataire est la vérité terrain ; le système ne la voit jamais.

| Hypothèse | Valeur | Justification |
|---|---|---|
| Capacité | `Créneaux/sem.` ÷ 5, soit 3 à 12 places | Déterministe, tirée de la base, identique partout |
| Prestataires | Les 504 médecins, 22 spécialités | Base K2 |
| Arrivées | Processus de Poisson : 50, 200 ou 600 patients/heure | Fixées sur les seeds de réglage |
| Spécialité du patient | Uniforme sur les 22 | Pas de donnée réelle |
| Ville du patient | Proportionnelle au nombre de médecins de la ville | Substitut de la population |
| Gravité | LOW 30 %, MEDIUM 45 %, HIGH 20 %, CRITICAL 5 % | Pas de donnée réelle |
| Suivi des recommandations | n°1 : 0,70 ; n°2 : 0,20 ; n°3 : 0,10 | Sensibilité mesurée en section 7 |
| Patients qui ne viennent pas | 10 % | Hypothèse |
| Trajet | 10-40 min dans la ville, 40-120 min sinon | Seule distance disponible |
| Durée de service | Log-normale, moyenne 60 min | Hypothèse |
| Charge de fond | 15 à 45 % d'occupation par prestataire | Patients hors système, identiques dans toutes les conditions |
| Publication | Toutes les 90 s environ, bruit de la Phase 1 aux mêmes taux | Doublons 12 %, non confirmés 35 %, périmés 5 %, aberrants 6 % |

Tout l'aléa est tiré avant la simulation. Les conditions rejouent donc exactement les mêmes patients ; seule la recommandation change.

**Conditions comparées.**

| | Condition | Flux | Réservations | Page-Hinkley |
|---|---|---|---|---|
| A | Statique | — | — | — |
| B | Seuil seul | Filtré | Non | Non |
| C | Système complet | Filtré | Oui | Oui |
| D | Complet sur flux brut | Brut | Oui | Oui |
| E | Complet sans réservations | Filtré | Non | Oui |
| F | Complet sans Page-Hinkley | Filtré | Oui | Non |

F ne figurait pas dans la demande. Je l'ai ajoutée pour isoler l'apport de Page-Hinkley en présence des réservations.

## 5. Réglage

Trois paramètres, grille complète de 64 configurations, condition C, charges moyenne et forte, seeds 100/101/102. Critère fixé avant de lire les résultats : taux de refus minimal ; à moins de 0,3 point du meilleur, plus petite perte de note.

| Paramètre | Grille | Retenu | Taux de refus moyen selon la valeur |
|---|---|---|---|
| Durée de réservation | 30, 60, 90, 120 min | 90 min | 4,0 % · 2,2 % · 1,7 % · 1,6 % |
| Marge d'hystérésis | 5, 10, 15, 20 points | 15 points | 2,5 % · 2,4 % · 2,3 % · 2,2 % |
| Pénalité d'alerte | 0,02 · 0,05 · 0,10 · 0,20 | 0,02 | 2,5 % · 2,4 % · 2,3 % · 2,2 % |

- **Seule la durée de réservation compte vraiment.** Une réservation plus courte que le trajet expire avant l'arrivée du patient.
- **22 configurations sur 64 sont dans la tolérance** : le système est peu sensible à la marge et à la pénalité.
- **Deux valeurs retenues sont au bord de leur grille** (pénalité 0,02 ; 120 min donne le moins de refus). La grille n'a pas été étendue après coup.

## 6. Résultats sur les seeds 7, 1 et 42

Moyenne ± écart-type entre seeds. Tableaux complets dans `feedback_tables.md`.

### L'hypothèse de départ

| Condition A (statique) | Faible | Moyenne | Forte |
|---|---|---|---|
| Part des patients chez les 3 prestataires les plus sollicités de la spécialité | 85,5 ± 0,6 % | 83,1 ± 0,4 % | 82,6 ± 0,1 % |
| Gini des patients reçus | 0,865 | 0,849 | 0,841 |
| Occupation moyenne de tous les prestataires | 30,4 % | 32,5 % | 36,2 % |
| Occupation moyenne du prestataire le plus chargé | 62,2 % | 74,6 % | 89,0 % |

Confirmée, et plus fortement que supposé : la concentration ne dépend presque pas de la ville. La proximité ne pèse qu'un critère sur sept, donc les mêmes prestataires arrivent en tête pour des patients de villes différentes.

### Effet de l'adaptation

| | A — Statique | B — Seuil seul | C — Complet | D — Flux brut | E — Sans réserv. | F — Sans PH |
|---|---|---|---|---|---|---|
| **Envoyés vers un prestataire saturé (%)** | | | | | | |
| Charge faible | 1,2 ± 0,2 | 1,1 ± 0,3 | 1,0 ± 0,2 | 0,9 ± 0,2 | 1,1 ± 0,3 | 1,0 ± 0,2 |
| Charge moyenne | 9,7 ± 0,6 | 8,2 ± 0,7 | 2,3 ± 0,1 | 2,0 ± 0,2 | 8,3 ± 0,7 | 2,4 ± 0,1 |
| Charge forte | 37,0 ± 0,8 | 28,4 ± 1,0 | 3,2 ± 0,2 | 3,7 ± 0,1 | 28,1 ± 1,0 | 3,3 ± 0,1 |
| **Refusés faute de place (%)** | | | | | | |
| Charge faible | 1,1 ± 0,3 | 0,9 ± 0,4 | 0,7 ± 0,2 | 0,6 ± 0,2 | 0,9 ± 0,4 | 0,7 ± 0,2 |
| Charge moyenne | 5,9 ± 0,9 | 4,8 ± 0,7 | 1,4 ± 0,2 | 1,3 ± 0,3 | 4,9 ± 0,6 | 1,4 ± 0,1 |
| Charge forte | 26,0 ± 0,3 | 19,7 ± 0,5 | 2,1 ± 0,1 | 2,5 ± 0,3 | 19,3 ± 0,6 | 2,1 ± 0,1 |
| **Gini des patients reçus** | | | | | | |
| Charge forte | 0,841 | 0,780 | 0,483 | 0,538 | 0,780 | 0,482 |

Figure 2 (`feedback_fig2_saturated_rate.png`) : ces taux par condition et par charge.

Figure 1 (`feedback_fig1_occupancy.png`) : le prestataire le plus chargé de la seed 7 à forte charge, un allergologue de 10 places. En statique, il reçoit 150 patients et passe 68,5 % de la journée à 90 % ou plus. Avec le système complet, il en reçoit 22 et y passe 6,3 % du temps.

### Coût de l'adaptation (condition C, par rapport à A)

| Pour le prestataire n°1 | Faible | Moyenne | Forte |
|---|---|---|---|
| Recommandations modifiées | 9,1 ± 1,9 % | 42,2 ± 1,8 % | 75,9 ± 0,7 % |
| Note sur 10 | −0,05 ± 0,02 | −0,29 ± 0,04 | −0,72 ± 0,02 |
| Coût | +0,2 TND | −1,4 TND | −4,8 TND |
| Délai de rendez-vous | −0,08 j | −0,19 j | −0,07 j |
| Dans la ville du patient | +1,2 point | +4,3 points | +3,4 points |

- **Le coût se paie en note, pas en distance.** Le n°1 est un peu plus souvent dans la ville du patient qu'avant, pas moins.
- **À forte charge, la perte de note est nette** : 0,72 point, soit plus de la moitié de l'écart-type des notes de la base (1,29). C'est le prix de 24 points de refus en moins.
- **À faible charge, l'adaptation modifie 9 % des recommandations pour un gain dans le bruit** (1,1 ± 0,3 % de refus contre 0,7 ± 0,2 %). Elle ne dégrade rien de mesurable, mais elle n'apporte presque rien non plus.

### Ce que chaque composant apporte

**Réservations provisoires : l'essentiel.** Sans elles (B, E), le système ne voit les patients qu'il a envoyés qu'après leur arrivée, puis après le retard du lissage. À forte charge, B ne détecte que 43 % des épisodes de saturation réelle, C 78 %.

**Page-Hinkley : rien.** C et F sont identiques à l'écart-type près à toutes les charges, comme B et E. Le détecteur émet 300 à 480 alertes par journée, dont 18 à 31 % sont suivies d'une saturation réelle dans les 30 minutes. Deux raisons : ses réglages (delta de 1,5 place) viennent de drifts de 4 à 6 places, alors que les capacités vont ici de 3 à 12 ; et quand une tendance devient visible dans la disponibilité lissée, les réservations ont déjà fait monter l'occupation estimée.

**Filtrage de la Phase 1 : un apport modeste.**

| C (filtré) contre D (brut) | C | D |
|---|---|---|
| Refusés, charge moyenne | 1,4 ± 0,2 % | 1,3 ± 0,3 % |
| Refusés, charge forte | 2,1 ± 0,1 % | 2,5 ± 0,3 % |
| Gini, charge forte | 0,483 | 0,538 |
| Épisodes de saturation détectés, charge faible | 24 % | 99,9 % |
| Déclarations de saturation par journée, charge faible | 66 | 2 382 |
| Déclarations à tort hors patients en route, charge faible | 8 % | 82 % |

Le flux brut réagit tout de suite, mais chaque valeur aberrante ou périmée fait basculer un prestataire : 36 fois plus de déclarations à faible charge, dont quatre sur cinq à tort. Le flux filtré est stable, mais il rate les trois quarts des saturations courtes à faible charge, à cause du retard de l'EWMA et des 35 % de mises à jour écartées. Pour les patients, les deux se valent, avec un léger avantage au filtrage à forte charge.

### Détection : comment lire les chiffres

| Condition C | Faible | Moyenne | Forte |
|---|---|---|---|
| Épisodes de saturation réelle (≥ 5 min à 90 %) | 232 | 318 | 622 |
| Détectés | 24,4 % | 46,0 % | 78,1 % |
| Délai de détection moyen | +9,3 min | −0,5 min | −6,5 min |
| Déclarations « à tort » | 21,3 % | 64,1 % | 80,6 % |
| … sans patient en route | 8,4 % | 15,3 % | 27,4 % |

- **Un délai négatif signifie une anticipation.** Il est borné à −10 minutes par construction : la médiane de −10 min à charge moyenne et forte est donc un plancher, pas une mesure.
- **Le taux de déclarations « à tort » est gonflé par la boucle elle-même.** Une déclaration est comptée à tort si l'occupation réelle est sous 80 % et n'atteint pas 90 % dans les 30 minutes. Or dès que le système déclare un prestataire saturé, il cesse de lui envoyer des patients, et la saturation n'a pas lieu. La ligne « sans patient en route » retire les déclarations fondées sur des patients réellement en chemin ; elle reste à 27 % à forte charge.
- **Les saturations dues à la seule charge de fond sont mal détectées.** À faible charge, la plupart des épisodes viennent de patients hors système chez de petits prestataires : le système en voit un quart.

### Contrôles de sécurité

| Sur 25 742 patients par condition | B | C | D | E | F |
|---|---|---|---|---|---|
| Spécialité recommandée identique à A | 100 % | 100 % | 100 % | 100 % | 100 % |
| Cas CRITICAL détournés hors de leur ville (sur 1 296) | 0 | 0 | 0 | 0 | 0 |
| Réponses « tous saturés » | 0 | 0 | 0 | 0 | 0 |
| Top 3 gardant un prestataire saturé faute d'alternative | 30 | 211 | 181 | 32 | 207 |

Le cas « tous saturés » ne s'est jamais produit en simulation ; il n'est couvert que par un test unitaire. Les Top 3 gardant un prestataire saturé sont permis par deux règles : la règle CRITICAL, et le complément du Top 3 quand moins de trois prestataires de la spécialité restent disponibles. Leur répartition entre les deux n'a pas été mesurée.

## 7. Sensibilité au suivi des recommandations

Charge forte, seeds 7/1/42.

| Probabilités n°1 / n°2 / n°3 | Refusés, A | Refusés, C | Variation de la note, C |
|---|---|---|---|
| 0,50 / 0,30 / 0,20 | 20,2 ± 0,7 % | 2,2 ± 0,1 % | −0,64 |
| 0,70 / 0,20 / 0,10 (défaut) | 26,0 ± 0,3 % | 2,1 ± 0,1 % | −0,72 |
| 0,90 / 0,07 / 0,03 | 33,4 ± 0,5 % | 2,2 ± 0,1 % | −0,76 |

Plus les patients suivent le n°1, plus le classement statique sature. Le système complet reste à 2,2 % dans les trois cas.

## 8. Tests

66 nouveaux tests, 177 au total, tous passants.

| Fichier | Tests | Ce qui est vérifié |
|---|---|---|
| `test_saturation.py` | 24 | Seuils, hystérésis sans oscillation, expiration et extinction des réservations, signal de tendance, flux filtré et brut |
| `test_adaptation.py` | 28 | Règle de reclassement, cas « tous saturés », règle CRITICAL, non-régression contre le Space, spécialité inchangée |
| `test_feedback_simulator.py` | 14 | Reproductibilité, mêmes patients entre conditions, occupation bornée, contrôles de sécurité |

## 9. Limites

1. **Le simulateur est le nôtre.** Les résultats dépendent de ses hypothèses : capacité, taux d'arrivée, suivi des recommandations, durées. Aucune donnée réelle ne les valide.
2. **La capacité est une hypothèse.** `Créneaux/sem.` ÷ 5 n'est pas une capacité mesurée.
3. **L'échelle de temps est celle d'un service d'urgence** (heures), alors que la base décrit des consultations à 16-48 jours de délai.
4. **Les niveaux de charge ont été choisis pour produire trois régimes** de saturation en condition statique. Ils ne viennent pas d'une demande observée.
5. **Le système connaît le choix du patient.** Les réservations supposent que le patient indique quel prestataire il a retenu. Sans cette information, on retombe sur la condition B.
6. **Un patient refusé quitte la simulation.** Il ne redemande pas de recommandation.
7. **La spécialité du patient est supposée correcte** et unique : le Top 3 diversifié entre spécialités n'est pas simulé.
8. **Les métriques de détection sont à la minute**, avec une anticipation bornée à 10 minutes.
9. **Trois seeds.** Les écarts-types sont petits, mais ils portent sur trois journées simulées.

## 10. Résumé pour la section Experiments

**Méthodologie.** Nous simulons la boucle de rétroaction entre les recommandations et la disponibilité des prestataires. Les 504 médecins de la base reçoivent une capacité de 3 à 12 places, dérivée de leur nombre de créneaux hebdomadaires. Des patients arrivent selon un processus de Poisson (50, 200 ou 600 par heure), reçoivent un Top 3, choisissent le premier prestataire avec une probabilité de 0,70, se déplacent, puis occupent une place pendant une heure en moyenne ou sont refusés. Chaque prestataire publie sa disponibilité réelle, recouverte du bruit de la Phase 1. L'occupation réelle n'est jamais visible du système. Six conditions rejouent les mêmes patients : classement statique (A), seuil de saturation seul (B), système complet avec réservations provisoires et Page-Hinkley (C), système complet sur flux non filtré (D), sans réservations (E), sans Page-Hinkley (F). Trois paramètres sont réglés sur les seeds 100, 101 et 102 ; les seuils de 80 % et 90 % sont ceux de la section 3.8. L'évaluation porte sur les seeds 7, 1 et 42, soit 25 742 patients par condition.

**Résultats.**

- Le classement statique dirige 83 à 86 % des patients d'une spécialité vers trois prestataires. À 600 patients par heure, l'occupation moyenne est de 36 %, mais 37,0 ± 0,8 % des patients arrivent chez un prestataire saturé et 26,0 ± 0,3 % sont refusés.
- Le système complet ramène ces taux à 3,2 ± 0,2 % et 2,1 ± 0,1 %, et l'indice de Gini de la charge de 0,84 à 0,48.
- Ce gain coûte 0,72 point de note sur 10 pour le premier prestataire recommandé, modifié pour 76 % des patients. À 50 patients par heure, 9 % des recommandations changent pour un gain non significatif.
- L'ablation attribue le gain aux réservations provisoires : le seuil seul laisse 28,4 % de patients envoyés vers un prestataire saturé. Le signal Page-Hinkley ne change aucun résultat. Le filtrage de bruit améliore peu les résultats pour les patients (2,1 % de refus contre 2,5 % à forte charge), mais divise par 36 le nombre de déclarations de saturation à faible charge.
- La réaction ne change jamais la spécialité recommandée et ne détourne aucun des 1 296 cas critiques hors de leur ville.

**Portée.** Ces résultats démontrent le mécanisme et l'effet de l'adaptation dans un simulateur dont les hypothèses sont les nôtres. Ils ne mesurent pas une efficacité en conditions réelles.

## 11. Intégration : ce qu'il faudrait changer (non fait ici)

**Dans le Space.**

1. `optimize_providers_nsga` : renvoyer le classement complet avec deux colonnes, `front` et `compromise_score`. Aujourd'hui le pont recopie la construction des sept objectifs pour les recalculer.
2. Accepter en entrée l'état des prestataires (`{id: normal | alert | saturated}`) et appliquer `adaptation.rerank` avant de couper au Top 3 ; ou bien exposer le Top 10 par spécialité et laisser le backend reclasser.
3. `app.py` : renvoyer l'identifiant des prestataires (`ID`), pas seulement leur nom, et une sortie structurée plutôt que du texte. 339 noms pour 504 médecins : le nom ne suffit pas à identifier un prestataire.
4. Ajouter une capacité réelle à la base des prestataires.

**Dans le backend.**

1. Un état persistant : le `SaturationMonitor` vit en mémoire, il faut le conserver entre les requêtes et les redémarrages.
2. Une source de disponibilité : un point d'entrée pour les mises à jour des prestataires, branché sur `FilteredAvailabilityFeed`.
3. Le suivi du patient : enregistrer le prestataire choisi, puis l'arrivée ou l'annulation (`reserve`, `arrived`, `cancel`). Sans le choix du patient, les réservations sont impossibles.
4. Une réévaluation périodique (`tick`) pour faire expirer les réservations.
5. `/analyze-full` : appliquer la réaction au classement et renvoyer `all_saturated` et le message associé au frontend.

**Décisions à prendre avant d'intégrer.**

- Garder ou retirer Page-Hinkley de la réaction : il n'apporte rien ici.
- Choisir entre flux filtré et flux brut, ou réduire la constante de temps de l'EWMA pour cet usage.
- Activer l'adaptation seulement au-dessus d'un niveau de charge, puisqu'elle coûte des changements sans gain à faible charge.
