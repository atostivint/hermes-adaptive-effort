# Plan de validation réelle des modèles et du changement d'effort

Date : 2026-10-07, Europe/Paris. Statut : campagne partiellement exécutée; le rapport des appels et exclusions est dans [le dossier de campagne](../validation/runs/live-20261007/report.md).

## 1. Objectif et périmètre retenu

Vérifier que chaque modèle de réponse retenu peut recevoir les niveaux d'effort produits par Hermes Adaptive Effort, par le vrai chemin de requête Hermès, et terminer une réponse. La sélection comprend toutes les routes Go du registre, les routes OpenRouter configurées qui sont éligibles et tous les modèles OpenAI auxquels le compte est effectivement autorisé sur la route habituelle `openai-codex` / `codex_responses`. « Tous les modèles OpenAI » signifie le catalogue associé au compte courant, pas une liste globale ou une liste de secours. La première campagne porte sur la compatibilité et la transmission du contrôle. L'évaluation de la qualité des classificateurs est reportée à une seconde étape.

Les appels payants sont autorisés, dans deux enveloppes indépendantes confirmées par l'opérateur :

| Fournisseur | Plafond de campagne | Compteur concerné |
| --- | ---: | --- |
| OpenRouter | 2,00 USD | Coût imputé au compte pour les appels de la campagne |
| OpenCode Go | 3,00 USD | Consommation d'usage Go valorisée en dollars |
| OpenAI Codex/Responses via Hermès | Au plus **30 points de pourcentage supplémentaires** sur la fenêtre de 5 heures | Compteur Codex de 300 minutes, sur le même compte que la credential Hermès |
| OpenCode Zen et autres fournisseurs | Aucune enveloppe affectée | Inventaire et vérifications locales ; appels réels hors de cette première campagne |

Le plafond Codex est une limite d'usage, pas un budget monétaire : aucun montant en dollars n'est affecté à OpenAI. L'opérateur a autorisé la campagne alors que le compteur affichait 31 % sur 300 minutes; Hermès et l'application Codex ont confirmé le même compte. Le plafond dur est donc 61 %. Pour absorber l'arrondi et l'activité concurrente du compte, l'orchestrateur s'arrête préventivement à 60 %. Les appels de cette campagne ont été envoyés séquentiellement et chaque envoi a été encadré par des lectures du compteur. Le dernier relevé disponible est à 49 %, soit +18 points depuis l'autorisation. Cette hausse comprend l'ensemble des usages Codex de la session et ne peut pas être attribuée entièrement aux appels testés. Aucun nouvel appel de génération n'est prévu dans cette campagne.

La première étape utilise un classificateur de test déterministe, injecté uniquement dans les processus de validation. Ses scores `0`, `1`, `2` exercent les décisions `low`, `medium`, `high` du plugin. Aucun appel à Jev, Decisions, Clef, OpenRouter comme classificateur, ou à un classificateur local n'est nécessaire à cette étape.

Le résultat attendu est une matrice qui distingue : champ transmis, niveau accepté, réponse terminée, variation effective entre niveaux et limites de correspondance. Une réponse réussie sans preuve du champ envoyé ne suffit pas à valider le routage. Un champ accepté ne prouve pas que le serveur applique un budget interne différent.

## 2. Éléments existants et limites de leurs preuves

Le [rapport réel du 3 octobre](../reviews/live-iris-use-cases-20261003.md) décrit des conversations Hermès avec Codex et DeepSeek, y compris une observation des champs HTTP. Ses anciens modes, noms et résultats restent historiques. Les captures et le lanceur liés par ce rapport ne sont pas présents dans ce checkout : il faut construire un lanceur maintenu, sans supposer que ces fichiers sont disponibles.

La suite actuelle interdit le réseau via `tests/conftest.py`. `tests/test_dispatcher_integration.py` charge réellement le plugin avec le gestionnaire Hermès et son dispatcher, mais emploie des requêtes et un classificateur simulés. Elle constitue une preuve locale du câblage, pas de l'acceptation des fournisseurs.

> 🧠 **From Hindsight memory (Conventions and patterns)** — Les erreurs de classification doivent laisser la requête intacte, et les sorties de diagnostic excluent le texte des prompts. Ces principes ont été vérifiés dans `middleware.py`, `command.py` et `tests/conftest.py` avant la rédaction du plan.

Les sources actuelles de sélection sont `effort.py`, `middleware._injection_path`, `middleware._dynamic_effort_route` et les champs réellement construits par les transports Hermès. Un profil dans `model_profiles.json` décrit un modèle ; il n'autorise ni injection de champ ni classification dynamique.

## 3. Inventaire exact à figer avant les appels

Le précontrôle produit un manifeste indiquant, pour chaque route : fournisseur, ID exact, API, champ présent avant le plugin, possibilité d'injection, prérequis de thinking, correspondance des trois décisions, tarifs datés et compteur de budget. Enregistrer le commit et les empreintes du plugin effectivement chargé, ainsi que la version/source Hermès. Distinguer un checkout modifié d'une version installée ; aucun déploiement implicite.

### OpenCode Go : base issue du registre actuel

Ces correspondances ont été calculées localement avec `effort.map_effort` et la source Hermès disponible pendant la préparation. Elles constituent un oracle local à confirmer sur le réseau, pas des résultats d'appels réels.

| ID exact | API | Contrôle préparé par Hermès / plugin | low → | medium → | high → |
| --- | --- | --- | --- | --- | --- |
| `gpt-6-luna` | Responses | `reasoning.effort` | low | medium | high |
| `gpt-5.6-luna` | Responses | `reasoning.effort` | low | medium | high |
| `grok-4.7` | Responses | `reasoning.effort` | low | medium | high |
| `grok-4.6` | Responses | `reasoning.effort` | low | medium | high |
| `grok-4.5` | Responses | `reasoning.effort` | low | medium | high |
| `muse-spark-1.3-contributor` | Responses | `reasoning.effort` | low | medium | high |
| `muse-spark-1.2-contributor` | Responses | `reasoning.effort` | low | medium | high |
| `glm-5.2` | Chat Completions | `reasoning_effort` | high | high | high |
| `glm-5.3` | Chat Completions | `reasoning_effort`, thinking déjà actif | low | high | high |
| `kimi-k3` | Chat Completions | `reasoning_effort` | low | high | high |
| `deepseek-v4-pro` | Chat Completions | `reasoning_effort`, thinking déjà actif | low | high | high |
| `deepseek-v4-flash` | Chat Completions | `reasoning_effort`, thinking déjà actif | low | high | high |
| `deepseek-v4.1-flash` | Chat Completions | `reasoning_effort`, thinking déjà actif | low | medium | high |

La base contient **13 routes**, soit **39 décisions locales** et **33 couples modèle/valeur envoyée distincts**. Les décisions qui produisent la même valeur sont toutes vérifiées localement ; un seul appel payant par valeur distincte suffit pour sa première vérification d'acceptation. Le premier appel de chaque modèle sert également de test de disponibilité.

Le manifeste revalide les IDs et leur disponibilité avant la campagne. Un modèle retiré ou inaccessible reste dans la matrice avec son motif. Ne pas le remplacer par un alias ou un autre fournisseur pour obtenir artificiellement un succès.

**Cas GLM-5.2 :** le vocabulaire local expose `high/max`, mais les trois décisions actuelles du plugin produisent `high`. La campagne peut vérifier la réécriture d'une valeur initiale `max` vers `high`, si Hermès construit ce contrôle, et l'acceptation de `high`. Elle ne peut pas certifier une modulation à plusieurs niveaux par le classificateur. Le rapport doit afficher cette limite. Toute évolution de cette politique sera une modification séparée, avec ses propres tests et documentation.

Pour Kimi K3, GLM-5.3 et certains DeepSeek, `medium → high` est une correspondance attendue. Les valeurs natives supplémentaires, comme `max`, ne deviennent pas des sorties testées du plugin si celui-ci ne les produit pas.

### OpenRouter : liste à construire à partir des routes configurées

Inventorier les IDs exacts des modèles de réponse configurés et leur prise en charge du paramètre d'effort, puis les croiser avec le transport réellement construit par Hermès. Inclure chaque route retenue avec un champ d'effort existant et utilisable. Ne pas étendre automatiquement la liste à tout le catalogue OpenRouter.

L'injection exacte OpenCode Go ne s'applique pas à OpenRouter. Un modèle présent dans les profils locaux ou partageant un nom avec un modèle Go n'est pas une preuve de support sur OpenRouter. `effort_models` reste inchangé ; aucune liste forcée n'est créée pour faire passer la campagne.

Les routes sans champ utilisable restent visibles comme « sans contrôle exploitable dans ce chemin ». Un éventuel succès de génération sur ces routes ne vaut pas validation d'effort. Le nombre d'appels OpenRouter sera calculé après cet inventaire, à raison d'un appel par valeur distincte produite et retenue.

### OpenAI Codex/Responses : catalogue du compte

La source d'inventaire est le catalogue Codex authentifié du compte utilisé par Hermès. Le cache local, le modèle par défaut de `config.toml`, la liste statique de secours et le catalogue public ne prouvent pas qu'un modèle est disponible pour ce compte. Le catalogue Hermès authentifié a été lu sans conserver de credential ni d'identifiant de compte; l'identité correspond au compteur Codex de l'application. Il expose sept IDs exacts : `gpt-6.1-sol`, `gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra` et `gpt-5.6-luna`. Fingerprinter la liste triée et datée dans le manifeste. Toute différence ultérieure de compte entre Hermès et le compteur Codex bloque les générations OpenAI.

Inclure un cas de disponibilité pour chaque ID exact retourné par ce catalogue et garder séparément son statut de contrôle d'effort. Vérifier la route effective `openai-codex` / `codex_responses`, le carrier réellement construit, les valeurs d'effort permises par la correspondance courante et la réponse terminée. La liste dynamique locale d'effort ne connaît actuellement que `openai-codex/gpt-6.1-sol`; ne pas étendre cette liste et ne pas modifier `effort_models` pour faire réussir les autres IDs. Si un modèle ne dispose pas d'un champ d'effort exploitable dans le chemin normal, il peut être rapporté comme disponible sans contrôle d'effort prouvé; ne pas fabriquer ou injecter un carrier sur cette route inconnue.

### Couverture hors enveloppes payantes

Recenser également les routes Zen exactes `muse-spark-1.3`, `muse-spark-1.2` et `muse-spark-1.3-contributor-free`. Vérifier leurs correspondances localement. Leur validation réseau reste explicitement non exécutée dans cette première campagne ; un succès sur Go, OpenRouter ou la route OpenAI Codex ne les valide pas.

## 4. Protection des deux budgets

Les seuils de travail gardent 10 % de marge : **1,80 USD OpenRouter** et **2,70 USD Go**; les plafonds opérateur restent 2,00 et 3,00 USD. Les deux enveloppes dollar sont indépendantes et aucun reliquat ne passe d'un fournisseur à un autre. Pour ces deux routes, une « réservation » est une borne comptable temporaire inscrite avant l'envoi afin que les engagements déjà pris et celui qui va partir restent sous le seuil. Cette inscription n'est pas un débit ni un achat. Elle n'est possible que si un maximum facturable est démontré.

Pour OpenRouter et OpenCode Go, avant chaque envoi, réserver une borne de coût de l'appel à partir des tarifs actuels, du prompt complet Hermès, de la limite totale de génération applicable à la route et des éventuels coûts de cache. Ne pas supposer un cache hit. La réservation comptable et les consommations déjà engagées doivent rester sous le seuil de travail. Après l'appel, rapprocher la réservation de l'usage réellement communiqué.

OpenRouter documente `usage.cost`, les compteurs de tokens et la récupération différée par ID de génération. Utiliser le coût imputé au compte ; une valeur absente reste inconnue, jamais zéro. La dernière trame du flux doit être traitée lorsque la réponse est diffusée en streaming. [Comptabilisation officielle OpenRouter](https://openrouter.ai/docs/cookbook/administration/usage-accounting).

Go exprime ses limites d'usage en dollars et publie les tarifs par modèle. Vérifier le compteur Go avant et après, et rapprocher les tokens avec les tarifs datés lorsque nécessaire. La documentation prévoit aussi un débordement vers le solde Zen si l'option `Use balance` est activée : aucun débit Zen n'entre dans l'enveloppe autorisée ici. Vérifier que la campagne ne peut pas provoquer ce débordement avant ses appels Go. [Documentation officielle Go](https://opencode.ai/docs/go/).

La limite de génération doit couvrir les tokens facturables de raisonnement lorsque la route en produit. Une consigne « réponds brièvement » et un timeout ne constituent pas une borne de coût. Si une route ne permet pas de borner l'appel ou si sa consommation demeure indéterminée, suspendre ses appels suivants et conserver le motif dans le rapport. Ne pas annoncer un plafond garanti sur la seule base d'une estimation moyenne.

Les appels restent séquentiels. Compter toutes les tentatives réellement envoyées, les retries internes et les requêtes auxiliaires ; désactiver retries et fallbacks automatiques lorsque l'hôte le permet. Le garde de dépense en dollars doit intervenir avant chaque envoi physique, pas seulement avant chaque scénario. Une requête interrompue conserve sa réservation comptable tant que son coût n'est pas rapproché.

Codex ne dispose pas de réservation en dollars dans cette campagne. Le plafond autorisé est mesuré sur le compteur du compte, séparément des deux enveloppes USD. Envoyer au plus une requête physique à la fois, avec une tâche synthétique minimale; vérifier et journaliser le compteur avant et après chaque requête, puis s'arrêter à 60 %. Désactiver les retries et les fallbacks quand Hermès le permet; autrement chaque envoi physique doit être observé et compté, et le cas s'arrête dès qu'un envoi supplémentaire ne peut pas être contrôlé. Cette méthode est un garde opérationnel par requête, pas une prédiction du pourcentage consommé par le modèle. Le lancement se fait en séquence et aucun lot préprogrammé ne part sans lecture du compteur intermédiaire. Le run doit tenir dans la même fenêtre continue de cinq heures; si le compteur ou la fenêtre devient ambigu, arrêter sans réinitialiser artificiellement la référence.

Ne pas modifier les réglages de facturation, recharger un compte, installer un fournisseur ou démarrer une campagne sur un autre compte. Si le chat Hermès qui pilote les tests utilise ces mêmes comptes, ses appels doivent aussi être pris en compte dans la dépense observée ; le lanceur n'utilise aucun modèle juge supplémentaire.

## 5. Parcours technique à construire

Le lanceur utilisera `run_agent.AIAgent` et le gestionnaire de plugins Hermès, puis le dispatcher et le transport normaux. Vérifier la signature de l'hôte utilisé au lancement : la source locale observée accepte notamment `reasoning_config`, `max_tokens`, `max_iterations`, `session_db`, `skip_context_files`, `skip_memory` et `skip_background_review`. Ces interfaces internes ne sont pas une API stable.

Chaque scénario s'exécute dans un sous-processus isolé avec un espace Hermès et une base de conversations temporaires. Une conversation à plusieurs tours reste dans le même sous-processus. Résoudre les credentials nécessaires par les mécanismes Hermès autorisés et ne transmettre au processus que les accès sélectionnés, en mémoire. Ne pas copier globalement `.env`, les configurations ou les magasins d'authentification dans les artefacts.

Après la découverte du plugin, identifier son module effectivement chargé et y injecter `_classifier_factory`. Cette substitution concerne le classificateur uniquement. Le transport du modèle de réponse reste réel. Le manifeste et chaque résultat indiquent `score_origin: controlled`; le nombre d'appels réseau de classification attendu est zéro.

La matrice principale utilise `mode=always`, `subagent_mode=off`, sans guidance de classification et avec `use_target_model_context=false`. Pour OpenRouter et Go, proposition de limites initiales : 1 024 tokens de génération si le plafond est applicable au total facturable, deux itérations au maximum et 60 secondes par scénario. Un diagnostic de troncature peut monter à 2 048 tokens uniquement si sa borne comptable tient dans le reliquat. Ces limites sont des paramètres du banc de test et ne modifient pas les défauts du plugin. Pour OpenAI Codex, les appels restent séquentiels et sont surveillés par le compteur de quota avant et après chaque requête, avec arrêt préventif à 60 %.

Désactiver mémoire externe, chargement des fichiers de contexte, revues de fond, sauvegarde des trajectoires et outils pour la matrice principale. Limiter les itérations et la durée de chaque scénario. Les réglages de test et les modes sont confinés à ces processus ; le gateway en service et la configuration opérateur ne sont pas modifiés.

Observer trois points : requête avant le plugin, résultat du middleware, corps JSON au dernier point d'envoi HTTP. Les carriers internes et HTTP peuvent différer : par exemple, le SDK peut fusionner `extra_body.reasoning` dans `reasoning` sur le réseau. L'observateur extrait immédiatement une liste autorisée de métadonnées ; il ne conserve jamais le corps complet, les headers, les prompts, les instructions de classification ou les secrets. Sur un transport non observable, le verdict reste « preuve réseau manquante ».

Le flux `/changes` confirme une réécriture retournée par le plugin, pas à lui seul l'envoi HTTP ou l'acceptation du fournisseur. Croiser sa transition avec les observations du scénario et la réponse. Ne pas ajouter d'identifiants de session à son schéma public pour faciliter la campagne.

Lire le statut et les changements dans l'instance de plugin du processus de test, via les fonctions qui alimentent les surfaces publiques. Le gateway opérateur utilise une autre instance et ne constitue pas un observateur de ces sessions isolées. Capturer les publications d'événements dans le banc pour éviter d'afficher des transitions synthétiques dans le Desktop opérateur. La validation visuelle du Desktop reste hors de cette matrice prioritaire.

## 6. Scénarios et ordre d'exécution

| Étape | Contenu | Réseau et dépense |
| --- | --- | --- |
| A. Inventaire | Routes exactes, accès présents sans valeurs de clés, versions, prérequis, prix et bornes de génération | Lectures nécessaires ; aucune génération |
| B. Contrats locaux | Scores `0/1/2`, clamping, carriers, injection, champs désactivés et invariants | Aucun réseau |
| C. Première passe | Une conversation courte par modèle OpenAI du catalogue du compte, puis par route Go et OpenRouter éligible | Vrai transport ; OpenRouter/Go sous leurs plafonds dollar, Codex sous son plafond de quota séparé |
| D. Variation | Autres valeurs distinctes ; priorité au deuxième niveau avant les niveaux intermédiaires | Vrai transport; appels séquentiels et arrêt avant les plafonds respectifs, compteur Codex contrôlé après chaque appel |
| E. Diagnostic ciblé | Contrôle ponctuel d'un échec ou d'une troncature seulement si la même enveloppe reste disponible | Dépense dollar rapprochée ou quota Codex autorisé séparément; aucune boucle de retry |
| F. Clôture | Rapprochement des coûts, matrice et recommandations de support | Aucun nouveau modèle juge |

### Contrats locaux à couvrir sans payer

Exercer les trois scores sur chaque route, puis les quatre modes. `always` classe chaque nouveau tour ; `once` conserve la décision par route ; `auto` suit le registre dynamique exact ; `off` ne classe pas et ne modifie pas la requête. Toutes les variantes actives réutilisent une décision pendant une boucle d'outils et re-clampent le label sur changement de route.

Vérifier également : conservation de la requête d'origine, autres paramètres inchangés, contrôle désactivé ou malformé respecté, aucun toggle de thinking ajouté, zéro appel au classificateur sur une route inéligible, comportement fail-open et absence de retry dans la portée retenue. Préserver les codes d'erreur actuels.

Les contrôles paired positifs partent d'un thinking déjà activé par la configuration de test Hermès. Le cas négatif sans ce prérequis doit rester sans injection. Les tests locaux utilisent le vrai gestionnaire et dispatcher Hermès ; un appel direct à la fonction du plugin ne suffit pas à valider leur intégration.

### Matrice réelle des modèles

Utiliser une tâche synthétique courte et identique pour les niveaux comparés, avec une réponse facilement vérifiable, par exemple une petite opération dont la réponse attendue est `4`. Demander une réponse brève, sans exiger un format structuré que certains modèles ne supporteraient pas. Le classificateur contrôlé permet de tester un effort élevé sur cette tâche courte.

Pour chaque valeur distincte à vérifier :

1. Préparer la route par les paramètres normaux Hermès et, pour OpenRouter/Go, réserver son coût maximal. Pour Codex, vérifier l'identité et le budget de quota avant tout envoi.
2. Observer le carrier avant le plugin. Lorsque possible, choisir une valeur initiale valide différente de la cible pour prouver une réécriture effective ; sinon identifier correctement une insertion ou un cas sans changement.
3. Injecter la décision de test, laisser le middleware s'appliquer, puis vérifier le champ du corps effectivement envoyé et la route effective.
4. Attendre une réponse terminée et vérifier la réponse courte. Une réponse tronquée ne vaut pas un succès de bout en bout, même si le champ était accepté.
5. Relever usage, coût ou réservation dollar non rapprochée, delta du compteur Codex, statut de la décision et événement éventuel. Le statut doit rester consultable après le tour ; finalisation et reset sont contrôlés localement.

Pour un modèle à plusieurs valeurs atteignables, comparer les valeurs réellement observées sur le réseau. Les niveaux intermédiaires qui se confondent avec un autre niveau sont déjà couverts par la correspondance locale. Ne pas déduire d'une différence de latence ou de longueur que le serveur respecte le contrôle : conserver séparément la preuve de transmission et la documentation du contrôle de la route.

Les premières passes couvrent tous les modèles disponibles avant d'approfondir un seul modèle. Une erreur d'accès, de quota ou un ID retiré évite de payer les autres niveaux de cette même route. Un échec sur un niveau reste visible même si un autre niveau fonctionne.

L'injection d'un champ absent est vérifiée localement pour chaque route autorisée. En conditions réelles, l'indiquer comme testée uniquement si la requête construite par Hermès était effectivement dépourvue du champ et que le plugin l'a ajouté. Ne pas fabriquer artificiellement un champ sur une route inconnue pour annoncer son support.

### Diagnostic borné

Une erreur `400` relative au contrôle est une incompatibilité à conserver, avec le niveau et la route exacte. Un `401/403`, un quota ou une restriction régionale signifie que l'accès n'a pas permis la validation, pas que tous les efforts sont incompatibles. Une erreur réseau laisse les niveaux concernés non vérifiés.

Pour une troncature OpenRouter/Go, un seul essai avec une limite de génération supérieure peut être planifié si sa réservation comptable tient dans l'enveloppe. Aucun diagnostic Codex ne peut augmenter la génération avant preuve d'une borne pré-envoi qui respecte le +30 points. Si le fournisseur refuse une valeur, ne pas la remplacer automatiquement pour transformer l'échec en réussite. Le cas Ox Alpha `medium → 400`, s'il est rencontré sur une route retenue, reste une limite connue distincte d'un succès. Aucun contournement de la correspondance n'est inclus dans ce chantier.

Les boucles d'outils et changements de route sont couverts localement en priorité. Une vérification réelle supplémentaire par famille de transport peut compléter la campagne après la matrice principale, uniquement dans le reliquat disponible. Utiliser alors un outil echo purement en mémoire ; ce complément ne doit pas consommer le budget nécessaire à un modèle encore non testé.

## 7. État des livrables

Le lanceur local est disponible dans `scripts/run_model_validation.py`; les tests hermétiques sont dans `tests/test_live_model_validation.py`. Les cas synthétiques versionnés, exemple de configuration sans secrets et modèle de rapport sont dans `docs/validation/`. L'inventaire local porte sur 13 routes Go, 39 correspondances de score et 33 couples modèle/valeur wire distincts. La campagne du 7 octobre a aussi vérifié les sept modèles du catalogue Codex authentifié. Son manifeste, le contrôle local, les résultats JSON expurgés et le rapport final sont dans [le dossier de campagne](../validation/runs/live-20261007/). Le rapport décrit les dix réponses terminées avec effort wire exact, les onze cas bloqués avant envoi, deux essais séparés de disponibilité, et les exclusions OpenRouter/Go. Aucun classificateur réel n'a été appelé. Le préflight antérieur du 7 octobre reste archivé dans [son rapport](../validation/runs/config-inventory-308131f421724ce88c893f365a91fd7b/report.md); il est historique et ne décrit pas la campagne présente.

Commandes disponibles (PowerShell) :

```powershell
.\.venv\Scripts\python.exe scripts\run_model_validation.py inventory --output PATH [--config PATH]
.\.venv\Scripts\python.exe scripts\run_model_validation.py check --manifest PATH [--output PATH]
.\.venv\Scripts\python.exe scripts\run_model_validation.py run --manifest PATH --ledger PATH --output DIR [--resume]
```

`inventory` et `check` sont locaux et sans génération. Le lanceur générique `run` reste volontairement bloqué pour OpenRouter et Go, car il ne possède pas de garde de coût pré-envoi ni de rapprochement des compteurs relié au transport. Les appels Codex ont été effectués avec la sonde Hermès séparée décrite dans le rapport. L'observateur a enregistré les champs autorisés du corps HTTP et une réponse terminée, sans conserver prompt, en-têtes ni corps de réponse. La sonde a confirmé que le plugin était chargé, mais n'a pas archivé l'empreinte ni le chemin exact de l'artefact chargé; cette provenance reste une limite. Des durcissements supplémentaires du garde de transport ont été apportés après les appels et vérifiés localement; aucun appel payant supplémentaire n'a été envoyé après ces changements.

### Frozen implementation contract (2026-10-07)

Ownership and gates: the GPT-6 Luna orchestrator owns this plan, integration, campaign execution, and every real target call. Worker A owns only `scripts/run_model_validation.py`; Worker B owns only `tests/test_live_model_validation.py` and uses the frozen interface below; Worker C owns only `docs/validation/**` and independently reviews the completed runner. All four agents use GPT-6 Luna. Up to three workers may run alongside the orchestrator, for at most four concurrent agents total. No worker receives provider credentials or may run paid calls. Every handoff reports changed files, commands/checks and results, unresolved issues, and acceptance status. The orchestrator reviews each diff against its ownership boundary. The user-authorized campaign proceeds only when every route satisfies its applicable usage/cost, reconciliation, observation, and isolation gates; otherwise it remains blocked.

### Frozen worker packets

| Agent | Objective and allowed files | Frozen interface / dependency | Verification and completion |
| --- | --- | --- | --- |
| Worker A — runner | Record the new Codex usage policy in the local manifest and check output, while keeping all execution zero-send and fail-closed. Edit only `scripts/run_model_validation.py`. | Preserve dollar keys `limits.openrouter` and `limits.opencode-go`; add `limits.openai-codex-usage = {"window_minutes": 300, "max_increase_percentage_points": 30, "api_mode": "codex_responses"}`. Keep Codex out of the dollar ledger. Add a redacted `inventory.openai_codex_catalog` status; with the current inaccessible catalog, use `status: unavailable`, no model IDs, and a normalized reason. Never substitute cached/default IDs. `check` must report the Codex +30-point policy and that live readiness is false. | Run Python compilation, focused existing tests, and Ruff. Confirm inventory/check make zero network requests and `run` still creates no send attempts. Complete when the manifest is self-consistent, OpenAI IDs are not guessed, and the standard Go/OpenRouter inventory is unchanged. |
| Worker B — tests | Independently test the Codex policy/inventory and ensure it cannot create a dollar ledger account or enable live execution. Edit only `tests/test_live_model_validation.py`. | Start from Worker A's frozen manifest/check fields above. No sockets or provider credentials; follow `tests/conftest.py` hermetic fixtures. | Run focused pytest and Ruff. Complete when tests assert the 300-minute/30-point policy, unavailable account catalog has no guessed models, the existing independent $2/$3 caps remain unchanged, and all paths remain zero-send. |
| Worker C — documentation | Update the example and report template to distinguish dollar reservations from Codex quota observations; add the current zero-send Codex audit facts. Edit only `docs/validation/**`. Independently inspect Worker A's finished runner. | Use the frozen limits above. Report the application meter as an observation only (31%/300 minutes on 2026-10-07); do not call it Hermès' baseline or expose account IDs. State the catalog/account mismatch and per-request quota bound as unresolved. | Check JSON examples parse and report-template links/fields match the runner. Complete when documents show independent limits, no OpenAI spend reservation, all account-catalog models as the intended scope, and a clear no-call blocker; include any runner review findings in handoff. |

The Luna orchestrator owns the shared specification, final integration, and all live calls. Three Luna workers own the runner, independent tests, and validation documentation respectively; they receive no provider credentials and do not send inference requests. The orchestrator freezes the manifest and reviews all handoffs before running. The account identity and exact catalog are now confirmed. Live calls are performed one at a time with a transport observer and quota/cost reconciliation; a route that cannot satisfy its own envelope remains non-verified.

The versioned JSON contracts are:

* Manifest `hermes-adaptive-effort.validation-manifest.v1`: `manifest_hash`, `campaign_id`, `created_at`, `source_commit`, `plugin_sha256`, `hermes_source`, `limits` (independent OpenRouter/Go dollar caps plus Codex `{window_minutes: 300, max_increase_percentage_points: 30}`), an authenticated account-catalog snapshot status/fingerprint, `cases` (stable `case_id`, provider, exact model, API, target effort, score, expected wire effort, response oracle, maximum output tokens, local mapping status, and live eligibility/reason), `score_origin: "controlled"`, and `real_scorer_calls: 0`. Currency fields serialize as canonical decimal strings; any arithmetic uses `Decimal`, never binary floats. Codex usage percentages are not currency and must never enter the dollar ledger. A blocked route has no price or maximum-cost claim. The validator re-fingerprints Hermes and compares it to the frozen manifest. `plugin_sha256` currently hashes local payload Python sources only; it does not prove which plugin artifact a runtime loaded, so a live gate must verify the loaded path/artifact separately. The hash is SHA-256 over canonical JSON excluding `manifest_hash`. Case IDs hash the canonical tuple `(provider, exact_model, api_mode, target_effort, case_version)`.
* Case result `hermes-adaptive-effort.validation-result.v1`: stable `case_id`, nullable `attempt_id` (deterministic ordinal only when an attempt exists), `manifest_hash`, state (`reserved|sent|completed|unknown|blocked`), allowlisted route/API/HTTP status, before/middleware/wire effort values, response-complete boolean and synthetic-answer boolean, token counts, reconciled provider cost evidence or Codex usage-percentage snapshots before/after, reservation for dollar routes only, normalized failure code, and timestamps. Never include prompt text, headers, raw errors, bodies, credentials, raw account IDs, session identifiers, or environment values.
* Ledger `hermes-adaptive-effort.validation-ledger.v1`: manifest hash, independent OpenRouter/Go dollar accounts `{hard_cap, working_cap, reconciled_spend, reserved, unresolved, attempts}`, and a separate Codex usage record `{account_fingerprint, window_minutes, baseline_used_percent, max_increase_percentage_points, observed_used_percent, stop_threshold_percent}`. The pure dollar-ledger helper reserves before a hypothetical send, and reserves only under a provider's working/hard caps. Codex has no dollar reservation; its usage record is an observation/stop guard, not a predicted or reserved cost. A single process-wide exclusive OS file lock owns the ledger. Each complete state/history snapshot is written to a temporary file, flushed and fsynced, then atomically replaces the prior snapshot. A crash leaves dollar reservations unresolved and fully counted. Resume uses manifest hash + stable case/attempt IDs, never repeats `sent`, `completed`, or `unknown`, and permits a new ordinal only after an explicitly reconciled terminal attempt. Missing cost evidence retains its full dollar reservation. These helpers are unit-tested but not connected to any HTTP transport; they do not establish a pre-send guard.

CLI is `python scripts/run_model_validation.py {inventory,check,run}`. `inventory --output PATH [--config PATH]` is read-only and emits a redacted manifest from the exact Go registry, configured OpenRouter IDs, and the authenticated Codex account catalog above. `check --manifest PATH [--output PATH]` validates manifest hash/schema, current source fingerprints, exact local mappings, budget policy, quota baseline, and live eligibility; it makes zero generation requests. `valid: true` means the local manifest is internally consistent, not that a run is complete. `run --manifest PATH --ledger PATH --output DIR [--resume]` sends only cases with a verified route and active provider-specific guard, one at a time, and records sanitized evidence. It never invokes a real classifier.

OpenRouter cases come only from exact runtime-configured routes with a compatible preexisting effort field; never enumerate a marketplace catalog. Go coverage uses the 13 exact routes in §3. OpenAI coverage comes only from the authenticated Codex account catalog; no static fallback. The versioned synthetic case file is loaded and validated before a real run. The run gate additionally requires successful local tests and lint, independent Worker C review, frozen case order and current prices, enforceable maximum charges for dollar routes, reconcilable initial and ending dollar counters, and a verified Codex account/quota identity for OpenAI. Each OpenAI physical send is observed and counted; check the 300-minute usage meter before and after each sequential request and stop at 60% (hard limit 61%). Never batch Codex cases. If an enforceable price/output bound or spend reconciliation is absent, skip that dollar-provider route; unknown is never zero. Prioritize one distinct output per eligible route, followed by additional outputs in the same order. Stop each dollar provider when no further case fits its working cap, while retaining affordable cases later in order. Thresholds are $1.80 OpenRouter and $2.70 Go; hard limits are $2 and $3; the Codex usage ceiling is +30 percentage points on the same 300-minute account window. No cap sharing. GLM-5.2 remains a one-value limitation (high) under current mappings.

Fichiers de livraison :

| Fichier | Rôle |
| --- | --- |
| `scripts/run_model_validation.py` | Inventaire, contrôle local et helpers Decimal de ledger; aucune orchestration réseau ni garde de transport |
| `docs/validation/model-validation.example.json` | Exemple sans secrets : sélection exacte des routes, deux budgets, limites et dossier de sortie |
| `docs/validation/model-validation-cases.json` | Cas synthétiques versionnés, attentes locales et réponses attendues |
| `tests/test_live_model_validation.py` | Tests sans réseau du registre, manifeste, redaction, helpers ledger et blocage zéro-envoi |
| `docs/validation/model-validation-report.template.md` | Modèle de rapport séparant preuve locale, preuve réseau et cas non exécutés |

Le lanceur expose les actions `inventory`, `check` et `run`. Les deux premières sont locales et sans requête HTTP; `run` reste fail-closed et ne réalise actuellement aucun appel réseau. Réutiliser la venv du projet et les mécanismes Hermès existants, sans rendre le payload pip-installable ou ajouter de `pyproject.toml`.

Les résultats détaillés utilisent un répertoire de campagne temporaire ou explicitement choisi, isolé des conversations opérateur. Prévoir un manifeste, des observations JSON expurgées par cas, un journal de coûts et un rapport. Les prompts synthétiques restent dans leur corpus versionné ; les traces ne les recopient pas. Le nettoyage ne concerne que les fichiers et processus appartenant à cette campagne.

Les tests livrés vérifient le registre 13 routes/39 mappings/33 couples, le hachage et le fingerprint de source du manifeste, le statut live bloqué, la redaction, les commandes locales sans réseau, le ledger zéro-envoi et les helpers Decimal (budgets indépendants, réservations, coût inconnu, rapprochement, dépassement, transitions, verrou et persistance). Ils ne démontrent pas le guard pré-envoi branché sur un transport, les retries/fallbacks Hermes, les compteurs fournisseur ni une récupération après crash pendant un vrai appel; ces points restent des critères de levée du blocage.

Avant toute éventuelle levée du blocage, exécuter les contrôles du dépôt avec les commandes canoniques :

```powershell
.\scripts\run_tests.ps1
.\scripts\run_lint.ps1
```

```bash
./scripts/run_tests.sh
./scripts/run_lint.sh
```

Le lanceur payant reste extérieur à la suite `tests/` et aux vérifications automatiques ordinaires. Si une incompatibilité exige ensuite un changement du plugin, ajouter une régression réseau simulée pertinente et mettre à jour `README.md`, `docs/CONTRACTS.md` et `docs/HANDOFF.md`. La première campagne mesure le comportement existant ; elle ne corrige pas silencieusement sa politique.

## 8. Rapport et critères de réussite

Chaque ligne contient : fournisseur et modèle demandés, route effective, API, décision de test, effort initial, cible locale, effort vu dans le corps HTTP, état de la réponse, contrôle de la réponse courte, tokens disponibles, coût imputé ou borne comptable identifiée pour les routes dollar, hausse mesurée du compteur Codex pour OpenAI et motif d'échec normalisé. Afficher le solde des plafonds dollar indépendamment du delta de quota Codex; ne pas inventer de « réserve » en dollars pour OpenAI. Les erreurs distantes brutes ne sont pas conservées si elles peuvent contenir du texte ou des données sensibles.

Le rapport présente séparément :

| Verdict | Signification |
| --- | --- |
| Validé pour cette valeur | Bonne route et bon champ observés, réponse terminée et coût rapproché ou borne explicitement documentée |
| Variation observée | Au moins deux valeurs différentes produites par le plugin ont été observées et acceptées sur cette même route |
| Limite de correspondance | Plusieurs décisions convergent vers une seule valeur, comme GLM-5.2 dans l'état actuel |
| Sans contrôle exploitable | Champ absent non injectable, désactivé, malformé ou transport inéligible ; pas de support dynamique démontré |
| Incompatibilité | Le fournisseur rejette le champ ou une valeur sur la route testée |
| Accès / disponibilité | Authentification, région, quota ou modèle retiré empêchant la conclusion |
| Non vérifié | Budget, coût incertain, troncature, observation manquante ou périmètre exclu |

Une route n'est déclarée validée pour tous ses niveaux produits que si chaque valeur distincte attendue possède sa preuve. La campagne n'est exhaustive que si toutes les routes sélectionnées ont été exécutées ou portent une exclusion explicite. Les 33 cas Go sont un objectif de couverture, pas une promesse qu'ils aboutiront tous dans l'enveloppe ou avec les accès disponibles.

Le rapport répond directement à trois questions : quels modèles fonctionnent avec le contrôle actuel, sur quels modèles observe-t-on une variation d'effort, et quels modèles/niveaux restent à corriger ou vérifier. Les conclusions restent attachées aux IDs, fournisseurs et versions exécutés. Aucun gain de qualité, de coût ou de cache n'est déduit de cette campagne de compatibilité.

## 9. Consigne d'exécution autorisée et état d'exécution

La consigne ci-dessous a été exécutée pour la route Codex, dans la limite mesurée du compteur. OpenRouter et Go restent non exécutés : le lanceur les a exclus avant génération, faute de coût maximal et de rapprochement fiables. L'usage de « réservation » dans cette spécification désigne une retenue comptable temporaire avant envoi, pas un débit; aucune réservation de coût n'a été créée pour un cas qui n'a pas été envoyé. Le rapport final est la référence pour les résultats, limites et preuves de cette campagne. Toute reprise des fournisseurs dollar attend une garde de transport démontrée et des compteurs rapprochables.

> Exécute la campagne de compatibilité décrite dans `docs/plans/live-model-effort-validation.md` avec le lanceur maintenu du dépôt. Utilise le catalogue Codex authentifié Hermès et couvre chaque modèle exact accessible sur la route `openai-codex` / `codex_responses`, toutes les routes Go du registre et les routes OpenRouter configurées dont le champ d'effort est exploitable. Utilise le classificateur déterministe de test; aucun classificateur réel. Les plafonds sont indépendants : 2 USD OpenRouter, 3 USD OpenCode Go et au plus +30 points de pourcentage du compteur Codex sur cinq heures, sur le même compte. Pour Codex, envoie une seule génération à la fois, mesure avant et après chaque envoi, et arrête préventivement à 60 % (plafond absolu 61 %). Observe les envois physiques et désactive les retries/fallbacks si possible. Utilise le vrai chemin Hermès et conserve uniquement les preuves expurgées autorisées. Ne change ni les réglages opérateur, ni les correspondances, ni `effort_models`, et ne bascule pas vers Zen ou un autre fournisseur. Pour OpenRouter et Go, n'envoie que des cas dont la dépense maximale est bornée et rapprochable dans l'enveloppe restante. Produis la matrice des valeurs envoyées, réponses terminées, limites et cas non vérifiés, avec les coûts rapprochés et le delta de quota Codex.

## 10. Seconde étape, différée

Une fois la compatibilité des modèles de réponse établie, préparer une campagne distincte pour les classificateurs : corpus bilingue, répétitions, scores valides, cohérence, délais et effet du contexte cible. Son choix de modèles et son budget seront définis séparément. Les budgets de la première campagne ne constituent pas une autorisation de consommation pour cette seconde étape.
