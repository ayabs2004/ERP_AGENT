"""Module providing the knowledge base node for the orchestrator. It defines an asynchronous function that processes KB-related actions by calling the MCP pool and handling errors."""

from api.mcp_pool import pool as mcp_pool
import logging
import json

logger = logging.getLogger(__name__)

async def noeud_kb(state, _safe_str):
    """Process a KB action based on the given state, invoking the appropriate MCP service and storing the raw response or error in the state."""
    logger.info("📚 [Agent KB]...")
    act = state["action"]
    try:
        if act == "RECHERCHE_PROCEDURE":
            raw = await mcp_pool.call("kb", "rechercher_procedure", {
                "requete": state["demande_brute"],
            })
        elif act == "RECOMMANDATION":
            raw = await mcp_pool.call("kb", "generer_recommandation", {
                "contexte":    state["demande_brute"],
                "code_client": state.get("code_client", ""),
                "indicateur":  "CA",
            })
        elif act == "SEUIL_STOCK":
            raw = await mcp_pool.call("kb", "verifier_seuil_stock", {
                "ref_article": state.get("ref_article", ""),
            })
        elif act == "LISTE_PROCEDURES":
            raw = await mcp_pool.call("kb", "lister_procedures", {})
        else:
            raw = f"Action KB non reconnue : {act}"
        state["reponse_brute"] = raw
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    except Exception as e:
        logger.error(f"❌ [Agent KB] Échec de l'action '{act}' : {_safe_str(e)}", exc_info=True)
        state["reponse_brute"] = f"__ERREUR__:recherche KB indisponible ({_safe_str(e)})"
    return state