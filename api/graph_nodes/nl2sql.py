"""
NL2SQL node for the orchestrator.
Extracted from orchestrateur_general.py lines 4086-4208.
"""

import asyncio
import re
import logging
from api.mcp_pool import pool as mcp_pool
from api.llm_anonymizer import anonymise_sync, deanonymise_with_map

logger = logging.getLogger(__name__)


_MARQUEURS_FALLBACK_GENERIQUE = (
    "aucun pattern sql trouvé", "résumé général", "resume general",
)


def _est_fallback_generique(rb: str) -> bool:
    """Check if the response is a generic fallback message."""
    if not rb:
        return False
    return any(m in rb.lower() for m in _MARQUEURS_FALLBACK_GENERIQUE)


async def noeud_nl2sql_libre(state, ENABLE_VANNA, _vanna_client, _vanna_generer_sql, 
                              _vanna_entrainer, _safe_str):
    """
    Handles free-form NL2SQL queries using Vanna or fallback patterns.
    """
    logger.info("🤖 [Agent NL2SQL]...")

    if ENABLE_VANNA and _vanna_client is not None:
        # Anonymisation avant envoi à Vanna/Groq
        _demande_safe, _restore_map = anonymise_sync(state["demande_brute"])

        sql, score = await asyncio.to_thread(_vanna_generer_sql, _demande_safe)

        # Désanonymisation du SQL généré avant exécution
        if sql and _restore_map:
            sql_original = sql
            sql = deanonymise_with_map(sql, _restore_map)
            if sql != sql_original:
                logger.debug(f"   🔓 [Anonymiseur Vanna] SQL restauré : tokens → valeurs réelles")

        if sql and score >= 0.5:
            code = state.get("code_client", "")
            _est_fournisseur = bool(re.match(r"^F(OUR|0)\w*", code, re.IGNORECASE)) if code else False
            _sql_est_achat   = "DO_DOMAINE=1" in sql.upper().replace(" ", "") or "DO_DOMAINE = 1" in sql.upper()
            if (sql and code
                    and code.upper() not in ("PROD-INT", "")
                    and f"'{code}'" not in sql.upper()
                    and not _est_fournisseur
                    and not _sql_est_achat):
                sql = re.sub(
                    r"(WHERE\s+)",
                    rf"\1e.CT_Num='{code}' AND ",
                    sql, count=1, flags=re.IGNORECASE
                )
                logger.debug(f"   🔧 [NL2SQL Fix] Filtre CT_Num='{code}' injecté dans le SQL")
            elif _est_fournisseur:
                logger.debug(f"   🔧 [NL2SQL Fix] Code fournisseur '{code}' → injection filtre ignorée (Vanna gère)")
                if code:
                    _code_upper = code.upper()
                    sql_fixed = re.sub(
                        r"'([A-Z]{2,5}\d{2,6})'",
                        lambda m: f"'{_code_upper}'" if m.group(1).upper() != _code_upper else m.group(0),
                        sql, flags=re.IGNORECASE
                    )
                    if sql_fixed != sql:
                        logger.debug(f"   🔧 [NL2SQL Fix] Code tiers corrigé → '{_code_upper}' dans le SQL")
                        sql = sql_fixed

            logger.info(f"   ✨ [Vanna] SQL généré (confiance {score:.0%}) : {sql[:80]}...")
            try:
                reponse = await mcp_pool.call(
                    "nl2sql", "executer_sql_vanna",
                    {"sql": sql, "description": state["demande_brute"]},
                )
                state["reponse_brute"] = reponse
                if _est_fallback_generique(state["reponse_brute"]):
                    logger.warning("   ⚠️  [NL2SQL] Fallback générique détecté → message explicite")
                    state["reponse_brute"] = (
                        "⚠️  Je n'ai pas trouvé cette information dans la base de données Sage 100.\n"
                        "Cette notion n'existe peut-être pas dans le schéma SQL "
                        "(elle est peut-être disponible dans la base documentaire)."
                    )
                    return state
                if reponse and "__ERREUR__" not in reponse:
                    asyncio.create_task(
                        asyncio.to_thread(_vanna_entrainer, state["demande_brute"], sql)
                    )
                return state
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error(f"   ⚠️  [Vanna] exécution : {_safe_str(e)}")
        else:
            if ENABLE_VANNA:
                logger.info(f"   ℹ️  [Vanna] Score insuffisant ({score:.0%}) → fallback patterns")
    elif ENABLE_VANNA and _vanna_client is None:
                logger.warning("   ⚠️  [Vanna] Non initialisé → fallback patterns")

    _question_enrichie = state["demande_brute"]
    _code_injecte = state.get("code_client", "")
    _demande_lower = state["demande_brute"].lower()
    _est_requete_globale = not any(w in _demande_lower for w in (
        "client", "fournisseur", "tiers", "pour", "de", "du",
        "facture de", "bl de", "commande de",
    )) or any(w in _demande_lower for w in (
        "toutes", "tous", "liste", "global", "général",
        "factures fournisseur", "factures fournisseurs",
    ))
    _est_code_fournisseur = bool(re.match(r"^F(OUR|0)\w*", _code_injecte, re.IGNORECASE)) if _code_injecte else False

    if (_code_injecte
            and _code_injecte.upper() not in ("PROD-INT", "")
            and not _est_requete_globale
            and not _est_code_fournisseur):
        if _code_injecte.upper() not in _question_enrichie.upper():
            _question_enrichie = f"{_question_enrichie} (code: {_code_injecte})"
            logger.debug(f"   🔧 [NL2SQL Fallback] Question enrichie avec code '{_code_injecte}'")
    elif _code_injecte and _est_requete_globale:
        logger.debug(f"   🔧 [NL2SQL Fallback] Requête globale → filtre '{_code_injecte}' NON injecté")
    elif _est_code_fournisseur:
        logger.debug(f"   🔧 [NL2SQL Fallback] Code fournisseur '{_code_injecte}' → injection ignorée")
    try:
        reponse = await mcp_pool.call(
            "nl2sql", "interpreter_et_analyser_via_sql",
            {"question_metier": _question_enrichie},
        )
        state["reponse_brute"] = reponse
        if (ENABLE_VANNA and _vanna_client is not None
                and reponse and "__ERREUR__" not in reponse):
            m = re.search(r"(SELECT.+?)(?:\n\n|$)", reponse, re.IGNORECASE | re.DOTALL)
            if m:
                asyncio.create_task(
                    asyncio.to_thread(_vanna_entrainer, state["demande_brute"], m.group(1).strip())
                )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state
