# Passation — carte `t_cb5d47d0` (plugin `jev-auto-effort`)

Document de référence du README : preuves, commandes, matrice de routes, rechargement et rollback.
Rédigé par Iris à partir de la passation de la carte et des vérifications refaites sur cette machine.

## Provenance

- Carte : `t_cb5d47d0` (tableau `recherche-emploi`), qui reprend `t_99de4875`, elle-même issue de la revue indépendante `t_c40555de`.
- Deux tentatives ont été tuées par le plafond de **60 itérations** de l'agent (`Iteration budget exhausted (60/60)`), et non par une erreur de code :
  - run 21 → 1165 s ; run 22 → 2169 s ; `budget_used 60 / budget_max 60` alors que `max_runtime_seconds = 3600`.
  - Les notifications émises disent « spawn failures » et « max_runtime=0s » : **aucune des deux ne décrit l'événement réel**.
- Le travail du run 22 a été préservé sur la branche `wip/kanban-t_cb5d47d0-run22` (commit `82d201a`), puis vérifié et fusionné par le run 24 (carte re-cadrée sur « vérifier + fusionner », cf. § Critères).

## État du dépôt

| Élément | Valeur |
|---|---|
| Dépôt | `/root/workspace/Hermes/jev-auto-effort-plugin` — aucun remote, rien n'a été poussé |
| `master` | `82d201a45ea7920d748f4a8cc99d7a0db8ec77c1` (fast-forward depuis l'ancien tip `7ca51bd`) |
| Branche `wip/kanban-t_cb5d47d0-run22` | conservée, pointe sur le même commit |
| Arbre de travail | propre (`git status --short` vide) |

Le message du commit `82d201a` (« wip … parked at 60/60 iterations ») décrit son origine, pas son contenu : il a été écrit au moment du ramassage de la tentative tuée, avant vérification. Il n'a pas été réécrit, car ce SHA est référencé par la carte et par ce document.

## Vérifier sur cette machine

```bash
cd /root/workspace/Hermes/jev-auto-effort-plugin
./scripts/bootstrap_test_env.sh -q     # recrée .venv puis lance la suite
./scripts/run_tests.sh -q              # 127 passed
./scripts/run_lint.sh                  # All checks passed!  (ruff 0.16.9)
./.venv/bin/python -m pytest tests/test_dispatcher_integration.py -rs -q   # 6 passed, aucun skip
```

Sorties observées : `127 passed` en 3,7 s à froid / ~20 s sous charge, `All checks passed!`.

Le venv est créé avec `--system-site-packages` : ce n'est pas un détail, le test d'intégration importe l'arbre Hermes installé (`hermes_cli.plugins`, `hermes_cli.config`, …), dont les dépendances tierces viennent de l'interpréteur qui les fournit.

## Critères (les 10 de `t_99de4875`)

| # | Critère | Verdict |
|---|---|---|
| 1 | README | **Fermé** — réécrit sur les faits vérifiés ; « 35 passed » disparu, compte 127 vérifié |
| 2 | Commande `/jev-auto-effort` | **Fermé** — 4 verbes `off\|recommend\|auto\|cache_safe` via `set_mode_override()` (override en mémoire, aucun fichier écrit) ; `/jev-auto-effort setup` mort supprimé ; 10 tests |
| 3 | Cache de décision | **Fermé** — entrée porte `provider` + `model` ; à la réutilisation, `_target_for_route()` re-clampe le LABEL sur la route courante ; route incapable d'exprimer le label → `unsupported`, rien n'est écrit |
| 4 | Suite verte en environnement capable | **Fermé** — les 2 tests rouges réparés à la racine (seam `_config_reader` + fixture autouse `hermetic_plugin_settings`), plus aucune lecture de `~/.hermes/config.yaml` par la suite |
| 5 | Intégration dispatcher | **Fermé** — 6 tests via `PluginManager.discover_and_load()` + `apply_llm_request_middleware` |
| 6 | Télémétrie | **Fermé** — `provider` + `model` dans `_touch()` / `_ENTRY_FIELDS`, rendus dans `status` et `status json` |
| 7 | Périmètre vivant | **Partiel — décision de l’opérateur** — en `HERMES_HOME` isolé c'est conforme, mais le profil vivant garde le plugin activé (`config.yaml` : `plugins.enabled`, `mode: auto`). Non touché ici |
| 8 | Risque résiduel Ox Alpha | **Partiel — documenté** — voir § Risques |
| 9 | Outillage | **Fermé** — `no_network` autouse et de portée session ; `run_lint.sh` + `pyproject.toml` (ruff, règles E/F/W/B) ; source committé |
| 10 | Passation | **Fermé** — ce document |

## Matrice route / vocabulaire d'effort

| Route modèle | Vocabulaire accepté |
|---|---|
| `kimi-k3*`, `k3`, `k3-256k`, `moonshot*` | `low \| high \| max` (`medium` → `high`) |
| `glm-5.2*` | `high \| max` |
| `glm-5.3*` | `low \| medium \| high \| max` |
| `openai-codex` | vocabulaire Codex du cœur |
| toute autre route | `route_supported_efforts()` |
| `x-preview-f-free` (Ox Alpha) | **non couvert** — risque 400 sur `medium` |

`wire_efforts()` / `wire_overrides()` couvrent Kimi K3/K2 et GLM-5.2/5.3, avec re-clamp à chaque requête.

## Commandes et cycle de vie

```text
/jev-auto-effort status | status json | probe <texte> | off | recommend | auto | cache_safe
```

- Un mode posé au chat vit dans le **processus**. Pour qu'il survive à un redémarrage : `plugins.entries.jev-auto-effort.settings.mode`, puis redémarrage.
- **Il n'existe pas de `hermes plugins reload`** (vérifié : `hermes plugins --help` n'expose que install/search/browse/validate/update/adopt/remove/list/enable/disable/capabilities/doctor/pack/show). Les plugins sont découverts au **démarrage du processus** : `hermes gateway restart`.
- `mode_source` (config vs override) est exposé dans `status`, donc l'origine du mode est lisible.

## Déploiement (non fait) et rollback

Le dépôt et le runtime divergent volontairement. `diff -r` entre la copie vivante `/root/.hermes/plugins/jev-auto-effort` et `master` : **identique sauf `__init__.py`, `command.py`, `middleware.py`**. Il manque donc au runtime les verbes de mode, la télémétrie `provider`/`model` et le re-clamp par route.

La mise en service n'a pas été faite : le plugin route encore les requêtes de l'opérateur ; la décision de mise en service lui revient.

- **Rollback dépôt** : `git reset --hard 7ca51bd` (l'ancien tip, commit intact).
- **Rollback vivant** : restaurer la copie de `plugins/jev-auto-effort` prise **avant** le déploiement, puis `hermes gateway restart`.

## Risques et points ouverts

- **Ox Alpha (`x-preview-f-free`)** : le cœur n'est pas sans détecteur — `agent/reasoning_effort.py:219-226` (`ox_alpha_reasoning_extras`) compare bien ce slug et clampe sur `OX_ALPHA_EFFORTS`/`OVERRIDES`. Ce qui manque est un accesseur réutilisable côté plugin. Le correctif (miroir du slug) est faisable côté plugin ; il n'a pas été fait. **Conséquence : ne pas activer `auto` ni `cache_safe` sur ce slug.**
- **Invalidation du cache entre deux tours** : structurelle via la clé `(session_id, turn_id)`, mais aucun test ne change de modèle *entre deux tours* — le cas n'est donc pas exercé. Un changement de modèle *dans* le tour re-dérive la valeur wire sans second appel Jev.
- **Nit** : les tests d'intégration passent `api_mode="chat"`, qui n'est pas une valeur d'`api_mode` d'Hermes (`_EXPLICIT_API_MODES = chat_completions | codex_responses | anthropic_messages`). Inoffensif ici (aucun de ces tests n'utilise `cache_safe`) mais la discrimination de route réelle n'est pas exercée.
- **Nit** : dans la table des routes du README, la ligne `kimi-k3*, k3, k3-256k, moonshot*` écrase le cas K2-era ; le code distingue correctement (`kimi_supported_efforts` : `kimi-k2.6` garde `medium`, test à l'appui).
- **Plafond de 60 itérations** : deux tentatives sont mortes en pleine écriture de code. Tant que `agent.max_turns` reste vide dans la configuration, tout travail de cette ampleur sera tronqué de la même façon — et compté comme un échec, deux échecs parquant la carte.
