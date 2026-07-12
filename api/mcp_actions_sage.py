#!/usr/bin/env python3
"""
mcp_sage.py — Serveur MCP Actions Sage 100 v4.1
================================================
Expose toutes les fonctions de actions_sage.py comme outils MCP.

v4.1 : tous les noms de TABLE et de COLONNE physiques proviennent
désormais de `adaptation/db_adapter.py` (lui-même alimenté par
`adaptation/db_config.json`). Pour brancher une autre base (vraie base
Sage, autre ERP...), il suffit de modifier db_config.json — ce fichier
n'a plus aucun nom de colonne en dur.
"""
import re
import json
import logging
import sqlite3
import os
import sys
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
from uuid import uuid4

# Add parent directory to path for database import
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.schema_sage import (
    DOC_CODES, DOC_DOMAINE, DOC_PREFIXES, DOC_TYPE, DOC_DESTOCKANTS, DOC_STOCKANTS,
    CURRENCY_SYMBOL,
)

# ── Mapping schéma DB centralisé (table/colonnes physiques) ───────────
import adaptation.db_adapter as sch

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

DB_PATH = os.getenv("DB_PATH", str(Path(__file__).parent.parent / "entreprise_mock.db"))

app = Server("sage100-mcp")

# ─────────────────────────────────────────────────────────────────────
# NOMS PHYSIQUES — tous résolus via adaptation.db_adapter (db_config.json)
# ─────────────────────────────────────────────────────────────────────

T_TIERS = sch.T_TIERS              # F_COMPTET
T_ARTICLE = sch.T_ARTICLE          # F_ARTICLE
T_STOCK = sch.T_STOCK              # F_ARTSTOCK
T_DOC_ENTETE = sch.T_DOC_ENTETE    # F_DOCENTETE
T_DOC_LIGNE = sch.T_DOC_LIGNE      # F_DOCLIGNE
T_MVT_STOCK = sch.T_MVT_STOCK      # mouvements_stock
T_REGLEMENTS = sch.T_REGLEMENTS    # reglements

C_CT_NUM = sch.C_CT_NUM
C_CT_INTITULE = sch.C_CT_INTITULE
C_CT_TYPE = sch.C_CT_TYPE
C_CT_VALIDITE = sch.C_CT_VALIDITE
C_CT_ENCOURS_MAX = sch.C_CT_ENCOURS_MAX
C_CT_ENCOURS = sch.C_CT_ENCOURS

C_AR_REF = sch.C_AR_REF
C_AR_DESIGN = sch.C_AR_DESIGN
C_AR_PRIXACH = sch.C_AR_PRIXACH
C_AR_PRIXVEN = sch.C_AR_PRIXVEN
C_AR_TYPE = sch.C_AR_TYPE

C_AS_REF = sch.C_AS_REF
C_AS_QTESTO = sch.C_AS_QTESTO
C_AS_QTECOM = sch.C_AS_QTECOM

C_DO_PIECE = sch.C_DO_PIECE
C_DO_DOMAINE = sch.C_DO_DOMAINE
C_DO_TYPE = sch.C_DO_TYPE
C_DO_DATE = sch.C_DO_DATE
C_DO_REF = sch.C_DO_REF
C_DO_TIERS = sch.C_DO_TIERS

C_DL_PIECE = sch.C_DL_PIECE
C_DL_REF = sch.C_DL_REF
C_DL_QTE = sch.C_DL_QTE
C_DL_PRIX = sch.C_DL_PRIX
C_DL_LIGNE = sch.C_DL_LIGNE

# F_NOMENCLAT est bien référencé dans db_config.json (table "nomenclature") :
# ces 4 constantes viennent donc, elles aussi, du mapping centralisé.
NOMENCLAT_TABLE = sch.T_NOMENCLAT
NOMENCLAT_REF_PF = sch.C_NO_REF_PF
NOMENCLAT_REF_MP = sch.C_NO_REF_MP
NOMENCLAT_QTE = sch.C_NO_QTE

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
    # Création des tables annexes manquantes au premier accès.
    # Ces tables sont internes à l'application (pas des tables Sage) :
    # seul leur NOM provient du mapping, leurs colonnes sont fixes.
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {T_MVT_STOCK} (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            {C_AR_REF}     TEXT,
            type_mouvement TEXT,
            qte            REAL,
            motif          TEXT,
            date_mouvement TEXT
        )
    """)
    conn.execute(f"""
    CREATE TABLE IF NOT EXISTS {T_REGLEMENTS} (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        {C_DO_PIECE}   TEXT,
        mode_paiement  TEXT,
        montant        REAL,
        date_reglement TEXT,
        numero_piece_paiement TEXT
    )
""")
    try:
        conn.execute(f"ALTER TABLE {T_REGLEMENTS} ADD COLUMN numero_piece_paiement TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn


def _generer_num_piece(type_doc: str, conn: Optional[sqlite3.Connection] = None) -> str:
    prefix = DOC_PREFIXES.get(type_doc.upper(), type_doc[:2].upper())
    ts = datetime.now().strftime("%y%m%d%H%M%S%f")
    suffix = uuid4().hex[:8].upper()
    return f"{prefix}{ts}{suffix}"


def _resolve_client(conn: sqlite3.Connection, code_ou_nom: str) -> Optional[dict]:
    """
    Recherche dans la table tiers par code exact, puis par nom partiel.
    """
    if not code_ou_nom:
        return None
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE {C_CT_NUM} = ? COLLATE NOCASE",
        (code_ou_nom,)
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE {C_CT_INTITULE} LIKE ? COLLATE NOCASE",
        (f"%{code_ou_nom}%",)
    ).fetchone()
    return dict(row) if row else None


def _lire_client(conn: sqlite3.Connection, code_ou_nom: str) -> dict:
    """Lit les détails d'un client depuis la table tiers."""
    row = _resolve_client(conn, code_ou_nom)
    if not row:
        return {"statut": "ERREUR", "message": f"Client '{code_ou_nom}' non trouvé"}
    if row.get(C_CT_TYPE) != 0:
        return {"statut": "ERREUR", "message": f"'{code_ou_nom}' n'est pas un client"}
    return {
        "statut": "SUCCES",
        "CT_Num": row[C_CT_NUM],
        "CT_Intitule": row[C_CT_INTITULE],
        "CT_Validite": row.get(C_CT_VALIDITE, "VALIDE"),
        "CT_EncoursMax": row.get(C_CT_ENCOURS_MAX, 0),
        "CT_Encours": row.get(C_CT_ENCOURS, 0),
    }


def _lire_fournisseur(conn: sqlite3.Connection, code_ou_nom: str) -> dict:
    """Lit les détails d'un fournisseur depuis la table tiers."""
    row = _resolve_client(conn, code_ou_nom)
    if not row:
        return {"statut": "ERREUR", "message": f"Fournisseur '{code_ou_nom}' non trouvé"}
    if row.get(C_CT_TYPE) != 1:
        return {"statut": "ERREUR", "message": f"'{code_ou_nom}' n'est pas un fournisseur"}
    return {
        "statut": "SUCCES",
        "CT_Num": row[C_CT_NUM],
        "CT_Intitule": row[C_CT_INTITULE],
        "CT_Validite": row.get(C_CT_VALIDITE, "VALIDE"),
        "CT_EncoursMax": row.get(C_CT_ENCOURS_MAX, 0),
        "CT_Encours": row.get(C_CT_ENCOURS, 0),
    }


def _lire_article(conn: sqlite3.Connection, ref_ou_design: str) -> dict:
    """Lit les détails d'un article depuis la table articles et la table stock."""
    row = _resolve_article(conn, ref_ou_design)
    if not row:
        return {"statut": "ERREUR", "message": f"Article '{ref_ou_design}' non trouvé"}

    stock_row = conn.execute(
        f"SELECT * FROM {T_STOCK} WHERE {C_AS_REF} = ? COLLATE NOCASE",
        (row[C_AR_REF],)
    ).fetchone()

    return {
        "statut": "SUCCES",
        "AR_Ref": row[C_AR_REF],
        "AR_Design": row[C_AR_DESIGN],
        "AR_PrixAch": row.get(C_AR_PRIXACH, 0),
        "AR_PrixVen": row.get(C_AR_PRIXVEN, 0),
        "AR_Type": row.get(C_AR_TYPE, 0),
        "AS_QteSto": stock_row[C_AS_QTESTO] if stock_row else 0,
        "AS_QteCom": stock_row[C_AS_QTECOM] if stock_row else 0,
    }


def _modifier_client(conn: sqlite3.Connection, code_client: str, **kwargs) -> dict:
    """Modifie les champs d'un client dans la table tiers (sauf code)."""
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE {C_CT_NUM} = ? COLLATE NOCASE",
        (code_client,)
    ).fetchone()
    if not row:
        return {"statut": "ERREUR", "message": f"Client '{code_client}' non trouvé"}
    if row[C_CT_TYPE] != 0:
        return {"statut": "ERREUR", "message": f"'{code_client}' n'est pas un client"}

    updates = []
    params = []
    if "intitule" in kwargs:
        updates.append(f"{C_CT_INTITULE} = ?")
        params.append(kwargs["intitule"])
    if "validite" in kwargs:
        updates.append(f"{C_CT_VALIDITE} = ?")
        params.append(kwargs["validite"])
    if "encours_max" in kwargs:
        updates.append(f"{C_CT_ENCOURS_MAX} = ?")
        params.append(kwargs["encours_max"])

    if not updates:
        return {"statut": "ERREUR", "message": "Aucun champ à modifier"}

    params.append(code_client)
    conn.execute(
        f"UPDATE {T_TIERS} SET {', '.join(updates)} WHERE {C_CT_NUM} = ? COLLATE NOCASE",
        params
    )
    conn.commit()
    return {"statut": "SUCCES", "message": f"Client '{code_client}' modifié avec succès"}


def _modifier_fournisseur(conn: sqlite3.Connection, code_fournisseur: str, **kwargs) -> dict:
    """Modifie les champs d'un fournisseur dans la table tiers (sauf code)."""
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE {C_CT_NUM} = ? COLLATE NOCASE",
        (code_fournisseur,)
    ).fetchone()
    if not row:
        return {"statut": "ERREUR", "message": f"Fournisseur '{code_fournisseur}' non trouvé"}
    if row[C_CT_TYPE] != 1:
        return {"statut": "ERREUR", "message": f"'{code_fournisseur}' n'est pas un fournisseur"}

    updates = []
    params = []
    if "intitule" in kwargs:
        updates.append(f"{C_CT_INTITULE} = ?")
        params.append(kwargs["intitule"])
    if "validite" in kwargs:
        updates.append(f"{C_CT_VALIDITE} = ?")
        params.append(kwargs["validite"])
    if "encours_max" in kwargs:
        updates.append(f"{C_CT_ENCOURS_MAX} = ?")
        params.append(kwargs["encours_max"])

    if not updates:
        return {"statut": "ERREUR", "message": "Aucun champ à modifier"}

    params.append(code_fournisseur)
    conn.execute(
        f"UPDATE {T_TIERS} SET {', '.join(updates)} WHERE {C_CT_NUM} = ? COLLATE NOCASE",
        params
    )
    conn.commit()
    return {"statut": "SUCCES", "message": f"Fournisseur '{code_fournisseur}' modifié avec succès"}


def _modifier_article(conn: sqlite3.Connection, ref_article: str, **kwargs) -> dict:
    """Modifie les champs d'un article dans la table articles (sauf ref)."""
    row = conn.execute(
        f"SELECT * FROM {T_ARTICLE} WHERE {C_AR_REF} = ? COLLATE NOCASE",
        (ref_article,)
    ).fetchone()
    if not row:
        return {"statut": "ERREUR", "message": f"Article '{ref_article}' non trouvé"}

    updates = []
    params = []
    if "designation" in kwargs:
        updates.append(f"{C_AR_DESIGN} = ?")
        params.append(kwargs["designation"])
    if "prix_achat" in kwargs:
        updates.append(f"{C_AR_PRIXACH} = ?")
        params.append(kwargs["prix_achat"])
    if "prix_vente" in kwargs:
        updates.append(f"{C_AR_PRIXVEN} = ?")
        params.append(kwargs["prix_vente"])
    if "type_article" in kwargs:
        updates.append(f"{C_AR_TYPE} = ?")
        params.append(kwargs["type_article"])

    if not updates:
        return {"statut": "ERREUR", "message": "Aucun champ à modifier"}

    params.append(ref_article)
    conn.execute(
        f"UPDATE {T_ARTICLE} SET {', '.join(updates)} WHERE {C_AR_REF} = ? COLLATE NOCASE",
        params
    )
    conn.commit()
    return {"statut": "SUCCES", "message": f"Article '{ref_article}' modifié avec succès"}


def _resolve_article(conn: sqlite3.Connection, ref_ou_nom: str) -> Optional[dict]:
    """
    Recherche dans la table articles par référence exacte, puis par
    désignation partielle.
    """
    if not ref_ou_nom:
        return None
    row = conn.execute(
        f"SELECT * FROM {T_ARTICLE} WHERE {C_AR_REF} = ? COLLATE NOCASE",
        (ref_ou_nom,)
    ).fetchone()
    if row:
        return dict(row)
    rows = conn.execute(
        f"SELECT * FROM {T_ARTICLE} WHERE {C_AR_DESIGN} LIKE ? COLLATE NOCASE LIMIT 5",
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
    """Lit la quantité en stock dans la table stock."""
    row = conn.execute(
        f"SELECT {C_AS_QTESTO} FROM {T_STOCK} WHERE {C_AS_REF} = ? COLLATE NOCASE",
        (ref_article,)
    ).fetchone()
    return float(row[C_AS_QTESTO]) if row else 0.0


def _ajuster_stock_db(
    conn: sqlite3.Connection,
    ref_article: str,
    qte: float,
    type_mouvement: str,
    motif: str = "",
) -> dict:
    """
    Met à jour la table stock et trace dans mouvements_stock.
    Utilise une mise à jour atomique et refuse les sorties qui feraient
    passer le stock sous zéro.
    """
    if qte < 0:
        raise ValueError("La quantité de mouvement ne peut pas être négative")

    type_mouvement = type_mouvement.upper()
    row = conn.execute(
        f"SELECT {C_AS_QTESTO} FROM {T_STOCK} WHERE {C_AS_REF} = ? COLLATE NOCASE",
        (ref_article,)
    ).fetchone()
    stock_avant = float(row[C_AS_QTESTO]) if row else 0.0
    if type_mouvement == "SORTIE" and stock_avant < qte:
        raise ValueError(f"Stock insuffisant pour {ref_article}: {stock_avant} < {qte}")

    nouveau_stock = stock_avant - qte if type_mouvement == "SORTIE" else stock_avant + qte
    conn.execute(
        f"UPDATE {T_STOCK} SET {C_AS_QTESTO} = ? WHERE {C_AS_REF} = ? COLLATE NOCASE",
        (nouveau_stock, ref_article)
    )
    conn.execute(
        f"""INSERT INTO {T_MVT_STOCK}
           ({C_AR_REF}, type_mouvement, qte, motif, date_mouvement)
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

def _calculer_encours_client(conn: sqlite3.Connection, code_client: str) -> Decimal:
    """Encours réel du client = somme des factures de vente non réglées.
    Factorise la logique déjà dupliquée dans _workflow_bl / _generer_facture_directe / transformer_document."""
    return _to_decimal(conn.execute(
        f"""
        SELECT COALESCE(SUM(l.{C_DL_QTE} * l.{C_DL_PRIX}), 0.0)
        FROM {T_DOC_ENTETE} e
        LEFT JOIN {T_DOC_LIGNE} l ON e.{C_DO_PIECE} = l.{C_DL_PIECE}
        WHERE e.{C_DO_TIERS} = ? AND e.{C_DO_TYPE} = 3 AND e.{C_DO_DOMAINE} = 0
          AND e.{C_DO_PIECE} NOT IN (SELECT {C_DO_PIECE} FROM {T_REGLEMENTS})
        """,
        (code_client,),
    ).fetchone()[0])


def _generer_prochain_code(conn: sqlite3.Connection, prefixe: str) -> str:
    """Génère le prochain code séquentiel disponible pour un préfixe (CLI, FOUR...)."""
    prefixe = (prefixe or "CLI").upper()
    rows = conn.execute(
        f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} LIKE ?",
        (f"{prefixe}%",),
    ).fetchall()
    nums = []
    for (code,) in rows:
        m = re.match(rf"^{re.escape(prefixe)}(\d+)$", code or "", re.IGNORECASE)
        if m:
            nums.append(int(m.group(1)))
    prochain = (max(nums) + 1) if nums else 1
    return f"{prefixe}{prochain:03d}"


def _verifier_encours_client(code_client: str, montant_supplementaire: float) -> dict:
    conn = _get_conn()
    try:
        client = _resolve_client(conn, code_client)
        if not client:
            return {"statut": "CLIENT_NON_TROUVE", "message": f"Client '{code_client}' introuvable."}
        encours_max = _to_decimal(client.get(C_CT_ENCOURS_MAX) or 0.0)
        if encours_max <= 0:
            return {"statut": "OK", "depasse": False, "encours_actuel": 0.0,
                     "encours_max": 0.0, "encours_projete": 0.0}
        encours_actuel = _calculer_encours_client(conn, client[C_CT_NUM])
        montant = _to_decimal(montant_supplementaire)
        encours_projete = encours_actuel + montant
        depasse = encours_projete > encours_max
        return {
            "statut": "OK",
            "depasse": depasse,
            "encours_actuel": float(encours_actuel),
            "encours_max": float(encours_max),
            "encours_projete": float(encours_projete),
            "message": (
                f"🚫 Encours client dépassé : ce document porterait l'encours de "
                f"**{client[C_CT_NUM]}** à **{float(encours_projete):.2f} TND** alors que "
                f"le plafond autorisé est de **{float(encours_max):.2f} TND**.\n"
                f"La création est annulée. Contactez le service commercial pour régulariser la situation."
            ) if depasse else "",
        }
    finally:
        conn.close()


def _generer_code_tiers(prefixe: str) -> dict:
    conn = _get_conn()
    try:
        return {"statut": "OK", "code": _generer_prochain_code(conn, prefixe)}
    finally:
        conn.close()
def _get_nomenclature(conn: sqlite3.Connection, ref_article: str) -> list[dict]:
    """
    Lit la nomenclature (composants) et joint la table articles pour la
    désignation et les prix du composant. Noms physiques résolus via
    adaptation.db_adapter (voir NOMENCLAT_* en tête de fichier).
    """
    rows = conn.execute(
        f"""SELECT n.{NOMENCLAT_REF_MP}  AS ref_composant,
                  n.{NOMENCLAT_QTE}       AS qte_necessaire,
                  a.{C_AR_DESIGN}         AS designation,
                  a.{C_AR_PRIXACH}        AS prix_achat,
                  a.{C_AR_PRIXVEN}        AS prix_vente
           FROM {NOMENCLAT_TABLE} n
           LEFT JOIN {T_ARTICLE} a
                  ON a.{C_AR_REF} = n.{NOMENCLAT_REF_MP} COLLATE NOCASE
           WHERE n.{NOMENCLAT_REF_PF} = ? COLLATE NOCASE""",
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
    Insère dans l'entête document et les lignes document.
    DO_Domaine : 0 = vente, 1 = achat, 2 = fabrication
    DO_Type    : 2 = BL, 3 = FA, 6 = BC, 1 = OF, 4 = BF, 9 = AV
    num_piece_of est stocké dans la colonne référence (champ libre).
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
                f"""INSERT INTO {T_DOC_ENTETE}
                   ({C_DO_PIECE}, {C_DO_DOMAINE}, {C_DO_TYPE}, {C_DO_DATE}, {C_DO_REF}, {C_DO_TIERS})
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
                    f"""INSERT INTO {T_DOC_LIGNE}
                       ({C_DL_PIECE}, {C_DL_REF}, {C_DL_QTE}, {C_DL_PRIX})
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
        f"""SELECT {C_CT_NUM} AS CT_Num, {C_CT_INTITULE} AS CT_Intitule
           FROM {T_TIERS}
           WHERE {C_CT_INTITULE} LIKE ? COLLATE NOCASE
           LIMIT 5""",
        (f"%{terme}%",)
    ).fetchall()
    return [{"CT_Num": r["CT_Num"], "CT_Intitule": r["CT_Intitule"]} for r in rows]


def _suggestions_articles(conn: sqlite3.Connection, terme: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {C_AR_REF} AS AR_Ref, {C_AR_DESIGN} AS AR_Design
           FROM {T_ARTICLE}
           WHERE {C_AR_DESIGN} LIKE ? COLLATE NOCASE
              OR {C_AR_REF}    LIKE ? COLLATE NOCASE
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

        code_reel  = client[C_CT_NUM]
        nom_client = client[C_CT_INTITULE]

        # CT_Validite : 'VALIDE' | 'BLOQUE' | 'SUSPECT'
        statut_cl = str(client[C_CT_VALIDITE] or "VALIDE").upper()

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

        ref_reelle  = article[C_AR_REF]
        desig       = article[C_AR_DESIGN]
        prix_auto   = _to_decimal(article[C_AR_PRIXVEN] or 0.0)
        prix_final  = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(prix_auto))
        stock_dispo = _get_stock(conn, ref_reelle)
        montant     = (prix_final * Decimal(str(quantite))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # ── Encours client ──
        encours_max = _to_decimal(client.get(C_CT_ENCOURS_MAX) or 0.0)
        encours_actuel = _to_decimal(conn.execute(
            f"""
            SELECT COALESCE(SUM(l.{C_DL_QTE} * l.{C_DL_PRIX}), 0.0) AS encours
            FROM {T_DOC_ENTETE} e
            LEFT JOIN {T_DOC_LIGNE} l ON e.{C_DO_PIECE} = l.{C_DL_PIECE}
            WHERE e.{C_DO_TIERS} = ? AND e.{C_DO_TYPE} = 3 AND e.{C_DO_DOMAINE} = 0
              AND e.{C_DO_PIECE} NOT IN (SELECT {C_DO_PIECE} FROM {T_REGLEMENTS})
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

        ref_reelle = article[C_AR_REF]
        desig      = article[C_AR_DESIGN]
        composants = _get_nomenclature(conn, ref_reelle)

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

        # ── Création OF ─────────────────────────────────────────────
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

        ref_reelle  = article[C_AR_REF]
        desig       = article[C_AR_DESIGN]
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
            num_piece_of=num_of
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

        statut_cl = str(client.get(C_CT_VALIDITE) or "VALIDE").upper()
        if statut_cl == "BLOQUE":
            return {
                "statut": "CLIENT_BLOQUE",
                "message": f"🚫 Impossible de créer la facture : client {client[C_CT_NUM]} est bloqué.",
            }

        prix_final = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(article[C_AR_PRIXVEN] or 0.0))
        montant    = float((prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        stock_dispo = _get_stock(conn, article[C_AR_REF])
        if stock_dispo < qte:
            return {
                "statut": "STOCK_INSUFFISANT",
                "message": f"📦 Stock insuffisant pour {article[C_AR_REF]} : dispo {stock_dispo}, demandé {qte}",
                "stock_dispo": stock_dispo,
                "qte_demandee": qte,
            }

        encours_max = _to_decimal(client.get(C_CT_ENCOURS_MAX) or 0.0)
        encours_actuel = _to_decimal(conn.execute(
            f"""
            SELECT COALESCE(SUM(l.{C_DL_QTE} * l.{C_DL_PRIX}), 0.0) AS encours
            FROM {T_DOC_ENTETE} e
            LEFT JOIN {T_DOC_LIGNE} l ON e.{C_DO_PIECE} = l.{C_DL_PIECE}
            WHERE e.{C_DO_TIERS} = ? AND e.{C_DO_TYPE} = 3 AND e.{C_DO_DOMAINE} = 0
              AND e.{C_DO_PIECE} NOT IN (SELECT {C_DO_PIECE} FROM {T_REGLEMENTS})
            """,
            (client[C_CT_NUM],),
        ).fetchone()[0])
        if encours_max > 0 and encours_actuel + _to_decimal(montant) > encours_max:
            return {
                "statut": "ENCORS_MAX_ATTEINT",
                "message": (
                    f"⚠️  Encours dépassé pour {client[C_CT_NUM]} : "
                    f"{_money_text(encours_actuel + _to_decimal(montant))} > {_money_text(encours_max)}"
                ),
            }

        num_fa = _inserer_document(
            conn, "FACTURE", "",
            client[C_CT_NUM], article[C_AR_REF],
            qte, prix_final, montant
        )
        _ajuster_stock_db(conn, article[C_AR_REF], qte, "SORTIE", motif=f"FACTURE {num_fa}")
        conn.commit()

        return {
            "statut":   "GENERE",
            "DO_Piece": num_fa,
            "DO_Tiers": client[C_CT_NUM],
            "AR_Ref":   article[C_AR_REF],
            "montant":  montant,
            "message": (
                f"✅ Facture créée !\n"
                f"   • Numéro  : {num_fa}\n"
                f"   • Client  : {client[C_CT_INTITULE]}\n"
                f"   • Article : {article[C_AR_DESIGN]}\n"
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

        prix_final = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(article[C_AR_PRIXVEN] or 0.0))
        montant    = float((prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        num_bc     = _inserer_document(
            conn, "BC", "",
            client[C_CT_NUM], article[C_AR_REF],
            qte, prix_final, montant
        )
        conn.commit()

        return {
            "statut":   "GENERE",
            "DO_Piece": num_bc,
            "message": (
                f"✅ Bon de Commande créé !\n"
                f"   • Numéro  : {num_bc}\n"
                f"   • Client  : {client[C_CT_INTITULE]}\n"
                f"   • Article : {article[C_AR_DESIGN]}\n"
                f"   • Montant : {_money_text(montant)}"
            ),
        }
    finally:
        conn.close()


def _workflow_bl_achat(
    code_fournisseur: str,
    ref_article: str,
    quantite: float,
    prix_unitaire: float = 0.0,
) -> dict:
    """
    Workflow Bon de Réception fournisseur (BL Achat).
    """
    conn = _get_conn()
    try:
        fournisseur = _resolve_client(conn, code_fournisseur)
        if not fournisseur:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": f"❌ Fournisseur '{code_fournisseur}' introuvable.",
                "suggestions": _suggestions_clients(conn, code_fournisseur),
            }

        code_reel  = fournisseur[C_CT_NUM]
        nom_four   = fournisseur[C_CT_INTITULE]
        type_tiers = int(fournisseur[C_CT_TYPE] or 0)

        if type_tiers == 0:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": (
                    f"⚠️  '{nom_four}' ({code_reel}) est un CLIENT, pas un fournisseur.\n"
                    f"   Utilisez un code fournisseur."
                ),
            }

        article = _resolve_article(conn, ref_article)
        if not article:
            return {
                "statut": "ARTICLE_NON_TROUVE",
                "message": f"❌ Article '{ref_article}' introuvable.",
                "suggestions": _suggestions_articles(conn, ref_article),
            }

        ref_reelle  = article[C_AR_REF]
        desig       = article[C_AR_DESIGN]
        prix_auto   = _to_decimal(article[C_AR_PRIXACH] or 0.0)
        prix_final  = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(prix_auto))
        stock_avant = _get_stock(conn, ref_reelle)
        montant     = (prix_final * Decimal(str(quantite))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        num_br = _inserer_document(
            conn, "BL_ACHAT", "", code_reel,
            ref_reelle, quantite, prix_final, montant
        )
        mvt = _ajuster_stock_db(
            conn, ref_reelle, quantite, "ENTREE",
            motif=f"Réception BR {num_br} / {nom_four}"
        )
        if prix_unitaire > 0:
            conn.execute(
                f"UPDATE {T_ARTICLE} SET {C_AR_PRIXACH} = ? WHERE {C_AR_REF} = ?",
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


# ─────────────────────────────────────────────────────────────────────
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
    name="verifier_encours_client",
    description=(
        "Vérifie si un montant supplémentaire ferait dépasser le plafond "
        "d'encours autorisé d'un client."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "code_client": {"type": "string", "description": "Code ou nom du client"},
            "montant_supplementaire": {"type": "number", "description": "Montant HT du document envisagé"},
        },
        "required": ["code_client", "montant_supplementaire"],
    },
),
types.Tool(
    name="generer_prochain_code",
    description="Génère le prochain code séquentiel disponible pour un préfixe (CLI, FOUR...).",
    inputSchema={
        "type": "object",
        "properties": {
            "prefixe": {"type": "string", "description": "Préfixe du code (CLI, FOUR, ...)"},
        },
        "required": ["prefixe"],
    },
),
        types.Tool(
            name="workflow_bl",
            description=(
                "Workflow complet Bon de Livraison : "
                "vérifie le client (statut, blocage), vérifie le stock, "
                "crée le BL et ajuste le stock. "
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
                "vérifie la nomenclature, contrôle le stock des composants, "
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
                "crée le BF et enregistre l'entrée en stock. "
                "Peut être lié à un OF existant."
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
            description="Crée un nouveau client.",
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
            description="Crée un nouveau fournisseur.",
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
                "Modifie le statut de validité d'un client. "
                "Valeurs acceptées : BLOQUE | SOMMEIL → 'BLOQUE' ; "
                "tout autre valeur → 'VALIDE'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client": {"type": "string",
                                   "description": "Code ou nom du client"},
                    "statut":      {"type": "string",
                                   "description": "Nouveau statut (BLOQUE, VALIDE, SUSPECT)"},
                },
                "required": ["code_client", "statut"],
            },
        ),

        types.Tool(
            name="modifier_statut_fournisseur",
            description=(
                "Modifie le statut de validité d'un fournisseur. "
                "Valeurs acceptées : BLOQUE | SOMMEIL → 'BLOQUE' ; "
                "tout autre valeur → 'VALIDE'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code ou nom du fournisseur"},
                    "statut":           {"type": "string",
                                         "description": "Nouveau statut (BLOQUE, VALIDE, SUSPECT)"},
                },
                "required": ["code_fournisseur", "statut"],
            },
        ),

        types.Tool(
            name="lire_client",
            description="Lit les détails d'un client.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client": {"type": "string",
                                   "description": "Code ou nom du client"},
                },
                "required": ["code_client"],
            },
        ),

        types.Tool(
            name="lire_fournisseur",
            description="Lit les détails d'un fournisseur.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code ou nom du fournisseur"},
                },
                "required": ["code_fournisseur"],
            },
        ),

        types.Tool(
            name="lire_article",
            description="Lit les détails d'un article.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article": {"type": "string",
                                   "description": "Référence ou désignation de l'article"},
                },
                "required": ["ref_article"],
            },
        ),

        types.Tool(
            name="modifier_client",
            description="Modifie les champs d'un client (sauf code).",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client":    {"type": "string",
                                      "description": "Code du client (non modifiable)"},
                    "intitule":       {"type": "string",
                                      "description": "Nouveau nom du client"},
                    "validite":       {"type": "string",
                                      "description": "Validité (VALIDE, BLOQUE, SUSPECT)"},
                    "encours_max":    {"type": "number",
                                      "description": "Encours maximum autorisé"},
                },
                "required": ["code_client"],
            },
        ),

        types.Tool(
            name="modifier_fournisseur",
            description="Modifie les champs d'un fournisseur (sauf code).",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code du fournisseur (non modifiable)"},
                    "intitule":         {"type": "string",
                                         "description": "Nouveau nom du fournisseur"},
                    "validite":         {"type": "string",
                                         "description": "Validité (VALIDE, BLOQUE, SUSPECT)"},
                    "encours_max":     {"type": "number",
                                         "description": "Encours maximum autorisé"},
                },
                "required": ["code_fournisseur"],
            },
        ),

        types.Tool(
            name="modifier_article",
            description="Modifie les champs d'un article (sauf référence).",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article":  {"type": "string",
                                     "description": "Référence de l'article (non modifiable)"},
                    "designation":  {"type": "string",
                                     "description": "Nouvelle désignation"},
                    "prix_achat":   {"type": "number",
                                     "description": "Prix d'achat"},
                    "prix_vente":   {"type": "number",
                                     "description": "Prix de vente"},
                    "type_article": {"type": "integer",
                                     "description": "Type d'article"},
                },
                "required": ["ref_article"],
            },
        ),

        types.Tool(
            name="transformer_document",
            description=(
                "Transforme un document existant en un autre type "
                "(ex : BL → FACTURE, BC → BL). "
                "Recopie les lignes depuis le document source."
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
                "Crée un avoir (AV) à partir d'une facture existante. "
                "Le montant est l'inverse du montant de la facture d'origine."
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
                "marque le document comme réglé et insère l'écriture de règlement."
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
                "Enregistre un mouvement de stock manuel "
                "et trace le mouvement."
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
                "pour un article auprès d'un fournisseur."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article":      {"type": "string",
                                         "description": "Référence de l'article à réapprovisionner"},
                    "qte_a_commander":  {"type": "number",
                                         "description": "Quantité à commander"},
                    "code_fournisseur": {"type": "string",
                                         "description": "Code du fournisseur"},
                },
                "required": ["ref_article", "qte_a_commander", "code_fournisseur"],
            },
        ),

        types.Tool(
            name="workflow_bl_achat",
            description=(
                "Workflow Bon de Réception fournisseur (achat) : "
                "vérifie le fournisseur, crée le bon de réception, "
                "incrémente le stock, "
                "et suggère la transformation en facture fournisseur."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code ou nom du fournisseur"},
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

    elif name == "workflow_bl":
        result = _workflow_bl(
            arguments["code_client"],
            arguments["ref_article"],
            float(arguments["quantite"]),
            float(arguments.get("prix_unitaire", 0.0)),
        )
        return _to_text(result)

    elif name == "workflow_of":
        result = _workflow_of(
            arguments["ref_article"],
            float(arguments["quantite"]),
            arguments.get("code_client", "PROD-INT"),
        )
        return _to_text(result)

    elif name == "workflow_bf":
        result = _workflow_bf(
            arguments["ref_article"],
            float(arguments["quantite"]),
            arguments.get("num_of", ""),
            arguments.get("code_client", "PROD-INT"),
        )
        return _to_text(result)

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
                f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?",
                (code_client,)
            ).fetchone()
            if existing:
                result = {
                    "statut":  "EXISTE_DEJA",
                    "message": f"⚠️  Client '{code_client}' existe déjà.",
                }
            else:
                conn.execute(
                    f"""INSERT INTO {T_TIERS}
                       ({C_CT_NUM}, {C_CT_INTITULE}, {C_CT_TYPE}, {C_CT_VALIDITE}, {C_CT_ENCOURS_MAX}, {C_CT_ENCOURS})
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

    elif name == "creer_nouveau_fournisseur":
        conn = _get_conn()
        try:
            code_fourn = arguments["code_fournisseur"]
            intitule   = arguments["intitule"]
            existing = conn.execute(
                f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?",
                (code_fourn,)
            ).fetchone()
            if existing:
                result = {
                    "statut":  "EXISTE_DEJA",
                    "message": f"⚠️  Fournisseur '{code_fourn}' existe déjà.",
                }
            else:
                conn.execute(
                    f"""INSERT INTO {T_TIERS}
                       ({C_CT_NUM}, {C_CT_INTITULE}, {C_CT_TYPE}, {C_CT_VALIDITE}, {C_CT_ENCOURS_MAX}, {C_CT_ENCOURS})
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
                nouveau_statut = arguments["statut"].upper()
                valeur = (
                    "BLOQUE"
                    if nouveau_statut in ("BLOQUE", "SOMMEIL")
                    else "VALIDE"
                )
                conn.execute(
                    f"UPDATE {T_TIERS} SET {C_CT_VALIDITE} = ? WHERE {C_CT_NUM} = ?",
                    (valeur, client[C_CT_NUM])
                )
                conn.commit()
                result = {
                    "statut":  "MODIFIE",
                    "message": (
                        f"✅ Statut de '{client[C_CT_INTITULE]}' "
                        f"({client[C_CT_NUM]}) → {valeur}."
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)
    elif name == "verifier_encours_client":
        result = _verifier_encours_client(
        arguments["code_client"], float(arguments["montant_supplementaire"])
    )
        return _to_text(result)

    elif name == "generer_prochain_code":
        result = _generer_code_tiers(arguments["prefixe"])
        return _to_text(result)
    elif name == "modifier_statut_fournisseur":
        conn = _get_conn()
        try:
            fournisseur = _resolve_client(conn, arguments["code_fournisseur"])
            if not fournisseur:
                result = {
                    "statut":  "FOURNISSEUR_NON_TROUVE",
                    "message": f"❌ Fournisseur '{arguments['code_fournisseur']}' introuvable.",
                }
            else:
                nouveau_statut = arguments["statut"].upper()
                valeur = (
                    "BLOQUE"
                    if nouveau_statut in ("BLOQUE", "SOMMEIL")
                    else "VALIDE"
                )
                conn.execute(
                    f"UPDATE {T_TIERS} SET {C_CT_VALIDITE} = ? WHERE {C_CT_NUM} = ?",
                    (valeur, fournisseur[C_CT_NUM])
                )
                conn.commit()
                result = {
                    "statut":  "MODIFIE",
                    "message": (
                        f"✅ Statut de '{fournisseur[C_CT_INTITULE]}' "
                        f"({fournisseur[C_CT_NUM]}) → {valeur}."
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    elif name == "transformer_document":
        conn = _get_conn()
        try:
            num_piece_source = arguments["num_piece_source"]
            type_destination = arguments["type_destination"].upper()

            entete = conn.execute(
                f"SELECT * FROM {T_DOC_ENTETE} WHERE {C_DO_PIECE} = ?",
                (num_piece_source,)
            ).fetchone()
            if not entete:
                result = {
                    "statut":  "NON_TROUVE",
                    "message": f"❌ Document '{num_piece_source}' introuvable.",
                }
            else:
                existing = conn.execute(
                    f"SELECT {C_DO_PIECE} FROM {T_DOC_ENTETE} WHERE {C_DO_REF} = ? AND {C_DO_TYPE} = ? AND {C_DO_DOMAINE} = ?",
                    (num_piece_source, DOC_TYPE.get(type_destination.upper(), 0), DOC_DOMAINE.get(type_destination.upper(), 0)),
                ).fetchone()
                if existing:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  Le document source '{num_piece_source}' a déjà été transformé en {type_destination.upper()} ({existing[C_DO_PIECE]}).",
                    }
                    conn.close()
                    return _to_text(result)

                lignes_source = conn.execute(
                    f"SELECT * FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE} = ? ORDER BY {C_DL_LIGNE}",
                    (num_piece_source,)
                ).fetchall()
                lignes_dest = [{
                    "ref_article": ligne[C_DL_REF],
                    "qte": float(ligne[C_DL_QTE]),
                    "prix_unit": float(ligne[C_DL_PRIX]),
                } for ligne in lignes_source]

                if type_destination.upper() in {"FACTURE", "FA", "FC"}:
                    client = conn.execute(
                        f"SELECT {C_CT_NUM}, {C_CT_ENCOURS_MAX}, {C_CT_VALIDITE} FROM {T_TIERS} WHERE {C_CT_NUM} = ?",
                        (entete[C_DO_TIERS],),
                    ).fetchone()
                    if client:
                        encours_max = _to_decimal(client[C_CT_ENCOURS_MAX] or 0.0)
                        encours_actuel = _to_decimal(conn.execute(
                            f"""
                            SELECT COALESCE(SUM(l.{C_DL_QTE} * l.{C_DL_PRIX}), 0.0) AS encours
                            FROM {T_DOC_ENTETE} e
                            LEFT JOIN {T_DOC_LIGNE} l ON e.{C_DO_PIECE} = l.{C_DL_PIECE}
                            WHERE e.{C_DO_TIERS} = ? AND e.{C_DO_TYPE} = 3 AND e.{C_DO_DOMAINE} = 0
                              AND e.{C_DO_PIECE} NOT IN (SELECT {C_DO_PIECE} FROM {T_REGLEMENTS})
                            """,
                            (client[C_CT_NUM],),
                        ).fetchone()[0])
                        montant_total = float(_decimal_sum(
                            (Decimal(str(l["qte"])) * Decimal(str(l["prix_unit"])) for l in lignes_dest)
                        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        if encours_max > 0 and encours_actuel + _to_decimal(montant_total) > encours_max:
                            result = {
                                "statut": "ENCORS_MAX_ATTEINT",
                                "message": (
                                    f"⚠️  Encours dépassé pour {client[C_CT_NUM]} : "
                                    f"{_money_text(encours_actuel + _to_decimal(montant_total))} > {_money_text(encours_max)}"
                                ),
                            }
                            conn.close()
                            return _to_text(result)

                num_dest = _inserer_document(
                    conn, type_destination, "",
                    entete[C_DO_TIERS],
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

    elif name == "creer_facture_avoir":
        conn = _get_conn()
        try:
            num_facture_origine = arguments["num_facture_origine"]

            entete = conn.execute(
                f"SELECT * FROM {T_DOC_ENTETE} WHERE {C_DO_PIECE} = ?",
                (num_facture_origine,)
            ).fetchone()
            if not entete:
                result = {
                    "statut":  "NON_TROUVE",
                    "message": f"❌ Facture '{num_facture_origine}' introuvable.",
                }
            else:
                lignes_source = conn.execute(
                    f"SELECT * FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE} = ? ORDER BY {C_DL_LIGNE}",
                    (num_facture_origine,)
                ).fetchall()
                lignes_dest = [{
                    "ref_article": ligne[C_DL_REF],
                    "qte": float(ligne[C_DL_QTE]),
                    "prix_unit": -float(ligne[C_DL_PRIX]),
                } for ligne in lignes_source]

                existing_avoir = conn.execute(
                    f"SELECT {C_DO_PIECE} FROM {T_DOC_ENTETE} WHERE {C_DO_REF} = ? AND {C_DO_TYPE} = 9 AND {C_DO_DOMAINE} = 0",
                    (num_facture_origine,),
                ).fetchone()
                if existing_avoir:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  Une avoir existe déjà pour cette facture ({existing_avoir[C_DO_PIECE]}).",
                    }
                    conn.close()
                    return _to_text(result)

                num_av = _inserer_document(
                    conn, "AV", "",
                    entete[C_DO_TIERS],
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

    elif name == "enregistrer_reglement_facture":
        conn = _get_conn()
        try:
            num_piece     = arguments["num_piece"]
            mode_paiement = arguments.get("mode_paiement", "Virement")
            numero_piece_paiement = arguments.get("numero_piece_paiement", "")
            entete = conn.execute(
                f"SELECT * FROM {T_DOC_ENTETE} WHERE {C_DO_PIECE} = ?",
                (num_piece,)
            ).fetchone()
            if not entete:
                result = {
                    "statut":  "NON_TROUVE",
                    "message": f"❌ Document '{num_piece}' introuvable.",
                }
            else:
                lignes = conn.execute(
                    f"SELECT {C_DL_QTE}, {C_DL_PRIX} FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE} = ?",
                    (num_piece,)
                ).fetchall()
                montant_total = float(_decimal_sum(
                    (Decimal(str(l[C_DL_QTE])) * Decimal(str(l[C_DL_PRIX])) for l in lignes)
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                existing_reglement = conn.execute(
                    f"SELECT 1 FROM {T_REGLEMENTS} WHERE {C_DO_PIECE} = ?",
                    (num_piece,),
                ).fetchone()
                if existing_reglement:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  La facture '{num_piece}' a déjà un règlement enregistré.",
                    }
                    conn.close()
                    return _to_text(result)

                conn.execute(
                    f"UPDATE {T_DOC_ENTETE} SET {C_DO_REF} = ? WHERE {C_DO_PIECE} = ?",
                    (f"SOLDEE / REGLEE - {mode_paiement}", num_piece)
                )
                conn.execute(
                    f"""INSERT INTO {T_REGLEMENTS}
                       ({C_DO_PIECE}, mode_paiement, montant, date_reglement, numero_piece_paiement)
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
                    conn, article[C_AR_REF],
                    qte_mouvement, type_mouvement, motif
                )
                conn.commit()
                result = {
                    "statut":      "MOUVEMENT_ENREGISTRE",
                    "AR_Ref":      article[C_AR_REF],
                    "stock_avant": mvt["stock_avant"],
                    "stock_apres": mvt["stock_apres"],
                    "message": (
                        f"✅ Mouvement stock enregistré !\n"
                        f"   • Article      : {article[C_AR_DESIGN]}\n"
                        f"   • Type         : {type_mouvement}\n"
                        f"   • Quantité     : {qte_mouvement} u\n"
                        f"   • Stock avant  : {mvt['stock_avant']} u\n"
                        f"   • Stock après  : {mvt['stock_apres']} u"
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

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

    elif name == "workflow_bl_achat":
        result = _workflow_bl_achat(
            arguments["code_fournisseur"],
            arguments["ref_article"],
            float(arguments["quantite"]),
            float(arguments.get("prix_unitaire", 0.0)),
        )
        return _to_text(result)

    elif name == "lire_client":
        conn = _get_conn()
        result = _lire_client(conn, arguments["code_client"])
        conn.close()
        return _to_text(result)

    elif name == "lire_fournisseur":
        conn = _get_conn()
        result = _lire_fournisseur(conn, arguments["code_fournisseur"])
        conn.close()
        return _to_text(result)

    elif name == "lire_article":
        conn = _get_conn()
        result = _lire_article(conn, arguments["ref_article"])
        conn.close()
        return _to_text(result)

    elif name == "modifier_client":
        conn = _get_conn()
        kwargs = {}
        if "intitule" in arguments:
            kwargs["intitule"] = arguments["intitule"]
        if "validite" in arguments:
            kwargs["validite"] = arguments["validite"]
        if "encours_max" in arguments:
            kwargs["encours_max"] = arguments["encours_max"]
        result = _modifier_client(conn, arguments["code_client"], **kwargs)
        conn.close()
        return _to_text(result)

    elif name == "modifier_fournisseur":
        conn = _get_conn()
        kwargs = {}
        if "intitule" in arguments:
            kwargs["intitule"] = arguments["intitule"]
        if "validite" in arguments:
            kwargs["validite"] = arguments["validite"]
        if "encours_max" in arguments:
            kwargs["encours_max"] = arguments["encours_max"]
        result = _modifier_fournisseur(conn, arguments["code_fournisseur"], **kwargs)
        conn.close()
        return _to_text(result)

    elif name == "modifier_article":
        conn = _get_conn()
        kwargs = {}
        if "designation" in arguments:
            kwargs["designation"] = arguments["designation"]
        if "prix_achat" in arguments:
            kwargs["prix_achat"] = arguments["prix_achat"]
        if "prix_vente" in arguments:
            kwargs["prix_vente"] = arguments["prix_vente"]
        if "type_article" in arguments:
            kwargs["type_article"] = arguments["type_article"]
        result = _modifier_article(conn, arguments["ref_article"], **kwargs)
        conn.close()
        return _to_text(result)

    else:
        return _to_text({
            "statut": "ERREUR",
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