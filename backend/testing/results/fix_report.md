# Corrections du pipeline principal : français, négations, Top 3

Suite du test de bout en bout (`pipeline_report.md`). Trois corrections, mesurées avant et après avec les mêmes 29 cas, la même vérité terrain et les mêmes métriques.

Chiffres issus de `fix_evaluation_report.json`. Pour les reproduire, depuis `backend/` :

```
python -m testing.run_fix_evaluation
python -m pytest testing realtime
```

Les chiffres d'avant correction sont conservés dans `pipeline_evaluation_report_before_fixes.json`.

> **Mise à jour.** Depuis la rédaction de ce rapport, les six fichiers ont été déployés sur le Space, et les chiffres ci-dessous ont été confirmés à l'identique en production : voir `deployed_report.md`. Les passages qui disent que rien n'est déployé (sections 1 et 6) décrivent l'état au moment de la mesure locale.

## 1. Conclusion

**Les trois corrections fonctionnent dans le code local ; aucune n'est déployée.** Le Space en production renvoie toujours une réponse vide en français.

- **Français.** L'exactitude Top-1 passe de 4 % à 83 % sur un jeu de contrôle de 24 cas écrits après coup, sans aucun service de traduction. Le chiffre sur les 29 cas d'origine (83 % aussi) est à lire avec prudence : le lexique a été rédigé en les connaissant.
- **Négations.** Les symptômes niés ne sont plus extraits, en français comme en anglais. La précision d'extraction gagne 2 à 5 points. Le prix : 2 vrais symptômes perdus sur 146 en français.
- **Top 3.** La spécialité attendue figure désormais parmi les 3 prestataires dans 58,6 % des cas en anglais, contre 44,8 % ; en français, dans 100 % des cas contre 82,8 %. Le premier prestataire ne change jamais.
- **Anglais.** Rien n'est cassé, mais rien n'est réparé non plus côté classification : Top-1 inchangé à 44,8 %, rappel d'extraction inchangé à 0,274, et les 8 cas sans prédiction le restent.

## 2. Diagnostic

Chaque cause a été vérifiée avant modification.

### Français

Le chemin prévu est : texte français → `GoogleTranslator` → extraction sur le texte anglais. L'appel de traduction est bien déclenché. Il échoue, et l'échec est avalé :

```python
try:
    text_en = GoogleTranslator(source='auto', target='en').translate(text).lower()
except Exception as e:
    text_en = text.lower()          # le texte français continue tel quel
    print(f"[DEBUG] Erreur traduction : {e}")
```

Le texte français est alors comparé à un vocabulaire anglais, ne correspond à rien, et la liste vide est renvoyée comme un résultat normal.

Vérifications :

- **Le blocage vient de Google, pas de la machine de test seule.** Trois jours après le premier test, les deux points d'entrée de Google Translate renvoient toujours une erreur 429 et une page « Sorry ». La bibliothèque `deep-translator` utilise un accès non officiel, que Google bloque par adresse.
- **Le Space déployé se comporte exactement comme un Space sans traduction** : 26 réponses vides sur 29, identiques au résultat local où la traduction échoue.
- **Rétablir la traduction suffit à ramener le français près de l'anglais.** Avec un autre service (MyMemory) et l'extracteur d'origine, le français est passé de 3,4 % à 34,5 % lors de l'exécution où ce service a répondu (section 4.2). La panne de traduction est donc bien la cause.

### Négations

`SymptomExtractor.extract` cherche les symptômes par présence dans le texte : sous-chaînes pour les synonymes, expression régulière sur le nom du symptôme, puis deux comparaisons floues (`fuzzywuzzy`). Aucune de ces étapes ne regarde ce qui précède le symptôme. Le commentaire « gère la négation » dans `pipeline.py` ne correspond à aucun code. Vérifié sur le cas R01 : « no fever, no cough and no chest pain » produit les trois symptômes.

### Top 3

`MedicalRecommender.predict` appelle `optimize_providers_nsga(top_spec, top_k=3)` avec la seule première spécialité. Vérifié sur les 21 cas qui renvoyaient des prestataires : aucun Top 3 ne contenait deux spécialités. `app.py` n'affichait que `nsga_top.iloc[0]`.

## 3. Ce qui a été corrigé

Tout est dans le clone local du Space (`spaces_src/emergency-savior-output/`) et dans `backend/main.py`.

### Français : extraction directe, traduction en secours

- **Nouveau module `src/french_lexicon.py`.** Il associe des expressions françaises à 271 des 300 identifiants de symptômes. La liste de symptômes est fermée, donc une correspondance directe est possible et ne dépend d'aucun réseau. Les 29 identifiants restants sont pour l'essentiel des signes d'examen clinique qu'un patient ne formule pas.
- **La traduction devient un secours.** Pour un texte français, elle n'est appelée que si le lexique ne trouve rien. Deux services sont essayés dans l'ordre : Google, puis MyMemory.
- **Un texte anglais n'est plus envoyé à la traduction.** Cela supprime un appel réseau d'environ une seconde.
- **L'échec n'est plus silencieux.** L'extracteur renvoie un statut, journalise les échecs, et le Space l'affiche :

| Statut | Signification |
|---|---|
| `ok` | Symptômes trouvés |
| `ok_without_translation` | Symptômes trouvés par le lexique, la traduction demandée a échoué |
| `no_symptom_found` | Texte compris, aucun symptôme |
| `no_symptom_translation_failed` | Texte non analysé faute de traduction |

Avant, les deux derniers cas étaient indiscernables.

**Pourquoi le lexique plutôt qu'une traduction fiabilisée.** Une traduction locale par modèle aurait ajouté une dépendance lourde au Space. Et la traduction, même quand elle répond, fait repasser le texte par l'extracteur anglais, qui est le maillon faible (rappel de 0,274). Les mesures le confirment : voir la section 4.2.

### Négations : masquage de portée

Nouveau module `src/negation.py`, inspiré de NegEx. Un marqueur de négation ouvre une portée de 5 mots au plus, fermée plus tôt par une ponctuation ou par « mais », « but », « sauf ». Les mots sous portée sont masqués avant toute recherche de symptôme, ce qui vaut pour tous les mécanismes de recherche d'un coup.

- Marqueurs anglais : no, not, without, never, neither, nor, none, denies, et les contractions (don't, isn't…).
- Marqueurs français : pas, aucun(e), sans, ni, jamais, et « plus » seulement après « ne ».
- Une incapacité n'est pas une négation : « ne peut plus respirer », « n'arrive pas à bouger le bras » restent intacts.
- Une tournure négative qui décrit un symptôme est protégée : « plus d'appétit », « ne dort plus », « no smell ».

La fenêtre de 5 mots est celle de NegEx ; elle couvre « pas de douleur dans la poitrine » sans déborder sur la proposition suivante quand la ponctuation manque.

### Top 3 : places réparties selon le score de classification

Règle, dans `ProviderFilter.diversified_top_providers` :

1. Les 3 places sont réparties entre les spécialités renvoyées par la classification, en proportion de leur score (méthode du plus fort reste). La première spécialité a toujours au moins une place.
2. Dans chaque spécialité, les prestataires sont pris dans l'ordre du classement existant, qui n'est pas modifié.
3. L'ordre final alterne les spécialités : le meilleur de la première, le meilleur de la deuxième, puis les suivants.

| Scores de classification | Places |
|---|---|
| 98 % / 1 % / 1 % | 3 / 0 / 0 |
| 68 % / 12 % / 9 % | 2 / 1 / 0 |
| 55 % / 39 % / 1 % | 2 / 1 / 0 |
| 42 % / 42 % / 16 % | 1 / 1 / 1 |

La diversité suit donc l'incertitude du classifieur : une classification nette donne trois médecins de la même spécialité, comme avant ; une classification indécise propose les spécialités en concurrence.

Le Space affiche maintenant les trois noms avec leur spécialité et leur ville, et `backend/main.py` les renvoie dans un nouveau champ `top_providers`. La ligne « Meilleur médecin » et le champ `best_provider` sont conservés.

## 4. Résultats avant / après

Les corrections sont activées une à une. Top-1 et Top-3 désignent la spécialité attendue en première position ou parmi les trois spécialités renvoyées. « Prestataires » désigne la spécialité attendue parmi les trois médecins recommandés.

### 4.1 Tableau principal

Configurations sans service de traduction, donc reproductibles hors ligne.

**Anglais, 29 cas.**

| Étape | Top-1 | Top-3 | Prestataires | Précision | Rappel | Sans prédiction |
|---|---|---|---|---|---|---|
| Avant | 44,8 % | 58,6 % | 44,8 % | 0,755 | 0,274 | 8 |
| + français | 44,8 % | 58,6 % | 44,8 % | 0,755 | 0,274 | 8 |
| + négations | 44,8 % | 58,6 % | 44,8 % | 0,800 | 0,274 | 8 |
| + Top 3 diversifié | 44,8 % | 58,6 % | **58,6 %** | 0,800 | 0,274 | 8 |

**Français, 24 cas de contrôle** (écrits après le gel du lexique ; la mesure à retenir).

| Étape | Top-1 | Top-3 | Prestataires | Précision | Rappel | Sans prédiction |
|---|---|---|---|---|---|---|
| Avant | 4,2 % | 4,2 % | 4,2 % | 1,000 | 0,011 | 23 |
| + français | **83,3 %** | 100 % | 83,3 % | 0,899 | 0,697 | 0 |
| + négations | 83,3 % | 100 % | 83,3 % | 0,939 | 0,697 | 0 |
| + Top 3 diversifié | 83,3 % | 100 % | **100 %** | 0,939 | 0,697 | 0 |

**Français, 29 cas d'origine** (vus pendant la rédaction du lexique ; résultat optimiste).

| Étape | Top-1 | Top-3 | Prestataires | Précision | Rappel | Sans prédiction |
|---|---|---|---|---|---|---|
| Avant | 3,4 % | 3,4 % | 3,4 % | 0,667 | 0,014 | 26 |
| + français | 82,8 % | 100 % | 82,8 % | 0,911 | 0,986 | 0 |
| + négations | 82,8 % | 100 % | 82,8 % | 0,928 | 0,973 | 0 |
| + Top 3 diversifié | 82,8 % | 100 % | 100 % | 0,928 | 0,973 | 0 |

L'écart de rappel entre les deux jeux français (0,973 contre 0,697) mesure le biais attendu : le lexique reconnaît presque tout sur les phrases que son auteur connaissait, et 70 % sur des phrases nouvelles. L'exactitude Top-1, elle, tient (83 % dans les deux cas), parce qu'il suffit de quelques symptômes discriminants pour orienter correctement.

La couverture des prestataires est strictement supérieure au Top-1 après diversification, dans les trois jeux. En anglais, elle atteint exactement le Top-3 des spécialités (58,6 %), qui est son plafond : la diversification ne peut pas proposer une spécialité que le classifieur n'a pas renvoyée.

### 4.2 Avec un service de traduction

Deux configurations dépendent du réseau. L'évaluation a été lancée deux fois, et les deux exécutions ne donnent pas le même résultat, ce qui est en soi l'information principale.

| Configuration | Jeu | 1re exécution (MyMemory répond) | 2e exécution (quota épuisé) |
|---|---|---|---|
| Extracteur d'origine + traduction | Français, 29 cas | 34,5 % (29 traductions sur 29) | 3,4 % (0 sur 29) |
| Extracteur d'origine + traduction | Français, contrôle | 29,2 % | 4,2 % |
| Pipeline corrigé + traduction systématique | Français, 29 cas | 79,3 % | 82,8 % (0 sur 29) |
| Pipeline corrigé + traduction systématique | Français, contrôle | 79,2 % | 83,3 % |
| Pipeline corrigé, réglages par défaut | Français, les deux jeux | — | 82,8 % et 83,3 %, aucun appel de traduction |

Les chiffres de la première exécution viennent de sa sortie console ; le fichier JSON enregistré est celui de la seconde.

Ce que cela établit :

- **Google Translate est resté bloqué** dans les deux exécutions (erreur 429 à chaque appel).
- **MyMemory n'est pas un secours fiable.** Il a traduit les 53 textes de la première exécution, puis a refusé tous les appels de la seconde, quelques dizaines de minutes plus tard : son quota gratuit est épuisé par une seule évaluation. Ses refus sont lents, de l'ordre de plusieurs secondes par appel.
- **La traduction seule ne suffit pas.** Quand elle fonctionne, le français plafonne à 34,5 %, sous l'anglais (44,8 %), parce que l'extracteur anglais reste le facteur limitant.
- **Ajouter la traduction au lexique n'aide pas.** Lors de la première exécution, la traduction systématique a fait gagner 4,5 points de rappel sur le jeu de contrôle, mais a fait basculer 2 cas corrects vers la mauvaise spécialité : « lower stomach » dans la traduction a déclenché `stomach_pain`, et une aménorrhée a été orientée en gastro-entérologie.

D'où le réglage retenu : pour le français, le lexique d'abord, la traduction seulement s'il ne trouve rien. Avec ce réglage, les 53 cas français sont traités sans un seul appel réseau, et les résultats sont identiques à ceux du tableau principal.

### 4.3 Les 8 cas sans prédiction

| Cas | Attendue | Anglais, après | Français, après |
|---|---|---|---|
| C02 | Allergologie | Toujours aucune | Allergologie ✓ |
| C03 | ORL | Toujours aucune | ORL ✓ |
| C13 | Ophtalmologie | Toujours aucune | Ophtalmologie ✓ |
| C15 | Rhumatologie | Toujours aucune | Orthopédie ✗ (rhumatologie 2e, à 0,2 point) |
| C18 | Endocrinologie | Toujours aucune | Endocrinologie ✓ |
| C20 | Urologie | Toujours aucune | Urologie ✓ |
| A05 | Orthopédie | Toujours aucune | Orthopédie ✓ |
| R02 | Médecine interne | Toujours aucune | Médecine interne ✓ |

- **En anglais, aucun n'est résolu.** Ces cas échouent parce que l'extracteur anglais ne reconnaît pas les formulations courantes ; aucune des trois corrections ne touche à cela. Ils renvoient maintenant le statut `no_symptom_found` au lieu d'une réponse vide sans explication.
- **En français, 7 sur 8 sont résolus**, parce que le lexique français couvre ces formulations. Le huitième (C15) reçoit une prédiction, mais l'orthopédie devance la rhumatologie de 0,2 point ; le Top 3 diversifié propose les deux.

### 4.4 Latence

Sans appel de traduction, 87 mesures par ligne (mesure faite à part, hors du fichier JSON).

| Chemin | Moyenne | P95 |
|---|---|---|
| Anglais | 85 ms | 155 ms |
| Français (lexique) | 19 ms | 42 ms |

Avant correction, chaque appel attendait environ une seconde l'échec de Google Translate.

## 5. Ce qui s'est dégradé ou reste insuffisant

1. **La négation supprime 2 vrais symptômes** sur les 29 cas français (rappel 0,986 → 0,973), sans changer aucune classification. Dans « je ne vois presque plus de cet œil et il me fait mal », la portée ouverte par « plus » traverse le « et » et masque la douleur de l'œil. Même mécanisme dans le cas C14. Aucun symptôme perdu en anglais ni sur le jeu de contrôle. En face, 9 symptômes niés ne sont plus extraits.
2. **La négation reste simple.** Elle ne traite ni la négation placée après le symptôme, ni les énumérations à virgules sous une seule négation, ni l'incertitude.
3. **Le Top 3 diversifié dilue les bonnes réponses.** Quand la première spécialité est la bonne mais que son score n'est pas écrasant, une place va à une autre spécialité : 5 cas sur 13 en anglais, 8 sur 24 en français. C'est le prix du rattrapage ; la règle le limite aux classifications indécises.
4. **Le lexique français a des trous**, visibles sur le jeu de contrôle : 4 cas sur 24 mal orientés (H02, H03, H13, H24) et 30 % des symptômes attendus non reconnus. Exemples : « la voix cassée », « ça me fait mal quand j'avale », « l'œil tout rouge ». Dans H24, « la tête qui tourne » est prise pour un vertige rotatoire et envoie en ORL.
5. **Le lexique n'a pas été validé de façon indépendante.** Les deux jeux de test ont le même auteur que le lexique. Le jeu de contrôle corrige le biais de mémorisation, pas le biais de style.
6. **L'anglais n'est pas amélioré** au-delà de la négation et du Top 3.
7. **Les erreurs d'arbre demeurent en français** : « crampes » d'estomac envoie toujours en phlébologie (C08) quand le texte ne précise pas « crampes d'estomac ».

## 6. Ce qui reste à déployer

Rien n'a été poussé. Le Space déployé a été réinterrogé après les corrections : 26 réponses vides sur 29 en français, aucune réponse listant trois prestataires.

À pousser sur le Space `emergency-savior-output` :

- `src/negation.py` et `src/french_lexicon.py` (nouveaux) ;
- `src/extraction.py`, `src/filtering.py`, `src/pipeline.py`, `app.py` (modifiés).

Aucune nouvelle dépendance : `deep-translator` fournit déjà MyMemory.

Point d'attention : les services de traduction sont appelés sans délai maximal. Un texte français où le lexique ne trouve rien attend donc leur réponse, plusieurs secondes quand ils refusent.

À redéployer côté backend : `backend/main.py`, qui lit les nouveaux champs et reste compatible avec l'ancien format du Space. Le frontend n'affiche pas encore `top_providers` ni `extraction_status`.

## 7. Limites hors périmètre, toujours présentes

Non corrigées dans ce round, comme convenu.

- **Optimisation à l'envers.** Le classement des prestataires n'a pas été touché et se comporte exactement comme avant : multiplier par dix le coût d'un médecin améliore son rang dans 144 essais sur 504 (29 %), et un délai d'un an dans 214 sur 504 (42 %). Le Top 3 diversifié hérite de ce classement à l'intérieur de chaque spécialité.
- **Gravité.** Toujours un simple booléen `urgent`, qui double le score de la cardiologie. Aucun cas ne bascule à cause de lui en français après correction, parce que l'extraction fournit assez de symptômes ; la règle elle-même est inchangée.

## 8. Résumé pour les sections Experiments et Limitations

**Corrections évaluées.** À la suite de l'évaluation de bout en bout, trois défauts ont été corrigés et réévalués avec le même protocole : (i) le traitement du français, qui dépendait d'un service de traduction externe dont l'échec était silencieux ; (ii) l'absence de gestion de la négation dans l'extraction des symptômes ; (iii) un Top 3 de prestataires tiré exclusivement de la première spécialité prédite.

**Méthode.** (i) Un lexique français associe des expressions courantes à 271 des 300 symptômes du système et permet l'extraction sans traduction ; la traduction n'intervient plus qu'en secours, et un statut distingue désormais « aucun symptôme » de « texte non analysé ». (ii) Une détection de portée de négation de type NegEx (fenêtre de 5 mots, fermée par la ponctuation et les conjonctions d'opposition) masque les symptômes niés en français et en anglais. (iii) Les trois places de recommandation sont réparties entre les spécialités candidates en proportion de leur score de classification (plus fort reste), le premier prestataire restant inchangé. Le lexique ayant été rédigé après lecture du jeu de test initial, un jeu de contrôle de 24 cas français a été écrit après son gel et n'a servi à aucun ajustement.

**Résultats.**

| Mesure | Avant | Après |
|---|---|---|
| Top-1, français, jeu de contrôle (24 cas) | 4,2 % | 83,3 % |
| Top-1, français, jeu initial (29 cas) | 3,4 % | 82,8 % |
| Top-1, anglais (29 cas) | 44,8 % | 44,8 % |
| Spécialité attendue parmi les 3 prestataires, anglais | 44,8 % | 58,6 % |
| Spécialité attendue parmi les 3 prestataires, français (contrôle) | 4,2 % | 100 % |
| Extraction, français (contrôle) : précision ; rappel | 1,000 ; 0,011 | 0,939 ; 0,697 |
| Extraction, anglais : précision ; rappel | 0,755 ; 0,274 | 0,800 ; 0,274 |

Sur le jeu de contrôle, le rappel d'extraction (0,70) est nettement inférieur à celui du jeu initial (0,97), ce qui quantifie le biais du jeu initial ; l'exactitude de classification est en revanche la même dans les deux jeux. La gestion de la négation supprime tous les symptômes niés des cas de test (9 au total) et fait perdre 2 symptômes affirmés sur 146.

**Limites.** Les corrections sont évaluées dans le code local et ne sont pas déployées. Le lexique français et les deux jeux de test ont le même auteur ; une validation sur des transcriptions réelles de patients reste à faire. L'extraction en anglais n'est pas améliorée : son rappel reste de 0,27 et 8 cas sur 29 ne produisent toujours aucune prédiction. La diversification du Top 3 remplace un prestataire de la bonne spécialité par un prestataire d'une autre dans environ un tiers des cas correctement classés. Deux limites identifiées précédemment ne sont pas traitées : le classement des prestataires n'est pas monotone (dégrader un critère peut améliorer le rang), et la gravité se réduit à un indicateur binaire.
