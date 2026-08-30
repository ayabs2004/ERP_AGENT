"""extraction_structuree.py – Structured entity extraction using a LLM fallback.

This module provides utilities to extract ERP entities (client, article, quantity, piece, document type) from free‑form French sentences when traditional regex‑based and NER‑based methods fail. It defines a JSON schema prompt for the LLM, a helper to clean LLM responses, and the main asynchronous function `extraire_entites_llm` that returns a dictionary containing only the non‑null extracted fields.
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

{
  "client": string ou null,
  "article": string ou null,
  "quantite": number ou null,
  "piece": string ou null,
  "type_doc": string ou null
}

Phrase : "{question}"
JSON :"""


def _extraire_json(texte: str) -> dict | None:
    """Extract the first valid JSON object from a raw LLM response.

    The LLM may wrap the JSON in markdown fences or add surrounding text.
    This helper removes such wrappers, searches for the first `{...}` block,
    and returns the parsed dictionary or ``None`` if parsing fails.
    """
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
    """Asynchronously invoke a language model to extract ERP entities from a sentence.

    The function sends a prompt containing a strict JSON schema to the provided
    ``llm_caller``, cleans the response with ``_extraire_json``, and builds a
    result dictionary that includes only non‑null, meaningful values. If the
    LLM call fails or the response cannot be parsed, an empty dictionary is
    returned.
    """
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