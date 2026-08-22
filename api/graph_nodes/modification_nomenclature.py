"""
Noeud conversationnel guide : modification d'une nomenclature existante (BOM).
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


async def _lire_article_mcp(ref_ou_nom: str) -> dict:
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
    """Rend la nomenclature sous forme de tableau markdown (mieux stylé par le frontend)."""
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
    """Retry up to 2 times, then cancel the modification flow."""
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
    logger.info("[Modif Nomenclature] entree")
    question = state.get("demande_brute", "").strip()
    c = state.get("modification_nomenclature_en_cours") or {}
    etape = c.get("etape", "INIT")

    # ── INIT ────────────────────────────────────────────────────────────────
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

    # ── ATTENTE_PARENT ───────────────────────────────────────────────────────
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

    # ── ATTENTE_ACTION ───────────────────────────────────────────────────────
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

    # ── AJOUT : composant ────────────────────────────────────────────────────
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
        state["reponse_finale"] = "Souhaitez-vous ajouter un **commentaire** ? (tapez 'non' pour ignorer)"
        state["action_buttons"] = ["Non"]
        return state

    if etape == "ATTENTE_COMMENTAIRE_AJOUT":
        comment = ""
        if not re.match(r"^(non|no|n|rien)$", _normaliser(question)):
            comment = question[:69]
        try:
            await mcp_pool.call("actions", "creer_ligne_nomenclature", {
                "ref_parent": c["ref_parent"],
                "ref_composant": c["ref_cible"],
                "qte": c["qte_cible"],
                "commentaire": comment
            })
            state["dernier_message"] = (
                f"✅ **Ajout réussi** — **{c['ref_cible']}** ({c.get('design_cible', '')}) · "
                f"Qté={c['qte_cible']} ajouté à la nomenclature de **{c['ref_parent']}**."
            )
        except Exception as e:
            logger.error(f"[ajout nomenclature] {e}")
            state["dernier_message"] = (
                f"❌ **Échec de l'ajout** du composant **{c['ref_cible']}** : {e}"
            )
        c.pop("ref_cible", None)
        c.pop("qte_cible", None)
        c.pop("design_cible", None)
        c["etape"] = "INIT"
        state["modification_nomenclature_en_cours"] = c
        state["action_buttons"] = ["Ajouter", "Modifier", "Supprimer", "Terminer"]
        return await noeud_modification_nomenclature(state)

    # ── MODIFICATION : composant → quantite ──────────────────────────────────
    if etape == "ATTENTE_COMPOSANT_MODIF":
        data = await _lire_article_mcp(question)
        if data.get("statut") != "SUCCES":
            if _traiter_retry_modif_nom(state, c, "ATTENTE_COMPOSANT_MODIF",
                                        f"❌ Composant '{question}' introuvable. Veuillez reessayer :"):
                return state
            return state
        ref_cible = data["AR_Ref"]
        refs_actuels = [comp.get("ref_composant") for comp in c.get("composants", [])]
        if ref_cible not in refs_actuels:
            if _traiter_retry_modif_nom(state, c, "ATTENTE_COMPOSANT_MODIF",
                                        f"❌ Le composant **{ref_cible}** n'est pas dans cette nomenclature. Choisissez un composant existant :"):
                return state
            return state
        c["ref_cible"] = ref_cible
        c["etape"] = "ATTENTE_QTE_MODIF"
        c.setdefault("tentatives_champ", {}).pop("ATTENTE_COMPOSANT_MODIF", None)
        state["modification_nomenclature_en_cours"] = c
        state["reponse_finale"] = f"Modification de **{ref_cible}**. Quelle est la **nouvelle quantité** ?"
        state["action_buttons"] = []
        return state

    if etape == "ATTENTE_QTE_MODIF":
        try:
            qte = float(question.replace(",", "."))
        except ValueError:
            if _traiter_retry_modif_nom(state, c, "ATTENTE_QTE_MODIF",
                                        "❌ Veuillez saisir un nombre valide :"):
                return state
            return state
        try:
            await mcp_pool.call("actions", "modifier_ligne_nomenclature", {
                "ref_parent": c["ref_parent"],
                "ref_composant": c["ref_cible"],
                "qte": qte
            })
            state["dernier_message"] = (
                f"✅ **Modification réussie** — nouvelle quantité de **{c['ref_cible']}** : {qte}."
            )
        except Exception as e:
            logger.error(f"[modif nomenclature] {e}")
            state["dernier_message"] = f"❌ **Échec de la modification** de **{c['ref_cible']}** : {e}"
        c.pop("ref_cible", None)
        c["etape"] = "INIT"
        state["modification_nomenclature_en_cours"] = c
        state["action_buttons"] = ["Ajouter", "Modifier", "Supprimer", "Terminer"]
        return await noeud_modification_nomenclature(state)

    # ── SUPPRESSION ──────────────────────────────────────────────────────────
    if etape == "ATTENTE_COMPOSANT_SUPPR":
        data = await _lire_article_mcp(question)
        if data.get("statut") != "SUCCES":
            if _traiter_retry_modif_nom(state, c, "ATTENTE_COMPOSANT_SUPPR",
                                        f"❌ Composant '{question}' introuvable. Veuillez reessayer :"):
                return state
            return state
        ref_cible = data["AR_Ref"]
        refs_actuels = [comp.get("ref_composant") for comp in c.get("composants", [])]
        if ref_cible not in refs_actuels:
            if _traiter_retry_modif_nom(state, c, "ATTENTE_COMPOSANT_SUPPR",
                                        f"❌ Le composant **{ref_cible}** n'est pas dans cette nomenclature. Choisissez un composant existant :"):
                return state
            return state
        try:
            await mcp_pool.call("actions", "supprimer_ligne_nomenclature", {
                "ref_parent": c["ref_parent"],
                "ref_composant": ref_cible
            })
            state["dernier_message"] = f"✅ **Suppression réussie** — **{ref_cible}** retiré de la nomenclature."
        except Exception as e:
            logger.error(f"[suppr nomenclature] {e}")
            state["dernier_message"] = f"❌ **Échec de la suppression** du composant **{ref_cible}** : {e}"
        c["etape"] = "INIT"
        state["modification_nomenclature_en_cours"] = c
        state["action_buttons"] = ["Ajouter", "Modifier", "Supprimer", "Terminer"]
        return await noeud_modification_nomenclature(state)

    # Etat inconnu
    state["modification_nomenclature_en_cours"] = {}
    state["reponse_finale"] = "Etat inconnu, annulation de la modification de nomenclature."
    state["action_buttons"] = []
    return state