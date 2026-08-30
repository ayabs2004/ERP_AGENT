"""Module implementing a guided modification flow for entities (clients, fournisseurs, articles).
It handles user interactions to select an entity, choose a field, confirm changes,
and apply updates via the MCP pool."""
import logging
import re
import unicodedata

from api.mcp_pool import pool as mcp_pool

logger = logging.getLogger(__name__)

CHAMPS_TIERS = {
    "intitule": {"label": "Nom / raison sociale", "cle_data": "CT_Intitule"},
    "adresse": {"label": "Adresse", "cle_data": "CT_Adresse"},
    "complement": {"label": "Complément d'adresse", "cle_data": "CT_Complement"},
    "code_postal": {"label": "Code postal", "cle_data": "CT_CodePostal"},
    "ville": {"label": "Ville", "cle_data": "CT_Ville"},
    "pays": {"label": "Pays", "cle_data": "CT_Pays"},
    "contact": {"label": "Contact principal", "cle_data": "CT_Contact"},
    "telephone": {"label": "Téléphone", "cle_data": "CT_Telephone"},
    "email": {"label": "Email", "cle_data": "CT_Email"},
    "site": {"label": "Site web", "cle_data": "CT_Site"},
    "ct_validite": {"label": "Statut (VALIDE / BLOQUE / SUSPECT)", "cle_data": "ct_validite"},
}

ALIAS_CHAMPS = {
    "nom": "intitule",
    "raison sociale": "intitule",
    "raison_sociale": "intitule",
    "intitule": "intitule",
    "intitulé": "intitule",
    "adresse": "adresse",
    "complement": "complement",
    "complément": "complement",
    "code postal": "code_postal",
    "code_postal": "code_postal",
    "cp": "code_postal",
    "ville": "ville",
    "pays": "pays",
    "contact": "contact",
    "telephone": "telephone",
    "téléphone": "telephone",
    "tel": "telephone",
    "tél": "telephone",
    "email": "email",
    "mail": "email",
    "e-mail": "email",
    "site": "site",
    "web": "site",
    "site web": "site",
    "statut": "ct_validite",
    "validite": "ct_validite",
    "validité": "ct_validite",
    "sommeil": "ct_validite",
}

CHAMPS_ARTICLE = {
    "designation": {"label": "Désignation", "cle_data": "AR_Design"},
    "prix_achat": {"label": "Prix d'achat", "cle_data": "AR_PrixAch"},
    "prix_vente": {"label": "Prix de vente", "cle_data": "AR_PrixVen"},
    "type_article": {"label": "Type d'article (valeur numérique — voir fiche Sage)", "cle_data": "AR_Type"},
}

ALIAS_CHAMPS_ARTICLE = {
    "designation": "designation",
    "désignation": "designation",
    "nom": "designation",
    "prix_achat": "prix_achat",
    "prix d'achat": "prix_achat",
    "achat": "prix_achat",
    "prix_vente": "prix_vente",
    "prix de vente": "prix_vente",
    "vente": "prix_vente",
    "type_article": "type_article",
    "type": "type_article",
}


def _normaliser(texte: str) -> str:
    """Normalize a string: lowercase and strip accents."""
    t = unicodedata.normalize("NFKD", texte.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _resoudre_champ(texte: str, entity_type: str = "client") -> str | None:
    """Resolve a user-provided field name to its canonical key."""
    t = _normaliser(texte)
    alias_dict = ALIAS_CHAMPS_ARTICLE if entity_type == "article" else ALIAS_CHAMPS
    if t in alias_dict:
        return alias_dict[t]
    for alias, cle in alias_dict.items():
        if alias in t or t in alias:
            return cle
    return None


def _formater_fiche(entity_type: str, data: dict) -> str:
    """Format an entity's data into a readable fiche string."""
    code = data.get("AR_Ref") if entity_type == "article" else data.get("CT_Num", "?")
    champs_dict = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS
    lignes = [f"📋 **Fiche {entity_type}** `{code}` :\n"]
    for cle, meta in champs_dict.items():
        val = data.get(meta["cle_data"], "")
        if val is None or val == "":
            val = "—"
        lignes.append(f"  🔸 **{meta['label']}** : {val}")
    ligne_sep = "────────────────────────────────────────────────────"
    return "\n".join(lignes[:1] + [ligne_sep] + lignes[1:])


def _est_oui(texte: str) -> bool:
    """Return True if the text matches a positive affirmation."""
    t = _normaliser(texte)
    return bool(re.match(r"^(oui|o|yes|y|ok|confirme?r?|valide?r?|ouais)$", t))


def _est_non(texte: str) -> bool:
    """Return True if the text matches a negative response."""
    t = _normaliser(texte)
    return bool(re.match(r"^(non|no|n|annuler?|annulation|stop|abandonner?)$", t))


async def noeud_modification(state, _parse_mcp_response):
    """Main entry point handling all steps of the guided modification flow."""
    logger.info("⚡ [Modification guidée] entrée")
    action = state.get("action", "")
    question = state.get("demande_brute", "").strip()
    mod = state.get("modification_en_cours") or {}
    if mod.get("entity_type"):
        entity_type = mod["entity_type"]
    elif action == "MODIFIER_ARTICLE":
        entity_type = "article"
    elif action == "MODIFIER_CLIENT":
        entity_type = "client"
    elif action == "MODIFIER_FOURNISSEUR":
        entity_type = "fournisseur"
    elif "fournisseur" in question.lower() or "supplier" in question.lower():
        entity_type = "fournisseur"
    elif "article" in question.lower() or "produit" in question.lower():
        entity_type = "article"
    else:
        entity_type = "client"
    etape = mod.get("etape", "INIT")
    if etape == "INIT":
        entity_id = None
        if entity_type == "article":
            entity_id = state.get("ref_article") or state.get("nom_article_brut") or ""
        elif entity_type == "client":
            entity_id = state.get("code_client") or state.get("nom_client_brut") or ""
        else:
            entity_id = state.get("code_fournisseur") or state.get("code_client") or state.get("nom_client_brut") or ""
        if entity_id and len(entity_id) <= 3 and entity_id.upper() in ("MOD", "CLI", "FOU", "ART"):
            entity_id = ""
        if entity_id:
            result = await _lire_entite(entity_type, entity_id, _parse_mcp_response)
            if result and result.get("statut") == "SUCCES":
                return _etape_afficher_champs(state, entity_type, entity_id, result)
        if entity_type == "article":
            label = "article"
        else:
            label = "client" if entity_type == "client" else "fournisseur"
        state["modification_en_cours"] = {"etape": "ATTENTE_ENTITE", "entity_type": entity_type}
        state["reponse_finale"] = f"🔍 Quel {label} souhaitez-vous modifier ? (code ou nom)"
        return state
    if etape == "ATTENTE_ENTITE":
        entity_id = question.strip()
        result = await _lire_entite(entity_type, entity_id, _parse_mcp_response)
        if not result or result.get("statut") != "SUCCES":
            msg = result.get("message", "introuvable") if result else "introuvable"
            label = "client" if entity_type == "client" else "fournisseur"
            tentatives = mod.get("tentatives_champ", 0) + 1
            if tentatives >= 2:
                state["modification_en_cours"] = {}
                state["reponse_finale"] = f"❌ {label.capitalize()} introuvable deux fois. Modification annulée."
                return state
            mod["tentatives_champ"] = tentatives
            state["reponse_finale"] = (
                f"❌ {label.capitalize()} « {entity_id} » non trouvé : {msg}\n"
                f"Réessayez avec le code exact ou le nom complet. (tentative {tentatives}/2)"
            )
            state["modification_en_cours"] = mod
            return state
        return _etape_afficher_champs(state, entity_type, entity_id, result)
    if etape == "ATTENTE_CHAMP":
        champ_cle = _resoudre_champ(question, entity_type)
        champs_dict = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS
        if not champ_cle:
            champs_liste = ", ".join(f"**{meta['label']}**" for meta in champs_dict.values())
            exemples = "**désignation**, **prix d'achat**" if entity_type == "article" else "**adresse**, **email**, **statut**"
            state["reponse_finale"] = (
                f"❓ Champ non reconnu : « {question} »\n\n"
                f"Champs disponibles : {champs_liste}\n\n"
                f"Exemple : tapez {exemples}…"
            )
            state["modification_en_cours"] = mod
            return state
        meta = champs_dict[champ_cle]
        current_data = mod.get("current_data", {})
        ancienne_val = current_data.get(meta["cle_data"], "")
        if ancienne_val is None:
            ancienne_val = ""
        mod["etape"] = "ATTENTE_VALEUR"
        mod["champ_choisi"] = champ_cle
        mod["ancienne_valeur"] = ancienne_val
        state["modification_en_cours"] = mod
        state["reponse_finale"] = (
            f"✏️ **{meta['label']}**\n"
            f"  Valeur actuelle : `{ancienne_val if ancienne_val != '' else '—'}`\n\n"
            f"Quelle est la nouvelle valeur ?"
        )
        return state
    if etape == "ATTENTE_VALEUR":
        nouvelle_val = question.strip()
        champ_cle = mod.get("champ_choisi", "")
        ancienne_val = mod.get("ancienne_valeur", "")
        champs_dict = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS
        meta = champs_dict.get(champ_cle, {})
        label = meta.get("label", champ_cle)
        entity_id = mod.get("entity_id", "")
        mod["etape"] = "ATTENTE_CONFIRMATION"
        mod["nouvelle_valeur"] = nouvelle_val
        state["modification_en_cours"] = mod
        state["reponse_finale"] = (
            f"✅ Confirmez-vous cette modification ?\n\n"