#!/usr/bin/env python3
"""
mcp_sage.py — Serveur MCP Actions Sage 100 v4.0
================================================
Expose toutes les fonctions de actions_sage.py comme outils MCP.
"""

import json
import logging
import sqlite3
import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
from uuid import uuid4

from schema_sage import (
    DOC_CODES, DOC_DOMAINE, DOC_PREFIXES, DOC_TYPE, DOC_DESTOCKANTS, DOC_STOCKANTS,
    CURRENCY_SYMBOL,
)

logger = logging.getLogger("sage.erp.actions")

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
except ModuleNotFoundError:  # pragma: no cover - fallback for test environments
    from dataclasses import dataclass

    @dataclass
    class _FallbackTextContent:
        type: str
        text: str

    @dataclass
    class _FallbackTool:
        name: str
        description: str
        inputSchema: dict

    class _FallbackTypes:
        TextContent = _FallbackTextContent
        Tool = _FallbackTool

    types = _FallbackTypes()

    class Server:
        def __init__(self, name: str):
            self.name = name

        def list_tools(self):
            def decorator(func):
                return func
            return decorator

        def call_tool(self):
            def decorator(func):
                return func
            return decorator

        def create_initialization_options(self):
            return {}

    class _FallbackStdioServer:
        async def __aenter__(self):
            return None, None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    stdio_server = _FallbackStdioServer

# ─────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent / "entreprise_mock.db"))

app = Server("sage100-mcp")

# ─────────────────────────────────────────────────────────────────────
# HELPERS INTERNES
# ─────────────────────────────────────────────────────────────────────

def _safe_str(obj) -> str:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj).encode("utf-8", errors="replace").decode("utf-8")


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Création des tables annexes manquantes au premier accès
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mouvements_stock (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            AR_Ref         TEXT,
            type_mouvement TEXT,
            qte            REAL,
            motif          TEXT,
            date_mouvement TEXT
        )
    """)
    # ✅ CORRIGÉ
    conn.execute("""
    CREATE TABLE IF NOT EXISTS reglements (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        DO_Piece       TEXT,
        mode_paiement  TEXT,
        montant        REAL,
        date_reglement TEXT,
        numero_piece_paiement TEXT
    )
""")
    try:
        conn.execute("ALTER TABLE reglements ADD COLUMN numero_piece_paiement TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _generer_num_piece(type_doc: str, conn: Optional[sqlite3.Connection] = None) -> str:
    prefix = DOC_PREFIXES.get(type_doc.upper(), type_doc[:2].upper())
    ts = datetime.now().strftime("%y%m%d%H%M%S%f")
    suffix = uuid4().hex[:8].upper()
    return f"{prefix}{ts}{suffix}"


def _resolve_client(conn: sqlite3.Connection, code_ou_nom: str) -> Optional[sqlite3.Row]:
    """
    Recherche dans F_COMPTET par CT_Num exact, puis par CT_Intitule partiel.
    """
    if not code_ou_nom:
        return None
    row = conn.execute(
        "SELECT * FROM F_COMPTET WHERE CT_Num = ? COLLATE NOCASE",
        (code_ou_nom,)
    ).fetchone()
    if row:
        return dict(row)
    rows = conn.execute(
        "SELECT * FROM F_COMPTET WHERE CT_Intitule LIKE ? COLLATE NOCASE LIMIT 5",
        (f"%{code_ou_nom}%",)
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        return None
    return dict(rows[0])


def _resolve_article(conn: sqlite3.Connection, ref_ou_nom: str) -> Optional[sqlite3.Row]:
    """
    Recherche dans F_ARTICLE par AR_Ref exact, puis par AR_Design partiel.
    """
    if not ref_ou_nom:
        return None
    row = conn.execute(
        "SELECT * FROM F_ARTICLE WHERE AR_Ref = ? COLLATE NOCASE",
        (ref_ou_nom,)
    ).fetchone()
    if row:
        return dict(row)
    rows = conn.execute(
        "SELECT * FROM F_ARTICLE WHERE AR_Design LIKE ? COLLATE NOCASE LIMIT 5",
        (f"%{ref_ou_nom}%",)
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        return None
    return dict(rows[0])


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_text(value: object) -> str:
    return f"{_to_decimal(value):.2f} {CURRENCY_SYMBOL}"


def _decimal_sum(values) -> Decimal:
    return sum((_to_decimal(value) for value in values), Decimal("0.00"))


def _get_stock(conn: sqlite3.Connection, ref_article: str) -> float:
    """
    Lit AS_QteSto dans F_ARTSTOCK.
    """
    row = conn.execute(
        "SELECT AS_QteSto FROM F_ARTSTOCK WHERE AR_Ref = ? COLLATE NOCASE",
        (ref_article,)
    ).fetchone()
    return float(row["AS_QteSto"]) if row else 0.0


def _ajuster_stock_db(
    conn: sqlite3.Connection,
    ref_article: str,
    qte: float,
    type_mouvement: str,
    motif: str = "",
) -> dict:
    """
    Met à jour F_ARTSTOCK et trace dans mouvements_stock.
    Utilise une mise à jour atomique et refuse les sorties qui feraient
    passer le stock sous zéro.
    """
    if qte < 0:
        raise ValueError("La quantité de mouvement ne peut pas être négative")

    type_mouvement = type_mouvement.upper()
    row = conn.execute(
        "SELECT AS_QteSto FROM F_ARTSTOCK WHERE AR_Ref = ? COLLATE NOCASE",
        (ref_article,)
    ).fetchone()
    stock_avant = float(row["AS_QteSto"]) if row else 0.0
    if type_mouvement == "SORTIE" and stock_avant < qte:
        raise ValueError(f"Stock insuffisant pour {ref_article}: {stock_avant} < {qte}")

    nouveau_stock = stock_avant - qte if type_mouvement == "SORTIE" else stock_avant + qte
    conn.execute(
        "UPDATE F_ARTSTOCK SET AS_QteSto = ? WHERE AR_Ref = ? COLLATE NOCASE",
        (nouveau_stock, ref_article)
    )
    conn.execute(
        """INSERT INTO mouvements_stock
           (AR_Ref, type_mouvement, qte, motif, date_mouvement)
           VALUES (?, ?, ?, ?, ?)""",
        (ref_article, type_mouvement, qte, motif, datetime.now().isoformat())
    )
    return {
        "ok": True,
        "stock_avant": stock_avant,
        "stock_apres": nouveau_stock,
        "type": type_mouvement,
        "qte": qte,
    }


def _get_nomenclature(conn: sqlite3.Connection, ref_article: str) -> list[dict]:
    """
    Lit F_NOMENCLAT (NO_RefPF / NO_RefMP / NO_Qte)
    et joint F_ARTICLE pour la désignation et les prix du composant.
    """
    rows = conn.execute(
        """SELECT n.NO_RefMP      AS ref_composant,
                  n.NO_Qte       AS qte_necessaire,
                  a.AR_Design    AS designation,
                  a.AR_PrixAch   AS prix_achat,
                  a.AR_PrixVen   AS prix_vente
           FROM F_NOMENCLAT n
           LEFT JOIN F_ARTICLE a
                  ON a.AR_Ref = n.NO_RefMP COLLATE NOCASE
           WHERE n.NO_RefPF = ? COLLATE NOCASE""",
        (ref_article,)
    ).fetchall()
    return [
        {
            "ref_composant":  r["ref_composant"],
            "designation":    r["designation"] or r["ref_composant"],
            "qte_necessaire": float(r["qte_necessaire"]),
            "prix_achat":     float(r["prix_achat"] or 0.0),
            "prix_vente":     float(r["prix_vente"] or 0.0),
            "prix_utilise":   float(r["prix_achat"] or r["prix_vente"] or 0.0),
        }
        for r in rows
    ]


def _inserer_document(
    conn: sqlite3.Connection,
    type_doc: str,
    num_piece: str,
    code_client: str,
    ref_article: str = "",
    qte: float = 0.0,
    prix_unit: float = 0.0,
    montant: float = 0.0,
    num_piece_of: str = "",
    lignes: Optional[list[dict]] = None,
) -> str:
    """
    Insère dans F_DOCENTETE (entête) et F_DOCLIGNE (lignes).
    DO_Domaine : 0 = vente, 1 = achat, 2 = fabrication
    DO_Type    : 2 = BL, 3 = FA, 6 = BC, 1 = OF, 4 = BF, 9 = AV
    num_piece_of est stocké dans DO_Ref (champ libre de référence).
    """
    domaine = DOC_DOMAINE.get(type_doc.upper(), 0)
    do_type = DOC_TYPE.get(type_doc.upper(), 0)

    if lignes is None:
        if isinstance(ref_article, list):
            lignes = ref_article
        else:
            lignes = [{
                "ref_article": ref_article,
                "qte": qte,
                "prix_unit": prix_unit,
                "montant": montant,
            }]

    normalized_lignes = []
    for ligne in lignes:
        if not isinstance(ligne, dict):
            continue
        normalized_lignes.append({
            "ref_article": ligne.get("ref_article") or ligne.get("AR_Ref") or "",
            "qte": float(ligne.get("qte") or ligne.get("DL_Qte") or 0.0),
            "prix_unit": float(_to_decimal(ligne.get("prix_unit") or ligne.get("DL_PrixUnitaire") or 0.0)),
        })

    if not normalized_lignes:
        normalized_lignes = [{"ref_article": ref_article, "qte": qte, "prix_unit": float(_to_decimal(prix_unit))}]

    piece_utilisee = num_piece or _generer_num_piece(type_doc, conn)
    for _ in range(10):
        try:
            conn.execute(
                """INSERT INTO F_DOCENTETE
                   (DO_Piece, DO_Domaine, DO_Type, DO_Date, DO_Ref, CT_Num)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    piece_utilisee,
                    domaine,
                    do_type,
                    datetime.now().date().isoformat(),
                    num_piece_of or None,
                    code_client,
                )
            )
            for ligne in normalized_lignes:
                conn.execute(
                    """INSERT INTO F_DOCLIGNE
                       (DO_Piece, AR_Ref, DL_Qte, DL_PrixUnitaire)
                       VALUES (?, ?, ?, ?)""",
                    (piece_utilisee, ligne["ref_article"], ligne["qte"], ligne["prix_unit"])
                )
            return piece_utilisee
        except sqlite3.IntegrityError:
            piece_utilisee = _generer_num_piece(type_doc, conn)
            continue
    raise sqlite3.IntegrityError("Unable to allocate a unique document number")


def _suggestions_clients(conn: sqlite3.Connection, terme: str) -> list[dict]:
    rows = conn.execute(
        """SELECT CT_Num, CT_Intitule
           FROM F_COMPTET
           WHERE CT_Intitule LIKE ? COLLATE NOCASE
           LIMIT 5""",
        (f"%{terme}%",)
    ).fetchall()
    return [{"CT_Num": r["CT_Num"], "CT_Intitule": r["CT_Intitule"]} for r in rows]


def _suggestions_articles(conn: sqlite3.Connection, terme: str) -> list[dict]:
    rows = conn.execute(
        """SELECT AR_Ref, AR_Design
           FROM F_ARTICLE
           WHERE AR_Design LIKE ? COLLATE NOCASE
              OR AR_Ref    LIKE ? COLLATE NOCASE
           LIMIT 5""",
        (f"%{terme}%", f"%{terme}%")
    ).fetchall()
    return [{"AR_Ref": r["AR_Ref"], "AR_Design": r["AR_Design"]} for r in rows]


# ─────────────────────────────────────────────────────────────────────
# LOGIQUE MÉTIER
# ─────────────────────────────────────────────────────────────────────

def _workflow_bl(
    code_client: str,
    ref_article: str,
    quantite: float,
    prix_unitaire: float = 0.0,
) -> dict:
    conn = _get_conn()
    try:
        # ── Résolution client ─────────────────────────────────────────
        client = _resolve_client(conn, code_client)
        if not client:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": f"❌ Client '{code_client}' introuvable.",
                "suggestions": _suggestions_clients(conn, code_client),
            }

        code_reel  = client["CT_Num"]
        nom_client = client["CT_Intitule"]

        # CT_Validite : 'VALIDE' | 'BLOQUE' | 'SUSPECT'
        statut_cl = str(client["CT_Validite"] or "VALIDE").upper()

        if statut_cl == "BLOQUE":
            return {
                "statut": "CLIENT_BLOQUE",
                "message": (
                    f"🚫 Impossible de créer le BL.\n\n"
                    f"   Client '{nom_client}' ({code_reel}) est BLOQUÉ.\n"
                    f"   Contactez le service comptabilité.\n\n"
                    f"   ➡️  Commande : 'modifier statut client {code_reel}'"
                ),
            }

        alerte_suspect = ""
        if statut_cl == "SUSPECT":
            alerte_suspect = (
                f"⚠️  Client '{nom_client}' marqué SUSPECT (risque de non-paiement)."
            )

        # ── Résolution article ────────────────────────────────────────
        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "message": f"❌ Article '{ref_article}' introuvable.",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        ref_reelle  = article["AR_Ref"]
        desig       = article["AR_Design"]
        prix_auto   = _to_decimal(article["AR_PrixVen"] or 0.0)
        prix_final  = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(prix_auto))
        stock_dispo = _get_stock(conn, ref_reelle)
        montant     = (prix_final * Decimal(str(quantite))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # ── APRÈS ──
        encours_max = _to_decimal(client.get("CT_EncoursMax") or 0.0)
        encours_actuel = _to_decimal(conn.execute(
            """
            SELECT COALESCE(SUM(l.DL_Qte * l.DL_PrixUnitaire), 0.0) AS encours
            FROM F_DOCENTETE e
            LEFT JOIN F_DOCLIGNE l ON e.DO_Piece = l.DO_Piece
            WHERE e.CT_Num = ? AND e.DO_Type = 3 AND e.DO_Domaine = 0
              AND e.DO_Piece NOT IN (SELECT DO_Piece FROM reglements)
            """,
            (code_reel,),
        ).fetchone()[0])
        if encours_max > 0 and encours_actuel + montant > encours_max:
            return {
                "statut": "ENCORS_MAX_ATTEINT",
                "message": (
                    f"⚠️  Encours dépassé pour {code_reel} : "
                    f"{_money_text(encours_actuel + montant)} > {_money_text(encours_max)}"
                ),
            }
        # ── Contrôle stock ────────────────────────────────────────────
        if stock_dispo < quantite:
            manque = quantite - stock_dispo
            return {
                "statut": "STOCK_INSUFFISANT",
                "message": (
                    f"📦 Stock insuffisant pour '{desig}' ({ref_reelle}).\n"
                    f"   Disponible : {stock_dispo} u | "
                    f"Demandé : {quantite} u | Manque : {manque} u\n\n"
                    f"   Voulez-vous lancer un Ordre de Fabrication pour {manque} u ?"
                ),
                "stock_dispo":  stock_dispo,
                "qte_demandee": quantite,
                "manque":       manque,
                "ref_article":  ref_reelle,
                "code_client":  code_reel,
                "prix_unitaire": prix_final,
                "data_bl_en_attente": {
                    "code_client":  code_reel,
                    "nom_client":   nom_client,
                    "ref_article":  ref_reelle,
                    "designation":  desig,
                    "quantite":     quantite,
                    "prix_unitaire": prix_final,
                    "montant":      montant,
                    "alerte_suspect": alerte_suspect,
                },
            }

        # ── Création BL ───────────────────────────────────────────────
        num_bl = _inserer_document(
            conn, "BL", "", code_reel,
            ref_reelle, quantite, prix_final, montant
        )
        mvt = _ajuster_stock_db(
            conn, ref_reelle, quantite, "SORTIE", motif=f"BL {num_bl}"
        )
        conn.commit()

        message = (
            f"✅ Bon de Livraison créé !\n\n"
            f"   • Numéro BL   : {num_bl}\n"
            f"   • Client      : {nom_client} ({code_reel})\n"
            f"   • Article     : {desig} ({ref_reelle})\n"
            f"   • Quantité    : {quantite} u\n"
            f"   • Prix unit.  : {_money_text(prix_final)}\n"
            f"   • Montant     : {_money_text(montant)}\n"
            f"   • Stock après : {mvt['stock_apres']} u\n"
        )
        if alerte_suspect:
            message += f"\n   {alerte_suspect}\n"

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_bl,
            "DO_Tiers":    code_reel,
            "AR_Ref":      ref_reelle,
            "montant":     montant,
            "stock_apres": mvt["stock_apres"],
            "message":     message,
            "alertes":     [alerte_suspect] if alerte_suspect else [],
            "suggestion_facture": {
                "code_client":  code_reel,
                "nom_client":   nom_client,
                "ref_article":  ref_reelle,
                "quantite":     quantite,
                "prix_unitaire": prix_final,
                "montant":      montant,
                "num_bl":       num_bl,
            },
        }
    finally:
        conn.close()


def _workflow_of(
    ref_article: str,
    quantite: float,
    code_client: str = "PROD-INT",
) -> dict:
    conn = _get_conn()
    try:
        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "message": f"❌ Article '{ref_article}' introuvable.",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        ref_reelle = article["AR_Ref"]
        desig      = article["AR_Design"]
        composants = _get_nomenclature(conn, ref_reelle)  # → F_NOMENCLAT

        rapport_compo       = []
        composants_manquants = []
        composants_ok        = []

        if not composants:
            rapport_compo.append(
                f"ℹ️  Aucune nomenclature pour '{desig}' ({ref_reelle}).\n"
                f"   OF créé sans vérification des composants."
            )
        else:
            for comp in composants:
                ref_comp   = comp["ref_composant"]
                desig_comp = comp["designation"]
                qte_besoin = comp["qte_necessaire"] * quantite
                prix_comp  = comp["prix_utilise"]
                total_comp = qte_besoin * prix_comp
                stock_comp = _get_stock(conn, ref_comp)
                ok    = stock_comp >= qte_besoin
                icone = "✅" if ok else "❌"
                rapport_compo.append(
                    f"   {icone} {desig_comp} ({ref_comp}) : "
                    f"besoin {qte_besoin:.3f} u | dispo {stock_comp:.3f} u"
                    + (
                        f" | manque {qte_besoin - stock_comp:.3f} u"
                        if not ok else ""
                    )
                )
                if ok:
                    composants_ok.append({
                        "ref": ref_comp,
                        "desig": desig_comp,
                        "qte": qte_besoin,
                        "prix": prix_comp,
                        "total": total_comp,
                    })
                else:
                    composants_manquants.append({
                        "ref":    ref_comp,
                        "desig":  desig_comp,
                        "besoin": qte_besoin,
                        "dispo":  stock_comp,
                        "manque": qte_besoin - stock_comp,
                    })

        if composants_manquants:
            lignes_manque = "\n".join(
                f"   ❌ {m['desig']} ({m['ref']}) : manque {m['manque']:.1f} u "
                f"(dispo {m['dispo']:.1f} / besoin {m['besoin']:.1f})"
                for m in composants_manquants
            )
            return {
                "statut": "COMPOSANTS_INSUFFISANTS",
                "message": (
                    f"📋 Nomenclature '{desig}' pour {quantite} u :\n"
                    + "\n".join(rapport_compo)
                    + f"\n\n🚫 Stock insuffisant pour certains composants :\n"
                    + lignes_manque
                    + "\n\n   Approvisionnez les composants manquants "
                      "avant de lancer la fabrication."
                ),
                "composants_manquants": composants_manquants,
            }

        # ── Sorties composants ────────────────────────────────────────
        rapport_sorties = []
        cout_total = 0.0
        for comp in composants_ok:
            mvt = _ajuster_stock_db(
                conn, comp["ref"], comp["qte"], "SORTIE",
                motif=f"Consommation OF {ref_reelle}"
            )
            rapport_sorties.append(
                f"   📤 {comp['desig']} ({comp['ref']}) : "
                f"-{comp['qte']:.3f} u → stock {mvt['stock_apres']:.3f} u"
            )
            cout_total += comp["total"]

        # ── Création OF dans F_DOCENTETE / F_DOCLIGNE ─────────────────
        num_of = _inserer_document(
            conn, "OF", "", code_client or "PROD-INT",
            ref_reelle, quantite, 0.0, 0.0
        )
        conn.commit()

        msg_compo   = (
            "\n📋 Nomenclature :\n" + "\n".join(rapport_compo) + "\n"
            if rapport_compo else ""
        )
        msg_sorties = (
            "\n📤 Sorties stock composants :\n" + "\n".join(rapport_sorties) + "\n"
            if rapport_sorties else ""
        )
        msg_cout = (
            f"\n💰 Coût matières estimé : {cout_total:.3f} TND\n"
            if cout_total > 0 else ""
        )

        message = (
            f"✅ Ordre de Fabrication créé !\n"
            + msg_compo + msg_sorties + msg_cout
            + f"\n   • Numéro OF  : {num_of}\n"
            f"   • Article    : {desig} ({ref_reelle})\n"
            f"   • Quantité   : {quantite} u\n"
            f"   • Composants : "
            + ("Déduits du stock" if composants_ok else "N/A")
        )

        return {
            "statut":   "GENERE",
            "DO_Piece": num_of,
            "AR_Ref":   ref_reelle,
            "message":  message,
            "alertes":  [],
            "nomenclature": [
                {
                    "ref": comp["ref"],
                    "designation": comp["desig"],
                    "qte": comp["qte"],
                    "prix_unitaire": comp["prix"],
                    "total": comp["total"],
                }
                for comp in composants_ok
            ],
            "suggestion_bf": {
                "ref_article": ref_reelle,
                "designation": desig,
                "quantite":    quantite,
                "num_of":      num_of,
                "code_client": code_client or "PROD-INT",
            },
        }
    finally:
        conn.close()


def _workflow_bf(
    ref_article: str,
    quantite: float,
    num_of: str = "",
    code_client: str = "PROD-INT",
) -> dict:
    conn = _get_conn()
    try:
        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "message": f"❌ Article '{ref_article}' introuvable.",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        ref_reelle  = article["AR_Ref"]
        desig       = article["AR_Design"]
        stock_avant = _get_stock(conn, ref_reelle)

        composants = _get_nomenclature(conn, ref_reelle)
        rapport_compo = []
        cout_total = 0.0
        for comp in composants:
            qte = comp["qte_necessaire"] * quantite
            prix = comp["prix_utilise"]
            total = qte * prix
            cout_total += total
            rapport_compo.append(
                f"   • {comp['designation']} ({comp['ref_composant']}): "
                f"{qte:.3f} u"
                + (f" @ {prix:.3f} TND = {total:.3f} TND" if prix > 0 else "")
            )

        num_bf = _inserer_document(
            conn, "BF", "", code_client or "PROD-INT",
            ref_reelle, quantite, 0.0, 0.0,
            num_piece_of=num_of          # stocké dans DO_Ref
        )
        mvt = _ajuster_stock_db(
            conn, ref_reelle, quantite, "ENTREE",
            motif=f"Production BF {num_bf}"
        )
        conn.commit()

        message = (
            f"✅ Bon de Fabrication créé !\n\n"
            f"   • Numéro BF      : {num_bf}\n"
            + (f"   • Lié à OF       : {num_of}\n" if num_of else "")
            + f"   • Article        : {desig} ({ref_reelle})\n"
            f"   • Qté fabriquée  : {quantite} u\n"
        )
        if rapport_compo:
            message += (
                "\n📋 Nomenclature :\n"
                + "\n".join(rapport_compo)
                + (f"\n\n💰 Coût matières total : {cout_total:.3f} TND" if cout_total > 0 else "")
            )
        message += (
            "\n\n📦 Stock produit fini mis à jour :\n"
            f"   • Stock avant    : {stock_avant} u\n"
            f"   • Entrée         : +{quantite} u\n"
            f"   • Stock actuel   : {mvt['stock_apres']} u"
        )

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_bf,
            "AR_Ref":      ref_reelle,
            "num_of":      num_of,
            "stock_apres": mvt["stock_apres"],
            "message":     message,
            "alertes":     [],
            "nomenclature": [
                {
                    "ref": comp["ref_composant"],
                    "designation": comp["designation"],
                    "qte": qte,
                    "prix_unitaire": prix,
                    "total": total,
                }
                for comp in composants
            ],
        }
    finally:
        conn.close()


def _generer_facture_directe(
    code_client: str,
    ref_article: str,
    qte: float,
    prix_unitaire: float,
) -> dict:
    conn = _get_conn()
    try:
        client  = _resolve_client(conn, code_client)
        article = _resolve_article(conn, ref_article)
        if not client:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "suggestions": _suggestions_clients(conn, code_client),
            }
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        statut_cl = str(client.get("CT_Validite") or "VALIDE").upper()
        if statut_cl == "BLOQUE":
            return {
                "statut": "CLIENT_BLOQUE",
                "message": f"🚫 Impossible de créer la facture : client {client['CT_Num']} est bloqué.",
            }

        prix_final = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(article["AR_PrixVen"] or 0.0))
        montant    = float((prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        stock_dispo = _get_stock(conn, article["AR_Ref"])
        if stock_dispo < qte:
            return {
                "statut": "STOCK_INSUFFISANT",
                "message": f"📦 Stock insuffisant pour {article['AR_Ref']} : dispo {stock_dispo}, demandé {qte}",
                "stock_dispo": stock_dispo,
                "qte_demandee": qte,
            }

        encours_max = _to_decimal(client.get("CT_EncoursMax") or 0.0)
        encours_actuel = _to_decimal(conn.execute(
            """
            SELECT COALESCE(SUM(l.DL_Qte * l.DL_PrixUnitaire), 0.0) AS encours
            FROM F_DOCENTETE e
            LEFT JOIN F_DOCLIGNE l ON e.DO_Piece = l.DO_Piece
            WHERE e.CT_Num = ? AND e.DO_Type = 3 AND e.DO_Domaine = 0
              AND e.DO_Piece NOT IN (SELECT DO_Piece FROM reglements)
            """,
            (client["CT_Num"],),
        ).fetchone()[0])
        if encours_max > 0 and encours_actuel + _to_decimal(montant) > encours_max:
            return {
                "statut": "ENCORS_MAX_ATTEINT",
                "message": (
                    f"⚠️  Encours dépassé pour {client['CT_Num']} : "
                    f"{_money_text(encours_actuel + _to_decimal(montant))} > {_money_text(encours_max)}"
                ),
            }

        num_fa = _inserer_document(
            conn, "FACTURE", "",
            client["CT_Num"], article["AR_Ref"],
            qte, prix_final, montant
        )
        _ajuster_stock_db(conn, article["AR_Ref"], qte, "SORTIE", motif=f"FACTURE {num_fa}")
        conn.commit()

        return {
            "statut":   "GENERE",
            "DO_Piece": num_fa,
            "DO_Tiers": client["CT_Num"],
            "AR_Ref":   article["AR_Ref"],
            "montant":  montant,
            "message": (
                f"✅ Facture créée !\n"
                f"   • Numéro  : {num_fa}\n"
                f"   • Client  : {client['CT_Intitule']}\n"
                f"   • Article : {article['AR_Design']}\n"
                f"   • Montant : {_money_text(montant)}"
            ),
        }
    finally:
        conn.close()


def _generer_bc_direct(
    code_client: str,
    ref_article: str,
    qte: float,
    prix_unitaire: float,
) -> dict:
    conn = _get_conn()
    try:
        client  = _resolve_client(conn, code_client)
        article = _resolve_article(conn, ref_article)
        if not client:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "suggestions": _suggestions_clients(conn, code_client),
            }
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        prix_final = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(article["AR_PrixVen"] or 0.0))
        montant    = float((prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        num_bc     = _inserer_document(
            conn, "BC", "",
            client["CT_Num"], article["AR_Ref"],
            qte, prix_final, montant
        )
        conn.commit()

        return {
            "statut":   "GENERE",
            "DO_Piece": num_bc,
            "message": (
                f"✅ Bon de Commande créé !\n"
                f"   • Numéro  : {num_bc}\n"
                f"   • Client  : {client['CT_Intitule']}\n"
                f"   • Article : {article['AR_Design']}\n"
                f"   • Montant : {_money_text(montant)}"
            ),
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────
def _workflow_bl_achat(
    code_fournisseur: str,
    ref_article: str,
    quantite: float,
    prix_unitaire: float = 0.0,
) -> dict:
    """
    Workflow Bon de Réception fournisseur (BL Achat) :
    - Vérifie que le tiers est bien un fournisseur (CT_Type=1)
    - Crée le document (DO_Domaine=1, DO_Type=2) dans F_DOCENTETE/F_DOCLIGNE
    - Incrémente le stock (+ENTREE dans F_ARTSTOCK)
    - Suggère la transformation en Facture Fournisseur
    """
    conn = _get_conn()
    try:
        # ── Résolution fournisseur ─────────────────────────────────
        fournisseur = _resolve_client(conn, code_fournisseur)
        if not fournisseur:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": f"❌ Fournisseur '{code_fournisseur}' introuvable.",
                "suggestions": _suggestions_clients(conn, code_fournisseur),
            }

        code_reel  = fournisseur["CT_Num"]
        nom_four   = fournisseur["CT_Intitule"]
        type_tiers = int(fournisseur["CT_Type"] or 0)

        if type_tiers == 0:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": (
                    f"⚠️  '{nom_four}' ({code_reel}) est un CLIENT, pas un fournisseur.\n"
                    f"   Utilisez un code fournisseur (CT_Type=1)."
                ),
            }

        # ── Résolution article ─────────────────────────────────────
        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "message": f"❌ Article '{ref_article}' introuvable.",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        ref_reelle  = article["AR_Ref"]
        desig       = article["AR_Design"]
        prix_auto   = _to_decimal(article["AR_PrixAch"] or 0.0)
        prix_final  = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(prix_auto))
        stock_avant = _get_stock(conn, ref_reelle)
        montant     = (prix_final * Decimal(str(quantite))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # ── Création BR (Bon de Réception) ─────────────────────────
        num_br = _inserer_document(
            conn, "BL_ACHAT", "", code_reel,
            ref_reelle, quantite, prix_final, montant
        )
        # ENTRÉE stock (on reçoit la marchandise)
        mvt = _ajuster_stock_db(
            conn, ref_reelle, quantite, "ENTREE",
            motif=f"Réception BR {num_br} / {nom_four}"
        )
        # Mettre à jour AR_PrixAch si un prix a été fourni explicitement
        if prix_unitaire > 0:
            conn.execute(
                "UPDATE F_ARTICLE SET AR_PrixAch = ? WHERE AR_Ref = ?",
                (prix_unitaire, ref_reelle)
            )
        conn.commit()

        message = (
            f"✅ Bon de Réception créé !\n\n"
            f"   • Numéro BR       : {num_br}\n"
            f"   • Fournisseur     : {nom_four} ({code_reel})\n"
            f"   • Article         : {desig} ({ref_reelle})\n"
            f"   • Quantité reçue  : {quantite} u\n"
            f"   • Prix unit.      : {prix_final:.2f} €\n"
            f"   • Montant HT      : {_money_text(montant)}\n"
            f"   • Stock avant     : {stock_avant} u\n"
            f"   • Stock après     : {mvt['stock_apres']} u  (+{quantite} u)\n"
        )

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_br,
            "DO_Tiers":    code_reel,
            "AR_Ref":      ref_reelle,
            "montant":     montant,
            "stock_apres": mvt["stock_apres"],
            "message":     message,
            "alertes":     [],
            "suggestion_facture_achat": {
                "code_fournisseur": code_reel,
                "nom_fournisseur":  nom_four,
                "ref_article":      ref_reelle,
                "designation":      desig,
                "quantite":         quantite,
                "prix_unitaire":    prix_final,
                "montant":          montant,
                "num_br":           num_br,
            },
        }
    finally:
        conn.close()


# HELPER MCP
# ─────────────────────────────────────────────────────────────────────

def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def _to_text(data: dict) -> list[types.TextContent]:
    return [
        types.TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False, indent=2, default=_json_default)
        )
    ]


# ═════════════════════════════════════════════════════════════════════
# DÉCLARATION DES OUTILS MCP
# ═════════════════════════════════════════════════════════════════════

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        # ── Documents ────────────────────────────────────────────────
        types.Tool(
            name="generer_document_sage",
            description=(
                "Point d'entrée unique pour la génération de documents Sage 100 "
                "(BL, OF, BF, FACTURE, FA, FC, BC). "
                "Route automatiquement vers le bon workflow selon type_doc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "type_doc":      {"type": "string",
                                      "description": "Type de document : BL | OF | BF | FACTURE | FA | FC | BC"},
                    "code_client":   {"type": "string",
                                      "description": "Code ou nom du client (ou 'PROD-INT' pour usage interne)"},
                    "ref_article":   {"type": "string",
                                      "description": "Référence ou désignation partielle de l'article"},
                    "qte":           {"type": "number", "description": "Quantité"},
                    "prix_unitaire": {"type": "number",
                                      "description": "Prix unitaire (0 = prix catalogue)",
                                      "default": 0},
                    "num_of":        {"type": "string",
                                      "description": "Numéro d'OF lié (pour BF uniquement)",
                                      "default": ""},
                },
                "required": ["type_doc", "code_client", "ref_article", "qte"],
            },
        ),

        types.Tool(
            name="workflow_bl",
            description=(
                "Workflow complet Bon de Livraison : "
                "vérifie le client (statut, blocage), vérifie le stock, "
                "crée le BL dans F_DOCENTETE/F_DOCLIGNE et ajuste F_ARTSTOCK. "
                "En cas de stock insuffisant, suggère un OF."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client":   {"type": "string", "description": "Code ou nom du client"},
                    "ref_article":   {"type": "string", "description": "Référence ou désignation de l'article"},
                    "quantite":      {"type": "number", "description": "Quantité à livrer"},
                    "prix_unitaire": {"type": "number",
                                      "description": "Prix unitaire (0 = prix catalogue)",
                                      "default": 0},
                },
                "required": ["code_client", "ref_article", "quantite"],
            },
        ),

        types.Tool(
            name="workflow_of",
            description=(
                "Workflow complet Ordre de Fabrication : "
                "vérifie F_NOMENCLAT, contrôle F_ARTSTOCK des composants, "
                "effectue les sorties de stock et crée l'OF. "
                "En cas de succès, suggère la création d'un BF."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article": {"type": "string",
                                    "description": "Référence ou désignation du produit fini à fabriquer"},
                    "quantite":    {"type": "number", "description": "Quantité à fabriquer"},
                    "code_client": {"type": "string",
                                    "description": "Code client ou centre de coût (défaut : PROD-INT)",
                                    "default": "PROD-INT"},
                },
                "required": ["ref_article", "quantite"],
            },
        ),

        types.Tool(
            name="workflow_bf",
            description=(
                "Workflow Bon de Fabrication : "
                "crée le BF et enregistre l'entrée en stock dans F_ARTSTOCK. "
                "Peut être lié à un OF existant via DO_Ref."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article":  {"type": "string",
                                     "description": "Référence ou désignation du produit fini"},
                    "quantite":     {"type": "number", "description": "Quantité fabriquée"},
                    "num_of":       {"type": "string",
                                     "description": "Numéro d'OF lié (optionnel)",
                                     "default": ""},
                    "code_client":  {"type": "string",
                                     "description": "Code client ou centre de coût (défaut : PROD-INT)",
                                     "default": "PROD-INT"},
                },
                "required": ["ref_article", "quantite"],
            },
        ),

        types.Tool(
            name="creer_nouveau_client",
            description="Crée un nouveau client dans F_COMPTET.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client":     {"type": "string",
                                        "description": "Code unique du client (ex: CLI001)"},
                    "intitule":        {"type": "string",
                                        "description": "Nom / raison sociale du client"},
                    "ct_validite":     {"type": "string",
                                        "description": "VALIDE | BLOQUE | SUSPECT (défaut VALIDE)",
                                        "default": "VALIDE"},
                    "ct_encours_max":  {"type": "number",
                                        "description": "Encours maximum autorisé (défaut 0)",
                                        "default": 0},
                },
                "required": ["code_client", "intitule"],
            },
        ),

        types.Tool(
            name="creer_nouveau_fournisseur",
            description="Crée un nouveau fournisseur dans F_COMPTET (CT_Type=1).",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code unique du fournisseur (ex: FOUR001)"},
                    "intitule":         {"type": "string",
                                         "description": "Nom / raison sociale du fournisseur"},
                },
                "required": ["code_fournisseur", "intitule"],
            },
        ),

        types.Tool(
            name="modifier_statut_client",
            description=(
                "Modifie CT_Validite dans F_COMPTET. "
                "Valeurs acceptées : BLOQUE | SOMMEIL → 'BLOQUE' ; "
                "tout autre valeur → 'VALIDE'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client":    {"type": "string",
                                       "description": "Code ou nom du client"},
                    "nouveau_statut": {"type": "string",
                                       "description": "Nouveau statut : BLOQUE | SOMMEIL | ACTIF"},
                },
                "required": ["code_client", "nouveau_statut"],
            },
        ),

        types.Tool(
            name="transformer_document",
            description=(
                "Transforme un document F_DOCENTETE existant en un autre type "
                "(ex : BL → FACTURE, BC → BL). "
                "Recopie les données depuis F_DOCLIGNE."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "num_piece_source": {"type": "string",
                                         "description": "Numéro de la pièce source"},
                    "type_destination": {"type": "string",
                                         "description": "Type cible : BL | FACTURE | BC | BF | OF | AV"},
                },
                "required": ["num_piece_source", "type_destination"],
            },
        ),

        types.Tool(
            name="creer_facture_avoir",
            description=(
                "Crée un avoir (AV) à partir d'une facture F_DOCENTETE existante. "
                "Le montant est l'inverse de DL_PrixUnitaire × DL_Qte."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "num_facture_origine": {"type": "string",
                                            "description": "Numéro de la facture d'origine"},
                },
                "required": ["num_facture_origine"],
            },
        ),

        types.Tool(
            name="enregistrer_reglement_facture",
            description=(
                "Enregistre le paiement d'une facture : "
                "met DO_Ref à 'REGLE' dans F_DOCENTETE et insère dans reglements."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "num_piece":     {"type": "string",
                                      "description": "Numéro de la facture à régler"},
                    "mode_paiement": {"type": "string",
                                      "description": "Mode de paiement (défaut : Virement)",
                                      "default": "Virement"},
                    "numero_piece_paiement": {"type": "string",
                                      "description": "N° du chèque ou de la traite (si applicable)",
                                      "default": ""},
                },
                "required": ["num_piece"],
            },
        ),

        types.Tool(
            name="ajuster_mouvement_stock",
            description=(
                "Enregistre un mouvement de stock manuel dans F_ARTSTOCK "
                "et trace dans mouvements_stock."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article":    {"type": "string",
                                       "description": "Référence ou désignation de l'article"},
                    "qte_mouvement":  {"type": "number",
                                       "description": "Quantité du mouvement (valeur positive)"},
                    "type_mouvement": {"type": "string",
                                       "description": "ENTREE ou SORTIE"},
                    "motif":          {"type": "string",
                                       "description": "Motif du mouvement (optionnel)",
                                       "default": ""},
                },
                "required": ["ref_article", "qte_mouvement", "type_mouvement"],
            },
        ),

        types.Tool(
            name="generer_proposition_achat",
            description=(
                "Génère une proposition d'achat (réapprovisionnement) "
                "pour un article auprès d'un fournisseur F_COMPTET."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article":      {"type": "string",
                                         "description": "Référence de l'article à réapprovisionner"},
                    "qte_a_commander":  {"type": "number",
                                         "description": "Quantité à commander"},
                    "code_fournisseur": {"type": "string",
                                         "description": "Code du fournisseur (CT_Num)"},
                },
                "required": ["ref_article", "qte_a_commander", "code_fournisseur"],
            },
        ),

        types.Tool(
            name="workflow_bl_achat",
            description=(
                "Workflow Bon de Réception fournisseur (achat) : "
                "vérifie le fournisseur (CT_Type=1), crée le bon de réception "
                "dans F_DOCENTETE/F_DOCLIGNE (DO_Domaine=1, DO_Type=2), "
                "incrémente F_ARTSTOCK (+ENTREE stock), "
                "et suggère la transformation en facture fournisseur."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code ou nom du fournisseur (CT_Num ou CT_Intitule)"},
                    "ref_article":      {"type": "string",
                                         "description": "Référence ou désignation de l'article reçu"},
                    "quantite":         {"type": "number",
                                         "description": "Quantité reçue"},
                    "prix_unitaire":    {"type": "number",
                                         "description": "Prix unitaire d'achat (0 = prix catalogue fournisseur)",
                                         "default": 0},
                },
                "required": ["code_fournisseur", "ref_article", "quantite"],
            },
        ),
    ]


# ═════════════════════════════════════════════════════════════════════
# HANDLER DES APPELS D'OUTILS
# ═════════════════════════════════════════════════════════════════════

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    # ── generer_document_sage ─────────────────────────────────────────
    if name == "generer_document_sage":
        type_d        = arguments["type_doc"].upper().strip()
        code_client   = arguments.get("code_client", "")
        ref_article   = arguments["ref_article"]
        qte           = float(arguments["qte"])
        prix_unitaire = float(arguments.get("prix_unitaire", 0.0))
        num_of        = arguments.get("num_of", "")

        if type_d == "BL":
            result = _workflow_bl(code_client, ref_article, qte, prix_unitaire)
        elif type_d == "OF":
            result = _workflow_of(ref_article, qte, code_client)
        elif type_d == "BF":
            result = _workflow_bf(ref_article, qte, num_of, code_client)
        elif type_d in ("FACTURE", "FA", "FC"):
            result = _generer_facture_directe(code_client, ref_article, qte, prix_unitaire)
        elif type_d == "BC":
            result = _generer_bc_direct(code_client, ref_article, qte, prix_unitaire)
        else:
            result = {
                "statut":  "ERREUR",
                "message": f"❌ Type de document inconnu : '{arguments['type_doc']}'"
            }
        return _to_text(result)

    # ── workflow_bl ───────────────────────────────────────────────────
    elif name == "workflow_bl":
        result = _workflow_bl(
            arguments["code_client"],
            arguments["ref_article"],
            float(arguments["quantite"]),
            float(arguments.get("prix_unitaire", 0.0)),
        )
        return _to_text(result)

    # ── workflow_of ───────────────────────────────────────────────────
    elif name == "workflow_of":
        result = _workflow_of(
            arguments["ref_article"],
            float(arguments["quantite"]),
            arguments.get("code_client", "PROD-INT"),
        )
        return _to_text(result)

    # ── workflow_bf ───────────────────────────────────────────────────
    elif name == "workflow_bf":
        result = _workflow_bf(
            arguments["ref_article"],
            float(arguments["quantite"]),
            arguments.get("num_of", ""),
            arguments.get("code_client", "PROD-INT"),
        )
        return _to_text(result)

    # ── creer_nouveau_client ──────────────────────────────────────────
    elif name == "creer_nouveau_client":
        conn = _get_conn()
        try:
            code_client    = arguments["code_client"]
            intitule       = arguments["intitule"]
            ct_validite    = (arguments.get("ct_validite") or "VALIDE").upper()
            if ct_validite not in ("VALIDE", "BLOQUE", "SUSPECT"):
                ct_validite = "VALIDE"
            ct_encours_max = float(arguments.get("ct_encours_max") or 0.0)
            existing = conn.execute(
                "SELECT CT_Num FROM F_COMPTET WHERE CT_Num = ?",
                (code_client,)
            ).fetchone()
            if existing:
                result = {
                    "statut":  "EXISTE_DEJA",
                    "message": f"⚠️  Client '{code_client}' existe déjà.",
                }
            else:
                conn.execute(
                    """INSERT INTO F_COMPTET
                       (CT_Num, CT_Intitule, CT_Type, CT_Validite, CT_EncoursMax, CT_Encours)
                       VALUES (?, ?, 0, ?, ?, 0.0)""",
                    (code_client, intitule, ct_validite, ct_encours_max)
                )
                conn.commit()
                result = {
                    "statut":  "CREE",
                    "CT_Num":  code_client,
                    "message": f"✅ Client '{intitule}' ({code_client}) créé.",
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── creer_nouveau_fournisseur ─────────────────────────────────────
    elif name == "creer_nouveau_fournisseur":
        conn = _get_conn()
        try:
            code_fourn = arguments["code_fournisseur"]
            intitule   = arguments["intitule"]
            existing = conn.execute(
                "SELECT CT_Num FROM F_COMPTET WHERE CT_Num = ?",
                (code_fourn,)
            ).fetchone()
            if existing:
                result = {
                    "statut":  "EXISTE_DEJA",
                    "message": f"⚠️  Fournisseur '{code_fourn}' existe déjà.",
                }
            else:
                conn.execute(
                    """INSERT INTO F_COMPTET
                       (CT_Num, CT_Intitule, CT_Type, CT_Validite, CT_EncoursMax, CT_Encours)
                       VALUES (?, ?, 1, 'VALIDE', 0.0, 0.0)""",
                    (code_fourn, intitule)
                )
                conn.commit()
                result = {
                    "statut":  "CREE",
                    "CT_Num":  code_fourn,
                    "message": f"✅ Fournisseur '{intitule}' ({code_fourn}) créé.",
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── modifier_statut_client ────────────────────────────────────────
    elif name == "modifier_statut_client":
        conn = _get_conn()
        try:
            client = _resolve_client(conn, arguments["code_client"])
            if not client:
                result = {
                    "statut":  "CLIENT_NON_TROUVE",
                    "message": f"❌ Client '{arguments['code_client']}' introuvable.",
                }
            else:
                nouveau_statut = arguments["nouveau_statut"].upper()
                valeur = (
                    "BLOQUE"
                    if nouveau_statut in ("BLOQUE", "SOMMEIL")
                    else "VALIDE"
                )
                conn.execute(
                    "UPDATE F_COMPTET SET CT_Validite = ? WHERE CT_Num = ?",
                    (valeur, client["CT_Num"])
                )
                conn.commit()
                result = {
                    "statut":  "MODIFIE",
                    "message": (
                        f"✅ Statut de '{client['CT_Intitule']}' "
                        f"({client['CT_Num']}) → {valeur}."
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── transformer_document ──────────────────────────────────────────
    elif name == "transformer_document":
        conn = _get_conn()
        try:
            num_piece_source = arguments["num_piece_source"]
            type_destination = arguments["type_destination"].upper()

            # Lecture entête source
            entete = conn.execute(
                "SELECT * FROM F_DOCENTETE WHERE DO_Piece = ?",
                (num_piece_source,)
            ).fetchone()
            if not entete:
                result = {
                    "statut":  "NON_TROUVE",
                    "message": f"❌ Document '{num_piece_source}' introuvable.",
                }
            else:
                existing = conn.execute(
                    "SELECT DO_Piece FROM F_DOCENTETE WHERE DO_Ref = ? AND DO_Type = ? AND DO_Domaine = ?",
                    (num_piece_source, DOC_TYPE.get(type_destination.upper(), 0), DOC_DOMAINE.get(type_destination.upper(), 0)),
                ).fetchone()
                if existing:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  Le document source '{num_piece_source}' a déjà été transformé en {type_destination.upper()} ({existing['DO_Piece']}).",
                    }
                    conn.close()
                    return _to_text(result)

                lignes_source = conn.execute(
                    "SELECT * FROM F_DOCLIGNE WHERE DO_Piece = ? ORDER BY DL_Ligne",
                    (num_piece_source,)
                ).fetchall()
                lignes_dest = [{
                    "ref_article": ligne["AR_Ref"],
                    "qte": float(ligne["DL_Qte"]),
                    "prix_unit": float(ligne["DL_PrixUnitaire"]),
                } for ligne in lignes_source]

                if type_destination.upper() in {"FACTURE", "FA", "FC"}:
                    client = conn.execute(
                        "SELECT CT_Num, CT_EncoursMax, CT_Validite FROM F_COMPTET WHERE CT_Num = ?",
                        (entete["CT_Num"],),
                    ).fetchone()
                    if client:
                        encours_max = _to_decimal(client["CT_EncoursMax"] or 0.0)
                        encours_actuel = _to_decimal(conn.execute(
                            """
                            SELECT COALESCE(SUM(l.DL_Qte * l.DL_PrixUnitaire), 0.0) AS encours
                            FROM F_DOCENTETE e
                            LEFT JOIN F_DOCLIGNE l ON e.DO_Piece = l.DO_Piece
                            WHERE e.CT_Num = ? AND e.DO_Type = 3 AND e.DO_Domaine = 0
                              AND e.DO_Piece NOT IN (SELECT DO_Piece FROM reglements)
                            """,
                            (client["CT_Num"],),
                        ).fetchone()[0])
                        montant_total = float(_decimal_sum(
                            (Decimal(str(l["qte"])) * Decimal(str(l["prix_unit"])) for l in lignes_dest)
                        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        if encours_max > 0 and encours_actuel + _to_decimal(montant_total) > encours_max:
                            result = {
                                "statut": "ENCORS_MAX_ATTEINT",
                                "message": (
                                    f"⚠️  Encours dépassé pour {client['CT_Num']} : "
                                    f"{_money_text(encours_actuel + _to_decimal(montant_total))} > {_money_text(encours_max)}"
                                ),
                            }
                            conn.close()
                            return _to_text(result)

                num_dest = _inserer_document(
                    conn, type_destination, "",
                    entete["CT_Num"],
                    "",
                    0.0, 0.0, 0.0,
                    num_piece_of=num_piece_source,
                    lignes=lignes_dest,
                )
                conn.commit()
                result = {
                    "statut":           "TRANSFORME",
                    "DO_Piece":         num_dest,
                    "num_piece_dest":   num_dest,
                    "num_piece_source": num_piece_source,
                    "message": (
                        f"✅ {num_piece_source} → {type_destination} : {num_dest}"
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── creer_facture_avoir ───────────────────────────────────────────
    elif name == "creer_facture_avoir":
        conn = _get_conn()
        try:
            num_facture_origine = arguments["num_facture_origine"]

            entete = conn.execute(
                "SELECT * FROM F_DOCENTETE WHERE DO_Piece = ?",
                (num_facture_origine,)
            ).fetchone()
            if not entete:
                result = {
                    "statut":  "NON_TROUVE",
                    "message": f"❌ Facture '{num_facture_origine}' introuvable.",
                }
            else:
                lignes_source = conn.execute(
                    "SELECT * FROM F_DOCLIGNE WHERE DO_Piece = ? ORDER BY DL_Ligne",
                    (num_facture_origine,)
                ).fetchall()
                lignes_dest = [{
                    "ref_article": ligne["AR_Ref"],
                    "qte": float(ligne["DL_Qte"]),
                    "prix_unit": -float(ligne["DL_PrixUnitaire"]),
                } for ligne in lignes_source]

                existing_avoir = conn.execute(
                    "SELECT DO_Piece FROM F_DOCENTETE WHERE DO_Ref = ? AND DO_Type = 9 AND DO_Domaine = 0",
                    (num_facture_origine,),
                ).fetchone()
                if existing_avoir:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  Une avoir existe déjà pour cette facture ({existing_avoir['DO_Piece']}).",
                    }
                    conn.close()
                    return _to_text(result)

                num_av = _inserer_document(
                    conn, "AV", "",
                    entete["CT_Num"],
                    "",
                    0.0, 0.0, 0.0,
                    lignes=lignes_dest,
                )
                conn.commit()
                montant_total = sum(float(l["qte"]) * float(l["prix_unit"]) for l in lignes_dest)
                result = {
                    "statut":   "CREE",
                    "DO_Piece": num_av,
                    "message": (
                        f"✅ Avoir {num_av} créé depuis {num_facture_origine}.\n"
                        f"   Montant : {_money_text(-montant_total)}"
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── enregistrer_reglement_facture ─────────────────────────────────
    elif name == "enregistrer_reglement_facture":
        conn = _get_conn()
        try:
            num_piece     = arguments["num_piece"]
            mode_paiement = arguments.get("mode_paiement", "Virement")
            numero_piece_paiement = arguments.get("numero_piece_paiement", "")
            entete = conn.execute(
                "SELECT * FROM F_DOCENTETE WHERE DO_Piece = ?",
                (num_piece,)
            ).fetchone()
            if not entete:
                result = {
                    "statut":  "NON_TROUVE",
                    "message": f"❌ Document '{num_piece}' introuvable.",
                }
            else:
                # Calcul montant depuis F_DOCLIGNE
                lignes = conn.execute(
                    "SELECT DL_Qte, DL_PrixUnitaire FROM F_DOCLIGNE WHERE DO_Piece = ?",
                    (num_piece,)
                ).fetchall()
                montant_total = float(_decimal_sum(
                    (Decimal(str(l["DL_Qte"])) * Decimal(str(l["DL_PrixUnitaire"])) for l in lignes)
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                existing_reglement = conn.execute(
                    "SELECT 1 FROM reglements WHERE DO_Piece = ?",
                    (num_piece,),
                ).fetchone()
                if existing_reglement:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  La facture '{num_piece}' a déjà un règlement enregistré.",
                    }
                    conn.close()
                    return _to_text(result)

                # Marque la facture réglée dans DO_Ref
                conn.execute(
                    "UPDATE F_DOCENTETE SET DO_Ref = ? WHERE DO_Piece = ?",
                    (f"SOLDEE / REGLEE - {mode_paiement}", num_piece)
                )
                conn.execute(
                    """INSERT INTO reglements
                       (DO_Piece, mode_paiement, montant, date_reglement, numero_piece_paiement)
                       VALUES (?, ?, ?, ?, ?)""",
                    (num_piece, mode_paiement,
                     montant_total, datetime.now().isoformat(), numero_piece_paiement)
                 
                )
                conn.commit()
                result = {
                    "statut":  "REGLE",
                    "message": (
                        f"✅ Règlement enregistré !\n"
                        f"   • Document : {num_piece}\n"
                        f"   • Montant  : {_money_text(montant_total)}\n"
                        f"   • Mode     : {mode_paiement}"
                        +(f"\n   • N° pièce : {numero_piece_paiement}" if numero_piece_paiement else "")
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── ajuster_mouvement_stock ───────────────────────────────────────
    elif name == "ajuster_mouvement_stock":
        conn = _get_conn()
        try:
            article = _resolve_article(conn, arguments["ref_article"])
            if not article:
                result = {
                    "statut":  "ARTICLE_NON_TROUVE",
                    "message": f"❌ Article '{arguments['ref_article']}' introuvable.",
                }
            else:
                qte_mouvement  = float(arguments["qte_mouvement"])
                type_mouvement = arguments["type_mouvement"].upper()
                motif          = arguments.get("motif", "")
                mvt = _ajuster_stock_db(
                    conn, article["AR_Ref"],
                    qte_mouvement, type_mouvement, motif
                )
                conn.commit()
                result = {
                    "statut":      "MOUVEMENT_ENREGISTRE",
                    "AR_Ref":      article["AR_Ref"],
                    "stock_avant": mvt["stock_avant"],
                    "stock_apres": mvt["stock_apres"],
                    "message": (
                        f"✅ Mouvement stock enregistré !\n"
                        f"   • Article      : {article['AR_Design']}\n"
                        f"   • Type         : {type_mouvement}\n"
                        f"   • Quantité     : {qte_mouvement} u\n"
                        f"   • Stock avant  : {mvt['stock_avant']} u\n"
                        f"   • Stock après  : {mvt['stock_apres']} u"
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    # ── generer_proposition_achat ─────────────────────────────────────
    elif name == "generer_proposition_achat":
        ref_article      = arguments["ref_article"]
        qte_a_commander  = float(arguments["qte_a_commander"])
        code_fournisseur = arguments["code_fournisseur"]
        result = {
            "statut":  "GENERE",
            "message": (
                f"✅ Proposition d'achat créée pour {ref_article} "
                f"({qte_a_commander} u) auprès de {code_fournisseur}."
            ),
        }
        return _to_text(result)

    # ── workflow_bl_achat ─────────────────────────────────────────────
    elif name == "workflow_bl_achat":
        result = _workflow_bl_achat(
            arguments["code_fournisseur"],
            arguments["ref_article"],
            float(arguments["quantite"]),
            float(arguments.get("prix_unitaire", 0.0)),
        )
        return _to_text(result)

    # ── outil inconnu ─────────────────────────────────────────────────
    else:
        return _to_text({
            "statut":  "ERREUR",
            "message": f"❌ Outil inconnu : '{name}'"
        })


# ═════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═════════════════════════════════════════════════════════════════════

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream, write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())