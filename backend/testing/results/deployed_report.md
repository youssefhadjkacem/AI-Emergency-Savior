# Vérification en production : corrections mesurées sur le Space déployé

Suite de `fix_report.md`. Les chiffres de ce rapport-là venaient du code exécuté en local ; ceux-ci viennent uniquement du Space en ligne `youssef0081/emergency-savior-output`, après déploiement des six fichiers corrigés.

> **Mise à jour du 6 octobre 2026.** Le round 2 (classement corrigé, gravité) a été déployé puis réévalué en production : voir la section 7. Les sections 1 à 6 décrivent la mesure du round 1 ; leurs chiffres sont conservés dans `deployed_evaluation_report_round1.json`, car `deployed_evaluation_report.json` a été régénéré.

Chiffres des sections 1 à 6 issus de `deployed_evaluation_report_round1.json`. Pour les reproduire, depuis `backend/` :

```
python -m testing.run_deployed_evaluation
```

## 1. Conclusion

**Les chiffres locaux sont confirmés en production, à l'identique.** Sur les 82 appels (29 cas en anglais, 29 en français, 24 du jeu de contrôle français), le Space a répondu 82 fois, sans erreur ni nouvelle tentative, et chaque réponse est identique au résultat local : mêmes symptômes, mêmes spécialités et scores, mêmes trois prestataires, même statut.

Le Top-1 de 83,3 % sur le jeu de contrôle français se retrouve donc tel quel en conditions réelles (20 cas sur 24).

Un seul écart entre local et production a été observé, hors des 53 cas : la traduction de secours répond depuis le Space alors qu'elle échoue depuis la machine de test (section 4).

## 2. Protocole

- Chaque cas est envoyé à l'URL publique du Space, par le protocole Gradio qu'utilise `backend/main.py`, et la réponse est lue par le parseur de `backend/main.py`.
- Une seconde de pause entre deux appels ; durée totale 118 secondes.
- Le pipeline n'est pas exécuté en local. La référence locale est lue dans `fix_evaluation_report.json` (configuration `after_default`, le pipeline corrigé avec ses réglages par défaut).
- La comparaison porte sur ce que le Space expose : symptômes, trois spécialités avec leur score à une décimale, trois prestataires (nom, spécialité, ville), statut d'extraction.

## 3. Local contre production

| Mesure | Anglais, local | Anglais, production | Français, local | Français, production | Contrôle FR, local | Contrôle FR, production |
|---|---|---|---|---|---|---|
| Top-1 strict | 44,8 % | 44,8 % | 82,8 % | 82,8 % | 83,3 % | 83,3 % |
| Top-1 large | 51,7 % | 51,7 % | 93,1 % | 93,1 % | 83,3 % | 83,3 % |
| Top-3 spécialités | 58,6 % | 58,6 % | 100 % | 100 % | 100 % | 100 % |
| Spécialité attendue chez le prestataire n°1 | 44,8 % | 44,8 % | 82,8 % | 82,8 % | 83,3 % | 83,3 % |
| Spécialité attendue parmi les 3 prestataires | 58,6 % | 58,6 % | 100 % | 100 % | 100 % | 100 % |
| Extraction : précision | 0,800 | 0,800 | 0,928 | 0,928 | 0,939 | 0,939 |
| Extraction : rappel | 0,274 | 0,274 | 0,973 | 0,973 | 0,697 | 0,697 |
| Cas sans prédiction | 8 | 8 | 0 | 0 | 0 | 0 |
| Cas identiques au local | — | 29 sur 29 | — | 29 sur 29 | — | 24 sur 24 |

Anglais et français : 29 cas chacun. Contrôle FR : 24 cas.

Aucun cas ne réussit d'un côté et échoue de l'autre. Les échecs sont les mêmes qu'en local :

- français, jeu d'origine : C08, C15, A01, A03, A04 ;
- français, jeu de contrôle : H02, H03, H13, H24 ;
- anglais : les 16 mêmes cas, dont les 8 sans prédiction.

**Trois prestataires.** Le Space renvoie trois noms dans les 53 réponses en français et dans 21 réponses sur 29 en anglais. Les 8 autres sont les cas sans symptôme reconnu, qui renvoient maintenant le statut `no_symptom_found` au lieu d'une réponse vide.

**Anglais.** Aucune régression par rapport à avant correction : même Top-1, même Top-3.

### Latence réelle

Mesurée depuis la machine de test, réseau compris, Space déjà réveillé.

| Jeu | Appels | Moyenne | Médiane | P95 | Maximum |
|---|---|---|---|---|---|
| Anglais | 29 | 415 ms | 400 ms | 480 ms | 933 ms |
| Français | 29 | 411 ms | 408 ms | 510 ms | 612 ms |
| Contrôle français | 24 | 458 ms | 434 ms | 658 ms | 752 ms |

Avant correction, le même Space répondait en 644 ms en moyenne (P95 734 ms) sur les 29 cas anglais. La baisse est cohérente avec la suppression de l'appel de traduction pour l'anglais, mais les deux mesures ont été prises à plusieurs jours d'écart : c'est une indication, pas une comparaison contrôlée.

L'essentiel de ces 400 ms est du réseau : le calcul local prenait 19 ms en français et 85 ms en anglais.

## 4. Le seul écart observé : la traduction de secours

Les 53 cas français ne déclenchent aucun appel de traduction, parce que le lexique y trouve toujours au moins un symptôme. Pour exercer ce chemin en production, trois textes sans symptôme ont été envoyés au Space, hors évaluation.

| Texte | Durée | Statut renvoyé par le Space | Statut en local |
|---|---|---|---|
| « Bonjour, je voudrais un renseignement s'il vous plaît. » | 3,0 s | `no_symptom_found` | `no_symptom_translation_failed` |
| « Je me sens patraque et tout drôle depuis ce matin. » | 3,6 s | `no_symptom_found` | non mesuré |
| « Hello, I would like some information please. » | 0,8 s | `no_symptom_found` | `no_symptom_found` |

Ce que cela montre :

- **Depuis le Space, la traduction de secours a abouti.** Le statut `no_symptom_found` n'est renvoyé pour un texte français que si une traduction a réussi. Depuis la machine de test, les deux services refusaient les appels. Le Space ne dit pas lequel des deux a répondu. Avant correction, Google échouait depuis le Space ; il est donc probable que ce soit MyMemory, sans que ce soit vérifiable de l'extérieur.
- **Ce chemin coûte environ 3 secondes**, contre 0,4 seconde pour un texte où le lexique suffit.
- **Il reste fragile.** Le quota gratuit de MyMemory s'est épuisé en une seule évaluation locale ; le même plafond s'applique au Space. Le jour où il est atteint, ces textes renverront `no_symptom_translation_failed` au lieu de `no_symptom_found`, sans conséquence pour les textes que le lexique comprend.

Le deuxième texte illustre aussi une limite déjà connue du lexique : « patraque » n'y figure pas, et la traduction n'a pas permis de le rattraper.

## 5. Ce que cette vérification ne couvre pas

1. Une seule exécution, depuis une seule machine, sur un Space déjà réveillé. Le démarrage à froid n'est pas mesuré.
2. L'identité entre local et production prouve que le code déployé est le bon. Elle ne dit rien de plus sur la qualité du lexique : les réserves de `fix_report.md` restent entières (jeux de test du même auteur que le lexique, 30 % des symptômes non reconnus sur le jeu de contrôle).
3. L'anglais reste à 44,8 %, avec 8 cas sur 29 sans prédiction.
4. Les deux limites hors périmètre n'ont pas été remesurées : le classement des prestataires et la règle d'urgence n'ont pas été modifiés.
5. `backend/main.py` et le frontend ne sont pas concernés par cette mesure : elle interroge le Space directement.

## 6. Résumé pour la section Experiments

Les chiffres déjà rapportés n'ont pas à être modifiés. Paragraphe à ajouter :

> Les corrections ont été déployées sur le service en ligne, puis réévaluées en conditions réelles en interrogeant uniquement ce service : 82 requêtes (29 cas en anglais, 29 en français, 24 du jeu de contrôle français), espacées d'une seconde. Le service a répondu aux 82 requêtes, et chaque réponse est identique au résultat obtenu localement (symptômes extraits, spécialités et scores, prestataires recommandés). Les métriques en production sont donc celles du tableau précédent, dont une exactitude Top-1 de 83,3 % sur le jeu de contrôle français et de 44,8 % en anglais. La latence de bout en bout, réseau compris, est de 411 à 458 ms en moyenne selon le jeu (P95 de 480 à 658 ms). Un texte français dans lequel le lexique ne reconnaît aucun symptôme déclenche un appel à un service de traduction externe ; ce chemin, non sollicité par les cas de test, a pris environ 3 secondes lors de nos essais et dépend d'un service gratuit à quota limité.

## 7. Round 2 en production (6 octobre 2026)

Après le dépôt des six fichiers du round 2 sur le Space (classement monotone avec poids 3 sur la note, niveaux de gravité), la même évaluation a été relancée. Chiffres issus de `deployed_evaluation_report.json` (régénéré) et de `deployed_round2_check.json`.

**Conclusion.** Le round 2 tourne en production et donne les résultats du code local : mêmes trois prestataires sur 82 cas sur 82, même niveau de gravité sur 24 cas sur 24. Top-1 et Top-3 n'ont pas bougé.

### 7.1 Classification et extraction : inchangées

82 appels, 82 réponses, aucune nouvelle tentative, 106 secondes.

| Mesure | Anglais (29) | Français (29) | Contrôle FR (24) |
|---|---|---|---|
| Top-1 strict | 44,8 % | 82,8 % | 83,3 % |
| Top-1 large | 51,7 % | 93,1 % | 83,3 % |
| Top-3 spécialités | 58,6 % | 100 % | 100 % |
| Spécialité attendue parmi les 3 prestataires | 58,6 % | 100 % | 100 % |
| Extraction : précision | 0,800 | 0,928 | 0,939 |
| Extraction : rappel | 0,274 | 0,973 | 0,697 |
| Cas sans prédiction | 8 | 0 | 0 |

Ce sont exactement les chiffres du round 1 (section 3).

### 7.2 Classement des prestataires : celui du round 2

`run_deployed_evaluation.py` compare le Space à la référence locale du round 1 (`fix_evaluation_report.json`). Il signale donc des prestataires différents dans les 74 réponses qui en contiennent (21 en anglais, 29 et 24 en français) : c'est l'effet attendu du nouveau classement. Les symptômes et le statut d'extraction sont identiques dans les 82 cas.

Pour vérifier que ces prestataires sont bien ceux du round 2, les 82 cas ont été rejoués sur le code local du round 2 (clone du Space, synchronisé avec le dépôt distant) :

| Jeu | Cas | Mêmes trois prestataires, dans le même ordre |
|---|---|---|
| Anglais | 29 | 29 |
| Français | 29 | 29 |
| Contrôle français | 24 | 24 |

L'ordre des trois spécialités est le même qu'au round 1 dans 77 cas sur 82 ; dans les 5 autres, seules la deuxième ou la troisième place changent. Les scores bougent de quelques dixièmes dans 23 cas, sous l'effet du bonus de gravité.

### 7.3 Gravité

Les 24 cas de `severity_cases.py` ont été envoyés au Space (sans drapeau d'urgence, sans ville ni budget).

| | Cas | Identique au local | Exact contre l'annotation |
|---|---|---|---|
| Tous | 24 | 24 | 16 (66,7 %) |
| Français | 12 | 12 | 9 (75,0 %) |
| Anglais | 12 | 12 | 7 (58,3 %) |

Ce sont les chiffres de `round2_final_report.md`. Quand le niveau est `UNKNOWN`, le Space n'affiche pas de ligne de gravité ; cette absence est comptée comme `UNKNOWN`.

### 7.4 Latence

| Jeu | Appels | Moyenne | Médiane | P95 | Maximum |
|---|---|---|---|---|---|
| Anglais | 29 | 326 ms | 261 ms | 356 ms | 1 957 ms |
| Français | 29 | 290 ms | 224 ms | 229 ms | 2 147 ms |
| Contrôle français | 24 | 226 ms | 222 ms | 246 ms | 264 ms |

Une seule exécution, un autre jour que celle du round 1 : la baisse par rapport aux 411-458 ms de la section 3 n'est pas une comparaison contrôlée.

### 7.5 Réserves

1. `deployed_round2_check.json` vient d'un script ponctuel qui n'est pas dans le dépôt. Pour le refaire : rejouer les cas avec `LocalPipeline()` et comparer à `deployed_evaluation_report.json` ; envoyer `SEVERITY_CASES` avec `call_deployed_space`.
2. Les tests de monotonie du classement (coût multiplié par 10, etc.) ne peuvent pas être faits contre le Space, qui n'accepte pas de base modifiée. Ils reposent sur l'identité du code et des Top 3.
3. Les réserves de la section 5 restent entières.

