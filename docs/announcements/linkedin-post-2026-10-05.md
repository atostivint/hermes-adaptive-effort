# Brouillon LinkedIn — lundi 5 octobre 2026

> **Obsolète — ne pas publier ni réutiliser.** Ce brouillon décrit un consentement séparé au scoreur qui a été retiré : l’activation du routage autorise désormais le partage borné du texte avec le scoreur choisi. Il ne mentionne ni OpenAI Decisions ni les changements de la v0.3.0. Conservé comme trace historique.

J’ai développé **Hermes Adaptive Effort**, un plugin open source pour Hermes Agent qui ajuste l’effort de raisonnement d’une requête à partir d’un classifieur externe.

Le point important : le classifieur est indépendant du modèle qui rédige la réponse. Jev reste le choix par défaut, mais on peut aussi sélectionner OpenRouter, Cloudflare Clef ou connecter son propre classifieur hébergé ou local avec une API System One ou Chat Completions.

Le plugin démarre désactivé, ne partage le texte qu’après un consentement explicite pour le fournisseur choisi et laisse la requête intacte si la classification échoue.

J’ai aussi installé Kev 0.8B et Kev 4B localement sur une RTX 4070 Ti. Sur 60 prompts synthétiques en anglais et en français, classés trois fois par modèle, l’accord avec les annotations fixées à l’avance est de 50 % pour le 0.8B et 60 % pour le 4B. Aucun des deux n’a détecté le niveau « high » dans ce corpus. Le premier appel à froid du 4B a dépassé le délai de trois secondes, puis ses 180 appels à chaud ont tous réussi.

Ces résultats sont exploratoires : les annotations sont synthétiques et je n’ai pas encore fait de comparaison avec Jev. Ils montrent surtout pourquoi je voulais pouvoir remplacer le classifieur sans changer le modèle principal.

Le code et les détails de l’essai : https://github.com/atostivint/hermes-adaptive-effort

Je serais intéressé par vos retours, en particulier sur les classifieurs et les grilles que vous aimeriez pouvoir brancher.
