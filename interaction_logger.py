"""
interaction_logger.py — journal des décisions de classification et
détection heuristique de corrections utilisateur.
Ne modifie JAMAIS EXEMPLES_PAR_ACTION automatiquement : ça reste une
validation humaine via enrichir_exemples.py, pour éviter qu'une
mauvaise interprétation ("non" pour une tout autre raison) ne pollue
le classifieur de référence.
C’est un système de journalisation + détection de feedback utilisateur.
🧠 1. Rôle global du fichier

Il fait 2 choses principales :

1) 📊 Logger les décisions du classifieur

Chaque fois que ton système choisit une action (ex: LISTE_CLIENTS, GENERER_DOC), il enregistre :

la question utilisateur
l’action prédite
la source (regex / sémantique / LLM)
la confiance

➡️ dans :

logs_classification.jsonl
2) 🚨 Détecter si l’utilisateur corrige le système

Exemple :

Système : “je vais créer une facture”
Utilisateur : “non je voulais un bon de livraison”

➡️ le fichier détecte ça automatiquement
"""
import json
import time
from pathlib import Path

_LOG_PATH         = Path("./logs_classification.jsonl")
_CORRECTIONS_PATH = Path("./corrections_a_verifier.jsonl")


def logger_decision(question: str, action: str, origine: str, confidence: float):
    entry = {
        "ts": time.time(), "question": question,
        "action": action, "origine": origine,
        "confidence": round(confidence, 3),
    }
    try:
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


_MOTS_TYPE_DOC = {
    "bl": "BL", "bon de livraison": "BL",
    "bc": "BC", "bon de commande": "BC",
    "of": "OF", "ordre de fabrication": "OF",
    "bf": "BF", "bon de fabrication": "BF",
    "facture": "FACTURE",
    "avoir": "CREER_AVOIR",
    "client": "CREER_CLIENT",
    "fournisseur": "CREER_FOURNISSEUR",
}


def detecter_correction(demande_precedente: str, action_predite: str,
                          demande_courante: str) -> dict | None:
    """
    Heuristique légère (pas une IA) : 'non' + mention d'un type de
    document juste après une action classifiée = correction probable.
    Mise en file d'attente pour revue humaine, jamais appliquée seule.
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