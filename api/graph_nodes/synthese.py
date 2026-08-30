"""Synthese node for the orchestrator.

This module provides the `noeud_synthese` coroutine that synthesizes final
responses from raw data using optional LLM generation or deterministic
formatters. It includes helper functions for detecting empty responses,
hallucinations, and formatting NL2SQL results.
"""

import asyncio
import json
import re
import logging
from api.llm_anonymizer import invoke_llm_anonymise
from formatting.formatters import _formater_resultats_generiques, _formater_nl2sql_brut as _formater_nl2sql_brut_base

logger = logging.getLogger(__name__)

_HALLUCINATION_MARKERS = (
    "par exemple", "supposons", "imaginons", "à titre d'exemple",
    "typiquement", "généralement", "en général", "il est probable",
    "il se peut que", "je suppose", "je présume", "hypothétiquement",
    "dans ce cas fictif", "données fictives", "exemple fictif",
)

def _rb_est_vide(rb: str) -> bool:
    """Return True if the raw response is empty or contains no meaningful data."""
    if not rb or not rb.strip():
        return True
    rb_strip = rb.strip()
    if rb_strip in ("{}", "[]", '{"statut": "OK"}', '{"statut":"OK"}'):
        return True
    if rb_strip.startswith(("🔍 Aucune", "ℹ️  Aucune")):
        return True
    try:
        data = json.loads(rb_strip)
        if isinstance(data, dict):
            values = [v for k, v in data.items() if k != "statut"]
            if all(v in (None, [], {}, "", 0) for v in values):
                return True
        elif isinstance(data, list) and len(data) == 0:
            return True
    except json.JSONDecodeError:
        pass
    return len(rb_strip) < 10

def _detecter_hallucination(synthese: str, rb: str) -> bool:
    """Detect whether the generated synthesis contains hallucinations."""
    if not synthese:
        return False
    s_lower = synthese.lower()
    for marker in _HALLUCINATION_MARKERS:
        if marker in s_lower:
            logger.warning(f"   🚨 [Anti-hallucination] Marqueur détecté : '{marker}'")
            return True
    if _rb_est_vide(rb) and len(synthese.strip()) > 200:
        logger.warning(f"   🚨 [Anti-hallucination] Données vides + synthèse longue ({len(synthese)} chars)")
        return True
    nb_synthese = set(re.findall(r'\b\d{4,}\b', synthese))
    nb_rb = set(re.findall(r'\b\d{4,}\b', rb))
    inventes = nb_synthese - nb_rb
    if len(inventes) > 2:
        logger.warning(f"   🚨 [Anti-hallucination] Nombres absents du rb : {inventes}")
        return True
    return False

def _formater_nl2sql_brut(rb: str, question: str, _FORMATEURS_JSON) -> str:
    """Format a raw NL2SQL response for display using the base formatter."""
    return _formater_nl2sql_brut_base(rb, question)

async def noeud_synthese(state, _FORMATEURS_JSON, ACTIONS_KB, ACTIONS_EXPORT,
                         ENABLE_LLM_SYNTHESE, SYNTHESE_TIMEOUT, ENABLE_MEM0,
                         _mem0_sauvegarder, _invoke_llm, _formater_reponse_directe,
                         _safe_str):
    """Synthesize the final response from raw data using LLM or deterministic formatters."""
    rb = state.get("reponse_brute", "") or ""
    act = state.get("action", "")

    modification_en_cours = state.get("modification_en_cours")
    attente_confirmation = state.get("attente_confirmation")

    def _restore_modification_state(s):
        """Restore temporary modification flags and handle de-anonymisation."""
        if modification_en_cours:
            s["modification_en_cours"] = modification_en_cours
        if attente_confirmation:
            s["attente_confirmation"] = attente_confirmation
        if s.get("reponse_finale") and s.get("mapping_anonymisation"):
            from llm_anonymizer import deanonymise_with_map
            s["reponse_finale"] = deanonymise_with_map(s["reponse_finale"], s["mapping_anonymisation"])
            if re.search(r'<<[A-Z_]+_\d+>>|\b(?:NOM|CLIENT|FOUR|ARTICLE|PIECE)_\d+\b', s["reponse_finale"]):
                logging.getLogger("api.graph_nodes").critical(f"🚨 Fuite de token d'anonymisation interceptée : {s['reponse_finale']}")
                s["reponse_finale"] = "⚠️ Erreur interne : impossible de démasquer certaines informations confidentielles."
        return s

    if state.get("reponse_finale"):
        return _restore_modification_state(state)

    if rb.startswith("__ERREUR__"):
        err = rb.replace("__ERREUR__:", "")
        state["reponse_finale"] = f"❌ Erreur système : {err}"
        return _restore_modification_state(state)

    if rb.startswith("__INCONNU__"):
        state["reponse_finale"] = f"⚠️  Action non reconnue : {rb}"
        return _restore_modification_state(state)

    if act == "NL2SQL_LIBRE" and rb and not rb.startswith("__"):
        _deja_formate = rb.startswith((
            "📊", "✅", "❌", "⚠️", "─", "👥", "📦", "🏆", "⏳", "Question :"
        ))
        if _deja_formate:
            logger.info("   ⚡ [Synthèse NL2SQL] Réponse déjà formatée → pas de LLM (gain de temps)")
            state["reponse_finale"] = rb
            return _restore_modification_state(state)

        if _rb_est_vide(rb):
            logger.warning("   ⚠️  [Synthèse NL2SQL] Données vides → pas de LLM (anti-hallucination)")
            state["reponse_finale"] = (
                "⚠️  Aucun résultat trouvé pour cette requête.\n"
                "Les données demandées ne sont pas disponibles dans la base."
            )
            return _restore_modification_state(state)

        _est_tabulaire = False
        try:
            _data_check = json.loads(rb)
            if isinstance(_data_check, list) and len(_data_check) > 1:
                _est_tabulaire = True
            elif isinstance(_data_check, dict):
                for _key in ("clients", "factures", "articles", "resultats", "rows", "data", "lignes"):
                    _items = _data_check.get(_key)
                    if isinstance(_items, list) and len(_items) > 1:
                        _est_tabulaire = True
                        break
        except (json.JSONDecodeError, ValueError):
            pass

        if _est_tabulaire:
            logger.info("   📋 [Synthèse NL2SQL] Données tabulaires → formateur déterministe (pas de LLM)")
            state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"], _FORMATEURS_JSON)
            return _restore_modification_state(state)

        if ENABLE_LLM_SYNTHESE:
            prompt = (
                f'Tu es un assistant ERP Sage 100 expert et rigoureux.\n'
                f'QUESTION UTILISATEUR : "{state["demande_brute"]}"\n\n'
                f'DONNÉES RETOURNÉES PAR LA BASE :\n'
                f'```\n{rb[:3000]}\n```\n\n'
                f'RÈGLES ABSOLUES :\n'
                f'1. Base-toi UNIQUEMENT sur les données ci-dessus.\n'
                f'2. N\'invente AUCUN nom, chiffre, date ou montant absent des données.\n'
                f'3. Si une information est absente → écris "Information non disponible".\n'
                f'4. N\'utilise JAMAIS : "par exemple", "supposons", "typiquement",\n'
                f'   "généralement", "il est probable", "je suppose".\n'
                f'5. N\'écris JAMAIS de JSON, SQL ou bloc de code.\n'
                f'6. Réponds uniquement en français, de façon directe et factuelle.\n\n'
                f'Analyse les données et réponds clairement. '
                f'Utilise des tableaux ASCII, emojis et formatage pour la lisibilité.\n'
                f'Commence directement par la réponse en français.'
            )
            try:
                synthese = await asyncio.wait_for(
                    invoke_llm_anonymise(prompt, _invoke_llm, use_smart=True),
                    timeout=SYNTHESE_TIMEOUT,
                )
                _s = synthese.strip()
                _est_json_llm = (
                    (_s.startswith("{") and _s.endswith("}"))
                    or (_s.startswith("[") and _s.endswith("]"))
                    or _s.startswith("```json")
                    or (_s.startswith("```") and ("SELECT" in _s.upper() or "{" in _s))
                    or (len(_s) > 10 and _s.count("\n") < 2 and _s.startswith("{"))
                )
                if _est_json_llm:
                    logger.warning("   ⚠️  [Synthèse NL2SQL] LLM a répondu en JSON → fallback formateur")
                    state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"], _FORMATEURS_JSON)
                elif _detecter_hallucination(_s, rb):
                    logger.warning("   🚨 [Synthèse NL2SQL] Hallucination détectée → fallback formateur")
                    state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"], _FORMATEURS_JSON)
                elif not _s:
                    logger.warning("   ⚠️  [Synthèse NL2SQL] Synthèse vide → fallback formateur")
                    state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"], _FORMATEURS_JSON)
                else:
                    state["reponse_finale"] = synthese
                if ENABLE_MEM0:
                    asyncio.create_task(
                        asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], state["reponse_finale"])
                    )
                return _restore_modification_state(state)
            except asyncio.TimeoutError:
                logger.warning(f"   ⚠️  [Synthèse NL2SQL] Timeout {SYNTHESE_TIMEOUT}s → réponse brute")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error(f"   ⚠️  [Synthèse NL2