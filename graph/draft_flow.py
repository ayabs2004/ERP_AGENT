"""
draft_flow.py — Cycle Draft → Preview → CONFIRM → Exécution → PDF — v1.0
==========================================================================
S'intègre à orchestrateur_general.py SANS toucher aux workflows MCP
(`_mcp_workflow_bl`, `_mcp_workflow_of`, `_mcp_workflow_bf`,
`_mcp_workflow_bl_achat`) qui restent les SEULS points d'exécution
finale en base. Ce module ne fait QUE :
  1. Collecter / compléter le brouillon (questions si infos manquantes)
  2. Générer un PDF de preview (filigrane BROUILLON) — AUCUNE écriture DB
  3. Attendre une confirmation stricte ("CONFIRM" / "VALIDATE")
  4. Appeler le workflow MCP réel SEULEMENT après confirmation
  5. Générer le PDF final + enchaîner les suggestions (BL→Facture, OF→BF)
     avec une ALERTE PERSISTANTE tant que le BF n'est pas créé.

Intégration : voir INTEGRATION.md
"""

from __future__ import annotations
from dateutil import parser as _dtparser

import re
import time
from datetime import datetime
from typing import Optional

from api.mcp_pool import pool as mcp_pool
from formatting.pdf_generator import generer_pdf_async
from api.mcp_actions_sage import _get_conn, _resolve_article, _get_nomenclature

# ─────────────────────────────────────────────────────────────────────
# Prévisualisation des documents OF/BF : enrichissement nomenclature
# ─────────────────────────────────────────────────────────────────────
# draft_flow.py — en tête de fichier
import adaptation.db_adapter as sch

def _enrichir_facture_depuis_bl(draft: dict) -> dict:
    type_doc = (draft.get("type_doc") or "").upper()
    if type_doc not in ("FACTURE", "FA_ACHAT"):
        return draft
    num_bl = draft.get("num_piece_source", "")
    if not num_bl:
        return draft
    if (draft.get("code_client") and draft.get("code_fournisseur") and draft.get("ref_article")
            and draft.get("quantite") and draft.get("date_livraison")):
        return draft
    conn = _get_conn()
    try:
        entete = conn.execute(
            f"SELECT {sch.C_DO_TIERS} AS DO_Tiers, {sch.C_DO_DATE} AS DO_Date "
            f"FROM {sch.T_DOC_ENTETE} WHERE {sch.C_DO_PIECE} = ?",
            (num_bl,)
        ).fetchone()
        if entete:
            if type_doc == "FA_ACHAT" and not draft.get("code_fournisseur"):
                draft["code_fournisseur"] = entete["DO_Tiers"]
            elif type_doc == "FACTURE" and not draft.get("code_client"):
                draft["code_client"] = entete["DO_Tiers"]
            if not draft.get("date_livraison") and entete["DO_Date"]:
                draft["date_livraison"] = entete["DO_Date"]
        ligne = conn.execute(
            f"SELECT {sch.C_DL_REF} AS AR_Ref, {sch.C_DL_QTE} AS DL_Qte, "
            f"{sch.C_DL_PRIX} AS DL_PrixUnitaire "
            f"FROM {sch.T_DOC_LIGNE} WHERE {sch.C_DL_PIECE} = ?",
            (num_bl,),
        ).fetchone()
        if ligne:
            if not draft.get("ref_article"):
                draft["ref_article"] = ligne["AR_Ref"]
            if not draft.get("quantite"):
                draft["quantite"] = ligne["DL_Qte"]
            if not draft.get("prix_unitaire"):
                draft["prix_unitaire"] = ligne["DL_PrixUnitaire"]
    except Exception as e:
        print(f"   ⚠️  [Facture depuis BL] {e}")
    finally:
        conn.close()
    return draft


def _enrichir_bf_depuis_of(draft: dict) -> dict:
    if (draft.get("type_doc") or "").upper() != "BF":
        return draft
    if draft.get("ref_article"):
        return draft
    num_of = (
        draft.get("num_of", "")
        or draft.get("num_piece_source", "")
        or draft.get("num_piece", "")
    ).strip().upper()
    if not num_of:
        return draft
    conn = _get_conn()
    try:
        row = conn.execute(
            f"SELECT {sch.C_DL_REF} AS AR_Ref, {sch.C_DL_QTE} AS DL_Qte "
            f"FROM {sch.T_DOC_LIGNE} WHERE UPPER({sch.C_DL_PIECE}) = ?",
            (num_of,),
        ).fetchone()
        if row:
            draft["ref_article"] = row["AR_Ref"]
            # Quantité PLANIFIÉE conservée à titre indicatif seulement.
            # On ne pré-remplit JAMAIS draft["quantite"] : la quantité
            # finale réellement produite doit toujours être confirmée
            # par l'utilisateur (elle peut différer du prévisionnel).
            draft["quantite_prevue"] = row["DL_Qte"]
            article = _resolve_article(conn, row["AR_Ref"])
            if article and not draft.get("designation_article"):
                draft["designation_article"] = article.get("AR_Design") or row["AR_Ref"]
        else:
            print(f"   ⚠️  [BF depuis OF] Aucune ligne trouvée pour la pièce '{num_of}'")
    except Exception as e:
        print(f"   ⚠️  [BF depuis OF] Erreur enrichissement : {e}")
    finally:
        conn.close()
    return draft
def _enrichir_nomenclature_preview(draft: dict) -> dict:
    type_doc = (draft.get("type_doc") or "").upper()
    if type_doc not in {"OF", "BF"}:
        return draft

    ref_article = draft.get("ref_article", "")
    if not ref_article:
        return draft

    try:
        quantite = float(draft.get("quantite", 0) or 0)
    except (ValueError, TypeError):
        quantite = 0.0

    if quantite <= 0:
        return draft
    if draft.get("nomenclature"):
        return draft

    conn = _get_conn()
    try:
        article = _resolve_article(conn, ref_article)
        if article:
            article = dict(article)
            ref_article = article["AR_Ref"]
            draft["ref_article"] = ref_article
            if not draft.get("designation_article"):
                designation = article.get("AR_Design") if article.get("AR_Design") else ref_article
                draft["designation_article"] = designation

        composants = _get_nomenclature(conn, ref_article)
        if not composants:
            return draft

        draft["nomenclature"] = [
            {
                "ref": comp["ref_composant"],
                "designation": comp["designation"],
                "qte": comp["qte_necessaire"] * quantite,
                "prix_unitaire": comp["prix_utilise"],
                "total": comp["qte_necessaire"] * quantite * comp["prix_utilise"],
            }
            for comp in composants
        ]
    finally:
        conn.close()

    return draft


def _enrichir_prix_preview(draft: dict) -> dict:
    type_doc = (draft.get("type_doc") or "").upper()
    if type_doc not in {"BL", "FACTURE", "BL_ACHAT", "FA_ACHAT", "BC", "AVOIR", "AV", "FA"}:
        return draft
    if draft.get("prix_unitaire") or not draft.get("ref_article"):
        return draft

    conn = _get_conn()
    try:
        article = _resolve_article(conn, draft["ref_article"])
        if article:
            article = dict(article)
            if not draft.get("designation_article") and article.get("AR_Design"):
                draft["designation_article"] = article["AR_Design"]
            col_prix = "AR_PrixAch" if type_doc in {"BL_ACHAT", "FA_ACHAT"} else "AR_PrixVen"
            prix = float(article.get(col_prix) or 0.0)
            if prix > 0:
                draft["prix_unitaire"] = prix
    finally:
        conn.close()
    return draft

# ─────────────────────────────────────────────────────────────────────
# SCHÉMAS — champs requis par type de document avant preview
# ─────────────────────────────────────────────────────────────────────
SCHEMAS_DOCUMENTS: dict[str, dict] = {
    "BL": {
        "champs": ["code_client", "ref_article", "quantite","date_livraison"],
        "label_champs": {
            "code_client": "Quel client ?",
            "ref_article": "Quelle référence article ?",
            "quantite":    "Quelle quantité ?",
            "date_livraison": "Quelle date de livraison souhaitée ? (JJ/MM/AAAA)",
        },
    },
    "FACTURE": {
        "champs": ["code_client", "ref_article", "quantite","date_livraison"],
        "label_champs": {
            "code_client": "Quel client ?",
            "ref_article": "Quelle référence article ?",
            "quantite":    "Quelle quantité ?",
            "date_livraison": "Quelle date de facturation souhaitée ? (JJ/MM/AAAA)",
        },
    },
    "BL_ACHAT": {
        "champs": ["code_fournisseur", "ref_article", "quantite", "prix_unitaire","date_livraison"],
        "label_champs": {
            "code_fournisseur": "Quel fournisseur ?",
            "ref_article":      "Quelle référence article ?",
            "quantite":         "Quelle quantité reçue ?",
            "prix_unitaire":    "Quel prix d'achat unitaire ?",
            "date_livraison": "Quelle date de livraison souhaitée ? (JJ/MM/AAAA)",
        },
    },
    "FA_ACHAT": {
        "champs": ["code_fournisseur", "ref_article", "quantite", "prix_unitaire","date_livraison"],
        "label_champs": {
            "code_fournisseur": "Quel fournisseur ?",
            "ref_article":      "Quelle référence article ?",
            "quantite":         "Quelle quantité ?",
            "prix_unitaire":    "Quel prix d'achat unitaire ?",
            "date_livraison": "Quelle date de livraison souhaitée ? (JJ/MM/AAAA)",
        },
    },
    "OF": {
        "champs": ["ref_article", "quantite"],
        "label_champs": {
            "ref_article": "Quel article fabriquer ?",
            "quantite":    "Quelle quantité à produire ?",
        },
    },
    "BF": {
        "champs": ["ref_article", "quantite", "num_of"],
        "label_champs": {
            "ref_article": "Quel article ?",
            "quantite":    "Quelle quantité finale produite ?",
            "num_of":      "Quel OF associé ?",
        },
    },
}

_MOTS_CONFIRM_STRICT = {
    "confirm", "confirme", "confirmé", "confirmer",
    "validate", "valide", "validé", "valider",
}
_MOTS_ANNULER_STRICT = {
    "annule", "annuler", "annulé", "stop", "cancel", "non merci",
}


def est_confirmation_stricte(texte: str) -> bool:
    return texte.strip().lower().rstrip("!.") in _MOTS_CONFIRM_STRICT


def est_annulation_stricte(texte: str) -> bool:
    return texte.strip().lower().rstrip("!.") in _MOTS_ANNULER_STRICT

def champs_manquants(type_doc: str, draft: dict) -> list[str]:
    schema = SCHEMAS_DOCUMENTS.get((type_doc or "").upper(), {})
    requis = schema.get("champs", [])
    manquants = []
    for c in requis:
        val = draft.get(c)
        if c in ("quantite", "prix_unitaire"):
            try:
                ok = val is not None and float(val) > 0
            except (TypeError, ValueError):
                ok = False
        else:
            ok = bool(val)
        if not ok:
            manquants.append(c)
    return manquants

def question_pour_champ(type_doc: str, champ: str, draft: dict | None = None) -> str:
    schema = SCHEMAS_DOCUMENTS.get((type_doc or "").upper(), {})
    base = schema.get("label_champs", {}).get(champ, f"Valeur pour '{champ}' ?")
    if champ == "quantite" and draft and draft.get("quantite_prevue"):
        base += f" (quantité prévue à l'OF : {draft['quantite_prevue']:g})"
    return base


async def _verifier_stock_draft(draft: dict) -> tuple[bool, str]:
    if (draft.get("type_doc", "").upper() != "BL"
            or not draft.get("ref_article")
            or float(draft.get("quantite", 0)) <= 0):
        return True, ""

    try:
        raw = await mcp_pool.call("nl2sql", "verifier_stock_article", {
            "ref_article": draft["ref_article"],
        })
        if not raw or raw.startswith("STOCK : NON_TROUVE"):
            return True, ""

        m = re.search(r"net\s*:\s*([+-]?\d+(?:[.,]\d+)?)", raw, re.IGNORECASE)
        if m:
            qte_net = float(m.group(1).replace(",", "."))
            qte_demandee = float(draft.get("quantite", 0) or 0)
            if qte_net < qte_demandee:
                return False, (
                    f"❌ Stock insuffisant pour '{draft['ref_article']}' : "
                    f"{qte_net:.0f} net disponible pour {qte_demandee:.0f} demandées."
                )
    except Exception as e:
        print(f"   ⚠️  [Stock draft] erreur vérification stock : {e}")
    return True, ""

def injecter_reponse_dans_draft(type_doc: str, draft: dict, texte_user: str) -> dict:
    manquants = champs_manquants(type_doc, draft)
    if not manquants:
        return draft
    champ = manquants[0]
    texte = texte_user.strip()

    if champ == "quantite":
        m = re.search(r"(\d+(?:[.,]\d+)?)", texte)
        if m:
            draft["quantite"] = float(m.group(1).replace(",", "."))
    elif champ == "prix_unitaire":
        m = re.search(r"(\d+(?:[.,]\d+)?)", texte)
        if m:
            draft["prix_unitaire"] = float(m.group(1).replace(",", "."))
    elif champ == "num_of":
        m = re.search(r"\b(OF[A-Z0-9]+)\b", texte, re.IGNORECASE)
        draft["num_of"] = (m.group(1).upper() if m else texte.upper())
    elif champ == "date_livraison":                      # ← AJOUT
        try:
            dt = _dtparser.parse(texte, dayfirst=True, fuzzy=True)
            if dt.date() < datetime.now().date():
                draft["_erreur_champ"] = "📅 Date déjà passée, merci d'indiquer une date future."
            else:
                draft["date_livraison"] = dt.strftime("%d/%m/%Y")
        except Exception:
            draft["_erreur_champ"] = "📅 Date non reconnue, merci de préciser (ex: 12/08/2026)."
    else:
        draft[champ] = texte

    return draft


# ─────────────────────────────────────────────────────────────────────
# CONSTRUCTION DU DRAFT depuis le state du classifier
# ─────────────────────────────────────────────────────────────────────
def construire_draft_depuis_state(state: dict) -> dict:
    """
    Reprend les champs déjà extraits par noeud_classifier
    (code_client, ref_article, quantite, type_doc, num_piece...)
    et construit le draft initial.
    """
    type_doc = (state.get("type_doc") or "BL").upper()
    draft = {
        "type_doc":       type_doc,
        "ref_article":    state.get("ref_article", ""),
        "quantite":       state.get("quantite", 0.0) or 0.0,
        
        "date_str":       datetime.now().strftime("%d/%m/%Y"),
    }
    if type_doc == "BL_ACHAT":
        draft["code_fournisseur"] = state.get("code_client", "")
    elif type_doc == "BF":
        draft["code_client"] = state.get("code_client", "") or "PROD-INT"
        draft["num_of"] = (
            state.get("num_piece", "")
            or state.get("num_piece_source", "")
            or state.get("dernier_num_piece", "")
        )
    elif type_doc == "OF":
        draft["code_client"] = state.get("code_client", "") or "PROD-INT"
    elif type_doc == "FA_ACHAT" and state.get("action") == "TRANSFORMER_DOC":
        draft["code_fournisseur"]  = state.get("code_client", "") or state.get("code_fournisseur", "")
        draft["num_piece_source"]  = state.get("num_piece", "")
    elif type_doc == "FACTURE" and state.get("action") == "TRANSFORMER_DOC":
        draft["code_client"]      = state.get("code_client", "")
        draft["num_piece_source"] = state.get("num_piece", "")
    else:  # BL, FACTURE
        draft["code_client"] = state.get("code_client", "")
    return draft

# ─────────────────────────────────────────────────────────────────────
# PREVIEW — génère le PDF brouillon + texte récapitulatif
# ─────────────────────────────────────────────────────────────────────
async def generer_preview(draft: dict) -> tuple[str, str]:
    draft = _enrichir_prix_preview(draft)
    draft = _enrichir_bf_depuis_of(draft)
    draft = _enrichir_nomenclature_preview(draft)

    pdf_path = await generer_pdf_async(draft, is_draft=True)

    type_doc = draft.get("type_doc", "")
    lignes = [f"📄 **Aperçu — {type_doc}** (brouillon, rien n'est encore enregistré)", "─" * 50]
    if draft.get("code_client"):
        intitule = draft.get("intitule_client", "")
        lignes.append(f"  Client       : {draft['code_client']}" + (f" — {intitule}" if intitule else ""))
    if draft.get("code_fournisseur"):
        intitule_f = draft.get("intitule_fournisseur", "")
        lignes.append(f"  Fournisseur  : {draft['code_fournisseur']}" + (f" — {intitule_f}" if intitule_f else ""))
    lignes.append(f"  Article      : {draft.get('ref_article', '—')}")
    lignes.append(f"  Quantité     : {draft.get('quantite', 0)}")
    if draft.get("prix_unitaire") is not None:
        montant_total = float(draft.get('quantite', 0) or 0) * float(draft['prix_unitaire'])
        lignes.append(f"  Prix unit.   : {draft['prix_unitaire']:.3f}")
        lignes.append(f"  Total HT     : {montant_total:.3f}")
    
    if draft.get("ref_article") and type_doc in ("BL", "BF"):
        try:
            from api.mcp_actions_sage import _article_a_des_lots, _lister_lots_disponibles, _get_conn
            conn = _get_conn()
            if _article_a_des_lots(conn, draft["ref_article"]):
                lots = _lister_lots_disponibles(conn, draft["ref_article"])
                if lots:
                    lignes.append("  Lots dispo.  :")
                    for lot in lots:
                        lignes.append(f"    - {lot['numero']} ({lot['qte_restante']:.0f} u) exp: {lot.get('peremption') or 'N/A'}")
                else:
                    lignes.append("  Lots dispo.  : ⚠️ Aucun lot en stock")
            conn.close()
        except Exception:
            pass
    if draft.get("num_of"):
        lignes.append(f"  OF lié       : {draft['num_of']}")
    if draft.get("nomenclature"):
        lignes.append("  Nomenclature :")
        for comp in draft["nomenclature"]:
            lignes.append(
                f"    - {comp.get('ref','?')} {comp.get('designation','').strip()} x{comp.get('qte',0):g}"
                + (f" @ {comp.get('prix_unitaire',0):.3f}" if comp.get('prix_unitaire') else "")
            )
    lignes.append("─" * 50)
    lignes.append(f"📎 PDF brouillon : {pdf_path}")
    lignes.append("")
    lignes.append("➡️  Tapez **CONFIRM** (ou **VALIDATE**) pour créer le document définitif,")
    lignes.append("    ou **ANNULER** pour abandonner.")

    return "\n".join(lignes), pdf_path
# ─────────────────────────────────────────────────────────────────────
# EXÉCUTION RÉELLE — appelle les workflows MCP existants (inchangés)
# ─────────────────────────────────────────────────────────────────────
async def executer_draft_confirme(
    draft: dict,
    mcp_workflow_bl,
    mcp_workflow_of,
    mcp_workflow_bf,
    mcp_workflow_bl_achat,
    mcp_pool_transformer_document=None,
    mcp_workflow_facture=None,
    mcp_workflow_fa_achat=None,
) -> dict:
    """
    Appelle le workflow MCP réel correspondant au type_doc.
    Retourne le dict résultat standard (statut, DO_Piece, message, ...)
    tel que renvoyé par mcp_sage.py — INCHANGÉ.
    """
    type_doc = (draft.get("type_doc") or "").upper()

    if type_doc == "BL":
        return await mcp_workflow_bl(
            draft.get("code_client", ""), draft.get("ref_article", ""),
            float(draft.get("quantite", 0)), float(draft.get("prix_unitaire", 0) or 0),
        )
    if type_doc == "BL_ACHAT":
        return await mcp_workflow_bl_achat(
            draft.get("code_fournisseur", ""), draft.get("ref_article", ""),
            float(draft.get("quantite", 0)), float(draft.get("prix_unitaire", 0) or 0),
        )
    if type_doc == "OF":
        return await mcp_workflow_of(
            draft.get("ref_article", ""), float(draft.get("quantite", 0)),
            draft.get("code_client", "PROD-INT"),
        )
    if type_doc == "BF":
        return await mcp_workflow_bf(
            draft.get("ref_article", ""), float(draft.get("quantite", 0)),
            draft.get("num_of", ""), draft.get("code_client", "PROD-INT"),
        )
    if type_doc in {"FACTURE", "FA_ACHAT"} and mcp_pool_transformer_document and draft.get("num_piece_source"):
        # Transformation d'un BL existant (BL -> FACTURE, ou BL_ACHAT -> FA_ACHAT)
        return await mcp_pool_transformer_document(draft["num_piece_source"], type_doc)
    if type_doc == "FACTURE" and mcp_workflow_facture:
        # Facture créée directement (sans BL source)
        return await mcp_workflow_facture(
            draft.get("code_client", ""), draft.get("ref_article", ""),
            float(draft.get("quantite", 0)), float(draft.get("prix_unitaire", 0) or 0),
        )
    if type_doc == "FA_ACHAT" and mcp_workflow_fa_achat:
        # Facture d'achat créée directement (sans BL source)
        return await mcp_workflow_fa_achat(
            draft.get("code_fournisseur", ""), draft.get("ref_article", ""),
            float(draft.get("quantite", 0)), float(draft.get("prix_unitaire", 0) or 0),
        )
    return {"statut": "ERREUR", "message": f"Type de document non géré par le flow : {type_doc}"}


async def generer_pdf_final(draft: dict, num_piece: str) -> str:
    draft_final = {**draft, "num_piece": num_piece}
    return await generer_pdf_async(draft_final, is_draft=False)


# ─────────────────────────────────────────────────────────────────────
# ALERTES PERSISTANTES (ex : OF sans BF)
# Stockées dans contexte_session["alertes_persistantes"] : list[dict]
# Chaque alerte : {"id", "type", "num_of", "ref_article", "message", "ts"}
# ─────────────────────────────────────────────────────────────────────
def ajouter_alerte_bf_requis(contexte_session: dict, num_of: str, ref_article: str, qte_prevue: float):
    alertes = contexte_session.setdefault("alertes_persistantes", [])
    # Évite les doublons pour le même OF
    if any(a.get("num_of") == num_of for a in alertes):
        return
    alertes.append({
        "id":           f"BF_{num_of}",
        "type":         "BF_REQUIS",
        "num_of":       num_of,
        "ref_article":  ref_article,
        "qte_prevue":   qte_prevue,
        "message": (
            f"⚠️  L'OF {num_of} ({ref_article}, {qte_prevue:g} u prévues) "
            f"n'a pas encore de Bon de Fabrication associé. "
            f"Tapez \"crée le BF pour {num_of}\" pour le finaliser."
        ),
        "ts": time.time(),
    })

# ─────────────────────────────────────────────────────────────────────
# ENRICHISSEMENT — intitulé client/fournisseur + prix article via MCP
# ─────────────────────────────────────────────────────────────────────
async def enrichir_draft(draft: dict, mcp_pool) -> dict:
    """
    Complète le draft avec l'intitulé du tiers et le prix de vente
    de l'article, si absents. Ne touche jamais prix_unitaire si déjà
    fourni explicitement par l'utilisateur (ex: BL_ACHAT).
    """
    import json

    code_client = draft.get("code_client", "")
    code_fourn  = draft.get("code_fournisseur", "")
    ref_article = draft.get("ref_article", "")

    if code_client and code_client != "PROD-INT" and not draft.get("intitule_client"):
        try:
            txt = await mcp_pool.call(
                "nl2sql", "rechercher_fiche_client", {"code_client": code_client}
            )
            if not txt:
                print(f"   ⚠️  [Enrichissement] client vide pour {code_client}")
                data = {}
            else:
                data = json.loads(txt)
            if data.get("CT_Intitule"):
                draft["intitule_client"] = data["CT_Intitule"]
        except Exception as e:
            print(f"   ⚠️  [Enrichissement] erreur client {code_client}: {e}")
            pass

    if code_fourn and not draft.get("intitule_fournisseur"):
        try:
            txt = await mcp_pool.call(
                "nl2sql", "executer_sql_vanna",
                {
                    "sql": f"SELECT TOP 1 CT_Intitule FROM F_COMPTET WHERE CT_Num='{code_fourn}'",
                    "description": f"Intitulé fournisseur {code_fourn}",
                },
            )
            if not txt:
                print(f"   ⚠️  [Enrichissement] fournisseur vide pour {code_fourn}")
                data = {}
            else:
                data = json.loads(txt)
            rows = data.get("resultats") or data.get("rows") or []
            if rows:
                first_row = rows[0]
                if isinstance(first_row, dict):
                    draft["intitule_fournisseur"] = first_row.get("CT_Intitule", "")
                elif "CT_Intitule" in first_row.keys():
                    draft["intitule_fournisseur"] = first_row["CT_Intitule"]
        except Exception:
            pass

    # Le prix unitaire n'est auto-rempli QUE s'il est absent/nul
    # (BL_ACHAT le demande explicitement à l'utilisateur → ne pas écraser)
    if ref_article and not draft.get("prix_unitaire"):
        try:
            col_prix = "AR_PrixAch" if type_doc in ("BL_ACHAT", "FA_ACHAT") else "AR_PrixVen"
            txt = await mcp_pool.call(
                "nl2sql", "executer_sql_vanna",
                {
                    "sql": f"SELECT TOP 1 AR_Design, {col_prix} FROM F_ARTICLE WHERE UPPER(AR_Ref)=UPPER('{ref_article}')",
                    "description": f"Prix et désignation de {ref_article}",
                },
            )
            if not txt:
                print(f"   ⚠️  [Enrichissement] prix vide pour {ref_article}")
                data = {}
            else:
                data = json.loads(txt)
            rows = data.get("resultats") or data.get("rows") or []
            if rows:
                first_row = rows[0]
                if isinstance(first_row, dict):
                    prix = first_row.get(col_prix)
                    design = first_row.get("AR_Design")
                else:
                    prix = first_row[col_prix] if col_prix in first_row.keys() else None
                    design = first_row["AR_Design"] if "AR_Design" in first_row.keys() else None
                if prix is not None:
                    draft["prix_unitaire"] = float(prix)
                if design and not draft.get("designation_article"):
                    draft["designation_article"] = design
        except Exception as e:
            print(f"   ⚠️  [Enrichissement] erreur prix: {e}")
            pass

    return draft
def resoudre_alerte_bf(contexte_session: dict, num_of: str):
    alertes = contexte_session.get("alertes_persistantes", [])
    contexte_session["alertes_persistantes"] = [
        a for a in alertes if a.get("num_of") != num_of
    ]


def formater_alertes_persistantes(contexte_session: dict) -> str:
    alertes = contexte_session.get("alertes_persistantes", [])
    if not alertes:
        return ""
    lignes = ["\n" + "─" * 50, "🔔 RAPPELS EN ATTENTE :"]
    for a in alertes:
        lignes.append(f"  {a['message']}")
    lignes.append("─" * 50)
    return "\n".join(lignes)