"""Module for guided conversational node handling creation of a Bill of Materials (BOM) nomenclature. Provides utilities for normalizing text, interpreting yes/no answers, formatting component summaries, handling retries, reading articles from MCP, and managing the stateful nomenclature creation flow."""

import logging
import re
import unicodedata
import json
from api.mcp_pool import pool as mcp_pool

logger = logging.getLogger(__name__)

def _normaliser(texte: str) -> str:
    """Normalize a string: strip, lowercase, and remove diacritics."""
    t = unicodedata.normalize("NFKD", texte.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))

def _est_oui(texte: str) -> bool:
    """Return True if the normalized text matches a yes expression."""
    t = _normaliser(texte)
    return bool(re.match(r"^(oui|o|yes|y|ok|confirme?r?|valide?r?|ouais)$", t))

def _est_non(texte: str) -> bool:
    """Return True if the normalized text matches a no expression."""
    t = _normaliser(texte)
    return bool(re.match(r"^(non|no|n|annuler?|annulation|stop|abandonner?)$", t))

def _formater_recap(lignes: list) -> str:
    """Render the list of added components as a markdown table."""
    if not lignes:
        return "_Aucun composant ajouté pour l'instant._"
    out = [
        "| Référence | Désignation | Qté | Commentaire |",
        "|---|---|---|---|",
    ]
    for l in lignes:
        comment = l.get("commentaire") or "-"
        out.append(f"| **{l['ref']}** | {l['design']} | {l['qte']} | {comment} |")
    return "\n".join(out)

def _traiter_retry_nomenclature(state: dict, c: dict, etape: str, message: str) -> bool:
    """Handle retry attempts for a given step; abort after two failures."""
    tentatives = c.setdefault("tentatives_champ", {})
    n = int(tentatives.get(etape, 0)) + 1
    tentatives[etape] = n
    if n >= 2:
        state["nomenclature_en_cours"] = {}
        state["reponse_finale"] = "❌ Création de nomenclature annulée après 2 essais infructueux."
        state["action_buttons"] = []
        return True
    state["nomenclature_en_cours"] = c
    state["reponse_finale"] = f"{message}\n\nTentative {n}/2."
    state["action_buttons"] = []
    return False

async def _lire_article_mcp(ref_ou_nom: str) -> dict:
    """Read an article from MCP given a reference or name; return parsed JSON data."""
    try:
        raw = await mcp_pool.call("actions", "lire_article", {"ref_article": ref_ou_nom})
        if not raw:
            return {}
        raw_str = str(raw[0].text if isinstance(raw, list) else raw)
        data = json.loads(raw_str)
        return data
    except Exception as e:
        logger.error(f"[_lire_article_mcp] {e}")
        return {}

async def noeud_nomenclature(state: dict) -> dict:
    """Main state machine handling the interactive creation of a nomenclature."""
    logger.info("⚡ [Création Nomenclature] entrée")
    question = state.get("demande_brute", "").strip()
    c = state.get("nomenclature_en_cours") or {}

    etape = c.get("etape", "INIT")

    if etape == "INIT":
        ref = state.get("ref_article") or state.get("nom_article_brut") or ""
        if not ref:
            c["etape"] = "ATTENTE_PARENT"
            state["nomenclature_en_cours"] = c
            state["reponse_finale"] = "🔍 Pour quel produit souhaitez-vous créer la nomenclature ? (Indiquez la référence ou le nom)"
            state["action_buttons"] = []
            return state

        data = await _lire_article_mcp(ref)
        if data.get("statut") != "SUCCES":
            state["nomenclature_en_cours"] = {}
            state["reponse_finale"] = f"❌ Produit '{ref}' introuvable. Veuillez réessayer avec une référence valide."
            state["action_buttons"] = []
            return state

        try:
            raw = await mcp_pool.call("actions", "lire_nomenclature", {"ref_parent": data["AR_Ref"]})
            rows = json.loads(str(raw[0].text if isinstance(raw, list) else raw)) if raw else []
            if isinstance(rows, list) and rows:
                state["nomenclature_en_cours"] = {}
                state["reponse_finale"] = (
                    f"❌ Une nomenclature existe déjà pour **{data['AR_Ref']}** ({data.get('AR_Design', '')}). "
                    "La création est annulée."
                )
                state["action_buttons"] = []
                return state
        except Exception:
            pass

        c["ref_parent"] = data["AR_Ref"]
        c["design_parent"] = data.get("AR_Design", "")
        c["etape"] = "ATTENTE_COMPOSANT"
        c["composants_ajoutes"] = 0
        c["lignes_ajoutees"] = []
        state["nomenclature_en_cours"] = c
        state["reponse_finale"] = (
            f"### 🆕 Nouvelle nomenclature — **{c['ref_parent']}** ({c['design_parent']})\n\n"
            "Veuillez indiquer la **référence ou le nom du premier composant** à ajouter :"
        )
        state["action_buttons"] = []
        return state

    if etape == "ATTENTE_PARENT":
        data = await _lire_article_mcp(question)
        if data.get("statut") != "SUCCES":
            if _traiter_retry_nomenclature(state, c, "ATTENTE_PARENT", f"❌ Produit '{question}' introuvable. Veuillez indiquer un article existant."):
                return state
            return state

        try:
            raw = await mcp_pool.call("actions", "lire_nomenclature", {"ref_parent": data["AR_Ref"]})
            rows = json.loads(str(raw[0].text if isinstance(raw, list) else raw)) if raw else []
            if isinstance(rows, list) and rows:
                state["nomenclature_en_cours"] = {}
                state["reponse_finale"] = (
                    f"❌ Une nomenclature existe déjà pour **{data['AR_Ref']}** ({data.get('AR_Design', '')}). "
                    "La création est annulée."
                )
                state["action_buttons"] = []
                return state
        except Exception:
            pass

        c["ref_parent"] = data["AR_Ref"]
        c["design_parent"] = data.get("AR_Design", "")
        c["etape"] = "ATTENTE_COMPOSANT"
        c["composants_ajoutes"] = 0
        c["lignes_ajoutees"] = []
        state["nomenclature_en_cours"] = c
        state["reponse_finale"] = (
            f"### 🆕 Nouvelle nomenclature — **{c['ref_parent']}** ({c['design_parent']})\n\n"
            "Veuillez indiquer la **référence ou le nom du composant** à ajouter :"
        )
        state["action_buttons"] = []
        return state

    if etape == "ATTENTE_COMPOSANT":
        data = await _lire_article_mcp(question)
        if data.get("statut") != "SUCCES":
            if _traiter_retry_nomenclature(state, c, "ATTENTE_COMPOSANT", f"❌ Composant '{question}' introuvable. Veuillez indiquer un article existant :"):
                return state
            return state

        c["ref_composant"] = data["AR_Ref"]
        c["design_composant"] = data.get("AR_Design", "")
        c["etape"] = "ATTENTE_QTE"
        state["nomenclature_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Composant sélectionné : **{c['ref_composant']}** ({c['design_composant']}).\n\n"
            "Quelle est la **quantité** nécessaire pour ce composant ?"
        )
        state["action_buttons"] = []
        return state

    if etape == "ATTENTE_QTE":
        try:
            qte = float(question.replace(',', '.'))