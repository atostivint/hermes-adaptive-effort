> Historical report: names, paths, defects and deployment facts below describe the original evaluation. The current plugin is **hermes-adaptive-effort**. Use the [README](../README.md), [runtime contracts](CONTRACTS.md) and [current handoff](HANDOFF.md) for current guidance.

Voici la revue. Elle est le fruit d'une lecture croisée du code du plugin contre la source réellement installée (`/usr/local/lib/hermes-agent`, version annoncée `0.21.5+4540.gaf26aca`) et contre le plugin voisin installé `jev-approvals`. Toute ligne citée a été lue.

---

# Revue sévère — plugin Hermes `jev-auto-effort`

## 1. Verdict global

Le plugin est **bien défendu** (fail-open partout, aucun secret ni prompt persisté, états bornés) et peut être activé **sans risque de crash ni de fuite**. En revanche il est **fonctionnellement cassé sur deux points** : sur la route `codex_responses` il ne fait **rien** pour les sessions normales (bug d'extraction du prompt), et sur les routes à vocabulaire d'effort étroit (Kimi K3, Ox Alpha, GLM-5.2) il **écrit une valeur d'effort invalide qui provoque un 400** chez le provider — en contradiction directe avec son invariant central (« never an unsupported wire value », `effort.py:10`). Il ne faut **pas** l'activer en `auto`/`cache_safe` tant que ces deux bugs ne sont pas corrigés ; `off` et `recommend` sont sans danger.

---

## 2. Contrats hôte

Vérification de chaque hypothèse du plugin/README contre la source installée.

| # | Hypothèse | Statut | Preuve réelle |
|---|---|---|---|
| 1 | Le middleware s'enregistre sous le nom `llm_request` | ✅ | `hermes_cli/middleware.py:24-26` — `VALID_MIDDLEWARE = {tool_request, tool_execution, llm_request, llm_execution}` |
| 2 | Contexte passé au middleware : `session_id`, `turn_id`, `task_id`, `api_request_id`, `platform`, `model`, `provider`, `base_url`, `api_mode`, `api_call_count` | ✅ | `agent/turn_api_request.py:145-150` ; `request` est le payload post-preflight (`turn_api_request.py:151`) |
| 3 | Clé de contexte `_meta` | ❌ | `hermes_cli/middleware.py:44-46` n'ajoute que `telemetry_schema_version` et `middleware_schema_version`. `_meta` n'existe pas. Conséquence : toute lecture de `_meta` par le plugin serait `None` (il n'en lit pas, donc sans effet pratique). |
| 4 | Hooks `on_session_end`, `subagent_start`, `subagent_stop` valides | ✅ | `register_hook` (`hermes_cli/plugins.py:974`) les accepte ; émis par `tools/delegate_tool.py:291` et `tools/delegate_tool_results.py:365` |
| 5 | `subagent_start` fournit `parent_session_id`, `child_session_id`, `child_goal` | ✅ | `tools/delegate_tool.py:291` (le plugin les lit en `middleware.py:181-183`) |
| 6 | Ordre d'exécution : middleware **après** `preflight_kwargs`, **avant** le hook `pre_api_request` | ✅ | `agent/turn_api_request.py:122` (build) → `:127` (sanitize) → `:128-132` (preflight codex) → `:145-150` (middleware) → `:158` (hook) |
| 7 | Fail-open au niveau hôte | ✅ | `agent/turn_api_request.py:154-156` — `except Exception` restitue le payload d'origine |
| 8 | Champ réécrivable `extra_body["reasoning"] = {"enabled": bool, "effort": str}` | ✅ | `agent/transports/chat_completions.py:529` |
| 9 | Champ réécrivable `reasoning_effort` (top-level, Kimi/TokenHub/lmstudio) | ✅ | `agent/transports/chat_completions.py:496-505` |
| 10 | Champ réécrivable `reasoning.effort` (codex_responses) | ✅ | `agent/transports/codex.py:596` (via `_reasoning_fields`) |
| 11 | Route anthropic porte un effort réécrivable | ⚠️ | Aucun slot du plugin ne correspond : l'anthropic porte `output_config.effort` (Claude 4.6+) ou `thinking.budget_tokens` (legacy) — `agent/anthropic_adapter.py:583-607`. Le plugin est donc un no-op sur cette route (voir §6). |
| 12 | `clamp_effort(effort, supported, overrides=None)` | ✅ | `agent/reasoning_effort.py:120-122` — signature correcte ; les refs de ligne du README (47-75) sont obsolètes |
| 13 | `EFFORT_LADDER` = `("none","minimal","low","medium","high","xhigh","max")` | ❌ | `agent/reasoning_effort.py:25` : le tuple réel est `(..., "max", "ultra")`. Le README omet `ultra` (interne Hermes, aucun wire ne l'accepte) |
| 14 | `OPENAI_COMPAT_WIRE_EFFORTS` = `("low","medium","high","xhigh","max")` | ❌ | `agent/reasoning_effort.py:28` : réel = `("none","minimal","low","medium","high","xhigh","max")`. Le README omet `none` et `minimal` |
| 15 | `route_supported_efforts(provider, model)` = « vocabulaire par route » | ❌ | `agent/reasoning_effort.py:153-159` : c'est le **clamp d'entrée** — `codex_supported_efforts(model)` pour `openai-codex`, sinon **le vocabulaire large** `OPENAI_COMPAT_WIRE_EFFORTS` (docstring : « narrower providers clamp again downstream »). C'est la cause du bug §3.2 |
| 16 | Secrets résolus via le scope Hermes (`get_secret`) | ✅ | `agent/secret_scope.py` `get_secret` ; le plugin retombe sur `os.environ` (`jev_client.py`) |
| 17 | `utils.request_payload()` / cap 50 Mo sur le payload middleware | ❌ | **Aucune fonction** `request_payload` dans le chemin middleware. Le seul `def request_payload` de l'arbre est `tools/connectors/operation.py:240` (sans rapport). L'affirmation du README est fabriquée/obsolète |

Bilan : les contrats **exécutifs** (noms, signatures, ordre, fail-open, champs de requête) sont tous encore vrais. Ce qui est faux est la **documentation des vocabulaires d'effort** (13, 14, 15) — et c'est précisément ce qui rend le bug §3.2 réel.

---

## 3. Bugs et correctifs

### Critique

Aucun. Le fail-open est réel à deux niveaux (`middleware.py:493`, `turn_api_request.py:154-156`), rien n'écrit de champ inexistant (`_effort_slot` ne renvoie un slot que si le champ existe déjà — `middleware.py:420-452`), et les états sont bornés (`_touch` eviction LRU `middleware.py:139`, registry FIFO `middleware.py:181+`). Aucun crash, aucune perte de données, aucune fail-fermée.

### Majeur

**B1 — `codex_responses` : les sessions normales ne sont jamais classées (no-op silencieux).**
`middleware.py:558`
```python
prompt = child_goal if child_goal is not None else _first_user_text(request.get("messages"))
```
Sur `codex_responses`, le payload passé au middleware est **post-preflight** (`turn_api_request.py:128-132`), donc de forme Responses : `{model, instructions, input, reasoning, prompt_cache_key, ...}` — **sans `messages`** (le transport convertit `messages`→`input`/`instructions` dans `build_kwargs`, puis `_preflight_codex_api_kwargs` exige `instructions`+`input` et rejette les clés inconnues, `agent/codex_responses_adapter.py:937+`). `_first_user_text(None)` renvoie `None` (`middleware.py:403-404`), donc `middleware.py:559-561` marque l'état `"unsupported"` et retourne `None` **pour chaque tour**.
Scénario : toute session normale dont le modèle primaire est `openai-codex` (le défaut de délégation selon le README) ne voit **jamais** son effort réécrit. Seuls les subagents passent (via `child_goal`). C'est le cas d'usage principal qui est mort-né.
Correction : lire le prompt depuis la forme Responses, par ex. premier item `user` de `request["input"]` (chaque item est `{role, content:[{type,text}]}`), en repli sur `instructions` :
```python
def _first_responses_user_text(input_items):
    if not isinstance(input_items, list):
        return None
    for item in input_items:
        if item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str): return content
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") in ("input_text", "text"):
                        return part.get("text")
    return None
```
et `prompt = child_goal if child_goal is not None else _first_user_text(request.get("messages")) or _first_responses_user_text(request.get("input"))`.

**B2 — `map_effort` écrit une valeur d'effort invalide sur les routes étroites (HTTP 400 provider).**
`middleware.py:586` → `effort.py:60-77` → `supported_efforts` → `route_supported_efforts`.
Le plugin croit clamper sur « le vocabulaire de la route », mais `route_supported_efforts` renvoie le **vocabulaire large** pour tout non-codex (`reasoning_effort.py:159`). Or `clamp_effort` renvoie verbatim toute valeur déjà présente dans ce vocabulaire large :
- Kimi K3 (`moonshot`, `kimi-k3`) : vocabulaire réel `("low","high","max")` (`reasoning_effort.py:54`), `medium` doit être arrondi à `high` (`KIMI_K3_OVERRIDES`, ligne 56). Le plugin envoie `medium` → **400**.
- Ox Alpha : `("low","high","max")` (`reasoning_effort.py:60`), `medium` → 400.
- GLM-5.2 : `("high","max")` uniquement (`reasoning_effort.py:68`), donc `low` **et** `medium` → 400.

Scénario : provider Kimi K3 en mode `auto`, Jev classe `medium` (le résultat du milieu, très fréquent) → le plugin écrit `reasoning_effort = "medium"` → Moonshot répond 400 et le tour échoue. Le plugin **aggrave** la requête, il ne la laisse pas simplement inchangée. C'est une violation directe de `effort.py:10` et de la revendication README (« uniquement un champ déjà existant et vérifié »).
Correction : ne pas s'appuyer sur `route_supported_efforts` (clamp d'entrée) mais sur le **vocabulaire wire réel** : utiliser `kimi_supported_efforts(model)` pour `moonshot`, et plus généralement refuser de réécrire quand le vocabulaire réel de la route n'est pas le vocabulaire large — c.-à-d. n'autoriser la réécriture que pour `openai-codex` (via `codex_supported_efforts`) et les routes dont le vocabulaire est `OPENAI_COMPAT_WIRE_EFFORTS`. La variante minimale sûre :
```python
def map_effort(label, provider, model, supported=None):
    # ... validation label inchangée ...
    if (provider or "").strip().lower() == "moonshot":
        from agent.reasoning_effort import kimi_supported_efforts, KIMI_K3_OVERRIDES
        return clamp_effort(level, kimi_supported_efforts(model), KIMI_K3_OVERRIDES)
    return clamp_effort(level, supported_efforts(provider, model))
```
et, par prudence, un garde-fou : si `map_effort` ne peut pas **prouver** que la valeur est dans le vocabulaire wire réel, retourner `None` (no-op) plutôt que d'écrire.

### Mineur

**B3 — Le test anthropic « not cache safe » porte sur une route que le plugin ne peut pas réécrire.**
`cache_safety.py` déclare anthropic non cache-safe, et `test_cache_safety.py:138-162` vérifie le pinning en injectant une requête `extra_body.reasoning.effort` (forme chat_completions) sous `provider="anthropic", api_mode="anthropic_messages"`. Mais une vraie requête anthropic n'a **aucun** de ces champs (`output_config.effort`/`thinking.budget_tokens`, `anthropic_adapter.py:583-607`), donc `_effort_slot` renvoie `None` et le plugin ne réécrit jamais rien sur anthropic. Le test valide le pinning sur une forme de requête **impossible en production**. Conséquence : la préoccupation « cache hostile sur anthropic » est théorique — le plugin y est no-op de toute façon. À corriger en documentant que anthropic n'est pas réécrivable, ou en supprimant cette entrée du tableau.

**B4 — `api_call_count` est décrit comme mécanisme de dé-duplication, mais n'est pas lu.**
Le README et `test_turn_scope.py:114-127` affirment que `api_call_count` maintient un tour multi-appels sur une seule décision. En réalité la dé-duplication vient du **key de mémorisation par tour** (`_decision_key(session_id, turn_id)`, `middleware.py:76`) ; `api_call_count` est passé par l'hôte (`turn_api_request.py:149`) mais ignoré par le code. Sans conséquence fonctionnelle (le key tour fait le travail), mais la documentation trompe.

### Nit

**N1** — `session_state()` (`middleware.py:93-96`) et `child_goals()` exposent `entry["target"]`/`label`/`score`, jamais le prompt ; correct. Mais `entry` est muté hors verrou après `_touch` (course bénigne : la mutation tombe sur un dict orphelin si l'entry est évincée LRU entre-temps). Aucun impact observable.

**N2** — Le test d'intégration passe `api_mode="chat"` (`test_dispatcher_integration.py:128,188`), qui n'est pas une valeur d'`api_mode` valide dans Hermes (`agent_init.py` `_EXPLICIT_API_MODES` = `chat_completions`, `codex_responses`, …). Inoffensif en mode `auto`, mais fausserait un test en `cache_safe` (où `api_mode` est discriminé).

**N3** — `_read_setting` relit la config (donc le fichier YAML) à chaque requête ; coût d'I/O par requête LLM en mode actif. Acceptable (édition à chaud) mais à mentionner.

---

## 4. Sécurité

**Résolution de la clé `TYPESAFE_API_KEY`** — correcte. Le client résout via le scope Hermes (`get_secret(SENTINEL_ENV, "")`), puis retombe sur `os.environ` (`jev_client.py`). La clé est présente dans `~/.hermes/.env` (vérifié par nom uniquement, valeur jamais lue). Le client ne construit **aucune** requête sans clé (`test_jev_client.py:95-99`). La clé n'apparaît que dans l'en-tête `Authorization: Bearer …` et n'est **jamais** loggée : les `logger.debug` ne portent que temps écoulé, statut HTTP et code de panne (`jev_client.py`), jamais le score ni le prompt.

**Transport** — `urllib.request.urlopen(request, timeout=…)` (`jev_client.py`) :
- TLS vérifié : `urlopen` avec contexte par défaut valide les certificats (pas de `ssl._create_unverified_context`).
- Timeout présent (3 s par défaut, testé `test_jev_client.py:102-106`).
- **Taille de réponse non bornée** : `json.load(resp)` lit le corps **entier** sans cap (`jev_client.py`). Le prompt émis est plafonné (`truncate_prompt`, `max_prompt_chars=4000`, testé `test_jev_client.py:124-129`), mais pas la réponse d'un endpoint compromis. Mineur : ajouter un `resp.read(MAX_RESPONSE_BYTES)` borné.
- **SSRF / URL injectable** : `endpoint` est un réglage libre (`settings.endpoint`, passé tel quel à `urllib.request.Request(self.endpoint, …)`). L'en-tête `Authorization` part donc vers **l'URL configurée**. Ce n'est exploitable que par qui peut écrire `plugins.entries.jev-auto-effort.settings.endpoint` (config locale), donc risque faible ; le README le signale d'ailleurs en question ouverte. À figer sur `https://api.typesafe.ai/v1` si on veut couper court.

**Ce qui est loggé** — uniquement durée/statut/raison d'échec. **Ce qui est persisté** — rien : `_SESSIONS`, `_CHILD_GOALS`, `_IN_FLIGHT` sont en mémoire, `session_state()` n'expose que des métadonnées d'effort (`middleware.py:93-96`), `child_goals()` n'expose que le `child_goal` (le but écrit par le parent, jamais le prompt de l'enfant — testé `test_subagent.py:259-266`). Le texte complet du prompt n'est **jamais** stocké au-delà de la durée de l'appel `run_probe`.

**Fail-open du chemin de classification** — tout échec (timeout, DNS, JSON invalide, HTTP≠200, score hors borne, `bool`/`NaN`/`str`) renvoie `None` et le middleware laisse le payload inchangé (`test_jev_client.py:66-99`). La gestion `bool`/`NaN`/`inf`/`str`/hors-`[0,2]` est correcte (`effort.py:26-28`, `jev_client.py`).

Verdict sécurité : **aucune fuite ni vulnérabilité exploitable à distance**. Deux durcissements mineurs : borner la taille de réponse, figer l'endpoint.

---

## 5. Tests

**Ce qui est réellement couvert** (bien couvert, de bonne qualité) :
- Mappage score→label : bornes `0.5`/`1.5`, invalides (`bool`, `NaN`, `inf`, `str`, hors-borne) — `test_effort.py:14-45`.
- Client Jev : réponses valides/malformées, timeout, exception transport, clé manquante (zéro requête), truncature du prompt, forme de requête TypeSafe — `test_jev_client.py`.
- Middleware : fail-open, non-mutation de l'appelant, `_effort_slot` (désactivé = pas de réécriture), modes `off`/`auto`/`cache_safe`, pinning session sur route non sûre — `test_middleware.py`, `test_cache_safety.py`.
- Subagents : registry bornée FIFO, `child_goal` verbatim, `subagent_mode` par défaut `off`, `stop` oublie l'enfant — `test_subagent.py`.
- Portée par tour : ré-classification au 2ᵉ tour, boucle outil = 1 appel, `turn_id` manquant → repli session — `test_turn_scope.py`.
- Intégration **réelle** via `apply_llm_request_middleware` (découverte PluginManager + non-réseau) — `test_dispatcher_integration.py:115-197`.

**Ce qui n'est pas couvert** (et qui masque les bugs) :
- **Aucun test ne passe une forme `codex_responses` réelle (`input`/`instructions`, sans `messages`)**. Tous les fixtures codex inventent `{"messages": …, "reasoning": {…}}` (`test_cache_safety.py:75-78`, `test_subagent.py:82-93`, `test_turn_scope.py:139-141`) — une forme qui n'existe pas en production. C'est précisément pourquoi **B1** n'est jamais détecté.
- **Aucun test ne vérifie le vocabulaire d'un provider étroit** (Kimi K3, Ox Alpha, GLM-5.2). `test_effort.py:32-38` ne teste que `openrouter` + un `supported` injecté à la main. **B2** n'est jamais détecté.

**Les 5 tests manquants les plus importants** (chacun casse un bug réel) :
1. `test_codex_responses_normal_session_classifies` — passer `request={"model":…, "input":[{"role":"user","content":[…]}] , "instructions":…, "reasoning":{"effort":"medium","summary":"auto"}}`, `api_mode="codex_responses"`, **sans** `messages` ni `child_goal` ; asserter que l'effort est réécrit. → casse **B1**.
2. `test_map_effort_kimi_k3_never_writes_medium` — `map_effort("medium", "moonshot", "kimi-k3")` doit renvoyer `"high"` (ou `None`), jamais `"medium"`. → casse **B2**.
3. `test_map_effort_glm52_rejects_low_and_medium` — `("low"|"medium", "zai", "glm-5.2")` → `None` (ou clamp valide). → casse **B2**.
4. `test_anthropic_request_has_no_writable_slot` — une vraie requête anthropic (`output_config`/`thinking`, pas `extra_body.reasoning`) doit donner `_effort_slot(...) is None`. → documente **B3**.
5. `test_response_body_size_is_bounded` — un transport qui renvoie un corps de plusieurs Mo doit être tronqué/refusé, pas lu en entier. → casse le point « taille de réponse non bornée » de §4.

---

## 6. Risque de cache de prompt

La revendication README (`cache_safe` = l'effort est un **champ de requête**, jamais du texte de prompt) **tient** sur `codex_responses` et `chat_completions`, mais est **moot** sur `anthropic_messages` :

- **`codex_responses`** — ✅ la revendication tient. `prompt_cache_key` est calculé sur `instructions` + outils + scope, **pas** sur `reasoning.effort` (`agent/transports/codex.py:735`, `_content_cache_key(instructions, response_tools, _cache_scope)`). L'effort est un champ `reasoning.effort` top-level (`codex.py:596`). Donc changer l'effort d'un tour à l'autre ne modifie pas le préfixe → **per-turn routing gratuit**. (À nuancer : le bug **B1** fait que, précisément sur cette route, rien n'est jamais routé pour les sessions normales — la propriété « cache-safe » est donc vraie mais inopérante aujourd'hui.)
- **`chat_completions`** — ✅ tient. L'effort est dans `extra_body.reasoning.effort` (`chat_completions.py:529`), c'est-à-dire un paramètre de corps hors du préfixe de messages ; le cache de préfixe (OpenRouter/OpenAI-compat) est indexé sur le préfixe des messages, pas sur ces paramètres. Le routing per-turn ne casse pas le cache.
- **`anthropic_messages`** — ⚠️ la conclusion est correcte mais le mécanisme cité est périmé. L'anthropic rend la config de thinking dans la requête : `output_config.effort` (Claude 4.6+) ou `thinking.budget_tokens` (legacy), `agent/anthropic_adapter.py:583-607` — pas `thinking.budget_tokens` seul comme l'écrit le README. Surtout, le plugin n'a **aucun** slot réécrivable sur cette route (voir **B3**), donc le mode `cache_safe` y est un no-op de toute façon.

En résumé : la distinction de route du README est **correcte dans sa conclusion** (codex/chat-completions = cache-safe, anthropic = non), mais l'écart entre la doc et le code réel du transport anthropic est réel, et la route anthropic est de toute façon non couverte par le plugin.

---

## 7. Verdict d'activation

| Mode | Risque | Verdict |
|---|---|---|
| `off` | Nul : zéro appel Jev, zéro réécriture (`middleware.py:507`) | ✅ Activable |
| `recommend` | Négligeable : un appel Jev/tour + annotation d'état, **jamais** de mutation du payload (`test_subagent.py:192-199`) | ✅ Activable |
| `auto` | Réel : **B2** → 400 sur Kimi K3 / Ox Alpha / GLM-5.2 ; **B1** → inopérant sur `codex_responses` pour les sessions normales | ❌ À corriger d'abord |
| `cache_safe` | Idem `auto` (le pinning n'empêche pas le **premier** tour de 400 sur route étroite) + pinning sur routes que le plugin ne réécrit pas | ❌ À corriger d'abord |

**Ce que j'installerais** : `recommend` aujourd'hui (pour observer les décisions Jev en toute sécurité), puis basculer sur `auto` **uniquement après** la correction de **B1** et **B2** et l'ajout des 5 tests ci-dessus. `cache_safe` n'apporte rien tant que **B1** n'est pas réglé (sur `codex_responses`, la route « cache-safe » par excellence est celle où le plugin ne fait rien).

---

*Note de méthode : je n'ai exécuté aucun test, rien installé, rien modifié — lecture seule, comme demandé. Les écarts de version (vocabulaires d'effort, `_meta`, `request_payload`) viennent du fait que le README documente une version de `agent/reasoning_effort.py` antérieure à celle installée.*
