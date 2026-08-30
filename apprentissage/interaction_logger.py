"""Utilities for logging classifier decisions and detecting user corrections.

This module provides two main functions:
- `logger_decision` records each classifier decision (question, predicted action,
  source, and confidence) to a JSON Lines log file.
- `detecter_correction` applies a lightweight heuristic to identify when a user
  corrects a previous system action (e.g., by replying with "non" followed by a
  document type). Detected corrections are queued for human review in a separate
  log file.
"""

import json
import time
from pathlib import Path

_LOG_PATH = Path("./logs_classification.jsonl")
_CORRECTIONS_PATH = Path("./corrections_a_verifier.jsonl")

def logger_decision(question: str, action: str, origine: str, confidence: float):
    """Log a classifier decision to the decision log file.

    Parameters
    ----------
    question: str
        The user's original question.
    action: str
        The action predicted by the classifier.
    origine: str
        The source of the prediction (e.g., regex, semantic, LLM).
    confidence: float
        The confidence score of the prediction; stored rounded to three decimals.
    """
    entry = {
        "ts": time.time(),
        "question": question,
        "action": action,
        "origine": origine,
        "confidence": round(confidence, 3),
    }
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

_MOTS_TYPE_DOC = {
    "bl": "BL",
    "bon de livraison": "BL",
    "bc": "BC",
    "bon de commande": "BC",
    "of": "OF",
    "ordre de fabrication": "OF",
    "bf": "BF",
    "bon de fabrication": "BF",
    "facture": "FACTURE",
    "avoir": "CREER_AVOIR",
    "client": "CREER_CLIENT",
    "fournisseur": "CREER_FOURNISSEUR",
}

def detecter_correction(demande_precedente: str, action_predite: str,
                        demande_courante: str) -> dict | None:
    """Detect a possible user correction based on a simple heuristic.

    The function checks whether the current user input starts with a negation
    keyword (e.g., "non", "faux", "erreur") and contains a known document type.
    If such a pattern is found, it records a correction entry for later human
    verification.

    Parameters
    ----------
    demande_precedente: str
        The previous user request that was classified.
    action_predite: str
        The action that was predicted for the previous request.
    demande_courante: str
        The current user input, potentially containing a correction.

    Returns
    -------
    dict | None
        A dictionary describing the detected correction, or ``None`` if no
        correction is identified.
    """
    if not demande_precedente or not action_predite:
        return None
    n = demande_courante.lower().strip()
    if not n.startswith(("non", "faux", "erreur")):
        return None
    for mot, cible in _MOTS_TYPE_DOC.items():
        if mot in n:
            entry = {
                "ts": time.time(),
                "question": demande_precedente,
                "action_predite": action_predite,
                "correction_supposee": cible,
                "message_correction": demande_courante,
            }
            try:
                with _CORRECTIONS_PATH.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
            print(f"   📝 [Correction détectée] '{demande_precedente}' "
                  f"était classé {action_predite}, l'utilisateur voulait {cible}")
            return entry
    return None