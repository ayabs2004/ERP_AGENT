En résumé
Production : orchestrateur_general.py pilote le système, utilise semantic_classifier.py pour comprendre la requête, response_cache.py pour accélérer les réponses, puis interaction_logger.py pour enregistrer ce qui s'est passé.
Logs : interaction_logger.py est le seul composant qui écrit dans logs_classification.jsonl et corrections_a_verifier.jsonl.
Amélioration continue : extraire_cas_logs.py transforme ces logs en jeux de données, valider_classification.py mesure les performances, calibrate_thresholds.py ajuste les seuils, apprentissage_semi_auto.py propose de nouveaux exemples, et run_learning_cycle.py orchestre automatiquement toute cette chaîne. Le point essentiel est qu'aucune modification du classifieur n'est faite sans validation humaine.

Ce que tu as construit n’est pas un modèle de machine learning “pur” au sens classique, mais plutôt un :

🧠 système hybride de classification + apprentissage supervisé assisté + règles métier

Je t’explique clairement les blocs.

🧠 1. Est-ce un modèle de ML ?

👉 Partiellement oui, mais pas un modèle entraîné “end-to-end” comme :

scikit-learn
neural network
transformer fine-tuné
⚙️ 2. Ce que tu as réellement

Ton système est composé de 3 couches :

🟦 A. Règles (Rule-based system)

Ex :

_regex → LISTE_CLIENTS

👉 C’est du :

pattern matching
logique déterministe

✔️ pas du ML

🟩 B. Sémantique (embedding / centroid-based classifier)

Tu as :

1505 exemples chargés et centroïdes calculés

👉 ça ressemble à :

vectorisation de texte
comparaison de similarité
classification par distance

💡 Ça, OUI → c’est du machine learning léger (non deep learning)

Mais :

pas entraînement de réseau
pas backpropagation

👉 plutôt :

classification vectorielle type nearest centroid / cosine similarity

🟨 C. Apprentissage semi-automatique (ton fichier apprentissage_semi_auto.py)

👉 ça n’entraîne pas automatiquement le modèle

ça fait :

collecte de données
filtrage
validation humaine
enrichissement dataset

💡 donc c’est :

supervised learning “assisté humain”

🧠 3. Donc en résumé

Ton système =

🔴 Regex (rules)
🟡 Similarité sémantique (ML léger)
🟢 LLM fallback (si utilisé)
🟣 boucle d’apprentissage humain (dataset evolution)
🚫 4. Ce que tu n’as PAS

Tu n’as pas :

❌ modèle entraîné automatiquement (training loop)
❌ gradient descent
❌ fine-tuning de transformer
❌ backpropagation
❌ dataset qui s’auto-update directement dans le modèle
🧠 5. Nom exact de ton système

Ton architecture s’appelle :

🔥 Hybrid Rule + Semantic Retrieval Classifier with Human-in-the-loop Learning

ou plus simple :

🧠 Système de classification intelligent basé sur règles + similarité + apprentissage supervisé assisté# Corrections appliquées sur le workflow de création de documents Sage

## 1. Numéros de pièce uniques et sans écrasement

Problème initial : l’insertion des documents utilisait un mécanisme de remplacement qui pouvait écraser une pièce existante si le numéro était déjà présent.

Correction :
- la fonction d’insertion des documents n’utilise plus un insert de type overwrite;
- si une contrainte d’unicité est rencontrée, un nouveau numéro est généré automatiquement et la création est retriée jusqu’à obtenir un numéro valide.
- le numéro est désormais construit avec un horodatage précis et un suffixe UUID, ce qui limite fortement les collisions.

Fichiers impactés :
- [mcp_actions_sage.py](mcp_actions_sage.py)

## 2. Insertion multi-lignes au lieu d’une seule ligne

Problème initial : les workflows de transformation et de création d’avoirs ne copiaient qu’une seule ligne, ce qui causait une perte de données sur les documents multi-lignes.

Correction :
- la logique d’insertion accepte maintenant une liste de lignes et insère chaque ligne dans F_DOCLIGNE;
- la transformation d’un document recopie toutes les lignes source vers le document cible;
- la création d’un avoir prend également en compte toutes les lignes de la facture source, avec des montants signés négativement pour refléter l’avoir.

Fichiers impactés :
- [mcp_actions_sage.py](mcp_actions_sage.py)

## 3. Confirmation non bloquante en mode API

Problème initial : le nœud de confirmation utilisait une saisie interactive via input(), ce qui pouvait bloquer le flux API et provoquer un deadlock.

Correction :
- un détecteur de mode API/production a été ajouté;
- en mode API, la confirmation n’attend plus une saisie utilisateur interactive ; elle est automatiquement validée et retourne un état de confirmation métier compatible avec le cycle draft/preview.

Fichiers impactés :
- [orchestrateur_general.py](orchestrateur_general.py)

## 4. Contrôle d’encours client avant création de facture / BL

Problème initial : les créations directes de factures ne vérifiaient pas le plafond d’encours défini sur le client, alors que la logique métier attendait ce contrôle.

Correction :
- avant de créer une facture directe, le workflow vérifie maintenant le montant courant de l’encours du client à partir des documents non réglés;
- si le nouveau montant dépasse le plafond défini par CT_EncoursMax, la création est refusée avec un statut explicite.
- le même contrôle a été appliqué au workflow BL pour garantir une cohérence entre les documents commerciaux.

Fichiers impactés :
- [mcp_actions_sage.py](mcp_actions_sage.py)

## 5. Vérification du stock et mouvement de stock pour la facture directe

Problème initial : la création directe de facture bypassait la logique de stock utilisée par les BL, ce qui permettait de générer une facture sans déduire le stock.

Correction :
- la facture directe vérifie maintenant la disponibilité en stock avant création;
- si le stock est insuffisant, la facture n’est pas créée et un statut STOCK_INSUFFISANT est renvoyé;
- si la création est autorisée, un mouvement de sortie est enregistrée dans F_ARTSTOCK avec un motif lié à la facture.

Attention importante : ce mouvement de stock n’est pas appliqué dans le chemin de transformation BL → facture. Dans ce cas, le stock a déjà été déduit au moment de la création du BL, et la transformation ne fait que recopier les lignes vers la facture.

Fichiers impactés :
- [mcp_actions_sage.py](mcp_actions_sage.py)

## 6. Tests de non-régression

Un jeu de tests a été ajouté pour verrouiller les comportements critiques suivants :
- insertion de documents avec plusieurs lignes;
- génération de numéros uniques;
- contrôle du stock pour la facture directe.

Fichiers impactés :
- [tests/test_document_workflows.py](tests/test_document_workflows.py)

## 7. Protection contre les écritures dupliquées

Problème identifié : les workflows de transformation, d’avoir et de règlement pouvaient être relancés plusieurs fois et créer des doublons fonctionnels (facture supplémentaire, avoir supplémentaire, règlement supplémentaire).

Correction :
- la transformation d’un document vérifie désormais s’un document cible existe déjà pour la pièce source et refuse la transformation avec un statut EXISTE_DEJA ;
- la création d’un avoir vérifie désormais l’existence d’un avoir déjà créé pour la facture source ;
- l’enregistrement d’un règlement vérifie désormais s’un règlement existe déjà pour la facture concernée.

Fichiers impactés :
- [mcp_actions_sage.py](mcp_actions_sage.py)

## 8. Mise à jour de stock atomique et sécurisée

Problème identifié : la logique de stock utilisait des lectures/écritures successives sans garde-fou, ce qui pouvait conduire à des écarts ou à des sorties non validées si le stock devenait insuffisant entre les étapes.

Correction :
- les mouvements de stock sont maintenant calculés à partir d’un état lu dans la même transaction ;
- une sortie de stock refuse explicitement toute opération qui ferait passer le stock sous zéro ;
- le code rejette désormais les mouvements négatifs ou incohérents.

Fichiers impactés :
- [mcp_actions_sage.py](mcp_actions_sage.py)

## 9. Schéma centralisé et cohérent

Problème identifié : les constantes de codes document (DO_Type/DO_Domaine) et les préfixes de pièce étaient dispersés, ce qui créait des divergences entre les serveurs MCP et l’orchestrateur.

Correction :
- [schema_sage.py](schema_sage.py) devient la source de vérité unique pour les codes et préfixes de documents ;
- [mcp_actions_sage.py](mcp_actions_sage.py) et [mcp_server_sage.py](mcp_server_sage.py) s’appuient désormais sur ces constantes centralisées.

## 10. Timestamps de cache basés sur l’horloge système

Problème identifié : les entrées du cache persistant utilisaient des timestamps monotoniques, ce qui pouvait devenir incohérent après un redémarrage ou une persistance longue.

Correction :
- le cache RAM et disque utilisent maintenant des timestamps basés sur time.time() pour une expiration plus fiable et plus compatible avec la persistance.

Fichiers impactés :
- [response_cache.py](response_cache.py)

## 11. Sessions API bornées et robustes

Problème identifié : les sessions de l’API pouvaient croître sans limite dans la mémoire et conserver des états obsolètes sur de longues périodes.

Correction :
- l’API nettoie désormais les sessions expirées sur base d’un TTL configurable ;
- un plafond maximal de sessions évite une croissance illimitée ;
- les sessions sont normalisées et réutilisées proprement, même si l’identifiant est vide ou absent.

Fichiers impactés :
- [api.py](api.py)

## 12. Journalisation structurée et gestion d’erreurs plus sûre

Problème identifié : les erreurs système étaient parfois émises via des print bruts ou des traces très larges, ce qui rendait l’observabilité et la robustesse moins bonnes.

Correction :
- l’API et le pool MCP utilisent maintenant un logger structuré avec niveau INFO/WARNING/ERROR ;
- les erreurs critiques sont consignées sans exposer inutilement les détails internes à l’utilisateur ;
- les réponses de l’API restent cohérentes même lorsqu’un graphe échoue pendant l’exécution.

Fichiers impactés :
- [api.py](api.py)
- [mcp_pool.py](mcp_pool.py)

## 13. Parsing MCP plus défensif et cohérence monétaire

Problème identifié : certaines réponses MCP pouvaient être vides ou non textuelles, et les montants affichés dans les messages utilisaient des formats incohérents.

Correction :
- le pool MCP vérifie désormais que la réponse contient bien du texte exploitable ;
- les montants de documents et de messages utilisent maintenant une représentation centralisée avec la devise partagée ;
- les libellés PDF et messages métier utilisent la même convention de devise pour éviter les écarts visuels.

Fichiers impactés :
- [mcp_pool.py](mcp_pool.py)
- [mcp_actions_sage.py](mcp_actions_sage.py)
- [pdf_generator.py](pdf_generator.py)
- [schema_sage.py](schema_sage.py)


