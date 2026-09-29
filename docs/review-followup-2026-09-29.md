# Jev-Auto Effort — suivi de la revue DeepSeek (2026-09-29)

Revue complète : [`review-deepseek-2026-09-29.md`](review-deepseek-2026-09-29.md)
(modèle `deepseek-v4-pro` via `opencode-go`, lecture seule, aucune exécution).

## Ce qui a été corrigé ici

**B1 — `codex_responses` : aucune session normale n'était classée (no-op silencieux).**

Sur cette route, le middleware voit le payload *après* `preflight_kwargs()` :
`messages` a été remplacé par `input` (`agent/codex_responses_adapter.py`,
`_preflight_codex_api_kwargs`). `_first_user_text(request["messages"])` renvoyait
donc toujours `None`, et seuls les subagents (classés depuis le but écrit par le
parent) étaient routés.

- `middleware.py` : nouveau `_first_responses_text()` (lit `input`, contenu `str`
  ou liste de blocs `input_text`) et `_first_user_prompt()` qui essaie `messages`
  puis `input`. Le site d'appel utilise `_first_user_prompt(request)`.

**B2 — `map_effort` écrivait une valeur d'effort invalide sur les routes étroites (HTTP 400).**

`route_supported_efforts()` est le clamp **d'entrée** d'Hermes : hors Codex il
renvoie le vocabulaire OpenAI-compatible le plus large, en supposant que le
transport reclampe en aval. Le middleware d'un plugin tourne **après** ce clamp
transport : écrire `medium` là où le fournisseur ne connaît que
`low/high/max` fait échouer la requête.

- `effort.py` : nouveau `wire_efforts()` (vocabulaire réel Moonshot/Kimi et
  GLM-5.2/5.3, réutilise `kimi_supported_efforts` de l'hôte) et
  `wire_overrides()` (Kimi K3 `medium → high`, GLM `xhigh → max`). `map_effort()`
  clampe désormais sur ce vocabulaire-là.

**Reste ouvert (impact mineur, non corrigé) :** Ox Alpha / `x-preview-f-free`
rejette aussi `medium` et l'hôte n'expose aucun détecteur pour ce slug — ne pas
activer `auto` sur ce modèle. Également non traités : B3, B4, N1–N3, et la taille
de réponse non bornée de `jev_client.py` (lire `resp.read(MAX_RESPONSE_BYTES)`).

## Tests

`tests/test_review_fixes.py` (8 cas, ajoutés) :

- B1 : payload Codex réel (`input`/`instructions`, sans `messages`) classé et
  réécrit ; contenu `str` lu ; `messages` prioritaire quand les deux existent.
- B2 : `kimi-k3` ne reçoit jamais `medium` (arrondi à `high`) ; `kimi-k2.6` garde
  `medium` ; `glm-5.2` plancher `high` ; routes larges inchangées (openrouter,
  `opencode-go` deepseek/mimo, `openai-codex`) ; `supported_efforts` rapporte le
  vocabulaire réel.

```
106 passed   # .venv/bin/python -m pytest tests -q
```

## État installé (vérifié le 2026-09-29)

| Élément | Valeur |
|---|---|
| Emplacement | `/root/.hermes/plugins/jev-auto-effort/` (copie octet pour octet du dépôt) |
| État | `enabled` dans `plugins.enabled`, rechargé dans la gateway en cours |
| `plugins.entries.jev-auto-effort.settings.mode` | `auto` (B1 et B2 corrigés) |
| `settings.endpoint` | `https://api.typesafe.ai/v1` (figé — point SSRF de §4 de la revue) |
| `settings.subagent_mode` | `off` (défaut) — les enfants Codex restent non routés |
| Clé | `TYPESAFE_API_KEY` (scope Hermes / `~/.hermes/.env`), jamais loggée |
| `hermes plugins doctor jev-auto-effort` | OK — 0 outil, 3 hooks, aucun avertissement |

Un seul appel Jev par session (mémoïsé), fail-open sur toute erreur. Les 20 tâches
cron qui tournent sur `gpt-6-luna` (codex) feront donc un appel Jev par exécution :
repasser `mode` à `recommend` si ce coût doit être supprimé.
