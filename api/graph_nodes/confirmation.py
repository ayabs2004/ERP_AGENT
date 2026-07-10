"""
Confirmation node for the orchestrator.
Extracted from orchestrateur_general.py lines 3931-4004.
"""

import json
import logging

logger = logging.getLogger(__name__)


async def noeud_confirmation(state, TYPES_DOC_FABRICATION, _hub_valider_demande, _construire_detail_confirmation):
    """
    Validates the action and asks for user confirmation if needed.
    """
    if state.get("statut_confirmation") == "CONFIRME":
        state["validation_ok"] = True
        return state

    act = state["action"]
    logger.info("\n🔧 [Hub] Validation...")

    if act in ("GENERER_DOC", "MOUVEMENT_STOCK") and state["quantite"] <= 0.0:
        state["validation_ok"] = False
        state["reponse_brute"] = "🚫 Validation refusée : Quantité manquante ou invalide (doit être > 0)"
        return state

    type_d = (state.get("type_doc") or "").upper()
    if type_d in TYPES_DOC_FABRICATION and state.get("code_client") == "PROD-INT":
        champs_requis = ["ref_article"]
    else:
        champs_requis_map = {
            "CREER_CLIENT":      ["code_client", "nom_client_brut"],
            "CREER_FOURNISSEUR": ["code_client", "nom_client_brut"],
            "MODIFIER_STATUT":   ["code_client"],  # Will be checked dynamically for client OR supplier
            "GENERER_DOC":       ["ref_article"],
            "TRANSFORMER_DOC":   ["num_piece", "type_doc"],
            "CREER_AVOIR":       ["num_piece"],
            "REGLEMENT":         ["num_piece"],
            "MOUVEMENT_STOCK":   ["ref_article"],
            "PROPOSITION_ACHAT": ["ref_article"],
        }
        champs_requis = champs_requis_map.get(act, [])
        
        # Special handling for MODIFIER_STATUT: accept either code_client OR code_fournisseur
        if act == "MODIFIER_STATUT":
            if state.get("code_client") or state.get("code_fournisseur"):
                champs_requis = []  # Already has required field

    _code_pour_hub = state["code_client"]
    if type_d == "BL_ACHAT" or act == "REGLEMENT":
        _code_pour_hub = ""

    hub_result = await _hub_valider_demande("ECRITURE", {
        "action": act, "code_client": _code_pour_hub,
        "nom_client_brut": state.get("nom_client_brut", ""),
        "ref_article": state["ref_article"], "quantite": state["quantite"],
        "num_piece": state["num_piece"], "type_doc": state["type_doc"],
        "champs_requis": champs_requis,
    })
    state["hub_validation"] = json.dumps(hub_result, ensure_ascii=False)

    if not hub_result.get("valide", True):
        state["validation_ok"] = False
        state["reponse_brute"] = f"🚫 Validation refusée : {hub_result.get('message')}"
        return state

    # Détail affiché : uniquement les champs pertinents pour l'action
    detail = _construire_detail_confirmation(state)

    # Sauvegarde de tout le contexte nécessaire pour réhydrater l'action
    state["pending_action"] = {
        "action":                state["action"],
        "code_client":           state["code_client"],
        "nom_client_brut":       state.get("nom_client_brut", ""),
        "ref_article":           state["ref_article"],
        "quantite":              state["quantite"],
        "num_piece":             state["num_piece"],
        "type_doc":              state["type_doc"],
        "mode_paiement":         state["mode_paiement"],
        "ct_validite":           state.get("ct_validite", "VALIDE"),
        "ct_encours_max":        state.get("ct_encours_max", 0.0),
        "numero_piece_paiement": state.get("numero_piece_paiement", ""),
    }
    state["statut_confirmation"] = "ATTENTE"
    state["validation_ok"] = False
    state["reponse_finale"] = f"Veuillez valider l'action suivante : **{state['action']}**{detail}"
    return state
