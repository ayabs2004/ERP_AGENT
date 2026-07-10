"""
extraction_structuree.py — Extraction d'entités structurée par LLM
extraction_structuree.py =

un filet de sécurité LLM qui transforme du langage libre en données ERP structurées quand les règles classiques échouent
Ce fichier extraction_structuree.py est un fallback intelligent d’extraction d’informations, utilisé uniquement quand tes méthodes classiques échouent.

Je te l’explique simplement mais correctement.

🧠 Rôle global

👉 Ce module sert à :

extraire des entités ERP (client, article, quantité, document) avec un LLM quand les regex et NER échouent

📌 Où il s’insère dans ton système ?

Tu as un pipeline d’extraction en cascade :

1. Regex (rapide, fiable pour formats ERP)
2. NER (modèle classique NLP)
3. ❌ si échec total → LLM (ce fichier)
🧾 2. Fonction logger_decision()
def logger_decision(question, action, origine, confidence):
Ce qu’elle fait :

À chaque classification :

{
  "ts": 123456,
  "question": "liste clients",
  "action": "LISTE_CLIENTS",
  "origine": "SEMANTIQUE",
  "confidence": 0.92
}
📌 Important :
elle append dans un fichier .jsonl
elle ne remplace jamais rien
donc les logs s’accumulent

👉 c’est TON historique de décisions

🧠 3. Dictionnaire _MOTS_TYPE_DOC
_MOTS_TYPE_DOC = {
    "bl": "BL",
    "bon de livraison": "BL",
    "facture": "FACTURE",
    ...
}
🎯 rôle :

C’est une table de correspondance :

👉 mots dans la phrase utilisateur → type d’action ERP

Exemple :

"bon de livraison" → BL
"facture" → FACTURE
🚨 4. Fonction critique : detecter_correction()

C’est la partie la plus importante de ton apprentissage

🎯 objectif :

Détecter automatiquement :

“l’utilisateur corrige le système”

🧪 exemple réel :
Tour 1 :
User : crée une facture pour client X
System → GENERER_DOC (facture)
Tour 2 :
User : non, bon de livraison
🧠 logique interne :
1. Vérifie qu’il y a correction implicite :
if not n.startswith(("non", "faux", "erreur")):
    return None

➡️ donc doit commencer par :

"non"
"faux"
"erreur"
2. Cherche un mot métier :
for mot, cible in _MOTS_TYPE_DOC.items():
    if mot in n:
3. Si match trouvé → il crée une correction :
entry = {
    "question": demande_precedente,
    "action_predite": action_predite,
    "correction_supposee": cible,
}

➡️ stocké dans :

corrections_a_verifier.jsonl
🧾 5. Exemple de sortie
{
  "question": "crée une facture",
  "action_predite": "GENERER_DOC",
  "correction_supposee": "BL",
  "message_correction": "non bon de livraison"
}
⚠️ 6. Point très important

👉 ce système ne modifie jamais ton modèle automatiquement

Il fait juste :

🔹 1. enregistrer les décisions
🔹 2. détecter les corrections
🔹 3. envoyer en file de validation humaine
======================================================================
Amélioration #6 : l'extraction d'entités (client/article/quantité/pièce)
repose presque entièrement sur des regex, fragiles face aux variations
de formulation naturelle. On ne les remplace PAS entièrement — elles
restent fiables et bon marché pour les identifiants ERP à format strict
(CLI001, FA2024, BL003...). En revanche, quand elles échouent toutes
(NER inclus) sur une action d'ÉCRITURE, on tente un dernier recours :
une extraction structurée par LLM avec un schéma JSON strict, plus
robuste aux formulations libres ("le client dont le nom commence par
Du...", "deux caisses de l'écran 4K"...).

Ce module est volontairement sans dépendance vers orchestrateur_general
(pour éviter tout cycle d'import) : l'appelant LLM est injecté.
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable

LlmCaller = Callable[[str], Awaitable[str]]

_SCHEMA_PROMPT = """Tu extrais des entités ERP d'une phrase en français. \
Réponds UNIQUEMENT avec un objet JSON strict, sans texte autour, sans \
balises markdown. Schéma exact (mets null si absent, jamais de texte \
placeholder comme "INCONNU") :

{{
  "client": string ou null,
  "article": string ou null,
  "quantite": number ou null,
  "piece": string ou null,
  "type_doc": string ou null
}}

Phrase : "{question}"
JSON :"""


def _extraire_json(texte: str) -> dict | None:
    """Tolère un LLM qui entoure quand même sa réponse de ```json ... ```
    ou de texte parasite ; extrait le premier objet JSON valide trouvé."""
    nettoye = texte.strip()
    nettoye = re.sub(r"^```(?:json)?", "", nettoye).strip()
    nettoye = re.sub(r"```$", "", nettoye).strip()
    match = re.search(r"\{.*\}", nettoye, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def extraire_entites_llm(question: str, llm_caller: LlmCaller) -> dict:
    """Dernier recours d'extraction, à n'appeler que si regex + NER ont
    échoué sur une action d'écriture (coût LLM justifié par l'enjeu).
    Retourne un dict avec uniquement les clés non nulles trouvées —
    jamais de placeholder, jamais de valeur inventée si le LLM échoue."""
    try:
        brut = await llm_caller(_SCHEMA_PROMPT.format(question=question))
    except Exception:
        return {}

    data = _extraire_json(brut)
    if not data:
        return {}

    resultat: dict = {}
    for cle in ("client", "article", "piece", "type_doc"):
        val = data.get(cle)
        if isinstance(val, str) and val.strip() and val.strip().upper() != "INCONNU":
            resultat[cle] = val.strip()
    qte = data.get("quantite")
    if isinstance(qte, (int, float)) and qte > 0:
        resultat["quantite"] = float(qte)
    return resultat
