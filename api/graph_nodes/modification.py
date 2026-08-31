"""
Modification node — flux conversationnel guidé.

Machine d'état dans state["modification_en_cours"] :
  étape 0 : détecter l'entité (client / fournisseur) et éventuellement l'ID
  étape ATTENTE_ENTITE        : attendre que l'utilisateur tape le code/nom
  étape ATTENTE_CHAMP         : afficher les champs actuels, attendre le choix du champ
  étape ATTENTE_VALEUR        : confirmer l'ancienne valeur, attendre la nouvelle
  étape ATTENTE_CONFIRMATION  : demander oui/non, puis appliquer
"""

import logging
import re
import unicodedata

from api.mcp_pool import pool as mcp_pool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Mapping des champs : libellé affiché → clé MCP
# ─────────────────────────────────────────────────────────────────────────────
CHAMPS_TIERS = {
    "intitule":     {"label": "Nom / raison sociale", "cle_data": "CT_Intitule"},
    "adresse":      {"label": "Adresse",              "cle_data": "CT_Adresse"},
    "complement":   {"label": "Complément d'adresse", "cle_data": "CT_Complement"},
    "code_postal":  {"label": "Code postal",          "cle_data": "CT_CodePostal"},
    "ville":        {"label": "Ville",                "cle_data": "CT_Ville"},
    "pays":         {"label": "Pays",                 "cle_data": "CT_Pays"},
    "contact":      {"label": "Contact principal",    "cle_data": "CT_Contact"},
    "telephone":    {"label": "Téléphone",            "cle_data": "CT_Telephone"},
    "email":        {"label": "Email",                "cle_data": "CT_Email"},
    "site":         {"label": "Site web",             "cle_data": "CT_Site"},
    "ct_validite":  {"label": "Statut (VALIDE / BLOQUE / SUSPECT)", "cle_data": "ct_validite"},
}

# Alias → clé canonique
ALIAS_CHAMPS = {
    # intitule
    "nom": "intitule", "raison sociale": "intitule", "raison_sociale": "intitule",
    "intitule": "intitule", "intitulé": "intitule",
    # adresse
    "adresse": "adresse",
    # complement
    "complement": "complement", "complément": "complement",
    # code_postal
    "code postal": "code_postal", "code_postal": "code_postal", "cp": "code_postal",
    # ville
    "ville": "ville",
    # pays
    "pays": "pays",
    # contact
    "contact": "contact",
    # telephone
    "telephone": "telephone", "téléphone": "telephone", "tel": "telephone", "tél": "telephone",
    # email
    "email": "email", "mail": "email", "e-mail": "email",
    # site
    "site": "site", "web": "site", "site web": "site",
    # ct_validite
    "statut": "ct_validite", "validite": "ct_validite", "validité": "ct_validite",
    "sommeil": "ct_validite",
}

CHAMPS_ARTICLE = {
    "designation":  {"label": "Désignation", "cle_data": "AR_Design"},
    "prix_achat":   {"label": "Prix d'achat", "cle_data": "AR_PrixAch"},
    "prix_vente":   {"label": "Prix de vente", "cle_data": "AR_PrixVen"},
    "type_article": {"label": "Type d'article (0=Standard...)", "cle_data": "AR_Type"},
}

ALIAS_CHAMPS_ARTICLE = {
    "designation": "designation", "désignation": "designation", "nom": "designation",
    "prix_achat": "prix_achat", "prix d'achat": "prix_achat", "achat": "prix_achat",
    "prix_vente": "prix_vente", "prix de vente": "prix_vente", "vente": "prix_vente",
    "type_article": "type_article", "type": "type_article",
}


def _normaliser(texte: str) -> str:
    """Minuscule + suppression accents."""
    t = unicodedata.normalize("NFKD", texte.strip().lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _resoudre_champ(texte: str, entity_type: str = "client") -> str | None:
    """Retourne la clé canonique du champ ou None si non reconnu."""
    t = _normaliser(texte)
    alias_dict = ALIAS_CHAMPS_ARTICLE if entity_type == "article" else ALIAS_CHAMPS
    
    # Correspondance directe
    if t in alias_dict:
        return alias_dict[t]
    # Correspondance partielle
    for alias, cle in alias_dict.items():
        if alias in t or t in alias:
            return cle
    return None


def _formater_fiche(entity_type: str, data: dict) -> str:
    """Formate la fiche d'une entité avec tous ses champs modifiables."""
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
    t = _normaliser(texte)
    return bool(re.match(r"^(oui|o|yes|y|ok|confirme?r?|valide?r?|ouais)$", t))


def _est_non(texte: str) -> bool:
    t = _normaliser(texte)
    return bool(re.match(r"^(non|no|n|annuler?|annulation|stop|abandonner?)$", t))


# ─────────────────────────────────────────────────────────────────────────────
# Nœud principal
# ─────────────────────────────────────────────────────────────────────────────
async def noeud_modification(state, _parse_mcp_response):
    """
    Point d'entrée unique pour toutes les étapes de modification guidée.
    L'état de la conversation est stocké dans state["modification_en_cours"].
    """
    logger.info("⚡ [Modification guidée] entrée")
    action   = state.get("action", "")
    question = state.get("demande_brute", "").strip()
    mod      = state.get("modification_en_cours") or {}

    # ── Déterminer le type d'entité ──────────────────────────────────────────
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

    # ════════════════════════════════════════════════════════════════════════
    # INIT : première fois → chercher si un ID est déjà dans la demande
    # ════════════════════════════════════════════════════════════════════════
    if etape == "INIT":
        # Tenter d'extraire un code directement depuis state ou la demande
        entity_id = None
        if entity_type == "article":
            entity_id = state.get("ref_article") or state.get("nom_article_brut") or ""
        elif entity_type == "client":
            entity_id = state.get("code_client") or state.get("nom_client_brut") or ""
        else:
            entity_id = state.get("code_fournisseur") or state.get("code_client") or state.get("nom_client_brut") or ""

        # Si l'ID est juste "MODIFIER" ou vide, ignorer
        if entity_id and len(entity_id) <= 3 and entity_id.upper() in ("MOD", "CLI", "FOU", "ART"):
            entity_id = ""

        if entity_id:
            # Tenter de lire directement
            result = await _lire_entite(entity_type, entity_id, _parse_mcp_response)
            if result and result.get("statut") == "SUCCES":
                return _etape_afficher_champs(state, entity_type, entity_id, result)

        # Demander l'identifiant
        if entity_type == "article":
            label = "article"
        else:
            label = "client" if entity_type == "client" else "fournisseur"
        state["modification_en_cours"] = {"etape": "ATTENTE_ENTITE", "entity_type": entity_type}
        state["reponse_finale"] = f"🔍 Quel {label} souhaitez-vous modifier ? (code ou nom)"
        return state

    # ════════════════════════════════════════════════════════════════════════
    # ATTENTE_ENTITE : l'utilisateur vient de taper le code/nom
    # ════════════════════════════════════════════════════════════════════════
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

    # ════════════════════════════════════════════════════════════════════════
    # ATTENTE_CHAMP : l'utilisateur choisit le champ à modifier
    # ════════════════════════════════════════════════════════════════════════
    if etape == "ATTENTE_CHAMP":
        champ_cle = _resoudre_champ(question, entity_type)
        champs_dict = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS
        
        if not champ_cle:
            # Lister les choix à nouveau
            champs_liste = ", ".join(
                f"**{meta['label']}**" for meta in champs_dict.values()
            )
            exemples = "**désignation**, **prix d'achat**" if entity_type == "article" else "**adresse**, **email**, **statut**"
            state["reponse_finale"] = (
                f"❓ Champ non reconnu : « {question} »\n\n"
                f"Champs disponibles : {champs_liste}\n\n"
                f"Exemple : tapez {exemples}…"
            )
            state["modification_en_cours"] = mod
            return state

        meta          = champs_dict[champ_cle]
        current_data  = mod.get("current_data", {})
        ancienne_val  = current_data.get(meta["cle_data"], "")
        if ancienne_val is None:
            ancienne_val = ""

        mod["etape"]          = "ATTENTE_VALEUR"
        mod["champ_choisi"]   = champ_cle
        mod["ancienne_valeur"] = ancienne_val
        state["modification_en_cours"] = mod

        state["reponse_finale"] = (
            f"✏️ **{meta['label']}**\n"
            f"  Valeur actuelle : `{ancienne_val if ancienne_val != '' else '—'}`\n\n"
            f"Quelle est la nouvelle valeur ?"
        )
        return state

    # ════════════════════════════════════════════════════════════════════════
    # ATTENTE_VALEUR : l'utilisateur tape la nouvelle valeur
    # ════════════════════════════════════════════════════════════════════════
    if etape == "ATTENTE_VALEUR":
        nouvelle_val  = question.strip()
        champ_cle     = mod.get("champ_choisi", "")
        ancienne_val  = mod.get("ancienne_valeur", "")
        champs_dict   = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS
        meta          = champs_dict.get(champ_cle, {})
        label         = meta.get("label", champ_cle)
        entity_id     = mod.get("entity_id", "")

        mod["etape"]          = "ATTENTE_CONFIRMATION"
        mod["nouvelle_valeur"] = nouvelle_val
        state["modification_en_cours"] = mod

        state["reponse_finale"] = (
            f"✅ Confirmez-vous cette modification ?\n\n"
            f"**{label}** de `{entity_id}` :\n"
            f"  `{ancienne_val if ancienne_val != '' else '—'}` → `{nouvelle_val}`\n"
        )
        state["action_buttons"] = ["Oui", "Non"]
        return state

    # ════════════════════════════════════════════════════════════════════════
    # ATTENTE_CONFIRMATION : oui / non
    # ════════════════════════════════════════════════════════════════════════
    if etape == "ATTENTE_CONFIRMATION":
        if _est_non(question):
            state["modification_en_cours"] = {}
            state["reponse_finale"] = "🛑 Modification annulée."
            return state

        if not _est_oui(question):
            state["reponse_finale"] = (
                "❓ Répondez **oui** pour confirmer ou **non** pour annuler."
            )
            state["modification_en_cours"] = mod
            return state

        # ── Appliquer la modification ────────────────────────────────────
        champ_cle    = mod.get("champ_choisi", "")
        nouvelle_val = mod.get("nouvelle_valeur", "")
        entity_id    = mod.get("entity_id", "")
        entity_type  = mod.get("entity_type", "client")

        kwargs = {}
        
        if entity_type == "article":
            param_key = "ref_article"
            if champ_cle in ("prix_achat", "prix_vente"):
                try:
                    kwargs[champ_cle] = float(str(nouvelle_val).replace(",", "."))
                except ValueError:
                    kwargs[champ_cle] = 0.0
            elif champ_cle == "type_article":
                try:
                    kwargs[champ_cle] = int(nouvelle_val)
                except ValueError:
                    kwargs[champ_cle] = 0
            else:
                kwargs[champ_cle] = nouvelle_val
        else:
            param_key = f"code_{entity_type}"
            if champ_cle == "ct_validite":
                val_up = str(nouvelle_val).strip().upper()
                kwargs["validite"] = "BLOQUE" if val_up in ("BLOQUE", "SOMMEIL", "1") else "VALIDE"
            else:
                kwargs[champ_cle] = nouvelle_val

        tool_name = f"modifier_{entity_type}"

        try:
            raw  = await mcp_pool.call("actions", tool_name, {param_key: entity_id, **kwargs})
            data = _parse_mcp_response(raw)
        except Exception as e:
            logger.exception("[Modification] erreur MCP %s", e)
            data = {"statut": "ERREUR", "message": str(e)}

        state["modification_en_cours"] = {}
        if data.get("statut") == "SUCCES":
            champs_dict = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS
            meta  = champs_dict.get(champ_cle, {})
            label = meta.get("label", champ_cle)
            state["reponse_finale"] = (
                f"✅ **{label}** mis à jour pour `{entity_id}`.\n"
                f"{data.get('message', '')}"
            )
        else:
            state["reponse_finale"] = (
                f"❌ Erreur lors de la mise à jour : {data.get('message', 'inconnu')}"
            )
        return state

    # ── Cas inattendu ──────────────────────────────────────────────────────
    state["modification_en_cours"] = {}
    state["reponse_finale"] = "❌ État de modification inattendu. Veuillez recommencer."
    return state


# ─────────────────────────────────────────────────────────────────────────────
# noeud_modification_confirmation
# Appelé par le graphe lorsque modification_en_cours est actif et qu'un
# nouveau message arrive (router). Délègue directement à noeud_modification.
# ─────────────────────────────────────────────────────────────────────────────
async def noeud_modification_confirmation(state, _parse_mcp_response):
    """Continuation du flux guidé (délègue à noeud_modification)."""
    return await noeud_modification(state, _parse_mcp_response)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────
async def _lire_entite(entity_type: str, entity_id: str, _parse_mcp_response) -> dict | None:
    if entity_type == "article":
        tool_name = "lire_article"
        param_key = "ref_article"
    else:
        tool_name = "lire_client" if entity_type == "client" else "lire_fournisseur"
        param_key = "code_client"  if entity_type == "client" else "code_fournisseur"
    try:
        raw    = await mcp_pool.call("actions", tool_name, {param_key: entity_id})
        return _parse_mcp_response(raw)
    except Exception as e:
        logger.exception("[Modification] _lire_entite erreur: %s", e)
        return {"statut": "ERREUR", "message": str(e)}


def _etape_afficher_champs(state, entity_type: str, entity_id: str, data: dict) -> dict:
    """Construit la réponse qui affiche la fiche et demande quel champ modifier."""
    fiche = _formater_fiche(entity_type, data)
    champs_dict = CHAMPS_ARTICLE if entity_type == "article" else CHAMPS_TIERS

    champs_liste = "\n".join(
        f"  • **{meta['label']}**" for meta in champs_dict.values()
    )
    
    id_key = data.get("AR_Ref", entity_id) if entity_type == "article" else data.get("CT_Num", entity_id)
    
    state["modification_en_cours"] = {
        "etape":       "ATTENTE_CHAMP",
        "entity_type": entity_type,
        "entity_id":   id_key,
        "current_data": data,
    }
    
    exemples = "**désignation**, **prix d'achat**" if entity_type == "article" else "**adresse**, **email**, **statut**, **encours**"
    state["reponse_finale"] = (
        f"{fiche}\n\n"
        f"🔧 **Quel champ souhaitez-vous modifier ?**\n"
        f"{champs_liste}\n\n"
        f"Tapez le nom du champ (ex : {exemples}…)"
    )
    return state
