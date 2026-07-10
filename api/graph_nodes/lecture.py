"""
Lecture node for the orchestrator.
Extracted from orchestrateur_general.py lines 4006-4083.
"""

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
            "LISTE_FOURNISSEURS": ("nl2sql", "executer_sql_vanna", {
                "sql": "SELECT CT_Num, CT_Intitule, CT_Encours, CT_EncoursMax, CT_Validite FROM F_COMPTET WHERE CT_Type=1 ORDER BY CT_Intitule",
                "description": "Liste des fournisseurs",
            }),
            "TOP_FOURNISSEURS": ("nl2sql", "executer_sql_vanna", {
                "sql": (
                    "SELECT c.CT_Num, c.CT_Intitule, "
                    "COUNT(DISTINCT e.DO_Piece) AS nb_commandes, "
                    "COALESCE(SUM(l.DL_Qte*l.DL_PrixUnitaire),0) AS volume_achat "
                    "FROM F_COMPTET c "
                    "LEFT JOIN F_DOCENTETE e ON c.CT_Num=e.CT_Num AND e.DO_Type=6 AND e.DO_Domaine=1 "
                    "LEFT JOIN F_DOCLIGNE l ON e.DO_Piece=l.DO_Piece "
                    "WHERE c.CT_Type=1 "
                    "GROUP BY c.CT_Num ORDER BY volume_achat DESC LIMIT 10"
                ),
                "description": "Top fournisseurs par volume d'achat",
            }),
            "FICHE_FOURNISSEUR": ("nl2sql", "executer_sql_vanna", {
                "sql": (
                    f"SELECT CT_Num, CT_Intitule, CT_Encours, CT_EncoursMax, CT_Validite "
                    f"FROM F_COMPTET "
                    f"WHERE CT_Type=1 AND (CT_Num='{state.get('code_client','')}' "
                    f"OR UPPER(CT_Intitule) LIKE UPPER('%{state.get('code_client','')}%')) LIMIT 1"
                ),
                "description": f"Fiche fournisseur {state.get('code_client','')}",
            }),
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
