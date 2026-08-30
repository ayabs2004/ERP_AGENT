"""Module providing the suggestion executor for the orchestrator.

This module defines the asynchronous function `_executer_suggestion` which processes
different suggestion types, interacts with the MCP pool, updates the session
context, and returns a textual status message.
"""

from datetime import datetime
from api.mcp_pool import pool as mcp_pool
import logging
import json
from datetime import datetime
logger = logging.getLogger(__name__)

async def _executer_suggestion(suggestion: dict, contexte_session: dict, _STATUTS_ERREUR_MCP,
                               _parse_mcp_response, _mcp_workflow_bf, _mcp_workflow_of,
                               _mcp_workflow_bl, generer_preview, _safe_str) -> str:
    """Execute a suggestion action and return a status message.

    The function examines the `type` field of the suggestion and performs the
    corresponding operation (e.g., creating invoices, drafts, payments, etc.).
    It updates `contexte_session` with relevant information and handles MCP
    responses, returning a human‑readable string describing the outcome.
    """
    type_sugg = suggestion.get("type", "")
    params    = suggestion.get("params", {})
    logger.info(f"\n   ✅ [Suggestion] {suggestion.get('description', type_sugg)}")

    if type_sugg == "CREER_FACTURE_ACHAT":
        num_br           = params.get("num_br", "")
        code_fournisseur = params.get("code_fournisseur", "")
        nom_fournisseur  = params.get("nom_fournisseur", code_fournisseur)
        montant          = float(params.get("montant", 0.0))
        try:
            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_br,
                "type_destination": "FA_ACHAT",
            })
            data   = _parse_mcp_response(raw)
            num_fa = data.get("DO_Piece") or data.get("num_piece_dest", "?")
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur création facture fournisseur depuis {num_br}.")
            contexte_session["dernier_document"] = {
                "type_doc": "FA_ACHAT", "num_piece": num_fa, "code_client": code_fournisseur,
            }
            contexte_session["suggestion_en_attente"] = {}
            return (
                f"✅ Facture Fournisseur créée depuis {num_br} !\n"
                f"   • Numéro Facture  : {num_fa}\n"
                f"   • Fournisseur     : {nom_fournisseur}\n"
                f"   • Montant HT      : {montant:.2f} €\n"
                f"   ℹ️  Document enregistré en achat (DO_Domaine=1, DO_Type=16)"
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"❌ Erreur création facture fournisseur : {_safe_str(e)}"
    elif type_sugg == "DRAFT_FACTURE_ACHAT_DEPUIS_BL":
        params = suggestion.get("params", {})
        draft = {
        "type_doc":         "FA_ACHAT",
        "code_fournisseur": params.get("code_fournisseur", ""),
        "ref_article":      params.get("ref_article", ""),
        "quantite":         params.get("quantite", 0.0),
        "prix_unitaire":    params.get("prix_unitaire", 0.0),
        "date_str":         datetime.now().strftime("%d/%m/%Y"),
        "num_piece_source": params.get("num_br", ""),
    }
        contexte_session["document_draft"] = draft
        contexte_session["statut_draft"]   = "PREVIEW"
        contexte_session["suggestion_en_attente"] = {}
        texte, pdf_path = await generer_preview(draft)
        contexte_session["pdf_path"] = pdf_path
        return texte
    elif type_sugg == "CREER_FACTURE":
        num_bl      = params.get("num_bl", "")
        code_client = params.get("code_client", "")
        nom_client  = params.get("nom_client", code_client)
        montant     = float(params.get("montant", 0.0))
        try:
            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_bl,
                "type_destination": "FACTURE",
            })
            data   = _parse_mcp_response(raw)
            num_fa = data.get("DO_Piece") or data.get("num_piece_dest", "?")
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur création facture depuis {num_bl}.")
            contexte_session["dernier_document"] = {
                "type_doc": "FACTURE", "num_piece": num_fa, "code_client": code_client,
            }
            contexte_session["suggestion_en_attente"] = {
                "type": "REGLER_FACTURE",
                "description": f"Régler la facture {num_fa}",
                "params": {
                    "num_piece": num_fa, "mode_paiement": "Virement",
                    "montant": montant, "nom_client": nom_client,
                },
            }
            return (
                f"✅ Facture créée depuis {num_bl} !\n"
                f"   • Numéro Facture : {num_fa}\n"
                f"   • Client         : {nom_client}\n"
                f"   • Montant        : {montant:.2f} €"
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"❌ Erreur création facture : {_safe_str(e)}"

    elif type_sugg == "REGLER_FACTURE":
        num_piece     = params.get("num_piece", "")
        mode_paiement = params.get("mode_paiement", "Virement")
        montant       = float(params.get("montant", 0.0))
        nom_client    = params.get("nom_client", "")
        try:
            raw  = await mcp_pool.call("actions", "enregistrer_reglement_facture", {
                "num_piece": num_piece, "mode_paiement": mode_paiement,
            })
            data = _parse_mcp_response(raw)
            if data.get("statut") in _STATUTS_ERREUR_MCP:
                return data.get("message", f"❌ Erreur règlement {num_piece}.")
            contexte_session["suggestion_en_attente"] = {}
            return (
                f"✅ Règlement enregistré !\n"
                f"   • Facture  : {num_piece}\n"
                f"   • Client   : {nom_client}\n"
                f"   • Montant  : {montant:.2f} €\n"
                f"   • Mode     : {mode_paiement}"
            )

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"❌ Erreur règlement : {_safe_str(e)}"

    elif type_sugg == "CREER_BF":
        ref_article = params.get("ref_article", "")
        quantite    = float(params.get("quantite", 0.0))
        num_of      = params.get("num_of", "")
        code_client = params.get("code_client", "PROD-INT")
        try:
            result = await _mcp_workflow_bf(ref_article, quantite, num_of, code_client)
            if result.get("statut") in _STATUTS_ERREUR_MCP:
                return result.get("message", "❌ Erreur BF.")
            contexte_session["suggestion_en_attente"] = {}
            return result.get("message", "✅ BF créé.")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"❌ Erreur BF : {_safe_str(e)}"

    elif type_sugg == "CREER_OF":
        ref_article = params.get("ref_article", "")
        quantite    = float(params.get("quantite", 0.0))
        data_bl     = params.get("data_bl_en_attente", {})
        rapport     = []
        try:
            result_of = await _mcp_workflow_of(ref_article, quantite, "PROD-INT")
            rapport.append(result_of.get("message", ""))
            if result_of.get("statut") == "GENERE":
                num_of  = result_of.get("DO_Piece", "")
                sugg_bf = result_of.get("suggestion_bf", {})
                if sugg_bf:
                    contexte_session["suggestion_en_attente"] = {
                        "type": "CREER_BF_PUIS_BL",
                        "description": f"Créer le BF pour OF {num_of} puis le BL",
                        "params": {**sugg_bf, "data_bl_apres_bf": data_bl},
                    }
            elif result_of.get("statut") in _STATUTS_ERREUR_MCP:
                contexte_session["suggestion_en_attente"] = {}
            return "\n\n".join(r for r in rapport if r)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"❌ Erreur OF : {_safe_str(e)}"

    elif type_sugg == "CREER_BF_PUIS_BL":
        ref_article = params.get("ref_article", "")
        quantite    = float(params.get("quantite", 0.0))
        num_of      = params.get("num_of", "")
        data_bl     = params.get("data_bl_apres_bf", {})
        rapport     = []

        try:
            result_bf = await _mcp_workflow_bf(ref_article, quantite, num_of, "PROD-INT")
            rapport.append(result_bf.get("message", ""))
            if result_bf.get("statut") not in _STATUTS_ERREUR_MCP and data_bl:
                result_bl = await _mcp_workflow_bl(
                    data_bl.get("code_client", ""),
                    data_bl.get("ref_article", ""),
                    float(data_bl.get("quantite", 0.0)),
                    float(data_bl.get("prix_unitaire", 0.0)),
                )
                rapport.append(result_bl.get("message", ""))
                if result_bl.get("statut") == "GENERE":
                    sugg_fa = result_bl.get("suggestion_facture", {})
                    if sugg_fa:
                        num_bl = sugg_fa.get("num_bl", "")
                        contexte_session["suggestion_en_attente"] = {
                            "type": "CREER_FACTURE",
                            "description": f"Créer la facture pour BL {num_bl}",
                            "params": sugg_fa,
                        }
                    else:
                        contexte_session["suggestion_en_attente"] = {}
                else:
                    contexte_session["suggestion_en_attente"] = {}
            else:
                contexte_session["suggestion_en_attente"] = {}
            return "\n\n".join(r for r in rapport if r)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return f"❌ Erreur BF→BL : {_safe_str(e)}"

    elif type_sugg == "FACTURE_ACHAT":
        num_bl = params.get("num_bl", "")
        try: