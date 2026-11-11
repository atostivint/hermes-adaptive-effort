# Espace orchestrateur - hermes-adaptive-effort

Ce dossier est la mémoire de l'orchestrateur. Il est versionné, donc il survit aux sessions, aux
compactions et aux worktrees.

| Fichier | Rôle |
|---|---|
| `POLICY.md` | Règles dures, contrat de preuve, périmètre, routage, format de réponse. Lu au triage. |
| `registry.md` | Domaine -> session worker, fichiers en jeu, statut. Mémoire long terme. |
| `inbox.md` | Items à trier et leur état (`ready`, `blocked`, `in_progress`, `done`). |
| `decisions.md` | Arbitrages datés de l'opérateur : une ligne par décision. |

Les notes de domaine vivent dans `.opencode/domains/`.

## Comment lire ces fichiers
1. `POLICY.md` d'abord : c'est le contrat. Un brief qui le contredit est refusé.
2. `inbox.md` ensuite : on prend l'item `ready` le plus haut, ou celui que l'opérateur nomme.
3. `registry.md` : la ligne du domaine donne la session à reprendre. Repasser la `sessionID` au
   subagent évite de repartir de zéro ; une session morte se recrée (`session: new`).
4. `decisions.md` : pourquoi les choses sont comme elles sont. On n'efface pas une décision, on la
   barre et on ajoute la nouvelle, datée.

## Ordre de démarrage d'une session
1. Ouvrir OpenCode à la racine `jev-auto-effort`.
2. Lire `AGENTS.md`, puis `.orchestrator/POLICY.md`, puis `.orchestrator/inbox.md`.
3. Vérifier `git status --short` et `git worktree list` : l'état réel prime sur la mémoire.
4. Trier l'item, écrire le brief, lancer **un** worker par tâche indépendante.
5. Vérifier le diff avant de lire le rapport, puis mettre à jour `registry.md` et la note de domaine.

## La boucle
1. `/triage <item>` ou « prends le prochain item de `inbox.md` ».
2. L'orchestrateur classe l'item contre `.opencode/domains/`, vérifie les worktrees, écrit le brief
   (GOAL / SCOPE / ACCEPTANCE / PROOF / STOP).
3. Un worker par tâche indépendante, en parallèle si les fichiers sont disjoints.
4. Pas de bloc PROOF -> renvoi une fois -> sinon clôture en échec.
5. Revue en une passe sur le diff.
6. Mise à jour de `registry.md`, de la note de domaine et du statut dans `inbox.md`.

## Ce qu'il ne faut pas faire
- Laisser l'orchestrateur modifier du code : il n'écrit que dans `.orchestrator/` et `.opencode/domains/`.
- Deux items en parallèle sur un fichier partagé (`middleware.py`, `effort.py`, `command.py`,
  `plugin.yaml`, `tests/conftest.py`, `CHANGELOG.md`).
- Dupliquer un travail déjà présent sur une branche `worktree-agent-*`.
- Committer, pousser ou toucher `~/.hermes/config.yaml` sans demande explicite.

## Passer en travail direct
Le sélecteur d'agent d'OpenCode (Tab) ramène à un agent de code classique, en respectant `AGENTS.md`.
La configuration d'orchestration reste en place pour le triage suivant.
