# Registry - hermes-adaptive-effort

Une ligne par domaine. `session` est le `sessionID` renvoyé par l'outil subagent ; le repasser
continue ce domaine au lieu de repartir de zéro. `files in play` est la surface de conflit actuelle.

| domain | session | last item | files in play | status |
|---|---|---|---|---|
|  |  |  |  |  |

## Modèles (à décider avec l'opérateur)
- Orchestrateur : triage, briefs, revue de preuve. Aucun fichier applicatif modifié.
- Workers : un par tâche indépendante, jamais deux sur le même fichier.
- Relance sur un autre modèle en cas de rate-limit ou de blocage.
