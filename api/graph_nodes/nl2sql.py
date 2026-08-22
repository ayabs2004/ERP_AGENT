"""
NL2SQL node for the orchestrator.
Extracted from orchestrateur_general.py lines 4086-4208.

v4.1 : CORRECTIF DE NEUTRALITÉ DB.
       Le post-traitement du SQL généré par Vanna injectait un filtre client
       et détectait le domaine achat/vente avec des noms physiques Sage codés
       en dur ("CT_Num", "DO_DOMAINE=1", motif '([A-Z]{2,5}\\d{2,6})' calé sur
       le format visuel de CT_Num). On ne maîtrise pas le SQL généré par Vanna
       lui-même (LLM externe), mais le post-traitement qu'on contrôle doit
       passer par adaptation/db_adapter.py comme le reste du projet.
       Les noms physiques viennent désormais de table()/col(), à l'identique
       de mcp_sage.py et nl2sql_server.py.

v4.2 : CORRECTIF BUG CRITIQUE — injection aveugle de filtre client.
       Le bloc d'injection appliquait `re.sub(r"(WHERE\\s+)", rf"\\1e.{_C_DO_TIERS}='{code}' AND ", sql)`
       sur N'IMPORTE QUEL SQL généré par Vanna, sans vérifier que ce SQL
       interrogeait bien la table doc_entete AVEC un alias "e". Résultat
       observé en prod : Vanna génère `SELECT * FROM F_ARTICLE WHERE AR_Ref = 'X'`
       (aucun alias e, DO_Tiers n'existe pas dans F_ARTICLE) → le post-traitement
       le transforme en `SELECT * FROM F_ARTICLE WHERE e.DO_Tiers='CODE' AND AR_Ref = 'X'`
       → erreur SQL Server 4104 "L'identificateur en plusieurs parties e.DO_Tiers
       ne peut pas être lié". Pire : ce SQL cassé était ensuite appris à la
       volée par Vanna (entraînement post-exécution), polluant durablement
       les générations futures.
       Fix : on n'injecte le filtre client QUE si le SQL contient bien
       `FROM <table_doc_entete> e` (ou `AS e`), alias sur lequel `e.{_C_DO_TIERS}`
       peut réellement être résolu. Même garde-fou appliqué à la détection
       "_sql_est_achat" : DO_DOMAINE=1 n'a de sens que si le SQL cible aussi
       doc_entete ; sinon on ne peut pas en tirer de conclusion fiable sur le
       domaine achat/vente et on ne doit pas s'en servir pour autoriser/bloquer
       l'injection.
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

# ── Noms physiques résolus via adaptation.db_adapter (db_config.json) ──
_C_CT_NUM             = col("clients_fournisseurs", "code")      # CT_Num dans F_COMPTET
_C_DO_TIERS           = col("doc_entete", "code_tiers")          # DO_Tiers dans F_DOCENTETE
_C_DO_DOMAINE         = col("doc_entete", "domaine")
_T_DOC_ENTETE         = table("doc_entete")                      # F_DOCENTETE

# Un code tiers logique ressemble typiquement à '<lettres><chiffres>'
# (ex: CLI001, FOUR01) quel que soit le nom physique réel de la colonne.
# Ce format n'est pas un nom physique — c'est une convention de valeur,
# indépendante du schéma — donc pas concerné par la règle d'or.
_RX_CODE_TIERS = re.compile(r"'([A-Z]{2,5}\d{2,6})'", re.IGNORECASE)

# Détecte si le SQL interroge bien la table entête document AVEC l'alias
# "e" (ex: `FROM F_DOCENTETE e` ou `FROM F_DOCENTETE AS e`). C'est une
# condition nécessaire avant tout post-traitement qui référence `e.<col>`
# ou qui s'appuie sur des colonnes propres à doc_entete (comme DO_Domaine)
# pour prendre une décision. Sans ce garde-fou, un SQL généré par Vanna sur
# une AUTRE table (F_ARTICLE, F_COMPTET, F_NOMENCLAT, ...) peut être cassé
# par l'injection d'un identifiant qui n'existe pas dans cette table.
_RX_ALIAS_E_SUR_DOCENTETE = re.compile(
    rf"\bFROM\s+{re.escape(_T_DOC_ENTETE)}\s+(?:AS\s+)?e\b",
    re.IGNORECASE,
)


def _sql_cible_doc_entete_avec_alias_e(sql: str) -> bool:
    """True si le SQL contient bien `FROM <table_doc_entete> e` (ou `AS e`).

    C'est le seul cas où il est valide de référencer `e.<colonne>` (comme
    `e.DO_Tiers` ou `e.DO_Domaine`) dans un post-traitement du SQL généré.
    """
    return bool(sql) and bool(_RX_ALIAS_E_SUR_DOCENTETE.search(sql))


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

    # 1. Essayer d'abord le pattern prédéfini (rapide, précis, sans LLM)
    fallback_reponse = None
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
                    m = re.search(r"(SELECT.+?)(?:\n\n|$)", reponse, re.IGNORECASE | re.DOTALL)
                    if m:
                        asyncio.create_task(
                            asyncio.to_thread(_vanna_entrainer, state["demande_brute"], m.group(1).strip())
                        )
                return state
            else:
                fallback_reponse = reponse
    except Exception as e:
        logger.error(f"   ⚠️  [NL2SQL] Erreur lors de l'évaluation du pattern prédéfini : {e}")

    # 2. Si pas de pattern, on passe à Vanna
    if ENABLE_VANNA and _vanna_client is not None:
        # Anonymisation avant envoi à Vanna/Groq
        _demande_safe, _restore_map = anonymise_sync(state["demande_brute"])

        try:
            sql, score = await asyncio.to_thread(_vanna_generer_sql, _demande_safe)
        except Exception as e:
            print(f"   ⚠️  [Vanna] {e}")
            print(traceback.format_exc())
            sql, score = None, 0.0

        # Désanonymisation du SQL généré avant exécution
        if sql and _restore_map:
            sql_original = sql
            sql = deanonymise_with_map(sql, _restore_map)
            if sql != sql_original:
                logger.debug(f"   🔓 [Anonymiseur Vanna] SQL restauré : tokens → valeurs réelles")

        if sql and score >= 0.65:  # Phase 3 : seuil relevé 0.5→0.65 (cohérent avec score plancher valider_sql_dialecte)
            code = state.get("code_client", "")
            _est_fournisseur = bool(re.match(r"^F(OUR|0)\w*", code, re.IGNORECASE)) if code else False

            # Garde-fou central : toute décision qui référence une colonne
            # via l'alias "e" (e.DO_Tiers, e.DO_Domaine) n'a de sens QUE si
            # le SQL cible bien doc_entete avec cet alias. Sinon on ne peut
            # ni injecter le filtre client, ni se fier à _sql_est_achat.
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
                # ── Phase 2 : injection sécurisée via paramètre bindé ────
                # On remplace la valeur littérale par '?' (paramètre bindé)
                # pour éviter toute interpolation directe du code client dans
                # le SQL (défense en profondeur contre l'injection SQL).
                sql, nb_sub = re.subn(
                    r"(WHERE\s+)",
                    rf"\1e.{_C_DO_TIERS}=? AND ",
                    sql, count=1, flags=re.IGNORECASE
                )
                if nb_sub == 0:
                    # Aucun WHERE trouvé : impossible d'injecter le filtre
                    # client en toute sécurité. On refuse d'exécuter plutôt
                    # que de laisser passer une requête non filtrée.
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
                # Point 3.1 : Détecter si Vanna a retourné un refus explicite (texte sans bloc SQL)
                # plutôt qu'un SQL de faible confiance — dans ce cas, transmettre le texte directement.
                if sql and not sql.strip().upper().startswith("SELECT") and score < 0.65:
                    logger.info("   💬 [Vanna] Réponse textuelle détectée (refus anti-hallucination) → transmis à l'utilisateur")
                    state["reponse_brute"] = sql  # sql contient en réalité du texte explicatif
                    return state
                logger.info(f"   ℹ️  [Vanna] Score insuffisant ({score:.0%}) → fallback patterns")
    elif ENABLE_VANNA and _vanna_client is None:
                logger.warning("   ⚠️  [Vanna] Non initialisé → fallback patterns")

    # 3. Fallback final si Vanna a échoué / désactivé
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