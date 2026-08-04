"""
Lecture node for the orchestrator.
Extracted from orchestrateur_general.py lines 4006-4083.

v4.1 : CORRECTIF DE NEUTRALITÉ DB.
       Les 3 entrées LISTE_FOURNISSEURS / TOP_FOURNISSEURS / FICHE_FOURNISSEUR
       du tool_map exécutaient du SQL avec des noms physiques Sage codés en dur
       (F_COMPTET, CT_Num, CT_Intitule, F_DOCENTETE, DO_Piece, F_DOCLIGNE,
       DL_Qte, DL_PrixUnitaire...) via "executer_sql_vanna". Ce SQL contournait
       intégralement adaptation/db_adapter.py.
       Ces trois cas dupliquaient des outils déjà neutralisés (table()/col())
       existant dans nl2sql_server.py : lister_fournisseurs,
       analyser_top_fournisseurs, rechercher_fiche_fournisseur.
       On les appelle désormais directement — plus aucun nom physique ici.
"""

import json
from api.mcp_pool import pool as mcp_pool
import logging

logger = logging.getLogger(__name__)


async def noeud_lecture(state, _rechercher_client_par_nom, _safe_str):
    """
    Handles read operations by calling appropriate MCP tools.
    """
    logger.info("📊 [Agent Lecture] Interrogation Sage...")
    act = state["action"]

    _actions_client_requis = {"FICHE_CLIENT","STATUT_CLIENT","TOUTES_FACTURES_CLIENT","FACTURES_NON_REGLEES","FACTURES_NON_REGLEES_FOURN"}
    if act in _actions_client_requis and not state.get("code_client"):
        nom_candidat = state.get("nom_client_brut") or state.get("dernier_code_client") or ""
        if nom_candidat:
            code = await _rechercher_client_par_nom(nom_candidat)
            if code:
                state["code_client"] = code
            else:
                state["code_client"] = nom_candidat

    try:
        tool_map = {
            "TOP_CLIENTS":             ("nl2sql", "analyser_top_clients_ca",       {}),
            "LISTE_CLIENTS":           ("nl2sql", "lister_clients_actifs",          {}),
            "LISTE_ARTICLES":          ("nl2sql", "lister_articles_catalogue",      {}),
            "PALMARES_ARTICLES":       ("nl2sql", "analyser_palmares_articles",     {}),
            "CA_GLOBAL":               ("nl2sql", "calculer_ca_global_periode",     {}),
            "CLIENTS_BAISSE":          ("nl2sql", "detecter_clients_baisse_ca",     {}),
            "FACTURES_NON_REGLEES":    ("nl2sql", "lister_factures_impayees",       {"code_client": state.get("code_client", "")}),
            "LISTE_FACTURES":          ("nl2sql", "lister_toutes_factures",         {}),
            "TOUTES_FACTURES_CLIENT":  ("nl2sql", "lister_toutes_factures_client",  {"code_client": state.get("code_client", "")}),
            "VERIFIER_STOCK":          ("nl2sql", "verifier_stock_article",         {"ref_article": state.get("ref_article", "")}),
            "FICHE_CLIENT":            ("nl2sql", "rechercher_fiche_client",        {"code_client": state.get("code_client", "")}),
            "STATUT_CLIENT":           ("nl2sql", "verifier_statut_client",         {"code_client": state.get("code_client", "")}),
            "DOCS_PERIODE":            ("nl2sql", "lister_documents_par_periode",   {"date_debut": state.get("date_debut",""), "date_fin": state.get("date_fin","")}),
            "RENTABILITE":             ("nl2sql", "analyser_rentabilite_clients",   {}),
            "SAISONNALITE":            ("nl2sql", "analyser_saisonnalite_ventes",   {}),
            "DSO":                     ("nl2sql", "calculer_dso_clients",           {"code_client": state.get("code_client","")}),
            "RFM":                     ("nl2sql", "analyser_rfm_clients",           {"code_client": state.get("code_client","")}),
            "OFFRE_PRIX_EXCEL":        ("nl2sql", "exporter_offre_prix_excel",      {"code_client": state.get("code_client","")}),
            "DECLARATION_EXCEL":       ("nl2sql", "generer_declaration_mensuelle_excel", {"periode": state.get("demande_brute", "")}),
            "BALANCE_AGEE_EXCEL":      ("nl2sql", "exporter_balance_agee_excel",    {}),
            "DASHBOARD_EXCEL":         ("nl2sql", "exporter_dashboard_kpi_excel",   {}),
            # ── Fournisseurs : neutralisé (table()/col() via nl2sql_server.py) ──
            # Anciennement : executer_sql_vanna avec SQL brut (F_COMPTET, CT_Num,
            # CT_Intitule, CT_Encours, CT_EncoursMax, CT_Validite, F_DOCENTETE,
            # F_DOCLIGNE, DO_Piece, DL_Qte, DL_PrixUnitaire en dur).
            # Remplacé par les outils dédiés, déjà neutres vis-à-vis du schéma.
            "LISTE_FOURNISSEURS":  ("nl2sql", "lister_fournisseurs", {}),
            "TOP_FOURNISSEURS":    ("nl2sql", "analyser_top_fournisseurs", {}),
            "FICHE_FOURNISSEUR":   ("nl2sql", "rechercher_fiche_fournisseur",
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
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state