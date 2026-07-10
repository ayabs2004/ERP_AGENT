"""
Synthese node for the orchestrator.
Extracted from orchestrateur_general.py lines 4619-4962.
"""

import asyncio
import json
import re
import logging
from api.llm_anonymizer import invoke_llm_anonymise

logger = logging.getLogger(__name__)


_HALLUCINATION_MARKERS = (
    "par exemple", "supposons", "imaginons", "à titre d'exemple",
    "typiquement", "généralement", "en général", "il est probable",
    "il se peut que", "je suppose", "je présume", "hypothétiquement",
    "dans ce cas fictif", "données fictives", "exemple fictif",
)


def _rb_est_vide(rb: str) -> bool:
    """Check if response is empty or contains no meaningful data."""
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
    """Detect if the LLM response contains hallucinations."""
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
    nb_rb       = set(re.findall(r'\b\d{4,}\b', rb))
    inventes    = nb_synthese - nb_rb
    if len(inventes) > 2:
        logger.warning(f"   🚨 [Anti-hallucination] Nombres absents du rb : {inventes}")
        return True
    return False


def _formater_nl2sql_brut(rb: str, question: str, _FORMATEURS_JSON) -> str:
    """Format raw NL2SQL response for display."""
    if not rb:
        return "⚠️  Aucun résultat trouvé."

    if rb.startswith(("📊", "✅", "❌", "⚠️", "─", "👥", "📦", "🏆", "⏳", "Question :")):
        return rb

    try:
        data = json.loads(rb)
        if isinstance(data, dict):
            if "erreur" in data:
                return f"❌ Erreur SQL : {data['erreur']}"
            statut = data.get("statut", "")
            if statut == "OK":
                for _act, _fmt in _FORMATEURS_JSON.items():
                    try:
                        r = _fmt(data)
                        if r:
                            return r
                    except (KeyError, ValueError, TypeError):
                        continue
                for key in ("clients", "factures", "articles", "resultats", "rows", "data", "lignes"):
                    items = data.get(key)
                    if items and isinstance(items, list) and items:
                        cols = list(items[0].keys()) if isinstance(items[0], dict) else []
                        lignes = [f"📊 {question} — {len(items)} résultat(s) :", "─" * 60]
                        for i, row in enumerate(items[:30], 1):
                            parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                            lignes.append(f"  {i:>3}. " + " │ ".join(parts))
                        if len(items) > 30:
                            lignes.append(f"  ... et {len(items) - 30} ligne(s) supplémentaire(s)")
                        lignes.append("─" * 60)
                        return "\n".join(lignes)
                parts = [f"{k}: {v}" for k, v in data.items()
                         if v is not None and not isinstance(v, (dict, list))]
                if parts:
                    return f"📊 {question} :\n  " + "\n  ".join(parts)
            if data.get("message"):
                return data["message"]
        elif isinstance(data, list):
            if not data:
                return f"📊 Résultat de « {question} » : Aucun résultat."
            cols = list(data[0].keys()) if isinstance(data[0], dict) else []
            if not cols:
                return str(data)
            lignes = [f"📊 Résultat de « {question} » — {len(data)} ligne(s) :", "─" * 60]
            for i, row in enumerate(data[:30], 1):
                parts = [f"{k}: {v}" for k, v in row.items() if v is not None]
                lignes.append(f"  {i:>3}. " + " │ ".join(parts))
            if len(data) > 30:
                lignes.append(f"  ... et {len(data) - 30} ligne(s) supplémentaire(s)")
            lignes.append("─" * 60)
            return "\n".join(lignes)
    except (json.JSONDecodeError, ValueError):
        pass

    if "│" in rb or "─" in rb or ":" in rb:
        return rb

    return f"📊 Résultat de « {question} » :\n{rb}"


async def noeud_synthese(state, _FORMATEURS_JSON, ACTIONS_KB, ACTIONS_EXPORT, 
                          ENABLE_LLM_SYNTHESE, SYNTHESE_TIMEOUT, ENABLE_MEM0,
                          _mem0_sauvegarder, _invoke_llm, _formater_reponse_directe,
                          _safe_str):
    """
    Synthesizes the final response from raw data using LLM or formatters.
    """
    rb  = state.get("reponse_brute", "") or ""
    act = state.get("action", "")

    # Preserve modification state
    modification_en_cours = state.get("modification_en_cours")
    attente_confirmation = state.get("attente_confirmation")
    
    def _restore_modification_state(s):
        if modification_en_cours:
            s["modification_en_cours"] = modification_en_cours
        if attente_confirmation:
            s["attente_confirmation"] = attente_confirmation
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
                logger.error(f"   ⚠️  [Synthèse NL2SQL] {_safe_str(e)}")
        state["reponse_finale"] = _formater_nl2sql_brut(rb, state["demande_brute"], _FORMATEURS_JSON)
        return _restore_modification_state(state)

    if act in ACTIONS_KB and rb and not rb.startswith("__"):
        if _rb_est_vide(rb):
            logger.warning("   ⚠️  [Synthèse KB] Aucun résultat KB → réponse canée (anti-hallucination)")
            state["reponse_finale"] = (
                "🔍 Je n'ai trouvé aucune information sur ce sujet dans la base de "
                "connaissance (procédures, fiches techniques, réclamations...).\n"
                "💡 Suggestion : contactez le service concerné ou reformulez votre demande."
            )
            return _restore_modification_state(state)

        if ENABLE_LLM_SYNTHESE:
            prompt = (
                f'Tu es un assistant ERP Sage 100 expert et rigoureux.\n'
                f'QUESTION UTILISATEUR : "{state["demande_brute"]}"\n\n'
                f'RÉSULTATS DE LA BASE DE CONNAISSANCE (KB) :\n'
                f'```\n{rb[:3000]}\n```\n\n'
                f'RÈGLES ABSOLUES :\n'
                f'1. Base-toi UNIQUEMENT sur les résultats ci-dessus (texte + score + fichier).\n'
                f'2. N\'invente AUCUNE information absente de ces résultats.\n'
                f'3. Si le texte ne répond pas clairement à la question, écris '
                f'"Information non disponible dans la base".\n'
                f'4. N\'utilise JAMAIS : "par exemple", "supposons", "typiquement", '
                f'"généralement", "il est probable", "je suppose".\n'
                f'5. Cite la source (doc_type / fichier) à la fin de ta réponse.\n'
            )
            try:
                synthese = await asyncio.wait_for(
                    invoke_llm_anonymise(prompt, _invoke_llm, use_smart=True),
                    timeout=SYNTHESE_TIMEOUT,
                )
                _s3 = synthese.strip()
                if _detecter_hallucination(_s3, rb) or not _s3:
                    logger.warning("   🚨 [Synthèse KB] Hallucination détectée → réponse brute KB")
                    state["reponse_finale"] = rb
                else:
                    state["reponse_finale"] = synthese
                return _restore_modification_state(state)
            except asyncio.TimeoutError:
                logger.warning(f"   ⚠️  [Synthèse KB] Timeout {SYNTHESE_TIMEOUT}s → réponse brute")
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.error(f"   ⚠️  [Synthèse KB] {_safe_str(e)}")
        state["reponse_finale"] = rb
        return _restore_modification_state(state)

    formatted = _formater_reponse_directe(act, rb)
    if formatted:
        if act in ACTIONS_EXPORT and rb and not rb.startswith("__"):
            try:
                _data_export = json.loads(rb)
                _fichier = _data_export.get("fichier", "")
                if _fichier:
                    state["pdf_path"] = _fichier
            except (json.JSONDecodeError, ValueError):
                pass
        state["reponse_finale"] = formatted
        if ENABLE_MEM0 and state.get("mem0_contexte") is not None:
            asyncio.create_task(
                asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], formatted)
            )
        return _restore_modification_state(state)

    if not formatted and rb and not rb.startswith("__"):
        try:
            data = json.loads(rb)
            if isinstance(data, dict) and data.get("statut") in ("OK", "TROUVE"):
                for _act_try in [act, act.upper()]:
                    if _act_try in _FORMATEURS_JSON:
                        try:
                            formatted = _FORMATEURS_JSON[_act_try](data)
                            if formatted:
                                state["reponse_finale"] = formatted
                                if ENABLE_MEM0 and state.get("mem0_contexte") is not None:
                                    asyncio.create_task(
                                        asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], formatted)
                                    )
                                return _restore_modification_state(state)
                        except (KeyError, ValueError, TypeError):
                            pass
                if "factures" in data or "clients" in data or "articles" in data:
                    state["reponse_finale"] = rb
                    return _restore_modification_state(state)
        except json.JSONDecodeError:
            pass

    if rb.startswith("✅") or rb.startswith("❌") or rb.startswith("⚠️"):
        state["reponse_finale"] = rb
        return _restore_modification_state(state)

    if ENABLE_LLM_SYNTHESE and rb and not rb.startswith("__"):
        if _rb_est_vide(rb):
            logger.warning("   ⚠️  [Synthèse] Données vides → pas de LLM (anti-hallucination)")
            state["reponse_finale"] = "⚠️  Aucune donnée disponible pour cette demande."
            return _restore_modification_state(state)

        mem_ctx = state.get("mem0_contexte", "")
        rag_ctx = state.get("rag_complement", "")

        prompt = (
            f'Tu es un assistant ERP Sage 100 expert et rigoureux.\n'
            f'DEMANDE UTILISATEUR : "{state["demande_brute"]}"\n'
        )
        if mem_ctx:
            prompt += f'CONTEXTE MÉMORISÉ : {mem_ctx}\n'
        if rag_ctx:
            prompt += f'INFORMATIONS COMPLÉMENTAIRES : {rag_ctx}\n'
        prompt += (
            f'\nDONNÉES BRUTES :\n```\n{rb[:2000]}\n```\n\n'
            f'RÈGLES ABSOLUES :\n'
            f'1. Base-toi UNIQUEMENT sur les données ci-dessus.\n'
            f'2. N\'invente AUCUN nom, chiffre, date ou montant absent des données.\n'
            f'3. Si une information est absente → écris "Information non disponible".\n'
            f'4. N\'utilise JAMAIS : "par exemple", "supposons", "typiquement",\n'
            f'   "généralement", "il est probable", "je suppose".\n'
            f'5. Réponds uniquement en français, de façon directe et factuelle.\n\n'
            f'Rédige une réponse claire, concise et structurée. '
            f'Utilise des emojis et du formatage pour la lisibilité.'
        )
        try:
            synthese = await asyncio.wait_for(
                invoke_llm_anonymise(prompt, _invoke_llm, use_smart=True),
                timeout=SYNTHESE_TIMEOUT,
            )
            _s2 = synthese.strip()
            if ((_s2.startswith("{") and _s2.endswith("}"))
                    or (_s2.startswith("[") and _s2.endswith("]"))
                    or _s2.startswith("```json")):
                logger.warning("   ⚠️  [Synthèse] LLM a répondu en JSON → réponse brute")
                state["reponse_finale"] = rb
            elif _detecter_hallucination(_s2, rb):
                logger.warning("   🚨 [Synthèse] Hallucination détectée → réponse brute")
                state["reponse_finale"] = rb
            else:
                state["reponse_finale"] = synthese
            if ENABLE_MEM0:
                asyncio.create_task(
                    asyncio.to_thread(_mem0_sauvegarder, state["demande_brute"], state["reponse_finale"])
                )
            return _restore_modification_state(state)
        except asyncio.TimeoutError:
            logger.warning(f"   ⚠️  [Synthèse] Timeout {SYNTHESE_TIMEOUT}s → réponse brute")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"   ⚠️  [Synthèse] Erreur : {_safe_str(e)}")

    state["reponse_finale"] = rb or "⚠️  Aucune réponse disponible."
    
    return _restore_modification_state(state)
