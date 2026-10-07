# Rapport de campagne — compatibilité d'effort Hermès

Date : 7 octobre 2026, Europe/Paris. État : **partiellement exécutée**. Les générations Codex autorisées ont été effectuées; les fournisseurs dollar ont été exclus avant génération faute de garde et de rapprochement vérifiables.

## Périmètre et contrôles

- Compte Codex : route Hermès `openai-codex` / `codex_responses`; sept IDs exacts tirés du catalogue authentifié.
- Effort : scores synthétiques locaux `0/1/2` pour exercer `low/medium/high`. **Zéro appel à un classificateur réel.**
- Requête : sonde Hermès, tools retirés, plafond de sortie 256 tokens, une seule tentative physique par envoi observé. Les prompts, en-têtes, credentials et corps de réponse ne sont pas enregistrés dans les résultats.
- Matrice : 21 cas modèle/valeur (7 × 3). Dix cas ont envoyé le champ attendu et reçu une réponse HTTP 200 terminée; onze ont été bloqués avant la génération, car la valeur d'effort finale était absente ou `none`.
- Disponibilité : deux requêtes séparées avec le plugin en mode inactif. Elles n'établissent aucune compatibilité d'effort.
- Contrôles locaux : **546 tests passés**; Ruff : **All checks passed**. Le manifeste est valide, empreinte source Hermès vérifiée, 7 modèles Codex inventoriés. Résultat du lanceur générique : `run_ready=false`, aucun envoi réseau.

Manifeste : [manifest.json](manifest.json), SHA-256 `6056d0f97a69a7b7e793794a6fd3c49733108baaec8f2ecc8a911d6e7f6b9e55`. Contrôle : [check.json](check.json). Résumé Codex : [openai-results.json](openai-results.json). La sortie du lanceur générique, exclusivement bloquée à zéro envoi, est dans [output/results.json/results.json](output/results.json/results.json); son journal se trouve dans [ledger.jsonl](ledger.jsonl). Les fichiers par cas sont nommés `gpt-<modèle>-<effort>.json` et `gpt-<modèle>-availability.json`.

## Résultats OpenAI Codex/Responses

`✓` signifie que la sonde a observé l'effort exact dans `reasoning.effort`, une réponse HTTP 200 terminée et une sortie synthétique conforme. `Bloqué` signifie que le cas s'est arrêté avant tout envoi de génération; il ne signifie pas que le fournisseur a refusé le champ.

| Modèle exact | `low` | `medium` | `high` | Conclusion effort |
| --- | --- | --- | --- | --- |
| `gpt-6.1-sol` | ✓ | ✓ | ✓ | Trois valeurs distinctes acceptées; variation observée. |
| `gpt-6-astra` | ✓ | ✓ | Bloqué | Deux valeurs prouvées; `high` choisi par le middleware, absent du corps final observé. |
| `gpt-6-sol` | Bloqué | Bloqué | Bloqué | Aucune valeur d'effort prouvée; le champ final vaut `none`. |
| `gpt-6-luna` | Bloqué | Bloqué | ✓ | `high` prouvé; `low` et `medium` absents du corps final. |
| `gpt-5.6-sol` | Bloqué | Bloqué | Bloqué | Aucune valeur d'effort prouvée; disponibilité seulement en mode plugin inactif. |
| `gpt-5.6-terra` | Bloqué | ✓ | ✓ | Deux valeurs prouvées; `low` absent du corps final. |
| `gpt-5.6-luna` | ✓ | ✓ | Bloqué | Deux valeurs prouvées; `high` absent du corps final. |

Les onze cas bloqués n'ont déclenché aucun POST vers le modèle : le middleware avait choisi une valeur, mais le corps HTTP final ne portait pas cette valeur. Cela localise l'écart dans l'assemblage/transport Hermès observé; cela ne mesure pas l'acceptation fournisseur de ces valeurs.

Pour `gpt-6-sol`, l'essai de disponibilité en mode plugin inactif a retourné une réponse HTTP 200 terminée, mais la commande Hermès s'est terminée en code 2 (`provider_resolution_failure`). Ce résultat est partiel et n'est pas une réussite Hermès propre. `gpt-5.6-sol` a retourné une réponse terminée avec une sortie Hermès propre en mode plugin inactif. Ces deux requêtes ne prouvent pas le contrôle d'effort. Les cinq autres modèles ont obtenu au moins une réponse terminée dans leurs cas d'effort valides.

L'effort envoyé est resté celui de la requête de test. Aucun résultat ne prouve un changement interne du budget de raisonnement, une amélioration de qualité, un gain de cache ou une économie.

## Quotas et fournisseurs non exécutés

Le compteur Codex de 300 minutes était à **31 %** au moment de l'autorisation. Le dernier relevé, à **11:43 UTC**, est **49 %**, soit **+18 points** sur cette fenêtre et le même compte confirmé pour Hermès. Cette hausse représente l'usage Codex global de la session (orchestration comprise); elle ne peut pas être imputée intégralement aux douze requêtes du modèle. Les relevés par envoi sont conservés dans les JSON de cas et le résumé agrégé. Seuil d'arrêt préventif : 60 %; plafond absolu : 61 %. Aucune autre génération ne sera lancée dans cette campagne.

| Fournisseur | Limite opérateur | Générations envoyées par cette campagne | Conclusion dépense |
| --- | ---: | ---: | --- |
| OpenRouter | $2.00 (seuil de travail $1.80) | 0 | Aucun coût de génération de campagne; compatibilité non vérifiée. |
| OpenCode Go | $3.00 (seuil de travail $2.70) | 0 | Aucun coût de génération de campagne; compatibilité réseau non vérifiée. |

Ces zéros désignent les envois de cette campagne, pas une mesure des compteurs globaux des comptes. Les cas ont été bloqués avant envoi, car le lanceur ne disposait pas d'une borne de facturation pré-envoi applicable et d'un rapprochement réel relié au transport. La « réservation » décrite dans le plan est une retenue comptable temporaire de la borne maximale d'une requête — pas un débit, ni une autorisation de dépense supplémentaire. Aucun montant n'a été réservé ni débité sur ces fournisseurs. Aucun fallback vers Zen ou un autre fournisseur n'a été utilisé.

L'inventaire local Go couvre toujours 13 routes, 39 correspondances et 33 couples modèle/valeur wire, sans validation réseau. **GLM-5.2** est explicitement une limite de correspondance : les trois décisions locales `low`, `medium` et `high` convergent vers `high`; aucune acceptation fournisseur n'a été testée. L'inventaire OpenRouter a deux routes configurées et six cas locaux; API/transport ou contrôle exploitable et plafond fiable non établis, donc aucun appel.

## Limites de preuve et suite

Les appels payants ont précédé le dernier durcissement de la sonde. Après revue indépendante, les contrôles ont été renforcés pour bloquer les transports non observés, exiger l'hôte/chemin exact, refuser les redirections et plafonds invalides, et tester le mode de disponibilité sans classificateur actif. Ces changements ont passé les tests locaux et un dry-run catalogue sans génération; aucune requête payante n'a été répétée avec la version durcie. La preuve des appels reste celle des champs capturés à leur frontière HTTP par la version utilisée pendant la campagne.

Les résultats confirment `plugin_loaded=true`, mais n'enregistrent ni le chemin exact ni l'empreinte de l'artefact du plugin chargé. L'empreinte source du manifeste vérifie le checkout, pas à elle seule le binaire/fichier réellement chargé. La provenance de l'artefact exécuté reste donc une limite à lever avant une certification stricte.

Conclusion : parmi les modèles testés, seul `gpt-6.1-sol` possède une preuve complète pour les trois valeurs. Les six autres ont une preuve partielle ou aucune preuve d'effort selon la matrice. La compatibilité des modèles Go et OpenRouter demeure non vérifiée. La campagne de classificateurs est différée; aucun de ses appels n'est autorisé par les enveloppes de celle-ci.

> **Redaction, 2026-10-07 (v0.3.0 release review):** `hermes_source.path` in `manifest.json` was replaced by `<HERMES_SOURCE_ROOT>` to remove a local user path before publication. The recorded `manifest_hash`/SHA-256 values were computed over the original file and are intentionally left unchanged, so `check` now reports `manifest_hash_mismatch` for this historical manifest. No other field was edited.
