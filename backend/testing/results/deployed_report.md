# Vérification en production : corrections mesurées sur le Space déployé

Suite de `fix_report.md`. Les chiffres de ce rapport-là venaient du code exécuté en local ; ceux-ci viennent uniquement du Space en ligne `youssef0081/emergency-savior-output`, après déploiement des six fichiers corrigés.

Chiffres issus de `deployed_evaluation_report.json`. Pour les reproduire, depuis `backend/` :

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
