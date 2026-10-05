# Phase 2 — Détection de concept drift : ADWIN contre Page-Hinkley

Section 3.8 du papier (« Performative Drift Handling »). Ce rapport couvre la détection du drift uniquement ; la réaction (seuil de saturation 80-90 %, bascule de recommandation) n'est pas implémentée.

Chiffres issus de `drift_evaluation_report.json` (évaluation) et `drift_tuning_report.json` (réglage). Pour les reproduire, depuis `backend/` :

```
python -m realtime.run_drift_tuning
python -m realtime.run_drift_evaluation
python -m pytest realtime
```

## 1. Conclusion

**Page-Hinkley est retenu comme détecteur par défaut** (`delta = 1.5`, `threshold = 10`).

Sur le scénario principal (baisse de 4 à 6 places), les deux algorithmes détectent presque tout sans fausse alerte : le F1 ne les départage pas. Le délai, si. Page-Hinkley signale un drift brusque en 10,8 points (≈ 23 min) contre 24,3 points (≈ 52 min) pour ADWIN, et un drift progressif en 53,7 points contre 61,1.

Le choix dépend du profil seulement quand le drift est faible (2 à 3 places) : ADWIN reste à 100 % de rappel sur un petit drift progressif, là où Page-Hinkley tombe à 85 %. Faire tourner les deux en parallèle n'apporte rien sur le scénario principal et n'est pas recommandé tel quel (section 4).

Deux réserves à garder en tête avant de citer ces chiffres :

- Les hyperparamètres par défaut de River sont inutilisables sur cette série (F1 de 0,36 à 0,63). Les bons résultats tiennent à un réglage fait sur données simulées.
- Les F1 proches de 1 reflètent un scénario simulé où le drift est net. Ils ne prédisent pas la performance sur des données réelles (section 5).

## 2. Protocole

**Données.** Pour chaque seed et chaque profil, 40 providers simulés : 20 avec un drift injecté, 20 stables. Chaque provider émet 600 mises à jour de disponibilité, tirées autour d'une moyenne latente connue (bruit gaussien d'écart-type 1,5 place, arrondi à l'entier), avec les quatre bruits de la Phase 1 aux mêmes taux (doublons 12 %, non-confirmés 35 %, valeurs périmées 5 %, pics aberrants 6 % des cycles).

**Entrée des détecteurs.** Le flux brut passe par le `NoiseFilterPipeline` de la Phase 1, inchangé. Les détecteurs reçoivent la disponibilité lissée (EWMA, tau = 20 min) qui en sort : 379 points par série en moyenne, un point toutes les 129 s (médiane).

**Profils de drift.** La moyenne passe de 7-10 places à 4-6 places de moins.

| Profil | Durée de la transition | Équivalent |
|---|---|---|
| Progressif | 150 mises à jour brutes, ≈ 100 points lissés | ≈ 3 h 30 |
| Brusque | 5 mises à jour brutes, ≈ 3 points lissés | ≈ 7 min |

Le début du drift est tiré au hasard par provider, entre 40 % et 60 % de la série.

**Vérité terrain.** L'instant de début du drift est enregistré par le simulateur et n'est jamais transmis au pipeline ni aux détecteurs, qui ne voient qu'une liste de valeurs.

**Métriques, au niveau épisode.** Une série contient au plus un épisode. Sa fenêtre va du début réel du drift à la fin de la transition plus 30 points (le temps que l'EWMA rejoigne son nouveau niveau).

- Épisode détecté : au moins un signalement dans la fenêtre.
- Fausse alerte : tout signalement hors fenêtre, c'est-à-dire sur une série stable, avant le drift, ou après la fenêtre.
- Délai : nombre de points entre le début réel et le premier signalement dans la fenêtre.
- Taux de faux positifs (FPR) : part des séries stables avec au moins un signalement.

**Seeds.** Évaluation sur 7, 1 et 42, comme en Phase 1 : 60 épisodes et 60 séries stables par profil. Pour un seed donné, les providers stables sont les mêmes dans les deux profils : le FPR est donc identique entre profils et ne compte qu'une fois.

**Réglage.** Les hyperparamètres ont été choisis par grille sur trois autres seeds (100, 101, 102), selon un critère fixé à l'avance : F1 moyen sur les deux profils, puis délai le plus court. Les seeds d'évaluation n'ont pas servi au réglage.

| Algorithme | Défauts River | Retenu | Grille |
|---|---|---|---|
| ADWIN | `delta = 0.002`, `clock = 32` | `delta = 1e-12`, `clock = 1` | 16 configurations |
| Page-Hinkley | `delta = 0.005`, `threshold = 50` | `delta = 1.5`, `threshold = 10` | 36 configurations |

## 3. Résultats

### 3.1 Scénario principal (drift de 4 à 6 places, hyperparamètres retenus)

Séries des trois seeds réunies ; le délai est en points lissés (1 point ≈ 2 min).

| Algorithme | Profil | Épisodes détectés | Précision | Rappel | F1 | Séries stables en fausse alerte | Fausses alertes | Délai moyen | Délai max |
|---|---|---|---|---|---|---|---|---|---|
| ADWIN | progressif | 60/60 | 1,000 | 1,000 | 1,000 | 0/60 | 0 | 61,1 | 83 |
| ADWIN | brusque | 59/60 | 0,983 | 0,983 | 0,983 | 0/60 | 1 | 24,3 | 32 |
| Page-Hinkley | progressif | 60/60 | 0,968 | 1,000 | 0,984 | 1/60 | 2 | 53,7 | 92 |
| Page-Hinkley | brusque | 60/60 | 0,984 | 1,000 | 0,992 | 1/60 | 1 | 10,8 | 20 |

L'unique épisode manqué par ADWIN est un drift brusque signalé juste après la fin de sa fenêtre : il compte à la fois comme épisode manqué et comme fausse alerte. Les fausses alertes de Page-Hinkley viennent d'une seule série stable (seed 1) et d'une re-détection tardive.

### 3.2 Stabilité entre seeds (moyenne ± écart-type sur 3 seeds)

| Algorithme | Profil | F1 | FPR | Délai moyen |
|---|---|---|---|---|
| ADWIN | progressif | 1,000 ± 0,000 | 0,00 ± 0,00 | 61,1 ± 1,2 |
| ADWIN | brusque | 0,983 ± 0,029 | 0,00 ± 0,00 | 24,3 ± 0,4 |
| Page-Hinkley | progressif | 0,984 ± 0,027 | 0,02 ± 0,03 | 53,7 ± 0,7 |
| Page-Hinkley | brusque | 0,992 ± 0,014 | 0,02 ± 0,03 | 10,8 ± 0,8 |

Les écarts de F1 entre algorithmes (0,01 à 0,02) sont plus petits que leur écart-type entre seeds : ils ne sont pas significatifs. Les écarts de délai (13,5 points en brusque, 7,4 en progressif) sont de 6 à 17 fois plus grands que l'écart-type entre seeds : ils sont robustes.

### 3.3 Point de départ : hyperparamètres par défaut de River

Mêmes scénarios, mêmes seeds.

| Algorithme | Profil | Précision | Rappel | F1 | Séries stables en fausse alerte | Fausses alertes |
|---|---|---|---|---|---|---|
| ADWIN | progressif | 0,353 | 1,000 | 0,522 | 49/60 | 110 |
| ADWIN | brusque | 0,241 | 0,683 | 0,357 | 49/60 | 129 |
| Page-Hinkley | progressif | 0,444 | 1,000 | 0,615 | 32/60 | 75 |
| Page-Hinkley | brusque | 0,468 | 0,983 | 0,634 | 32/60 | 67 |

Deux causes. La série lissée par EWMA est fortement autocorrélée (chaque point contient environ 90 % du précédent), alors que la garantie statistique d'ADWIN suppose des points indépendants : son `delta` nominal sous-estime massivement le taux réel de fausses alertes. Et le `delta` par défaut de Page-Hinkley suppose une série normalisée entre 0 et 1, pas une série en places.

### 3.4 Robustesse : drift réduit à 2-3 places, hyperparamètres inchangés

| Détecteur | Profil | Épisodes détectés | Précision | Rappel | F1 (± entre seeds) | Délai moyen |
|---|---|---|---|---|---|---|
| ADWIN | progressif | 60/60 | 1,000 | 1,000 | 1,000 ± 0,000 | 85,5 |
| ADWIN | brusque | 14/60 | 0,233 | 0,233 | 0,233 ± 0,076 | 31,0 |
| Page-Hinkley | progressif | 51/60 | 0,927 | 0,850 | 0,886 ± 0,057 | 88,7 |
| Page-Hinkley | brusque | 44/60 | 0,721 | 0,733 | 0,726 ± 0,116 | 21,5 |
| Les deux en parallèle | progressif | 60/60 | 0,938 | 1,000 | 0,968 ± 0,013 | 82,0 |
| Les deux en parallèle | brusque | 44/60 | 0,423 | 0,733 | 0,537 ± 0,093 | 21,5 |

Sur le petit drift brusque, les deux algorithmes finissent par signaler les 60 épisodes, mais trop tard : 46 des 60 signalements d'ADWIN et 16 des 60 de Page-Hinkley tombent après la fenêtre d'épisode (plus de 33 points, soit environ 70 min). Ils ne sont pas aveugles, ils sont lents.

## 4. Recommandation

**Par défaut : Page-Hinkley seul.**

- F1 équivalent à ADWIN sur le scénario principal (0,984 et 0,992 contre 1,000 et 0,983).
- Délai 2,2 fois plus court sur le drift brusque, le cas le plus urgent pour la saturation, et 12 % plus court sur le drift progressif.
- Meilleur que ADWIN sur le petit drift brusque (F1 0,73 contre 0,23).

**Ce que ADWIN fait mieux.** Aucune fausse alerte sur les 60 séries stables (contre une série pour Page-Hinkley), et un rappel de 100 % sur le petit drift progressif (contre 85 %).

**Les deux en parallèle (signalement dès que l'un des deux se déclenche).** Sur le scénario principal, la combinaison se comporte comme Page-Hinkley seul : mêmes délais, F1 de 0,984 sur les deux profils. Elle n'est utile que sur le petit drift progressif (rappel de 85 % à 100 %), et elle dégrade nettement la précision sur le petit drift brusque (0,72 à 0,42), parce que les signalements tardifs d'ADWIN s'ajoutent à ceux de Page-Hinkley. Elle n'est donc pas recommandée en l'état. Si les données réelles montrent des drifts lents et de faible amplitude, ADWIN pourra être ajouté comme second canal, à condition de fusionner les signalements d'un même épisode — ce qui reste à concevoir et à évaluer.

## 5. Limites et résultats décevants

1. **Les défauts de River ne fonctionnent pas** sur une série lissée par EWMA (F1 de 0,36 à 0,63, au moins une fausse alerte sur 53 % à 82 % des séries stables). Tout le résultat repose sur le réglage.
2. **Le `delta` d'ADWIN n'a plus de sens probabiliste.** À `1e-12`, c'est un bouton de sensibilité réglé empiriquement. Il dépend de tau (EWMA) et de la cadence des mises à jour : si l'un des deux change, il faut le re-régler.
3. **Le `delta` de Page-Hinkley est au bord de la grille**, et ce n'est pas un hasard : tous les drifts de réglage font 4 à 6 places, donc un `delta` plus grand sépare toujours mieux. La grille n'a pas été étendue pour ne pas sur-spécialiser. La contrepartie est mesurée en 3.4 : rappel de 85 % et 73 % sur un drift de 2 à 3 places. Un drift durable de moins de 1,5 place environ est invisible.
4. **La détection est lente, par construction.** Les détecteurs lisent une série lissée avec tau = 20 min : un drift brusque n'est signalé qu'au bout de 23 min environ (Page-Hinkley), un drift progressif vers la moitié de la transition (environ 1 h 55 après son début). C'est un signal de confirmation d'un changement de régime, pas une alerte précoce.
5. **Effet plafond.** Avec un drift de 4 à 6 places pour un bruit résiduel d'environ 0,5 place sur la série lissée, la tâche est facile une fois le détecteur réglé. Les F1 proches de 1 ne permettent pas de classer les algorithmes ; seuls le délai et le test d'amplitude réduite le font.
6. **Taille d'échantillon.** 60 épisodes et 60 séries stables par profil. Un FPR observé de 0/60 est compatible avec un taux réel allant jusqu'à 5 % environ. Avec trois seeds, l'écart-type est un ordre de grandeur, pas un intervalle de confiance.
7. **Ce que le simulateur ne contient pas.** Pas de cycle jour/nuit ni d'effet jour de semaine : sur des données réelles, une variation saisonnière normale serait signalée comme un drift. Seules des baisses ont été testées (pas de retour à la normale), avec un seul épisode par série. Après chaque détection, Page-Hinkley est aveugle pendant 30 points, ce qui n'a pas été mis à l'épreuve.
8. **Biais du protocole.** Un signalement qui tombe par hasard dans la fenêtre d'épisode compte comme une vraie détection. L'effet est négligeable avec les réglages retenus (0 à 2 fausses alertes au total), mais il gonfle le rappel des configurations par défaut du tableau 3.3. La marge de 30 points après la transition est un choix ; elle pèse surtout sur les résultats du petit drift brusque.

## 6. Résumé pour la section Experiments

**Objectif.** Détecter un changement durable de la disponibilité d'un prestataire, par opposition aux fluctuations déjà absorbées par le filtrage de bruit, et comparer deux détecteurs de la bibliothèque River : ADWIN et Page-Hinkley.

**Méthode.** Les détecteurs sont appliqués à la disponibilité lissée (EWMA, tau = 20 min) produite par le pipeline de filtrage, et non au flux brut. Nous simulons 40 prestataires par scénario (20 avec drift, 20 stables), chacun émettant 600 mises à jour bruitées autour d'une moyenne latente connue, avec les mêmes taux de bruit qu'à l'étape de filtrage. Le drift est une baisse de 4 à 6 places depuis une moyenne de 7 à 10 places, soit progressive (environ 3 h 30), soit brusque (environ 7 min). La vérité terrain n'est jamais visible des détecteurs. Les métriques sont calculées par épisode : un épisode est détecté si un signalement tombe entre le début réel du drift et 30 points après la fin de la transition ; tout autre signalement est une fausse alerte. Les hyperparamètres sont réglés par grille sur trois seeds dédiés, puis évalués sur trois seeds distincts (7, 1, 42), soit 60 épisodes et 60 séries stables par profil.

**Résultats.** Avec leurs hyperparamètres par défaut, les deux détecteurs sont inutilisables sur une série lissée (F1 de 0,36 à 0,63 ; fausse alerte sur 82 % des séries stables pour ADWIN, 53 % pour Page-Hinkley), parce que l'autocorrélation introduite par l'EWMA viole l'hypothèse d'indépendance d'ADWIN et que l'échelle par défaut de Page-Hinkley ne correspond pas à des valeurs en places. Après réglage (ADWIN : delta = 1e-12, test à chaque point ; Page-Hinkley : delta = 1,5, seuil = 10), les deux atteignent un F1 par épisode d'au moins 0,98 sur les deux profils.

| | F1 progressif | F1 brusque | Séries stables en fausse alerte | Délai progressif (points) | Délai brusque (points) |
|---|---|---|---|---|---|
| ADWIN | 1,000 ± 0,000 | 0,983 ± 0,029 | 0/60 | 61,1 ± 1,2 | 24,3 ± 0,4 |
| Page-Hinkley | 0,984 ± 0,027 | 0,992 ± 0,014 | 1/60 | 53,7 ± 0,7 | 10,8 ± 0,8 |

Moyenne ± écart-type sur 3 seeds ; 1 point ≈ 2 min.

Les deux algorithmes se distinguent par le délai, pas par le F1 : Page-Hinkley signale un drift brusque 2,2 fois plus vite (environ 23 min contre 52 min). Nous le retenons par défaut. Sur un drift deux fois plus faible (2 à 3 places), sans re-réglage, ADWIN conserve un rappel de 100 % en progressif mais chute à 23 % en brusque, tandis que Page-Hinkley obtient 85 % et 73 % ; dans les deux cas brusques, les épisodes manqués sont des détections trop tardives et non des absences de détection.

**Limites.** Les résultats sont obtenus sur données simulées, sans saisonnalité, avec un seul épisode de baisse par série, et dépendent d'un réglage lié à l'amplitude du drift simulé et à la constante de temps de l'EWMA. Le délai de détection est borné inférieurement par le lissage : il s'agit d'une confirmation de changement de régime, pas d'une alerte précoce.
