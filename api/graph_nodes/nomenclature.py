"""
Nœud conversationnel guidé : création d'une nomenclature (BOM).
"""

import logging
import re
import unicodedata
import json
from api.mcp_pool import pool as mcp_pool

logger = logging.getLogger(__name__)

def _normaliser(texte: str) -> str:
    """Minuscule + suppression accents."""
    t = unicodedata.normalize("NFKD", texte.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))

def _est_oui(texte: str) -> bool:
    t = _normaliser(texte)
    return bool(re.match(r"^(oui|o|yes|y|ok|confirme?r?|valide?r?|ouais)$", t))

def _est_non(texte: str) -> bool:
    t = _normaliser(texte)
    return bool(re.match(r"^(non|no|n|annuler?|annulation|stop|abandonner?)$", t))


def _formater_recap(lignes: list) -> str:
    """Rend la liste des composants déjà ajoutés sous forme de tableau markdown."""
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
    logger.info("⚡ [Création Nomenclature] entrée")
    question = state.get("demande_brute", "").strip()
    c = state.get("nomenclature_en_cours") or {}

    etape = c.get("etape", "INIT")

    # ── INIT ────────────────────────────────────────────────────────────────
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

    # ── ATTENTE_PARENT ───────────────────────────────────────────────────────
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

    # ── ATTENTE_COMPOSANT ────────────────────────────────────────────────────
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

    # ── ATTENTE_QTE ──────────────────────────────────────────────────────────
    if etape == "ATTENTE_QTE":
        try:
            qte = float(question.replace(',', '.'))
        except ValueError:
            if _traiter_retry_nomenclature(state, c, "ATTENTE_QTE", "❌ Veuillez entrer un nombre valide pour la quantité :"):
                return state
            return state

        c["qte"] = qte
        c["etape"] = "ATTENTE_COMMENTAIRE"
        state["nomenclature_en_cours"] = c
        state["reponse_finale"] = (
            f"✅ Quantité : **{qte}**\n\n"
            "Souhaitez-vous ajouter un **commentaire** pour cette ligne ? (Sinon, répondez 'non')"
        )
        state["action_buttons"] = ["Non"]
        return state

    # ── ATTENTE_COMMENTAIRE ──────────────────────────────────────────────────
    if etape == "ATTENTE_COMMENTAIRE":
        if _est_non(question) or question.lower() == "non":
            c["commentaire"] = ""
        else:
            c["commentaire"] = question

        succes = False
        msg = "✅ Ligne insérée."
        try:
            raw = await mcp_pool.call("actions", "creer_ligne_nomenclature", {
                "ref_parent": c["ref_parent"],
                "ref_composant": c["ref_composant"],
                "qte": c["qte"],
                "commentaire": c["commentaire"]
            })
            raw_str = str(raw[0].text if isinstance(raw, list) else raw)
            if raw_str:
                try:
                    res_data = json.loads(raw_str)
                    if res_data.get("statut") == "SUCCES":
                        succes = True
                    else:
                        msg = f"❌ **Échec de l'ajout** — {res_data.get('message')}"
                except json.JSONDecodeError:
                    succes = True
            else:
                succes = True
        except Exception as e:
            logger.error(f"[nomenclature] Erreur MCP : {e}")
            msg = f"❌ **Échec de l'ajout** : {e}"

        if succes:
            msg = (
                f"✅ **Ajout réussi** — **{c['ref_composant']}** ({c['design_composant']}) · "
                f"Qté={c['qte']}"
            )
            c["composants_ajoutes"] += 1
            c.setdefault("lignes_ajoutees", []).append({
                "ref": c["ref_composant"],
                "design": c["design_composant"],
                "qte": c["qte"],
                "commentaire": c.get("commentaire", ""),
            })

        c["etape"] = "ATTENTE_AUTRE_COMPOSANT"
        state["nomenclature_en_cours"] = c
        recap = _formater_recap(c.get("lignes_ajoutees", []))
        state["reponse_finale"] = (
            f"{msg}\n\n"
            f"### 📋 Composants ajoutés jusqu'ici ({c['composants_ajoutes']})\n\n{recap}\n\n"
            "❓ Souhaitez-vous ajouter un **autre composant** à la nomenclature ?"
        )
        state["action_buttons"] = ["Oui", "Non"]
        return state

    # ── ATTENTE_AUTRE_COMPOSANT ──────────────────────────────────────────────
    if etape == "ATTENTE_AUTRE_COMPOSANT":
        if _est_oui(question):
            c["etape"] = "ATTENTE_COMPOSANT"
            for k in ["ref_composant", "design_composant", "qte", "commentaire"]:
                c.pop(k, None)
            state["nomenclature_en_cours"] = c
            state["reponse_finale"] = "Indiquez la **référence ou le nom du composant** :"
            state["action_buttons"] = []
            return state
        elif _est_non(question):
            recap = _formater_recap(c.get("lignes_ajoutees", []))
            ref_parent = c.get("ref_parent", "")
            design_parent = c.get("design_parent", "")
            total = c.get("composants_ajoutes", 0)
            state["nomenclature_en_cours"] = {}
            state["reponse_finale"] = (
                f"### 🏁 Nomenclature créée — **{ref_parent}** ({design_parent})\n\n"
                f"{recap}\n\n"
                f"**{total}** composant(s) ajouté(s) au total."
            )
            state["action_buttons"] = []
            return state
        else:
            state["nomenclature_en_cours"] = c
            state["reponse_finale"] = "❓ Répondez **oui** pour ajouter un autre composant ou **non** pour terminer."
            state["action_buttons"] = ["Oui", "Non"]
            return state

    return state