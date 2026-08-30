"""Module providing the confirmation node used by the orchestrator.
It validates actions, interacts with the hub for validation, prepares pending
action details, and returns an updated state awaiting user confirmation."""

import json
import logging

logger = logging.getLogger(__name__)

async def noeud_confirmation(state, TYPES_DOC_FABRICATION, _hub_valider_demande, _construire_detail_confirmation):
    """Validate the action and prepare a confirmation request.

    Parameters
    ----------
    state : dict
        Current workflow state.
    TYPES_DOC_FABRICATION : iterable
        Document types considered as fabrication.
    _hub_valider_demande : coroutine
        Callable to send a validation request to the hub.
    _construire_detail_confirmation : callable
        Function that builds a human‑readable detail string for the confirmation.

    Returns
    -------
    dict
        Updated state with validation results and pending action information.
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
            "MODIFIER_STATUT":   ["code_client"],
            "GENERER_DOC":       ["ref_article"],
            "TRANSFORMER_DOC":   ["num_piece", "type_doc"],
            "CREER_AVOIR":       ["num_piece"],
            "REGLEMENT":         ["num_piece"],
            "MOUVEMENT_STOCK":   ["ref_article"],
            "PROPOSITION_ACHAT": ["ref_article"],
        }
        champs_requis = champs_requis_map.get(act, [])
        if act == "MODIFIER_STATUT":
            if state.get("code_client") or state.get("code_fournisseur"):
                champs_requis = []

    _code_pour_hub = state["code_client"]
    if type_d == "BL_ACHAT" or act in ("REGLEMENT", "PROPOSITION_ACHAT"):
        _code_pour_hub = ""

    hub_result = await _hub_valider_demande("ECRITURE", {
        "action": act,
        "code_client": _code_pour_hub,
        "nom_client_brut": state.get("nom_client_brut", ""),
        "ref_article": state["ref_article"],
        "quantite": state["quantite"],
        "num_piece": state["num_piece"],
        "type_doc": state["type_doc"],
        "champs_requis": champs_requis,
    })
    state["hub_validation"] = json.dumps(hub_result, ensure_ascii=False)

    if not hub_result.get("valide", True):
        state["validation_ok"] = False
        state["reponse_brute"] = f"🚫 Validation refusée : {hub_result.get('message')}"
        return state

    detail = _construire_detail_confirmation(state)

    state["pending_action"] = {
        "action":                state["action"],
        "code_client":           state["code_client"],
        "code_fournisseur":      state.get("code_fournisseur", ""),
        "nom_client_brut":       state.get("nom_client_brut", ""),
        "ref_article":           state["ref_article"],
        "quantite":              state["quantite"],
        "num_piece":             state["num_piece"],
        "type_doc":              state["type_doc"],
        "mode_paiement":         state["mode_paiement"],
        "numero_piece_paiement": state.get("numero_piece_paiement", ""),
        "intitule":              state.get("intitule", ""),
        "adresse":               state.get("adresse", ""),
        "complement":            state.get("complement", ""),
        "code_postal":           state.get("code_postal", ""),
        "ville":                 state.get("ville", ""),
        "pays":                  state.get("pays", ""),
        "contact":               state.get("contact", ""),
        "telephone":             state.get("telephone", ""),
        "email":                 state.get("email", ""),
        "site":                  state.get("site", ""),
        "ct_validite":           state.get("ct_validite", "VALIDE"),
        "ct_sommeil":            state.get("ct_sommeil", 0),
        "ct_encours":            state.get("ct_encours", 0.0),
        "pending_document":      state.get("pending_document", {}),
    }
    state["statut_confirmation"] = "ATTENTE"
    state["validation_ok"] = False
    state["reponse_finale"] = f"Veuillez valider l'action suivante : **{state['action']}**{detail}"
    return state