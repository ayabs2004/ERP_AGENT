"""Module handling conversational node for modifying product nomenclature (BOM) via MCP API.
Provides utilities to read articles, read nomenclature, format it, and manage an interactive
state machine that allows adding, modifying, or deleting components in a nomenclature."""

import logging
import re
import unicodedata
import json
from api.mcp_pool import pool as mcp_pool

logger = logging.getLogger(__name__)

def _normaliser(texte: str) -> str:
    """Normalize text by converting to lowercase and removing diacritics."""
    t = unicodedata.normalize("NFKD", texte.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))

async def _lire_article_mcp(ref_ou_nom: str) -> dict:
    """Retrieve article data from MCP given a reference or name."""
    try:
        raw = await mcp_pool.call("actions", "lire_article", {"ref_article": ref_ou_nom})
        if not raw:
            return {}
        raw_str = str(raw[0].text if isinstance(raw, list) else raw)
        return json.loads(raw_str)
    except Exception as e:
        logger.error(f"[_lire_article_mcp] {e}")
        return {}

async def _lire_nomenclature_mcp(ref_parent: str) -> list:
    """Retrieve nomenclature components for a given parent reference from MCP."""
    try:
        raw = await mcp_pool.call("actions", "lire_nomenclature", {"ref_parent": ref_parent})
        if not raw:
            return []
        raw_str = str(raw[0].text if isinstance(raw, list) else raw)
        return json.loads(raw_str)
    except Exception as e:
        logger.error(f"[_lire_nomenclature_mcp] {e}")
        return []

def _formater_nomenclature(composants: list) -> str:
    """Format a list of components into a markdown table."""
    if not composants:
        return "_La nomenclature est actuellement vide._"
    lignes = [
        "| Référence | Désignation | Qté | Commentaire |",
        "|---|---|---|---|",
    ]
    for comp in composants:
        qte = comp.get("qte", 0)
        design = comp.get("design_composant") or ""
        ref = comp.get("ref_composant") or ""
        comment = comp.get("commentaire") or "-"
        lignes.append(f"| **{ref}** | {design} | {qte} | {comment} |")
    return "\n".join(lignes)

def _traiter_retry_modif_nom(state: dict, c: dict, etape: str, message: str) -> bool:
    """Handle retry logic for a modification step, cancelling after two failed attempts."""
    tentatives = c.setdefault("tentatives_champ", {})
    n = int(tentatives.get(etape, 0)) + 1
    tentatives[etape] = n
    if n >= 2:
        state["modification_nomenclature_en_cours"] = {}
        state["reponse_finale"] = "❌ Modification annulée après 2 essais infructueux."
        state["action_buttons"] = []
        return True
    state["modification_nomenclature_en_cours"] = c
    state["reponse_finale"] = f"{message}\n\nTentative {n}/2."
    state["action_buttons"] = []
    return False

async def noeud_modification_nomenclature(state: dict) -> dict:
    """Main conversational node handling the nomenclature modification workflow."""
    logger.info("[Modif Nomenclature] entree")
    question = state.get("demande_brute", "").strip()
    c = state.get("modification_nomenclature_en_cours") or {}
    etape = c.get("etape", "INIT")

    if etape == "INIT":
        ref = state.get("ref_article") or state.get("nom_article_brut") or c.get("ref_parent") or ""
        if not ref:
            c["etape"] = "ATTENTE_PARENT"
            c.pop("dernier_message", None)
            state["modification_nomenclature_en_cours"] = c
            state["reponse_finale"] = (
                "Pour quelle reference ou quel produit souhaitez-vous modifier la nomenclature ?"
            )
            state["action_buttons"] = []
            return state
        data = await _lire_article_mcp(ref)
        if data.get("statut") != "SUCCES":
            state["modification_nomenclature_en_cours"] = {}
            state["dernier_message"] = f"❌ Produit introuvable : {ref}. Veuillez reessayer."
            state["reponse_finale"] = state["dernier_message"]
            state["action_buttons"] = []
            return state
        ref_parent = data["AR_Ref"]
        c["ref_parent"] = ref_parent
        c["design_parent"] = data.get("AR_Design", "")
        composants = await _lire_nomenclature_mcp(ref_parent)
        c["composants"] = composants
        c["etape"] = "ATTENTE_ACTION"
        state["modification_nomenclature_en_cours"] = c
        liste_str = _formater_nomenclature(composants)
        dernier_message = state.get("dernier_message")
        intro = f"{dernier_message}\n\n---\n\n" if dernier_message else ""
        state["reponse_finale"] = (
            f"{intro}### 📋 Nomenclature de **{ref_parent}** ({c['design_parent']})\n\n"
            f"{liste_str}\n\n"
            "Que souhaitez-vous faire ?"
        )
        state["action_buttons"] = ["Ajouter", "Modifier", "Supprimer", "Terminer"]
        return state

    if etape == "ATTENTE_PARENT":
        data = await _lire_article_mcp(question)
        if data.get("statut") != "SUCCES":
            state["reponse_finale"] = f"❌ Produit '{question}' introuvable. Veuillez reessayer."
            state["action_buttons"] = []
            return state
        c["ref_parent"] = data["AR_Ref"]
        c["design_parent"] = data.get("AR_Design", "")
        composants = await _lire_nomenclature_mcp(c["ref_parent"])
        c["composants"] = composants
        c["etape"] = "ATTENTE_ACTION"
        state["modification_nomenclature_en_cours"] = c
        liste_str = _formater_nomenclature(composants)
        state["reponse_finale"] = (
            f"### 📋 Nomenclature de **{c['ref_parent']}** ({c['design_parent']})\n\n"
            f"{liste_str}\n\n"
            "Que souhaitez-vous faire ?"
        )
        state["action_buttons"] = ["Ajouter", "Modifier", "Supprimer", "Terminer"]
        return state

    if etape == "ATTENTE_ACTION":
        t = _normaliser(question)
        if re.search(r"\b(terminer?|fin|quitter?|stop|non|rien|aucun)\b", t):
            state["modification_nomenclature_en_cours"] = {}
            state["dernier_message"] = f"✅ Modification de la nomenclature de **{c.get('ref_parent', '')}** terminée."
            state["reponse_finale"] = state["dernier_message"]
            state["action_buttons"] = []
            return state
        if re.search(r"\b(ajouter?|nouveau|creer?|plus|inserer?|rajouter?)\b", t):
            c["etape"] = "ATTENTE_COMPOSANT_AJOUT"
            state["modification_nomenclature_en_cours"] = c
            state["reponse_finale"] = "➕ Quel composant souhaitez-vous **ajouter** (référence ou nom) ?"
            state["action_buttons"] = []
            return state
        if re.search(r"\b(modifier?|changer?|editer?|maj|mettre a jour|update)\b", t):
            if not c.get("composants"):
                c["etape"] = "ATTENTE_COMPOSANT_AJOUT"
                state["modification_nomenclature_en_cours"] = c
                state["reponse_finale"] = "La nomenclature est vide. Quel composant souhaitez-vous **ajouter** ?"
                state["action_buttons"] = []
                return state
            c["etape"] = "ATTENTE_COMPOSANT_MODIF"
            state["modification_nomenclature_en_cours"] = c
            state["reponse_finale"] = "✏️ Quel composant souhaitez-vous **modifier** (référence ou nom) ?"
            state["action_buttons"] = []
            return state
        if re.search(r"\b(supprimer?|effacer?|enlever?|retirer?|moins|delete|virer)\b", t):
            if not c.get("composants"):
                c["etape"] = "ATTENTE_COMPOSANT_AJOUT"
                state["modification_nomenclature_en_cours"] = c
                state["reponse_finale"] = "La nomenclature est déjà vide. Voulez-vous **ajouter** un composant ?"
                state["action_buttons"] = []
                return state
            c["etape"] = "ATTENTE_COMPOSANT_SUPPR"
            state["modification_nomenclature_en_cours"] = c
            state["reponse_finale"] = "🗑️ Quel composant souhaitez-vous **supprimer** (référence ou nom) ?"
            state["action_buttons"] = []
            return state
        state["reponse_finale"] = (
            "Je n'ai pas compris. Veuillez choisir : **Ajouter**, **Modifier**, **Supprimer**, ou **Terminer**."
        )
        state["action_buttons"] = ["Ajouter", "Modifier", "Supprimer", "Terminer"]
        return state

    if etape == "ATTENTE_COMPOSANT_AJOUT":
        data = await _lire_article_mcp(question)
        if data.get("statut") != "SUCCES":
            if _traiter_retry_modif_nom(state, c, "ATTENTE_COMPOSANT_AJOUT",
                                        f"❌ Composant '{question}' introuvable. Veuillez reessayer :"):
                return state
            return state
        c["ref_cible"] = data["AR_Ref"]
        c["design_cible"] = data.get("AR_Design", "")
        c["etape"] = "ATTENTE_QTE_AJOUT"
        c.setdefault("tentatives_champ", {}).pop("ATTENTE_COMPOSANT_AJOUT", None)
        state["modification_nomenclature_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Composant sélectionné : **{c['ref_cible']}** ({c['design_cible']}).\n"
            "Quelle **quantité** faut-il prévoir ?"
        )
        state["action_buttons"] = []
        return state

    if etape == "ATTENTE_QTE_AJOUT":
        try:
            qte = float(question.replace(",", "."))
            if qte <= 0:
                raise ValueError("qte <= 0")
        except ValueError:
            if _traiter_retry_modif_nom(state, c, "ATTENTE_QTE_AJOUT",
                                        "❌ Veuillez saisir un nombre valide (> 0) pour la quantité :"):
                return state
            return state
        c["qte_cible"] = qte
        c["etape"] = "ATTENTE_COMMENTAIRE_AJOUT"
        c.setdefault("tentatives_champ", {}).pop("ATTENTE_QTE_AJOUT", None)
        state["modification_nomenclature_en_cours"] = c
        state["reponse