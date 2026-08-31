"""NL2SQL node for the orchestrator.

This module processes natural‑language queries, generates SQL via Vanna,
applies safety checks (alias verification, client‑code injection, fallback
handling) and executes the resulting query against the Sage 100 database.
"""

import asyncio
import json
import re
import logging
import traceback
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from adaptation.db_adapter import col, table

from api.mcp_pool import pool as mcp_pool
from api.llm_anonymizer import anonymise_sync, deanonymise_with_map

logger = logging.getLogger(__name__)

_MARQUEURS_FALLBACK_GENERIQUE = (
    "aucun pattern sql trouvé", "résumé général", "resume general",
)

_C_CT_NUM = col("clients_fournisseurs", "code")
_C_DO_TIERS = col("doc_entete", "code_tiers")
_C_DO_DOMAINE = col("doc_entete", "domaine")
_T_DOC_ENTETE = table("doc_entete")

_RX_CODE_TIERS = re.compile(r"'([A-Z]{2,5}\d{2,6})'", re.IGNORECASE)

_RX_ALIAS_E_SUR_DOCENTETE = re.compile(
    rf"\bFROM\s+{re.escape(_T_DOC_ENTETE)}\s+(?:AS\s+)?e\b",
    re.IGNORECASE,
)

def _sql_cible_doc_entete_avec_alias_e(sql: str) -> bool:
    """Return True if the SQL contains a `FROM <doc_entete> e` clause.

    The presence of the alias `e` on the `doc_entete` table is required before
    any post‑processing that references columns via `e.<column>` can be applied.
    """
    return bool(sql) and bool(_RX_ALIAS_E_SUR_DOCENTETE.search(sql))

def _est_fallback_generique(rb: str) -> bool:
    """Check whether the response string is a generic fallback message."""
    if not rb:
        return False
    return any(m in rb.lower() for m in _MARQUEURS_FALLBACK_GENERIQUE)

_RX_QUALIFICATIFS_NL2SQL = re.compile(
    r"\b(d[ée]croissant|croissant|trier?|class(?:e|er|é|és)|"
    r"sup[ée]rieur|inf[ée]rieur|moins\s+de|plus\s+de|"
    r"compar[ée]|par\s+rapport|\bvs\b|"
    r"moyenne|ratio|panier\s+moyen|"
    r"entre\s+\d|au\s+moins|au\s+plus)\b",
    re.IGNORECASE,
)

async def noeud_nl2sql_libre(state, ENABLE_VANNA, _vanna_client, _vanna_generer_sql,
                              _vanna_entrainer, _safe_str):
    """Process a free‑form NL2SQL request.

    The function enriches the user's question, attempts to match predefined
    patterns, falls back to Vanna generation when needed, injects a client
    filter safely, executes the SQL, and handles generic fallbacks.
    """
    logger.info("🤖 [Agent NL2SQL]...")

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

    _skip_pattern_predefini = bool(_RX_QUALIFICATIFS_NL2SQL.search(state["demande_brute"]))
    fallback_reponse = None
    if not _skip_pattern_predefini:
        try:
            reponse = await mcp_pool.call(
                "nl2sql", "interpreter_et_analyser_via_sql",
                {"question_metier": _question_enrichie},
            )
            if reponse and "__ERREUR__" not in reponse:
                if "aucun pattern sql trouvé" not in reponse.lower():
                    logger.info("   🎯 [NL2SQL] Match pattern prédéfini !")
                    state["reponse_brute"] = reponse
                    if ENABLE_VANNA and _vanna_client is not None:
                        _sql_extrait = None
                        _idx = reponse.upper().find("SELECT")
                        if _idx >= 0:
                            _depth = 0
                            _end = len(reponse)
                            for _ci, _ch in enumerate(reponse[_idx:], start=_idx):
                                if _ch == '(':
                                    _depth += 1
                                elif _ch == ')':
                                    _depth = max(0, _depth - 1)
                                elif _ch == ';' and _depth == 0:
                                    _end = _ci
                                    break
                            _sql_extrait = reponse[_idx:_end].strip()
                        if _sql_extrait:
                            asyncio.create_task(
                                asyncio.to_thread(_vanna_entrainer, state["demande_brute"], _sql_extrait)
                            )
                    return state
                else:
                    fallback_reponse = reponse
        except Exception as e:
            logger.error(f"   ⚠️  [NL2SQL] Erreur lors de l'évaluation du pattern prédéfini : {e}")
    else:
        logger.info("   ⏭️  [NL2SQL Patch W] Pattern prédéfini sauté (qualificatif détecté) → Vanna direct")
    if ENABLE_VANNA and _vanna_client is not None:
        _demande_safe, _restore_map = anonymise_sync(state["demande_brute"])

        try:
            sql, score = await asyncio.to_thread(_vanna_generer_sql, _demande_safe)
        except Exception as e:
            print(f"   ⚠️  [Vanna] {e}")
            print(traceback.format_exc())
            sql, score = None, 0.0

        if sql and _restore_map:
            sql_original = sql
            sql = deanonymise_with_map(sql, _restore_map)
            if sql != sql_original:
                logger.debug(f"   🔓 [Anonymiseur Vanna] SQL restauré : tokens → valeurs réelles")

        if sql and score >= 0.65:
            code = state.get("code_client", "")
            _est_fournisseur = bool(re.match(r"^F(OUR|0)\w*", code, re.IGNORECASE)) if code else False

            _sql_cible_docentete = _sql_cible_doc_entete_avec_alias_e(sql)

            _sql_est_achat = _sql_cible_docentete and (
                f"{_C_DO_DOMAINE.upper()}=1" in sql.upper().replace(" ", "")
                or f"{_C_DO_DOMAINE.upper()} = 1" in sql.upper()
            )

            if (sql and code
                    and code.upper() not in ("PROD-INT", "")
                    and f"'{code}'" not in sql.upper()
                    and not _est_fournisseur
                    and not _sql_est_achat
                    and _sql_cible_docentete):

                sql_avant = sql
                sql, nb_sub = re.subn(
                    r"(WHERE\s+)",
                    rf"\1e.{_C_DO_TIERS}=? AND ",
                    sql, count=1, flags=re.IGNORECASE
                )
                if nb_sub == 0:
                    logger.error(
                        f"   🚫 [NL2SQL Phase 2] Impossible d'injecter le filtre client "
                        f"(pas de WHERE dans le SQL) → exécution bloquée pour '{code}'"
                    )
                    state["reponse_brute"] = (
                        "⚠️  Je ne peux pas garantir que cette requête est filtrée sur votre "
                        "compte. Reformulez votre demande pour inclure explicitement le client."
                    )
                    return state
                params_filtre = (code,)
                logger.debug(f"   🔧 [NL2SQL Phase 2] Filtre {_C_DO_TIERS}=? injecté (bindé) pour '{code}'")
            elif _est_fournisseur:
                logger.debug(f"   🔧 [NL2SQL Fix] Code fournisseur '{code}' → injection filtre ignorée (Vanna gère)")
                params_filtre = ()
                if code and _sql_cible_docentete:
                    _code_upper = code.upper()
                    sql_fixed = _RX_CODE_TIERS.sub(
                        lambda m: f"'{_code_upper}'" if m.group(1).upper() != _code_upper else m.group(0),
                        sql
                    )
                    if sql_fixed != sql:
                        logger.debug(f"   🔧 [NL2SQL Fix] Code tiers corrigé → '{_code_upper}' dans le SQL")
                        sql = sql_fixed
                elif code and not _sql_cible_docentete:
                    logger.debug(
                        f"   🔧 [NL2SQL Fix] SQL ne cible pas {_T_DOC_ENTETE} avec alias 'e' "
                        f"→ correction code tiers fournisseur '{code}' ignorée (évite d'écraser "
                        f"un littéral non lié au tiers, ex: référence article)"
                    )
            elif code and code.upper() not in ("PROD-INT", "") and not _sql_cible_docentete:
                params_filtre = ()
                logger.debug(
                    f"   🔧 [NL2SQL Fix] SQL ne cible pas {_T_DOC_ENTETE} avec alias 'e' "
                    f"→ injection filtre client '{code}' ignorée (évite une erreur SQL "
                    f"type 4104 sur une colonne inexistante dans la table ciblée)"
                )
            else:
                params_filtre = ()

            logger.info(f"   ✨ [Vanna] SQL généré (confiance {score:.0%}) : {sql[:80]}...")
            if re.search(r'<<[A-Z_]+_\d+>>|\b(?:NOM|CLIENT|FOUR|ARTICLE|PIECE)_\d+\b', sql):
                logger.error("🚫 SQL contient un token d'anonymisation non résolu, exécution bloquée")
                state["reponse_brute"] = "⚠️ Erreur interne : impossible de résoudre les identifiants dans la requête."
                return state
            
            try:
                call_params: dict = {"sql": sql, "description": state["demande_brute"]}
                if params_filtre:
                    call_params["params"] = list(params_filtre)
                reponse = await mcp_pool.call(
                    "nl2sql", "executer_sql_vanna",
                    call_params,
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
                if sql and not sql.strip().upper().startswith("SELECT") and score < 0.65:
                    logger.info("   💬 [Vanna] Réponse textuelle détectée (refus anti-hallucination) → transmis à l'utilisateur")
                    state["reponse_brute"] = sql
                    return state
                logger.info(f"   ℹ️  [Vanna] Score insuffisant ({score:.0%}) → fallback patterns")
    elif ENABLE_VANNA and _vanna_client is None:
                logger.warning("   ⚠️  [Vanna] Non initialisé → fallback patterns")

    if fallback_reponse:
        state["reponse_brute"] = fallback_reponse
    else:
        try:
            reponse = await mcp_pool.call(
                "nl2sql", "interpreter_et_analyser_via_sql",
                {"question_metier": _question_enrichie},
            )
            state["reponse_brute"] = reponse
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"

    return state