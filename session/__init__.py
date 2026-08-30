"""
session/ — Module de gestion d'état et de session.
Contient :
  - CopilotState : TypedDict de l'état
  - _etat_initial() : création état initial
  - _extraire_dernier_document() : extraction dernier document
  - Gestion draft/confirmation/alertes
"""

import json
from typing import TypedDict, Optional
from datetime import datetime


class CopilotState(TypedDict):
    """État du graphe LangGraph."""
    demande_brute: str
    intention: str
    action: str
    ambigue: bool
    score_confiance: float
    code_client: str
    ref_article: str
    quantite: float
    num_piece: str
    type_doc: str
    type_doc_code: int
    date_debut: str
    date_fin: str
    mode_paiement: str
    validation_ok: bool
    hub_validation: str
    reponse_brute: str
    rag_complement: str
    reponse_finale: str
    hallucination_flag: bool
    mem0_contexte: str
    dernier_type_doc: str
    dernier_num_piece: str
    dernier_code_client: str
    dernier_ref_article: str
    dernier_quantite: float
    plan_execution: list
    etape_courante: int
    nom_client_brut: str
    suggestion_en_attente: dict
    pending_action: dict
    pending_document: dict
    attente_complements: bool
    document_draft: dict
    statut_draft: str
    pdf_path: str
    num_of_resolu: str
    dernier_action_classifiee: str
    derniere_question_classifiee: str
    statut_confirmation: str
    ct_validite: str
    numero_piece_paiement: str


def _etat_initial(demande: str, contexte_session: dict | None = None) -> CopilotState:
    """Crée un état initial depuis une demande brute."""
    ctx = contexte_session or {}
    dd = ctx.get("dernier_document", {})
    _dernier_num = ctx.get("dernier_num_piece", "") or dd.get("num_piece", "")
    _dernier_type = ctx.get("dernier_type_doc", "") or dd.get("type_doc", "")
    
    return CopilotState(
        demande_brute=demande,
        intention="",
        action="",
        ambigue=False,
        score_confiance=1.0,
        code_client="",
        ref_article="",
        quantite=0.0,
        num_piece="",
        type_doc="",
        type_doc_code=0,
        date_debut="",
        date_fin="",
        mode_paiement="Virement",
        validation_ok=False,
        hub_validation="",
        reponse_brute="",
        rag_complement="",
        reponse_finale="",
        hallucination_flag=False,
        mem0_contexte="",
        dernier_type_doc=_dernier_type,
        dernier_num_piece=_dernier_num,
        dernier_code_client=ctx.get("dernier_code_client", ""),
        dernier_ref_article=ctx.get("dernier_ref_article", ""),
        dernier_quantite=ctx.get("dernier_quantite", 0.0),
        plan_execution=[],
        etape_courante=0,
        nom_client_brut=ctx.get("dernier_nom_client", ""),
        suggestion_en_attente={},
        pending_action=ctx.get("pending_action", {}),
        document_draft={},
        statut_draft="",
        pdf_path="",
        pending_document={},
        attente_complements=False,
        ct_validite=ctx.get("ct_validite", "VALIDE"),
        num_of_resolu="",
        dernier_action_classifiee=ctx.get("dernier_action_classifiee", ""),
        derniere_question_classifiee=ctx.get("derniere_question_classifiee", ""),
        statut_confirmation=ctx.get("statut_confirmation", ""),
        numero_piece_paiement="",
    )


def _extraire_dernier_document(final_state: dict) -> dict | None:
    """Extrait les infos du dernier document généré."""
    if final_state.get("action") != "GENERER_DOC":
        return None
    if not final_state.get("validation_ok", True):
        return None
    rb = final_state.get("reponse_brute", "") or ""
    try:
        data = json.loads(rb)
        if data.get("DO_Piece"):
            return {
                "type_doc": final_state.get("type_doc"),
                "num_piece": data["DO_Piece"],
                "code_client": final_state.get("code_client", ""),
                "ref_article": final_state.get("ref_article", ""),
            }
    except Exception:
        pass
    return None