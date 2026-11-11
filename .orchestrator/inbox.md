# Inbox - hermes-adaptive-effort

File de travail de l'orchestrateur. Une ligne par item ; `ready` = prêt à partir, `blocked` = attend
une info de l'opérateur, `in_progress` = un worker travaille dessus, `done` = mergé et vérifié.
Les décisions qui expliquent ces items sont dans `decisions.md`.

| id | item | domain | status | note |
|---|---|---|---|---|

<!-- Exemple de forme (commenté, à supprimer quand la file se remplit) :
| EX-1 | Vérifier que `visibility` de l'entrée de statut est bien dans `command._ENTRY_FIELDS` | plugin-core | ready | brief : GOAL = confirmer l'allowlist, SCOPE = `command.py` + `tests/test_command.py`, PROOF = `run_tests.ps1 -q` + `-k entry_fields`, STOP = aucun changement hors de ces deux fichiers |
-->
