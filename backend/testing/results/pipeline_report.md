# Test de bout en bout du pipeline principal

Entrée patient → extraction NLP → classification de spécialité → optimisation multi-objectifs → Top 3 prestataires.

Chiffres issus de `pipeline_evaluation_report.json`. Pour les reproduire, depuis `backend/` :

```
python -m testing.run_pipeline_evaluation
python -m pytest testing
```

Les modules optionnels (vision, extraction de carte d'identité) et `backend/realtime/` ne sont pas sollicités.

## 1. Conclusion

**Le pipeline ne fonctionne pas en français en conditions réelles, et en anglais il oriente vers la bonne spécialité dans 13 cas sur 29 (45 %).** Le maillon faible est l'extraction des symptômes, pas le classifieur.

- **Français.** Le Space déployé ne détecte aucun symptôme dans 26 des 29 descriptions en français et n'en oriente qu'une correctement. Whisper étant configuré en français dans le Space speech, c'est le chemin de production qui est touché.
- **Extraction (anglais).** Elle ne retrouve que 27 % des symptômes attendus (précision 75 %). Dans 8 cas sur 29 elle ne trouve rien, et le pipeline ne renvoie alors ni spécialité ni prestataire.
- **Classifieur.** Alimenté avec les symptômes attendus, il donne la spécialité attendue dans 26 cas sur 29 (90 %), et une spécialité défendable dans les 29.
- **Optimisation.** Le classement réagit aux contraintes, mais faiblement et parfois à l'envers : rendre un médecin dix fois plus cher améliore son rang dans 29 % des essais. Le n°1 est toujours le médecin le mieux noté de la spécialité.
- **Latence.** 103 ms en moyenne en calcul local (P95 152 ms), 644 ms via le Space déployé (P95 734 ms).

Plusieurs étapes ne correspondent pas à ce que le papier décrit (section 2).

## 2. Le pipeline réel

Tout le chemin texte → recommandation tient dans le Space public `emergency-savior-output`, appelé par `backend/main.py`.

| Étape | Code | Ce qu'il fait |
|---|---|---|
| 2. NLP | `SymptomExtractor.extract` | Traduction vers l'anglais (Google Translate, bibliothèque non officielle), puis recherche de 300 identifiants de symptômes : dictionnaire de synonymes, correspondance exacte, correspondance floue |
| 3. Classification | `K1Brain.score` | `0,60 × Score_arbre + 0,40 × Probabilité_RF` sur 22 spécialités, puis Top 3 |
| 4. Optimisation | `ProviderFilter.optimize_providers_nsga` | Tri de Pareto + densité k-NN sur les médecins de la première spécialité |
| 5. Recommandation | `app.py` | Texte renvoyé à l'appelant |

### Écarts entre le papier et le code

| Le papier décrit | Le code fait |
|---|---|
| Extraction d'entités médicales | Recherche de chaînes et correspondance floue. Aucune gestion de la négation, alors que `pipeline.py` l'annonce en commentaire |
| Random Forest à poids figés | Aucun modèle n'est chargé ni exécuté. « Probabilité_RF » est une somme normalisée de `importance Gini × IDF × coefficient de profil`, lue dans des tableaux Excel. Seuls 80 des 300 symptômes ont une importance ; les autres reçoivent 0,001 |
| Score d'arbre pondéré par la sévérité | Sévérité fixée à 1. La « confiance de branche » et le poids ×1,0 des symptômes discriminants, présents dans la fiche K1, ne sont pas implémentés |
| Optimisation sur distance, coût, adéquation de spécialité | 7 objectifs : qualité, coût (ou écart au budget), délai de rendez-vous, créneaux, ville, CNAM, téléconsultation. L'adéquation de spécialité n'est pas un objectif : c'est un filtre sur la première spécialité prédite |
| Distance | 0 si le médecin est dans la ville du patient (égalité exacte de chaînes), 1 sinon |
| NSGA-II+ | Pas de population, de générations, de croisement ni de mutation : un seul tri non dominé suivi d'un tri par densité, sur une liste fixe de 18 à 27 médecins. C'est un classement, pas une optimisation évolutionnaire |
| Top 3 prestataires | Le Top 3 est calculé, mais le Space n'affiche que le nom du premier, et `backend/main.py` ne lit que `best_provider` |
| Niveaux de gravité | Le pipeline ne reçoit qu'un booléen `urgent`, dont le seul effet est de doubler le score de la cardiologie |

Deux points relevés à la lecture du code, non exécutés ici faute d'audio :

- Dans `/analyze-full`, `urgent` vaut vrai si le module vision (optionnel) renvoie HIGH ou CRITICAL, ou si `age_group == "senior"`. Or le Space speech renvoie `"elderly"`, jamais `"senior"`. Sans le module vision, `urgent` est donc toujours faux.
- Pour la même raison, l'âge transmis vaut 35 pour un patient classé « elderly ».

### Dépendances réseau au moment du test

| Dépendance | État | Conséquence |
|---|---|---|
| Space `emergency-savior-output` | Accessible, 58 appels sur 58 | Testé en conditions réelles |
| Space `emergency-savior-speech` | Accessible (HTTP 200) | Non exercé : l'entrée est un texte, comme prévu |
| Google Translate | Refusé depuis la machine de test (erreur 429 sur 174 appels sur 174) | La traduction n'a pas pu être testée en fonctionnement. L'extracteur continue alors en silence avec le texte non traduit |

Le code local est bien celui qui tourne en production : symptômes, spécialités, scores et premier médecin sont identiques entre l'exécution locale et le Space déployé sur 29 cas sur 29 en anglais, et 29 sur 29 en français. Comme la traduction échoue en local, cette identité en français indique qu'elle est tout aussi inopérante sur le Space ; la cause côté Space n'est pas observable de l'extérieur.

## 3. Protocole

**Cas de test.** 29 cas écrits à la main (`pipeline_cases.py`) :

- 22 cas standards, un par spécialité, donc les 7 branches de l'arbre ;
- 5 cas ambigus, avec une spécialité attendue et une alternative acceptable ;
- 2 cas de robustesse : négations explicites, plainte vague.

Chaque cas contient une description en français telle qu'un patient la formulerait, sa traduction anglaise fidèle, un âge, une gravité (7 LOW, 11 MEDIUM, 9 HIGH, 2 CRITICAL), une ville parmi 12 et un budget (50 à 150 TND, ou aucun).

**Vérité terrain.** La spécialité attendue et la liste des symptômes attendus ont été fixées avant toute exécution et n'ont pas été modifiées ensuite. Ce sont des hypothèses de test fondées sur le bon sens médical et sur l'arbre K1, pas une vérité clinique.

**Gravité.** Convertie en `urgent` avec la règle de `backend/main.py` : vrai pour HIGH et CRITICAL.

**Conditions.**

| Condition | Rôle |
|---|---|
| Anglais, local, 3 répétitions | Condition principale ; sorties intermédiaires et latence par étape |
| Anglais, Space déployé | Conditions réelles ; identité avec le local ; latence réseau comprise |
| Symptômes attendus → classifieur | Sépare les erreurs de l'extraction de celles du classifieur |
| `urgent` vrai puis faux | Effet du drapeau d'urgence |
| Français, local et Space déployé | Chemin de production |
| Optimisation, 22 spécialités | Réaction du classement aux contraintes |

**Métriques de classification.** Top-1 strict : la première spécialité est celle attendue. Top-1 large : celle attendue ou l'alternative acceptable. Top-3 : la spécialité attendue figure dans les trois renvoyées. Un cas sans aucune spécialité renvoyée compte comme une erreur.

## 4. Résultats

### 4.1 Classification de spécialité

| Condition | Cas | Top-1 strict | Top-1 large | Top-3 | Sans prédiction |
|---|---|---|---|---|---|
| Anglais, pipeline complet | 29 | 44,8 % (13) | 51,7 % (15) | 58,6 % (17) | 8 |
| — cas non ambigus | 24 | 50,0 % (12) | 50,0 % (12) | 58,3 % (14) | 7 |
| — cas ambigus | 5 | 20,0 % (1) | 60,0 % (3) | 60,0 % (3) | 1 |
| Anglais, symptômes attendus | 29 | 89,7 % (26) | 100 % (29) | 100 % (29) | 0 |
| Français, Space déployé | 29 | 3,4 % (1) | 3,4 % (1) | 3,4 % (1) | 26 |

Les résultats du Space déployé en anglais sont identiques à la première ligne.

**Par branche** (anglais, pipeline complet). Exactitude au niveau branche : 58,6 % ; F1 macro : 0,661.

| Branche | Cas | Précision | Rappel | F1 |
|---|---|---|---|---|
| B1 Peau · Allergie · ORL | 4 | 1,000 | 0,500 | 0,667 |
| B2 Cœur · Poumons · Sang · Veines | 5 | 0,800 | 0,800 | 0,800 |
| B3 Digestif · Foie · Oncologie · Pédiatrie | 5 | 0,750 | 0,600 | 0,667 |
| B4 Neuro · Ophtalmo · Psychiatrie | 4 | 1,000 | 0,750 | 0,857 |
| B5 Articulations · Os · Infectieux | 4 | 1,000 | 0,500 | 0,667 |
| B6 Hormones · Gynéco · Urologie · Rein | 5 | 1,000 | 0,400 | 0,571 |
| B7 Médecine interne | 2 | 0,333 | 0,500 | 0,400 |

**Matrice de confusion par branche** (ligne : attendue ; colonne : prédite).

| | B1 | B2 | B3 | B4 | B5 | B6 | B7 | Aucune |
|---|---|---|---|---|---|---|---|---|
| B1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 2 |
| B2 | 0 | 4 | 0 | 0 | 0 | 0 | 1 | 0 |
| B3 | 0 | 1 | 3 | 0 | 0 | 0 | 1 | 0 |
| B4 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 1 |
| B5 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 2 |
| B6 | 0 | 0 | 1 | 0 | 0 | 2 | 0 | 2 |
| B7 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |

L'erreur dominante est l'absence de prédiction (8 cas), pas la confusion entre branches (4 cas). Les précisions élevées et les rappels faibles disent la même chose : quand le système répond, il se trompe rarement de branche ; souvent, il ne répond pas.

Spécialités sans aucune prédiction : allergologie, ORL (C03), ophtalmologie, rhumatologie, endocrinologie, urologie, orthopédie (A05), médecine interne (R02). Avec un seul cas par spécialité, on ne peut pas conclure qu'une spécialité est systématiquement manquée ; on peut conclure que 8 descriptions réalistes sur 29 ne déclenchent rien.

### 4.2 Extraction NLP (anglais)

| Précision | Rappel | F1 | Symptômes trouvés | Symptômes en trop | Symptômes manqués | Cas sans aucun symptôme |
|---|---|---|---|---|---|---|
| 0,755 | 0,274 | 0,402 | 40 | 13 | 106 | 8 sur 29 |

L'extracteur ne reconnaît un symptôme que si le texte contient presque mot pour mot son identifiant ou une entrée du dictionnaire de synonymes. Une formulation ordinaire échappe à tout : « I am thirsty all the time, I urinate very often » ne donne ni `excessive_thirst` ni `polyuria` (cas C18, aucun symptôme extrait).

Les 13 symptômes en trop viennent de trois mécanismes :

- **Négation ignorée.** « no fever, no cough and no chest pain » produit `high_fever`, `cough` et `chest_pain` (cas R01).
- **Mots pris hors contexte.** « stomach pain with cramps » produit `cramps`, symptôme exclusif de la phlébologie (C08). Il suffit que « belly » et « pain » figurent quelque part dans le texte, même éloignés, pour produire `belly_pain`, symptôme exclusif de la pédiatrie (C19, A02).
- **Correspondance floue trop large.** « heavy bleeding » pendant les règles ressemble assez à « easy bleeding » (89 sur 100, seuil à 85) pour produire `easy_bleeding`, symptôme d'hématologie (C19).

### 4.3 Recommandation finale (anglais)

| Mesure | Résultat |
|---|---|
| Cas avec un Top 3 de prestataires | 21 sur 29 |
| Spécialité attendue chez le prestataire n°1 | 44,8 % |
| Spécialité attendue parmi les 3 prestataires | 44,8 % |
| Top 3 contenant plus d'une spécialité | 0 sur 21 |

Les deux taux de couverture sont égaux par construction : les trois prestataires viennent toujours de la première spécialité prédite. Le Top 3 ne rattrape donc jamais une erreur de classification, alors que la spécialité attendue est en 2e ou 3e position dans 4 cas (C08, C19, A01, A04).

### 4.4 Cas ambigus

| Cas | Attendue / acceptable | Spécialités renvoyées | Écart 1re–2e | Verdict |
|---|---|---|---|---|
| A01 | Pneumologie / cardiologie | Cardiologie 47,1 %, pneumologie 22,5 % | 24,5 pts | Alternative acceptable |
| A02 | Hépatologie / gastro-entérologie | Pédiatrie 55,6 %, gastro-entérologie 21,9 % | 33,7 pts | Autre spécialité |
| A03 | Neurologie / ophtalmologie | Neurologie 41,9 %, médecine interne 21,9 % | 20,0 pts | Attendue |
| A04 | Urologie / gynécologie | Gynécologie 63,7 %, urologie 36,3 % | 27,5 pts | Alternative acceptable |
| A05 | Orthopédie / rhumatologie | Aucune | — | Aucune prédiction |

Le système ne signale pas l'ambiguïté : l'écart entre la première et la deuxième spécialité est de 20 à 34 points, y compris quand il se trompe (A02 : un homme de 46 ans orienté vers la pédiatrie à cause de « belly »). Les scores sont normalisés pour sommer à 100 %, ce qui gonfle l'écart dès que peu de symptômes sont extraits.

Avec les symptômes attendus, les cinq cas ambigus tombent sur la spécialité attendue ou sur l'alternative.

### 4.5 Effet du drapeau d'urgence

`urgent = True` double le score de la cardiologie, quel que soit le tableau. Sur les symptômes réellement extraits, ce doublement fait basculer deux cas vers la cardiologie : C06 (syndrome hémorragique, seul `fatigue` extrait) et C10 (amaigrissement et adénopathies, `fatigue` et `sweating` extraits). Ces deux cas sont de gravité MEDIUM et n'ont donc pas basculé dans l'évaluation principale ; ils basculeraient s'ils étaient signalés urgents. Avec les symptômes attendus, aucun cas ne bascule.

### 4.6 Cohérence de l'optimisation

Tests sur les 22 spécialités, avec le vrai `optimize_providers_nsga`, patient à Tunis ou budget de 70 TND.

**Structure du classement.**

| Mesure | Résultat |
|---|---|
| Médecins sur le premier front de Pareto | 66,6 % en moyenne (minimum 44,4 %) |
| Points du premier front à densité infinie | 6,6 en moyenne (minimum 4) |
| Spécialités où le n°1 est le médecin le mieux noté | 22 sur 22 |
| Places du Top 3 occupées par le pire de sa spécialité sur un critère | 29 sur 66, dans 19 spécialités sur 22 |

Avec 7 objectifs et une vingtaine de candidats, deux tiers des médecins sont non dominés : le tri de Pareto discrimine peu. Le code donne ensuite une densité infinie aux deux extrémités de chaque objectif, la meilleure et la pire. Il y a toujours au moins 4 de ces points, donc ce sont eux qui remplissent le Top 3, et la densité k-NN n'intervient jamais dans le choix. En pratique, les ex æquo sortent dans l'ordre d'entrée, qui est trié par note décroissante : le Top 3 est constitué des médecins les mieux notés parmi ceux qui sont extrêmes sur un critère, dans un sens ou dans l'autre.

**Réaction aux contraintes du patient.**

| Contrainte fournie | Top 3 modifié | Effet mesuré |
|---|---|---|
| Ville (Tunis) | 10 spécialités sur 22 | Médecins de Tunis dans le Top 3 : 0,64 → 0,86 sur 3, alors que chaque spécialité en compte 4 en moyenne |
| Budget (70 TND) | 6 spécialités sur 22 | Écart moyen au budget du Top 3 : 32,4 → 32,3 TND, contre 20,6 TND pour l'ensemble des médecins |

Le classement n'est pas figé, mais la ville ajoute en moyenne moins d'un quart de médecin local au Top 3, et le budget ne rapproche pas le Top 3 du budget demandé : le Top 3 en reste plus éloigné que la moyenne des médecins.

**Modification d'un seul médecin** (rang 1 = meilleur).

| Modification | Essais | Rang amélioré | Inchangé | Rang dégradé | Rang moyen | Dans le Top 3 |
|---|---|---|---|---|---|---|
| Déplacé dans la ville du patient | 417 | 254 (61 %) | 146 | 17 | 12,9 → 8,1 | 47 → 88 |
| Coût ramené au budget | 461 | 311 (67 %) | 138 | 12 | 12,3 → 5,7 | 64 → 142 |
| Note portée au maximum | 504 | 482 (96 %) | 22 | 0 | 12,1 → 1,0 | 66 → 504 |
| Note mise à zéro | 504 | 65 (13 %) | 79 | 360 | 12,1 → 18,0 | 66 → 6 |
| Coût multiplié par 10 | 504 | 144 (29 %) | 174 | 186 | 12,1 → 13,3 | 66 → 117 |
| Délai porté à 365 jours | 504 | 214 (42 %) | 221 | 69 | 12,1 → 10,2 | 66 → 119 |

Les trois premières lignes vont dans le bon sens : rapprocher un médecin du patient ou de son budget améliore son rang dans environ deux tiers des essais. Les deux dernières montrent le défaut : rendre un médecin dix fois plus cher, ou lui donner un an de délai, améliore son rang dans 29 % et 42 % des essais et fait presque doubler sa présence dans le Top 3. Devenir le pire sur un critère en fait un point extrême, donc prioritaire.

### 4.7 Latence

Local : 87 mesures (29 cas × 3). Space déployé : 29 appels, Space déjà réveillé.

| Étape | Moyenne (ms) | Médiane (ms) | P95 (ms) |
|---|---|---|---|
| Extraction NLP, hors appel de traduction | 96,6 | 93,8 | 146,4 |
| Classification | 0,2 | 0,1 | 0,4 |
| Optimisation | 5,5 | 5,2 | 14,6 |
| Mise en forme du Top 3 | 0,4 | 0,4 | 1,3 |
| **Bout en bout local, hors appel de traduction** | **102,7** | **98,1** | **151,6** |
| Appel de traduction (en échec) | 1 165,2 | 1 176,9 | 1 361,1 |
| **Space déployé, bout en bout, réseau compris** | **643,5** | **645,0** | **734,1** |

L'extraction représente 94 % du temps de calcul, à cause de la correspondance floue de chaque n-gramme contre 300 symptômes. La durée de l'appel de traduction est celle d'un appel refusé : elle ne dit rien d'une traduction réussie. Le chargement initial des fichiers Excel prend 0,5 s.

## 5. Trois exemples

### C04 — la classification fonctionne, la recommandation interroge

- **Entrée** : « My husband has had a very strong pain in his chest for twenty minutes, it feels tight like a vise and it goes down his left arm. He is sweating a lot and he says his heart is beating irregularly. » 58 ans, CRITICAL, Tunis, budget 120 TND.
- **NLP** : `chest_pain`, `left_arm_pain`, `sweating`. Manqués : `chest_tightness`, `irregular_heartbeat`.
- **Classification** : cardiologie 67,5 %, médecine interne 11,8 %, pneumologie 8,8 %. Correct.
- **Top 3** :

| Rang | Médecin | Ville | Note | Coût | Délai de rendez-vous |
|---|---|---|---|---|---|
| 1 | Dr. Leila Boukadida | Monastir | 10,0 | 120 TND | 46 jours |
| 2 | Dr. Hamza Saidi | Sousse | 9,2 | 135 TND | 40 jours |
| 3 | Dr. Olfa Khelifi | Tunis | 9,0 | 115 TND | 36 jours |

- **Durée** : 126 ms en local, 736 ms via le Space.

Pour une suspicion d'infarctus à Tunis, le premier médecin proposé est à Monastir, avec le délai de rendez-vous le plus long de sa spécialité. La base K2 décrit des consultations sur rendez-vous (délais de 16 à 48 jours) : elle ne contient rien qui corresponde à une prise en charge en urgence.

### C19 — une femme de 29 ans orientée vers la pédiatrie

- **Entrée** : « I have very painful periods with heavy bleeding, pain in my lower belly and pelvis, and an unusual vaginal discharge for a week. » 29 ans, MEDIUM, Tunis, budget 90 TND.
- **NLP** : `vaginal_discharge` (correct), `belly_pain` et `easy_bleeding` (en trop). Manqués : `dysmenorrhea`, `menorrhagia`, `pelvic_pain`.
- **Classification** : pédiatrie 42,28 %, gynécologie 42,03 %, hématologie 15,69 %. Écart de 0,25 point.
- **Top 3** : trois pédiatres (Nabeul, Nabeul, Tunis).
- Avec les symptômes attendus : gynécologie 100 %.

`belly_pain` est un symptôme exclusif de la pédiatrie dans l'arbre, et la présence des mots « belly » et « pain » dans le texte suffit à le déclencher. Le bonus pédiatrique lié à l'âge ne joue pas ici : l'erreur vient entièrement de l'extraction.

### C08 — des crampes d'estomac envoient en phlébologie

- **Entrée** : « Since last night I have had strong stomach pain with cramps, I vomited three times and I have diarrhea. I feel nauseous all the time and I cannot keep anything down. » 34 ans, MEDIUM, Nabeul, budget 60 TND.
- **NLP** : `stomach_pain`, `diarrhoea` (corrects), `abdominal_pain`, `cramps` (en trop). Manqués : `vomiting` (« vomited ») et `nausea` (« nauseous »).
- **Classification** : phlébologie 54,9 %, gastro-entérologie 39,1 %.
- **Top 3** : trois phlébologues, à Kebili, Kairouan et Tunis, aucun à Nabeul.
- Avec les symptômes attendus : gastro-entérologie 58,2 %.

## 6. Limites

**Du système testé.**

1. Le français n'est pas traité en conditions réelles, sans aucun message d'erreur : le pipeline renvoie une réponse vide.
2. L'extraction ne comprend que des formulations proches de son vocabulaire, ignore les négations et prend des mots hors contexte.
3. Quand rien n'est extrait, aucune spécialité ni aucun prestataire n'est renvoyé. Il n'y a pas de repli vers une spécialité généraliste.
4. Le Top 3 de prestataires ne contient qu'une spécialité, et seul le premier nom parvient à l'utilisateur.
5. Le classement des prestataires réagit peu à la ville et au budget, et récompense le fait d'être le pire sur un critère.
6. Le drapeau d'urgence favorise la cardiologie sans rapport avec les symptômes.

**De ce test.**

1. 29 cas, dont un seul par spécialité pour 15 des 22 : les F1 par branche reposent sur 2 à 5 cas et une erreur les déplace de 10 à 25 points. Les ordres de grandeur sont fiables, pas les décimales.
2. La vérité terrain est posée par l'auteur des tests, sans validation par un médecin.
3. Le résultat avec les symptômes attendus (90 %) est une borne haute optimiste : ces symptômes ont été choisis dans le vocabulaire du système, en connaissant l'arbre.
4. La traduction n'a pas pu être testée en état de marche. Les résultats en anglais correspondent à ce que le pipeline ferait avec une traduction parfaite du français, pas à ce qu'il fait aujourd'hui.
5. L'étape speech (Whisper, correction du transcript) n'est pas exercée : le texte d'entrée est propre, sans erreur de transcription.
6. La latence du Space est mesurée depuis une seule machine, sur un Space déjà réveillé ; un démarrage à froid n'est pas mesuré.

## 7. Résumé pour la section Experiments

**Objectif.** Évaluer de bout en bout le chemin principal du système : description des symptômes en texte libre, extraction des symptômes, classification parmi 22 spécialités, classement multi-objectifs des prestataires, recommandation finale.

**Méthode.** Nous avons rédigé 29 cas de test : un par spécialité (22 cas, couvrant les 7 branches de l'arbre hiérarchique), 5 cas volontairement ambigus entre deux spécialités, et 2 cas de robustesse (négations, plainte vague). Chaque cas comprend une description en langage courant, en français et en traduction anglaise, un âge, un niveau de gravité, une ville et un budget. La spécialité et les symptômes attendus ont été fixés avant exécution et constituent une hypothèse de test, non une vérité clinique. Les cas sont soumis au code de production, exécuté localement étape par étape et via le service déployé ; les deux donnent des résultats identiques sur les 29 cas. Pour séparer les erreurs d'extraction des erreurs de classification, la classification est rejouée avec les symptômes attendus. La cohérence du classement des prestataires est testée sur les 22 spécialités en modifiant un seul attribut d'un seul prestataire à la fois (2 894 essais).

**Résultats.**

| Mesure | Résultat |
|---|---|
| Exactitude Top-1 de la spécialité (anglais) | 44,8 % (13/29) |
| Couverture Top-3 de la spécialité (anglais) | 58,6 % (17/29) |
| Exactitude au niveau branche ; F1 macro | 58,6 % ; 0,661 |
| Cas sans aucune prédiction | 8/29 |
| Extraction des symptômes : précision ; rappel | 0,755 ; 0,274 |
| Exactitude Top-1 avec les symptômes attendus | 89,7 % (26/29) |
| Exactitude Top-1 sur le texte français, service déployé | 3,4 % (1/29) |
| Latence de bout en bout, calcul local : moyenne ; P95 | 103 ms ; 152 ms |
| Latence de bout en bout, service déployé : moyenne ; P95 | 644 ms ; 734 ms |

L'extraction des symptômes est le facteur limitant : elle ne retrouve que 27 % des symptômes attendus et n'en trouve aucun dans 8 cas, pour lesquels le système ne renvoie rien. Avec des symptômes corrects, le classifieur atteint 90 %, et 100 % en acceptant l'alternative des cas ambigus. Sur le texte français, la traduction automatique n'étant pas opérante dans le service déployé, 26 cas sur 29 ne produisent aucun symptôme.

Le classement des prestataires réagit aux contraintes du patient dans le sens attendu pour environ deux tiers des perturbations favorables (61 % pour la ville, 67 % pour le budget), mais il n'est pas monotone : multiplier par dix le coût d'un prestataire améliore son rang dans 29 % des essais, parce que les extrémités de chaque objectif, bonnes ou mauvaises, sont classées en tête. Deux tiers des prestataires d'une spécialité sont non dominés sur les 7 objectifs, et le premier recommandé est le mieux noté dans les 22 spécialités.

**Limites.** Le jeu de test est petit (un cas par spécialité pour la plupart) et sa vérité terrain n'a pas été validée par un clinicien. L'étape de reconnaissance vocale n'est pas incluse. La traduction n'a pas pu être évaluée en état de marche. L'étape d'optimisation est un tri de Pareto unique suivi d'un tri par densité, sans boucle générationnelle, et la composante « Random Forest » du score est un calcul sur des importances précalculées, sans modèle exécuté.
