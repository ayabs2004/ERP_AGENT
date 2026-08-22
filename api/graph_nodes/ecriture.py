"""
Ecriture node for the orchestrator.
Extracted from orchestrateur_general.py lines 4211-4537.

v4.1 : CORRECTIF DE NEUTRALITÉ DB.
       La branche TRANSFORMER_DOC ouvrait une connexion sqlite3 DIRECTE
       (sqlite3.connect(str(_db_path))) et exécutait du SQL avec des noms
       physiques Sage codés en dur (F_DOCENTETE, DO_Ref, DO_Type, DO_Domaine).
       Ceci contournait intégralement adaptation/db_adapter.py ET la couche
       MCP (accès disque direct depuis le node orchestrateur).
       Cette vérification de doublon existe déjà, neutralisée (table()/col()),
       dans nl2sql_server.py : verifier_document_deja_transforme(). On l'appelle
       désormais via mcp_pool — plus de sqlite3, plus de nom physique, plus
       d'accès disque hors MCP.
"""

import json
import sqlite3
import logging
import asyncio
from api.mcp_pool import pool as mcp_pool
from cache.response_cache import cache as response_cache

logger = logging.getLogger(__name__)


async def noeud_ecriture(state, _STATUTS_ERREUR_MCP, _mcp_workflow_bl_achat, _mcp_workflow_bl,
                         _mcp_workflow_of, _mcp_workflow_bf, _parse_mcp_response, _safe_str):
    """
    Handles write operations (create documents, clients, modify status, etc.).
    """
    if not state["validation_ok"]:
        return state

    logger.info("⚡ [Agent Écriture]...")
    act    = state["action"]
    type_d = (state.get("type_doc") or "").upper()
    state["suggestion_en_attente"] = {}

    def _traiter_erreur_mcp(data: dict) -> str | None:
        statut = data.get("statut", "")
        if statut == "ERREUR":
            return data.get("message", "Erreur inconnue.")
        if statut not in _STATUTS_ERREUR_MCP:
            return None
        message = data.get("message", "")
        if statut == "CLIENT_NON_TROUVE":
            suggestions = data.get("suggestions", [])
            sugg_txt = ""
            if suggestions:
                sugg_txt = "\n💡 Clients similaires : " + ", ".join(
                    f"{s['CT_Num']} ({s['CT_Intitule']})" for s in suggestions
                )
            return f"❌ Client '{state.get('code_client') or state.get('nom_client_brut')}' introuvable.{sugg_txt}"
        if statut == "CLIENT_BLOQUE":
            return message or "🚫 Client bloqué."
        if statut == "ARTICLE_NON_TROUVE":
            suggestions = data.get("suggestions", [])
            sugg_txt = ""
            if suggestions:
                sugg_txt = "\n💡 Articles similaires : " + ", ".join(
                    f"{s['AR_Ref']} ({s['AR_Design']})" for s in suggestions
                )
            return f"❌ Article '{state['ref_article']}' introuvable.{sugg_txt}"
        if statut == "STOCK_INSUFFISANT":
            return message or (
                f"❌ Stock insuffisant pour '{state['ref_article']}'.\n"
                f"   Dispo : {data.get('stock_dispo', 0)} u | "
                f"Demandé : {data.get('qte_demandee', 0)} u"
            )
        if statut == "COMPOSANTS_INSUFFISANTS":
            return message or "❌ Composants insuffisants pour la fabrication."
        if statut == "NON_TROUVE":
            return f"❌ Document '{state.get('num_piece', '?')}' introuvable."
        if statut == "EXISTE_DEJA":
            return message or "⚠️  Élément déjà existant."
        return message or f"❌ Erreur MCP : statut={statut}"

    try:
        if act == "GENERER_DOC":
            code_client_final = (
                state.get("code_client") or state.get("nom_client_brut", "") or ""
            )

            if type_d == "BL_ACHAT":
                doc = state.get("pending_document", {})
                data = await _mcp_workflow_bl_achat(
                    code_fournisseur = doc.get("code_fournisseur", code_client_final),
                    ref_article      = doc.get("ref_article",   state["ref_article"]),
                    quantite         = doc.get("quantite",      state["quantite"]),
                    prix_unitaire    = doc.get("prix_unitaire", 0.0),
                )
                err  = _traiter_erreur_mcp(data)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["num_piece"]     = data.get("DO_Piece", "")
                state["reponse_brute"] = json.dumps(data, ensure_ascii=False)
                state["suggestion_en_attente"] = {
                    "type":        "FACTURE_ACHAT",
                    "description": f"Créer la facture fournisseur pour BL {data.get('DO_Piece', '')}",
                    "params":      {"num_bl": data.get("DO_Piece")},
                }
                state["reponse_finale"] = (
                    data.get("message", "")
                    + "\n\n💡 Tapez **ok** pour créer la facture fournisseur."
                )
                return state

            elif type_d == "BL":
                result = await _mcp_workflow_bl(
                    code_client_final, state["ref_article"], state["quantite"], 0.0,
                )
                err = _traiter_erreur_mcp(result)
                if result.get("statut") == "STOCK_INSUFFISANT":
                    state["suggestion_en_attente"] = {
                        "type": "CREER_OF",
                        "description": f"Lancer un OF pour {result.get('manque', 0)} u de '{state['ref_article']}'",
                        "params": {
                            "ref_article":        result.get("ref_article", state["ref_article"]),
                            "quantite":           result.get("manque", state["quantite"]),
                            "code_client":        "PROD-INT",
                            "data_bl_en_attente": result.get("data_bl_en_attente", {}),
                        },
                    }
                    state["reponse_finale"] = (
                        result.get("message", "") + "\n\n💡 Tapez **ok** pour lancer l'OF, ou **non** pour annuler."
                    )
                    return state
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["num_piece"]     = result.get("DO_Piece", "")
                state["reponse_brute"] = json.dumps(result, ensure_ascii=False)
                rapport = [result.get("message", "")]
                sugg_fa = result.get("suggestion_facture", {})
                if sugg_fa:
                    num_bl = sugg_fa.get("num_bl", "")
                    state["suggestion_en_attente"] = {
                        "type": "CREER_FACTURE",
                        "description": f"Créer la facture pour BL {num_bl}",
                        "params": sugg_fa,
                    }
                    rapport.append("💡 Tapez **ok** pour créer la Facture.")
                state["reponse_finale"] = "\n\n".join(rapport)
                return state
            elif type_d == "OF":
                result = await _mcp_workflow_of(
                    state["ref_article"], state["quantite"],
                    code_client_final or "PROD-INT",
                )
                err = _traiter_erreur_mcp(result)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["num_piece"]     = result.get("DO_Piece", "")
                state["reponse_brute"] = json.dumps(result, ensure_ascii=False)
                rapport = [result.get("message", "")]
                sugg_bf = result.get("suggestion_bf", {})
                if sugg_bf:
                    state["suggestion_en_attente"] = {
                        "type": "CREER_BF",
                        "description": f"Créer le BF pour OF {sugg_bf.get('num_of', '')}",
                        "params": sugg_bf,
                    }
                    rapport.append("💡 Tapez **ok** pour créer le BF.")
                state["reponse_finale"] = "\n\n".join(r for r in rapport if r)
                return state

            elif type_d == "BF":
                num_of_lie = state.get("num_piece", "")
                if not num_of_lie:
                    msg = (
                        "🚫 Impossible de créer un Bon de Fabrication sans Ordre de Fabrication.\n\n"
                        "   Un BF doit obligatoirement être lié à un OF existant.\n\n"
                        "   💡 Commencez par créer un OF :\n"
                        f"      → \"crée un OF de {state.get('quantite', '?')} pièces de "
                        f"{state.get('ref_article', '?')}\"\n"
                        "   Puis le système vous proposera automatiquement de créer le BF."
                    )
                    state["reponse_brute"]  = msg
                    state["reponse_finale"] = msg
                    return state
                result = await _mcp_workflow_bf(
                    state["ref_article"], state["quantite"],
                    num_of_lie, code_client_final or "PROD-INT",
                )
                err = _traiter_erreur_mcp(result)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                state["reponse_brute"]  = json.dumps(result, ensure_ascii=False)
                state["reponse_finale"] = result.get("message", "❌ Erreur BF.")
                return state

            else:
                if not type_d:
                    type_d = "FACTURE" if any(
                        w in state["demande_brute"].lower() for w in ("facture", "facturer")
                    ) else "BL"
                raw = await mcp_pool.call("actions", "generer_document_sage", {
                    "type_doc":      type_d,
                    "code_client":   code_client_final or "PROD-INT",
                    "ref_article":   state["ref_article"],
                    "qte":           state["quantite"],
                    "prix_unitaire": 0.0,
                    "num_of":        "",
                })
                data = _parse_mcp_response(raw)
                err  = _traiter_erreur_mcp(data)
                if err:
                    state["reponse_brute"]  = err
                    state["reponse_finale"] = err
                    return state
                if data.get("DO_Piece") and type_d == "FACTURE":
                    state["num_piece"] = data["DO_Piece"]
                    state["type_doc"]  = "FACTURE"
                state["reponse_brute"] = json.dumps(data, ensure_ascii=False)

        elif act == "CREER_CLIENT":
            pd = state.get("pending_document", {})
            _intitule = pd.get("intitule") or state.get("intitule") or state.get("nom_client_brut") or state.get("code_client") or "Nouveau Client"
            payload = {
        "code_client":    state.get("code_client"),
        "intitule":       _intitule,
        "ct_validite":    pd.get("ct_validite")    or state.get("ct_validite", "VALIDE"),
        "ct_encours_max": pd.get("ct_encours_max") or state.get("ct_encours_max", 0.0),
        "adresse":        pd.get("adresse")        or state.get("adresse", ""),
        "complement":     pd.get("complement")     or state.get("complement", ""),
        "code_postal":    pd.get("code_postal")    or state.get("code_postal", ""),
        "ville":          pd.get("ville")          or state.get("ville", ""),
        "pays":           pd.get("pays")           or state.get("pays", ""),
        "contact":        pd.get("contact")        or state.get("contact", ""),
        "telephone":      pd.get("telephone")      or state.get("telephone", ""),
        "email":          pd.get("email")          or state.get("email", ""),
        "site":           pd.get("site")           or state.get("site", ""),
        "cg_num_princ":   pd.get("cg_num_princ", ""),
    }

            logger.debug("[CREER_CLIENT] payload -> %s", payload)
            try:
                raw = await mcp_pool.call("actions", "creer_nouveau_client", payload)
            except Exception as e:
                logger.exception("[CREER_CLIENT] erreur appel MCP creer_nouveau_client: %s", e)
                data = {"statut": "ERREUR", "message": _safe_str(e)}
            else:
                logger.debug("[CREER_CLIENT] raw MCP response: %r", raw)
                data = _parse_mcp_response(raw)
            # If code already exists, attempt to generate a new code and retry once
            if data.get("statut") == "EXISTE_DEJA":
                # Try to obtain a fresh sequential code from MCP and retry once
                try:
                    logger.debug("[CREER_CLIENT] demande generer_prochain_code (CLI)")
                    raw_code = await mcp_pool.call("actions", "generer_prochain_code", {"prefixe": "CLI"})
                    logger.debug("[CREER_CLIENT] raw_code MCP response: %r", raw_code)
                    data_code = _parse_mcp_response(raw_code)
                    new_code = data_code.get("code") if data_code.get("statut") == "OK" else None
                except Exception as e:
                    logger.exception("[CREER_CLIENT] erreur generer_prochain_code: %s", e)
                    new_code = None
                if not new_code:
                    # Fallback: append '_1' to previous code
                    old = state.get("code_client") or "CLI"
                    new_code = f"{old}_1"
                state["code_client"] = new_code
                payload["code_client"] = new_code
                try:
                    raw = await mcp_pool.call("actions", "creer_nouveau_client", payload)
                    logger.debug("[CREER_CLIENT] raw MCP retry response: %r", raw)
                    data = _parse_mcp_response(raw)
                except Exception as e:
                    logger.exception("[CREER_CLIENT] erreur retry creer_nouveau_client: %s", e)
                    data = {"statut": "ERREUR", "message": _safe_str(e)}

            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        elif act == "CREER_FOURNISSEUR":
            pd = state.get("pending_document", {})
            _intitule = pd.get("intitule") or state.get("intitule") or state.get("nom_client_brut") or state.get("code_client") or "Nouveau Fournisseur"
            payload = {
        "code_fournisseur": state["code_client"],
        "intitule":         _intitule,
        "adresse":          pd.get("adresse")        or state.get("adresse", ""),
        "complement":       pd.get("complement")     or state.get("complement", ""),
        "code_postal":      pd.get("code_postal")    or state.get("code_postal", ""),
        "ville":            pd.get("ville")          or state.get("ville", ""),
        "pays":             pd.get("pays")           or state.get("pays", ""),
        "contact":          pd.get("contact")        or state.get("contact", ""),
        "telephone":        pd.get("telephone")      or state.get("telephone", ""),
        "email":            pd.get("email")          or state.get("email", ""),
        "site":             pd.get("site")           or state.get("site", ""),
        "ct_encours_max":   pd.get("ct_encours_max") or state.get("ct_encours_max", 0.0),
        "ct_validite":      pd.get("ct_validite")    or state.get("ct_validite", "VALIDE"),
    }
            raw  = await mcp_pool.call("actions", "creer_nouveau_fournisseur", payload)
            data = _parse_mcp_response(raw)
            if data.get("statut") == "EXISTE_DEJA":
                try:
                    raw_code = await mcp_pool.call("actions", "generer_prochain_code", {"prefixe": "FOUR"})
                    data_code = _parse_mcp_response(raw_code)
                    new_code = data_code.get("code") if data_code.get("statut") == "OK" else None
                except Exception:
                    new_code = None
                if not new_code:
                    old = state.get("code_client") or "FOUR"
                    new_code = f"{old}_1"
                state["code_client"] = new_code
                payload["code_fournisseur"] = new_code
                raw = await mcp_pool.call("actions", "creer_nouveau_fournisseur", payload)
                data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        elif act == "MODIFIER_STATUT":
            _statut_cible = state.get("type_doc", "BLOQUE") or "BLOQUE"
            if _statut_cible not in ("BLOQUE", "VALIDE", "SUSPECT"):
                _statut_cible = "BLOQUE"

            # Detect if it's a supplier or client
            is_fournisseur = bool(state.get("code_fournisseur"))

            if is_fournisseur:
                raw = await mcp_pool.call("actions", "modifier_statut_fournisseur", {
                    "code_fournisseur": state["code_fournisseur"],
                    "statut": _statut_cible,
                })
            else:
                raw = await mcp_pool.call("actions", "modifier_statut_client", {
                    "code_client": state["code_client"],
                    "statut": _statut_cible,
                })
            data = _parse_mcp_response(raw)
            err = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        elif act == "REGLEMENT":
            raw = await mcp_pool.call("actions", "enregistrer_reglement_facture", {
                "num_piece":             state["num_piece"],
                "mode_paiement":         state["mode_paiement"],
                "numero_piece_paiement": state.get("numero_piece_paiement", ""),
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "TRANSFORMER_DOC":
            num_piece_src = state["num_piece"]
            type_dest     = state["type_doc"] or "FACTURE"

            # ── Vérification anti-doublon — neutralisée (table()/col()) ──
            # Anciennement : sqlite3.connect(_db_path) + SQL en dur sur
            # F_DOCENTETE / DO_Ref / DO_Type / DO_Domaine, en accès disque
            # direct hors MCP. Remplacé par l'outil déjà neutre
            # verifier_document_deja_transforme() de nl2sql_server.py,
            # qui couvre FACTURE/FA/FACTURE_ACHAT/FA_ACHAT/BF.
            try:
                raw_verif = await mcp_pool.call("nl2sql", "verifier_document_deja_transforme", {
                    "num_piece_source": num_piece_src,
                    "type_destination": type_dest,
                })
                data_verif = _parse_mcp_response(raw_verif)
                if data_verif.get("deja_transforme"):
                    message = data_verif.get("message") or (
                        f"⚠️  Le document **{num_piece_src}** a déjà été transformé "
                        f"en {type_dest}."
                    )
                    state["reponse_brute"]  = message
                    state["reponse_finale"] = message
                    return state

            except (json.JSONDecodeError, KeyError, ValueError) as e_verif:
                logger.warning(f"   ⚠️  [Vérif doublon] {_safe_str(e_verif)}")

            raw  = await mcp_pool.call("actions", "transformer_document", {
                "num_piece_source": num_piece_src,
                "type_destination": type_dest,
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "CREER_AVOIR":
            raw  = await mcp_pool.call("actions", "creer_facture_avoir", {
                "num_facture_origine": state["num_piece"],
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "MOUVEMENT_STOCK":
            raw  = await mcp_pool.call("actions", "ajuster_mouvement_stock", {
                "ref_article":    state["ref_article"],
                "qte_mouvement":  state["quantite"],
                "type_mouvement": "SORTIE" if "sort" in state["demande_brute"].lower() else "ENTREE",
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or json.dumps(data, ensure_ascii=False)

        elif act == "PROPOSITION_ACHAT":
            raw  = await mcp_pool.call("actions", "generer_proposition_achat", {
                "ref_article":      state["ref_article"],
                "qte_a_commander":  state["quantite"] or 0.0,
                "code_fournisseur": "FOUR01",
            })
            data = _parse_mcp_response(raw)
            err  = _traiter_erreur_mcp(data)
            state["reponse_brute"] = err or data.get("message", json.dumps(data, ensure_ascii=False))

        else:
            state["reponse_brute"] = f"__INCONNU__:{act}"
            return state

        await response_cache.invalidate_writes()

    except BaseException as e:
        # Capture tous les types d'erreurs (sqlite3.Error, pyodbc.Error, etc.)
        # pour éviter que les erreurs MSSQL non attrapées remontent dans
        # le TaskGroup d'anyio et causent "unhandled errors in a TaskGroup".
        import traceback
        _subs = getattr(e, 'exceptions', None)
        if _subs:
            logger.error("   ⚠️  [Écriture] ExceptionGroup avec %d sous-exceptions:", len(_subs))
            for i, sub in enumerate(_subs):
                logger.error("   ⚠️  [Écriture] Sous-exception #%d: %s", i+1, sub)
                traceback.print_exception(type(sub), sub, sub.__traceback__)
            state["reponse_brute"] = f"__ERREUR__:{_safe_str(_subs[0])}"
        else:
            state["reponse_brute"] = f"__ERREUR__:{_safe_str(e)}"
    return state