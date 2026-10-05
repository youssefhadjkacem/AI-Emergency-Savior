# Fichiers à déposer sur le Space `youssef0081/emergency-savior-output`

Second round de corrections : classement monotone des prestataires (poids 3 sur la note) et niveaux de gravité.

Ce dossier reproduit l'arborescence du Space. Dépose chaque fichier au même endroit sur le Space (onglet **Files** → **Add file** → **Upload files**), en un seul commit.

| Fichier | Emplacement sur le Space | État |
|---|---|---|
| `src/severity.py` | `src/severity.py` | **nouveau** |
| `src/nsga2.py` | `src/nsga2.py` | modifié |
| `src/filtering.py` | `src/filtering.py` | modifié |
| `src/k1_brain.py` | `src/k1_brain.py` | modifié |
| `src/pipeline.py` | `src/pipeline.py` | modifié |
| `app.py` | `app.py` (racine) | modifié |

Les six fichiers vont ensemble : `pipeline.py` importe `severity.py`, et `filtering.py` appelle `nsga2.py` avec de nouveaux arguments. En déposer une partie seulement ferait planter le Space au démarrage.

Aucune dépendance à ajouter dans `requirements.txt`.

`backend/hospital.py` et `backend/main.py` ne vont **pas** sur le Space : ils font partie du backend et sont livrés par le dépôt GitHub.

## Vérification après dépôt

Saisir dans le champ Symptômes du Space, avec la localisation `Tunis` :

```
Mon mari a une douleur très forte dans la poitrine, ça lui serre comme un étau et ça descend dans le bras gauche. Il transpire beaucoup.
```

Résultat attendu : une ligne `Gravité estimée : CRITICAL`, la cardiologie en première spécialité, et trois prestataires dont au moins deux à Tunis.

Ce dossier est une copie faite au moment de la préparation. Une fois le Space mis à jour, il peut être supprimé.
