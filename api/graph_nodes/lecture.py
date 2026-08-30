"""Lecture node for the orchestrator.

Provides the asynchronous `noeud_lecture` function that interprets the
requested action in the given state, optionally resolves a client code,
dispatches the appropriate nl2sql tool via the MCP pool and returns the
updated state with the raw response. All SQL is performed through neutralized
nl2sql helpers; no direct SQL strings are embedded in this file.
"""

import json
from api.mcp_pool import pool as mcp_pool
import logging

logger = logging.getLogger(__name__)

async def noeud_lecture(state, _rechercher_client_par_nom, _safe_str):
    """Execute a read operation based on the current state.

    Parameters
    ----------
    state: dict
        The mutable conversation state containing keys such as ``action``,
        ``code_client``, ``nom_client_brut`` and other parameters required by
        the selected tool.
    _rechercher_client_par_nom: callable
        Asynchronous function that receives a client name and returns the
        corresponding client code or ``None`` if not found.
    _safe_str: callable
        Unused placeholder kept for compatibility with the original signature.

    Returns
    -------
    dict
        The updated ``state`` dictionary with a ``reponse_brute`` entry containing
        either the tool result, an error message or a not‑found indication.
    """
    logger.info("📊 [Agent Lecture] Interrogation Sage...")
    act = state["action"]

    _actions_client_requis = {"FICHE_CLIENT","STATUT_CLIENT","TOUTES_FACTURES_CLIENT","FACTURES_NON_REGLEES","FACTURES_NON_REGLEES_FOURN","LIRE_ENCOURS_CLIENT"}
    if act in _actions_client_requis and not state.get("code_client"):
        nom_candidat = state.get("nom_client_brut") or state.get("dernier_code_client") or ""
        if nom_candidat:
            code = await _rechercher_client_par_nom(nom_candidat)
            if code:
                state["code_client"] = code
            else:
                state["reponse_brute"] = json.dumps({
                "statut": "NON_TROUVE",
                "message": f"Client '{nom_candidat}' introuvable dans la base."
            }, ensure_ascii=False)
                return state

    try:
        tool_map = {
            "AFFICHER_NOMENCLATURE": ("nl2sql", "lire_nomenclature_article",
                           {"ref_article": state.get("ref_article", "")}),
            "TOP_CLIENTS": ("nl2sql", "analyser_top_clients_ca", {"top_n": int(state.get("quantite") or 5)}),
            "LISTE_CLIENTS": ("nl2sql", "lister_clients_actifs", {}),
            "LISTE_ARTICLES": ("nl2sql", "lister_articles_catalogue", {}),
            "PALMARES_ARTICLES": ("nl2sql", "analyser_palmares_articles", {"top_n": int(state.get("quantite") or 3)}),
            "CA_GLOBAL": ("nl2sql", "calculer_ca_global_periode", {
                                "date_debut": state.get("date_debut", ""),
                                "date_fin":   state.get("date_fin", ""),
                            }),
            "CLIENTS_BAISSE": ("nl2sql", "detecter_clients_baisse_ca", {}),
            "CLIENTS_INACTIFS": ("nl2sql", "lister_clients_inactifs", {"duree_jours": 90}),
            "FACTURES_NON_REGLEES": ("nl2sql", "lister_factures_impayees", {"code_client": state.get("code_client", "")}),
            "LISTE_FACTURES": ("nl2sql", "lister_toutes_factures", {}),
            "TOUTES_FACTURES_CLIENT": ("nl2sql", "lister_toutes_factures_client", {"code_client": state.get("code_client", "")}),
            "VERIFIER_STOCK": ("nl2sql", "verifier_stock_article", {"ref_article": state.get("ref_article", "")}),
            "FICHE_CLIENT": ("nl2sql", "rechercher_fiche_client", {"code_client": state.get("code_client", "")}),
            "LIRE_ENCOURS_CLIENT": ("nl2sql", "rechercher_fiche_client", {"code_client": state.get("code_client", "")}),
            "STATUT_CLIENT": ("nl2sql", "verifier_statut_client", {"code_client": state.get("code_client", "")}),
            "DOCS_PERIODE": ("nl2sql", "lister_documents_par_periode", {"date_debut": state.get("date_debut",""), "date_fin": state.get("date_fin",""), "type_doc": state.get("type_doc") or "FACTURE"}),
            "RENTABILITE": ("nl2sql", "analyser_rentabilite_clients", {}),
            "SAISONNALITE": ("nl2sql", "analyser_saisonnalite_ventes", {}),
            "DSO": ("nl2sql", "calculer_dso_clients", {"code_client": state.get("code_client","")}),
            "RFM": ("nl2sql", "analyser_rfm_clients", {"code_client": state.get("code_client","")}),
            "OFFRE_PRIX_EXCEL": ("nl2sql", "exporter_offre_prix_excel", {"code_client": state.get("code_client","")}),
            "DECLARATION_EXCEL": ("nl2sql", "generer_declaration_mensuelle_excel", {"periode": state.get("demande_brute", "")}),
            "BALANCE_AGEE_EXCEL": ("nl2sql", "exporter_balance_agee_excel", {}),
            "DASHBOARD_EXCEL": ("nl2sql", "exporter_dashboard_kpi_excel", {}),
            "LISTE_FOURNISSEURS": ("nl2sql", "lister_fournisseurs", {}),
            "TOP_FOURNISSEURS": ("nl2sql", "analyser_top_fournisseurs", {}),
            "FICHE_FOURNISSEUR": ("nl2sql", "rechercher_fiche_fournisseur",
                                    {"code_fournisseur": state.get("code_client", "")}),
            "FACTURES_NON_REGLEES_FOURN": ("nl2sql", "lister_factures_fournisseurs_non_reglees", {
                "code_fournisseur": state.get("code_client", ""),
            }),
        }
        if act in tool_map:
            server, tool, args = tool_map[act]
            state["reponse_brute"] = await mcp_pool.call(server, tool, args)
        else:
            state["reponse_brute"] = f"__INCONNU__:{act}"
    except Exception as e:
        import traceback
        import uuid
        error_id = str(uuid.uuid4())[:8]
        logger.error(f"[{error_id}] [Lecture] Erreur action={act}:\n{traceback.format_exc()}")
        state["reponse_brute"] = f"__ERREUR__:Une erreur technique est survenue (référence : {error_id}). Veuillez réessayer ou contacter le support."
    return state