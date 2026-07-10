"""
Workflow node for the orchestrator.
Extracted from orchestrateur_general.py lines 4538-4617.
"""

import asyncio
import re
import logging
from api.mcp_pool import pool as mcp_pool
from cache.response_cache import cache as response_cache

logger = logging.getLogger(__name__)


async def noeud_workflow(state, _hub_contexte_client, _mcp_workflow_bl, _mcp_workflow_of,
                         _mcp_workflow_bf, _parse_mcp_response, _input, _safe_str):
    """
    Handles workflow operations for command processing.
    """
    if not state["validation_ok"]:
        return state
    logger.info("🔄 [Workflow] Flux commande...")
    logs = []
    try:
        txt_c, txt_s = await asyncio.gather(
            mcp_pool.call("nl2sql", "verifier_statut_client",  {"code_client": state["code_client"]}),
            mcp_pool.call("nl2sql", "verifier_stock_article",  {"ref_article": state["ref_article"]}),
        )
        statut = (
            "NON_TROUVE" if "NON_TROUVE" in txt_c else
            "BLOQUE"     if "BLOQUE"     in txt_c else
            "SUSPECT"    if "SUSPECT"    in txt_c else "VALIDE"
        )
        m = re.search(r"net:\s*([\d.]+)", txt_s)
        stock_dispo = float(m.group(1)) if m else 0.0

        ctx      = await _hub_contexte_client(state["code_client"], statut, stock_dispo, state["quantite"])
        decision = ctx.get("decision", "VALIDER")
        logs.extend(f"⚠️  {a}" for a in ctx.get("alertes", []))

        if decision == "BLOQUER":
            state["reponse_brute"] = (
                f"🛑 COMMANDE BLOQUÉE — {state['code_client']}\n"
                + "\n".join(f"  • {l}" for l in logs)
            )
            return state

        if decision == "CREER_CLIENT":
            c = await _input(f"❓ Client '{state['code_client']}' inconnu. Créer ? [Y/n] : ")
            if c.strip().lower() in ("n", "no", "non"):
                state["reponse_brute"] = "🛑 Flux annulé."
                return state
            raw = await mcp_pool.call("actions", "creer_nouveau_client", {
                "code_client": state["code_client"],
                "intitule":    state.get("nom_client_brut") or state["code_client"],
            })
            data = _parse_mcp_response(raw)
            logs.append(data.get("message", "✅ Client créé."))

        result_bl = await _mcp_workflow_bl(
            state["code_client"], state["ref_article"], state["quantite"], 0.0
        )
        logs.append(result_bl.get("message", ""))

        if result_bl.get("statut") == "STOCK_INSUFFISANT":
            result_of = await _mcp_workflow_of(state["ref_article"], state["quantite"])
            logs.append(result_of.get("message", ""))
            if result_of.get("statut") == "GENERE":
                result_bf = await _mcp_workflow_bf(
                    state["ref_article"], state["quantite"], result_of.get("DO_Piece", "")
                )
                logs.append(result_bf.get("message", ""))
                result_bl2 = await _mcp_workflow_bl(
                    state["code_client"], state["ref_article"], state["quantite"], 0.0
                )
                logs.append(result_bl2.get("message", ""))
                if result_bl2.get("statut") == "GENERE":
                    result_bl = result_bl2

        if result_bl.get("statut") == "GENERE":
            num_bl = result_bl.get("DO_Piece", "")
            state["num_piece"] = num_bl
            raw_fa = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_bl,
                "type_destination": "FACTURE",
            })
            data_fa = _parse_mcp_response(raw_fa)
            logs.append(data_fa.get("message", ""))

        state["reponse_brute"] = "\n".join(l for l in logs if l)
        await response_cache.invalidate_writes()

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state
