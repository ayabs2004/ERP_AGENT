#!/usr/bin/env python3
"""
mcp_sage.py — Serveur MCP Actions Sage 100 v4.2
================================================
Expose toutes les fonctions de actions_sage.py comme outils MCP.

v4.1 : tous les noms de TABLE et de COLONNE physiques proviennent
désormais de `adaptation/db_adapter.py` (lui-même alimenté par
`adaptation/db_config.json`). Pour brancher une autre base (vraie base
Sage, autre ERP...), il suffit de modifier db_config.json — ce fichier
n'a plus aucun nom de colonne en dur.

v4.2 : CORRECTIF « détection de doublon trop tardive ».
       creer_nouveau_client / creer_nouveau_fournisseur ne détectaient
       un intitulé déjà existant (_verifier_nom_tiers_existe) qu'au
       moment de l'INSERT final, c'est-à-dire après que l'orchestrateur
       ait fait répondre l'utilisateur à 10+ questions de complément
       (adresse, ville, CP, contact, tél, email...) pour rien.
       Ajout d'un outil MCP dédié `verifier_nom_tiers_existe`,
       appelable dès que le nom est connu, AVANT de lancer la collecte
       des champs complémentaires côté orchestrateur.
"""
import asyncio
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

_db_call_lock = asyncio.Lock()
import adaptation.db_adapter as sch
import re
import unicodedata
import json
import logging

from typing import Any
import os
import sys
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional
from uuid import uuid4


def _is_mssql() -> bool:
    """Dialecte SQL courant (mssql vs sqlite/mock). Doit rester identique
    à la version dans declaration.py / mcp_nl2sql.py — logique volontairement
    dupliquée pour éviter tout import circulaire entre ces modules."""
    return os.getenv("DB_DRIVER", "sqlite").lower() == "mssql"


# En tête de fichier, avec les autres imports de sch.*
T_LOT_SERIE = sch.T_LOT_SERIE
C_LS_REF = sch.C_LS_REF
C_LS_NUMERO = sch.C_LS_NUMERO
C_LS_FABRICATION = sch.C_LS_FABRICATION
C_LS_PEREMPTION = sch.C_LS_PEREMPTION
C_LS_QTE_INIT = sch.C_LS_QTE_INIT
C_LS_QTE_RESTE = sch.C_LS_QTE_RESTE
C_LS_QTE_RES = sch.C_LS_QTE_RES
C_LS_EPUISE = sch.C_LS_EPUISE
C_LS_DEPOT = sch.C_LS_DEPOT
C_LS_DL_IN = sch.C_LS_DL_IN
C_LS_DL_OUT = sch.C_LS_DL_OUT
C_LS_MVT = sch.C_LS_MVT
C_LS_CBMARQ = sch.C_LS_CBMARQ
_json_dumps_orig = json.dumps
def _json_dumps_safe(obj, **kwargs):
    kwargs.setdefault("default", lambda o: float(o) if isinstance(o, Decimal) else str(o))
    return _json_dumps_orig(obj, **kwargs)
json.dumps = _json_dumps_safe

def _top(n: int) -> str:
    """SELECT prefix for row limiting (TOP N)."""
    return f'TOP {n} '


# Add parent directory to path for database import
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.schema_sage import (
    DOC_CODES, DOC_DOMAINE, DOC_PREFIXES, DOC_TYPE, DOC_DESTOCKANTS, DOC_STOCKANTS,
    CURRENCY_SYMBOL, TVA_TAUX,
)

# ── Mapping schéma DB centralisé (table/colonnes physiques) ───────────


logger = logging.getLogger("sage.erp.actions")
import sys
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger.setLevel(logging.INFO)

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
T_FAMILLE = sch.T_FAMILLE          # F_FAMILLE
T_STOCK = sch.T_STOCK              # F_ARTSTOCK
T_DOC_ENTETE = sch.T_DOC_ENTETE    # F_DOCENTETE
T_DOC_LIGNE = sch.T_DOC_LIGNE      # F_DOCLIGNE
# TEMPORAIRE: T_MVT_STOCK désactivé le temps de décider de la structure
# T_MVT_STOCK = sch.T_MVT_STOCK      # mouvements_stock
T_MVT_STOCK = "mouvements_stock"
T_REGLEMENTS = sch.T_REGLEMENTS    # reglements

C_CT_NUM = sch.C_CT_NUM
C_CT_INTITULE = sch.C_CT_INTITULE
C_CT_TYPE = sch.C_CT_TYPE
C_CT_SOMMEIL = sch.C_CT_SOMMEIL
C_CT_ENCOURS = sch.C_CT_ENCOURS
C_CT_ADRESSE     = sch.C_CT_ADRESSE
C_CT_COMPLEMENT  = sch.C_CT_COMPLEMENT
C_CT_CODEPOSTAL  = sch.C_CT_CODEPOSTAL
C_CT_VILLE       = sch.C_CT_VILLE
C_CT_PAYS        = sch.C_CT_PAYS
C_CT_CONTACT     = sch.C_CT_CONTACT
C_CT_TELEPHONE   = sch.C_CT_TELEPHONE
C_CT_TELECOPIE   = sch.C_CT_TELECOPIE
C_CT_EMAIL       = sch.C_CT_EMAIL
C_CT_SITE        = sch.C_CT_SITE
C_CT_CGNUMPRINC  = sch.C_CT_CGNUMPRINC
C_AR_REF = sch.C_AR_REF
C_AR_DESIGN = sch.C_AR_DESIGN
C_AR_PRIXACH = sch.C_AR_PRIXACH
C_AR_PRIXVEN = sch.C_AR_PRIXVEN
C_AR_TYPE = sch.C_AR_TYPE
C_AR_FAMILLE = sch.C_AR_FAMILLE
C_AR_NATURE = sch.C_AR_NATURE
C_AR_UNITEVEN = sch.C_AR_UNITEVEN
C_AR_SUIVISTOCK = sch.C_AR_SUIVISTOCK

C_FA_CODE = sch.C_FA_CODE
C_FA_INTITULE = sch.C_FA_INTITULE

C_AS_REF = sch.C_AS_REF
C_AS_DENO = sch.C_AS_DENO
C_AS_QTESTO = sch.C_AS_QTESTO
C_AS_QTECOM = sch.C_AS_QTECOM
C_AS_MONTSTO = sch.C_AS_MONTSTO
C_AS_PRINCIPAL = sch.C_AS_PRINCIPAL

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
C_DL_MVTSTOCK = sch.C_DL_MVTSTOCK
C_DL_DENO = sch.C_DL_DENO
C_DL_PRIXRU = sch.C_DL_PRIXRU
C_DL_CMUP = sch.C_DL_CMUP
C_DL_ARCOMPOSE = sch.C_DL_ARCOMPOSE
C_DL_TTC = sch.C_DL_TTC
C_DL_VALORISE = sch.C_DL_VALORISE
C_DL_NONLIVRE = sch.C_DL_NONLIVRE
C_DL_PIECEBL = sch.C_DL_PIECEBL
C_DL_QTEBL = sch.C_DL_QTEBL

DEPOT_DEFAUT = int(os.getenv("DEPOT_DEFAUT", "1"))

# F_NOMENCLAT est bien référencé dans db_config.json (table "nomenclature") :
# ces 4 constantes viennent donc, elles aussi, du mapping centralisé.
NOMENCLAT_TABLE = sch.T_NOMENCLAT
NOMENCLAT_REF_PF = sch.C_NO_REF_PF
NOMENCLAT_REF_MP = sch.C_NO_REF_MP
NOMENCLAT_QTE = sch.C_NO_QTE
C_CT_CBMARQ = sch.C_CT_CBMARQ
C_DE_CBMARQ = sch.C_DE_ENTETE_CBMARQ
C_DL_CBMARQ = sch.C_DL_CBMARQ
C_DL_PFNUM  = sch.C_DL_PFNUM
C_REGL_PIECE    = sch.C_REGL_PIECE
C_REGL_TYPE     = sch.C_REGL_TYPE
C_REGL_MONTANT  = sch.C_REGL_MONTANT
C_REGL_DATE     = sch.C_REGL_DATE
C_REGL_REFERENCE = sch.C_REGL_REFERENCE
C_REGL_MODE_PAI = sch.C_REGL_MODE_PAI
# Colonnes requises par le trigger TG_INS_F_DOCREGL (jointure avec F_DOCENTETE)
C_REGL_DOMAINE  = sch.C_REGL_DOMAINE    # DO_Domaine
C_REGL_TYPE_DOC = sch.C_REGL_TYPE_DOC  # DO_Type
C_REGL_CB_PIECE = sch.C_REGL_CB_PIECE  # cbDO_Piece
C_REGL_CBMARQ   = sch.C_REGL_CBMARQ    # cbMarq

# ─────────────────────────────────────────────────────────────────────
# HELPERS INTERNES
# ─────────────────────────────────────────────────────────────────────

_DEFAULTS_DOCENTETE_CONSTANTES = {
    "AB_No": 0, "CA_No": 0, "CA_NumIFRS": "",
    "cbCT_NumCentrale": "", "cbDE_No": 1, "cbDO_FactureFrs": "",
    "cbDO_PieceOrig": "       ", "cbFlag": 0, "cbProt": 0, "cbReplication": 0,
    "CFAR_No": 0, "CO_NoCaissier": 0,
    "DO_AdressePaiement": "", "DO_Attente": 0, "DO_Cloture": 0,
    "DO_CodeService": "", "DO_Coffre": 0, "DO_Contact": "",
    "DO_Conversion": 0,
    "DO_Coord01": "", "DO_Coord02": "", "DO_Coord03": "", "DO_Coord04": "",
    "DO_DateExpedition": None, "DO_DateLivrRealisee": None,
    "DO_DebutAbo": None, "DO_DebutPeriod": None,
    "DO_FinAbo": None, "DO_FinPeriod": None,
    "DO_DemandeRegul": 0, "DO_Ecart": 0.0, "DO_EStatut": 0, "DO_Exclure": 0,
    "DO_FactureElec": 0, "DO_FactureFrs": "",
    "DO_Imprim": 0, "DO_Langue": 0, "DO_MajCpta": 0,
    "DO_MontantRegle": 0.0, "DO_Motif": "", "DO_MotifDevis": 0,
    "DO_NoWeb": "", "DO_PaiementLigne": 0, "DO_PieceOrig": "",
    "DO_Provenance": 0, "DO_RefExterne": "", "DO_Reliquat": 0,
    "DO_Souche": 0, "DO_StatutBAP": 0, "DO_Taxe2": 0.0, "DO_Taxe3": 0.0,
    "DO_Transfere": 0, "DO_TVADebit": 0, "DO_TypeCalcul": 0,
    "DO_TypeFrais": 0, "DO_TypeFranco": 0,
    "DO_TypeLigneFrais": 0, "DO_TypeLigneFranco": 0,
    "DO_TypeTaux1": 0, "DO_TypeTaux2": 0, "DO_TypeTaux3": 0,
    "DO_TypeTaxe1": 0, "DO_TypeTaxe2": 0, "DO_TypeTaxe3": 0,
    "DO_TypeTransac": 0, "DO_Ventile": 0,
    "EB_No": 0, "ET_No": 0, "FAC_No": 0,
    "DE_No": 1,
}

_DEFAULTS_TIERS_CONSTANTES = {
    "N_Devise": 0, "CT_Langue": 0, "N_Condition": 1,
    "CT_Facture": 1, "CT_BLFact": 1, "DE_No": 1,
    "N_Analytique": 0, "CT_ValidEch": 0, "CT_Saut": 0,
    "N_Risque": 0, "N_CatTarif": 1, "N_CatCompta": 1,
    "N_Period": 1, "CT_ControlEnc": 0, "CT_NotRappel": 0,
}

_CG_NUM_PAR_TYPE = {0: "4110000", 1: "4010000"}  # 0=client, 1=fournisseur

_COMPTE_PAR_DOMAINE = {
    0: "4110000",  # vente -> compte client
    1: "4010000",  # achat -> compte fournisseur
    # 2 (stock/production) et 4 (SAV/autres) : pas de tiers comptable -> CG_Num reste NULL
}

def _cg_num_pour_domaine(domaine: int) -> str | None:
    return _COMPTE_PAR_DOMAINE.get(domaine)

_table_columns_cache = {}

def _get_table_columns(conn, table_name: str) -> set[str]:
    """Retourne l'ensemble des colonnes physiques non calculées (is_computed = 0) de la table en base de données."""
    if table_name in _table_columns_cache:
        return _table_columns_cache[table_name]
    try:
        cursor = conn.execute(
            "SELECT c.name FROM sys.columns c "
            "JOIN sys.objects o ON c.object_id = o.object_id "
            "WHERE o.name = ? AND c.is_computed = 0",
            (table_name,)
        )
        cols = {r[0] for r in cursor.fetchall()}
        _table_columns_cache[table_name] = cols
        return cols
    except Exception as e:
        logger.warning(f"Impossible d'introspecter la table {table_name}: {e}")
        return set()


def _safe_str(obj) -> str:
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return str(obj).encode("utf-8", errors="replace").decode("utf-8")
def _normaliser_valeur(val):
    """
    Convention : un utilisateur qui tape '.' pour un champ optionnel veut
    explicitement mettre NULL en base (et non une chaîne vide, ni laisser
    le champ inchangé). Retourne None dans ce cas, sinon la valeur inchangée.
    """
    if isinstance(val, str) and val.strip() == ".":
        return None
    return val

import contextvars
_conn_override = contextvars.ContextVar('conn_override', default=None)

def _get_conn():
    override = _conn_override.get()
    if override is not None:
        return override
    conn = sch.get_connection()
    # Création des tables annexes manquantes au premier accès.
    # Ces tables sont internes à l'application (pas des tables Sage) :
    # seul leur NOM provient du mapping, leurs colonnes sont fixes.
    # TEMPORAIRE: Désactivation de la création de la table T_MVT_STOCK le temps de décider de sa structure
    # conn.execute(f"""
    #     CREATE TABLE IF NOT EXISTS {T_MVT_STOCK} (
    #         id             INTEGER PRIMARY KEY AUTOINCREMENT,
    #         {C_AR_REF}     TEXT,
    #         type_mouvement TEXT,
    #         qte            REAL,
    #         motif          TEXT,
    #         date_mouvement TEXT
    #     )
    # """)

    return conn
def _generer_num_piece(type_doc: str, conn: Optional[Any] = None) -> str:
    prefix = DOC_PREFIXES.get(type_doc.upper(), type_doc[:2].upper())
    # Budget total : 9 caractères (limite de certaines bases Sage pour DO_Piece / PF_Num)
    suffix = uuid4().hex[:7].upper()                   # 7 caractères aléatoires
    return f"{prefix}{suffix}"

def _generer_cbmarq(conn: Any, table: str, cbmarq_col: str) -> int:
    row = conn.execute(
        f"SELECT COALESCE(MAX({cbmarq_col}), 0) + 1 FROM {table}"
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 1

def _resolve_client_with_suggestions(conn: Any, code_ou_nom: str) -> tuple[Optional[dict], list[dict]]:
    """
    Recherche dans la table tiers par code exact, puis par nom partiel.
    Retourne (tiers_dict, candidats) :
      - (row, [])           → trouvé exactement
      - (None, [])          → introuvable
      - (None, [c1, c2...]) → ambigu : plusieurs candidats LIKE
    """
    if not code_ou_nom:
        return None, []
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE UPPER({C_CT_NUM}) = UPPER(?)",
        (code_ou_nom,)
    ).fetchone()
    if row:
        return dict(row), []
    rows = conn.execute(
        f"SELECT {_top(5)}* FROM {T_TIERS} WHERE UPPER({C_CT_INTITULE}) LIKE UPPER(?)",
        (f"%{code_ou_nom}%",)
    ).fetchall()
    if not rows:
        return None, []
    if len(rows) > 1:
        candidats = [
            {C_CT_NUM: r[C_CT_NUM], C_CT_INTITULE: r[C_CT_INTITULE], C_CT_TYPE: r.get(C_CT_TYPE, "?")}
            for r in rows
        ]
        return None, candidats
    return dict(rows[0]), []


def _resolve_client(conn: Any, code_ou_nom: str) -> Optional[dict]:
    """
    Wrapper rétro-compatible : retourne le tiers unique ou None.
    Utiliser _resolve_client_with_suggestions pour les messages d'erreur détaillés.
    """
    tiers, _ = _resolve_client_with_suggestions(conn, code_ou_nom)
    return tiers


def _verifier_nom_tiers_existe(conn: Any, intitule: str, type_tiers: int) -> bool:
    if not intitule or not str(intitule).strip():
        return False
    row = conn.execute(
        f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_TYPE} = ? AND UPPER({C_CT_INTITULE}) = UPPER(?)",
        (type_tiers, str(intitule).strip()),
    ).fetchone()
    return bool(row)


def _lire_client(conn: Any, code_ou_nom: str) -> dict:
    """Lit les détails d'un client depuis la table tiers."""
    row, candidats = _resolve_client_with_suggestions(conn, code_ou_nom)
    if not row:
        if candidats:
            return {
                "statut": "AMBIGU",
                "message": (
                    f"⚠️ '{code_ou_nom}' correspond à {len(candidats)} clients différents. "
                    f"Précisez le code exact (CT_Num)."
                ),
                "suggestions": candidats,
            }
        return {"statut": "ERREUR", "message": f"Client '{code_ou_nom}' non trouvé"}
    if row.get(C_CT_TYPE) != 0:
        return {"statut": "ERREUR", "message": f"'{code_ou_nom}' n'est pas un client"}
    sommeil = row.get(C_CT_SOMMEIL, 0)
    return {
        "statut":      "SUCCES",
        "CT_Num":      row[C_CT_NUM],
        "CT_Intitule": row[C_CT_INTITULE],
        "CT_Sommeil":  sommeil,
        "CT_Encours":  row.get(C_CT_ENCOURS, 0),
        # ── champs optionnels (peuvent être absents si colonne non encore créée) ──
        "CT_Adresse":    row[C_CT_ADRESSE]    if C_CT_ADRESSE    in row.keys() else "",
        "CT_Complement": row[C_CT_COMPLEMENT] if C_CT_COMPLEMENT in row.keys() else "",
        "CT_CodePostal": row[C_CT_CODEPOSTAL] if C_CT_CODEPOSTAL in row.keys() else "",
        "CT_Ville":      row[C_CT_VILLE]      if C_CT_VILLE      in row.keys() else "",
        "CT_Pays":       row[C_CT_PAYS]       if C_CT_PAYS       in row.keys() else "",
        "CT_Contact":    row[C_CT_CONTACT]    if C_CT_CONTACT    in row.keys() else "",
        "CT_Telephone":  row[C_CT_TELEPHONE]  if C_CT_TELEPHONE  in row.keys() else "",
        "CT_Email":      row[C_CT_EMAIL]      if C_CT_EMAIL      in row.keys() else "",
        "CT_Site":       row[C_CT_SITE]       if C_CT_SITE       in row.keys() else "",
        # ct_validite : valeur lisible dérivée de CT_Sommeil
        "ct_validite":   "BLOQUE" if sommeil == 1 else "VALIDE",
    }


def _lire_fournisseur(conn: Any, code_ou_nom: str) -> dict:
    """Lit les détails d'un fournisseur depuis la table tiers."""
    row, candidats = _resolve_client_with_suggestions(conn, code_ou_nom)
    if not row:
        if candidats:
            return {
                "statut": "AMBIGU",
                "message": (
                    f"⚠️ '{code_ou_nom}' correspond à {len(candidats)} tiers différents. "
                    f"Précisez le code exact (CT_Num)."
                ),
                "suggestions": candidats,
            }
        return {"statut": "ERREUR", "message": f"Fournisseur '{code_ou_nom}' non trouvé"}
    if row.get(C_CT_TYPE) != 1:
        return {"statut": "ERREUR", "message": f"'{code_ou_nom}' n'est pas un fournisseur"}
    sommeil = row.get(C_CT_SOMMEIL, 0)
    return {
        "statut":      "SUCCES",
        "CT_Num":      row[C_CT_NUM],
        "CT_Intitule": row[C_CT_INTITULE],
        "CT_Sommeil":  sommeil,
        "CT_Encours":  row.get(C_CT_ENCOURS, 0),
        # ── champs optionnels (peuvent être absents si colonne non encore créée) ──
        "CT_Adresse":    row[C_CT_ADRESSE]    if C_CT_ADRESSE    in row.keys() else "",
        "CT_Complement": row[C_CT_COMPLEMENT] if C_CT_COMPLEMENT in row.keys() else "",
        "CT_CodePostal": row[C_CT_CODEPOSTAL] if C_CT_CODEPOSTAL in row.keys() else "",
        "CT_Ville":      row[C_CT_VILLE]      if C_CT_VILLE      in row.keys() else "",
        "CT_Pays":       row[C_CT_PAYS]       if C_CT_PAYS       in row.keys() else "",
        "CT_Contact":    row[C_CT_CONTACT]    if C_CT_CONTACT    in row.keys() else "",
        "CT_Telephone":  row[C_CT_TELEPHONE]  if C_CT_TELEPHONE  in row.keys() else "",
        "CT_Email":      row[C_CT_EMAIL]      if C_CT_EMAIL      in row.keys() else "",
        "CT_Site":       row[C_CT_SITE]       if C_CT_SITE       in row.keys() else "",
        # ct_validite : valeur lisible dérivée de CT_Sommeil
        "ct_validite":   "BLOQUE" if sommeil == 1 else "VALIDE",
    }


def _lire_article(conn: Any, ref_ou_design: str) -> dict:
    """Lit les détails d'un article depuis la table articles et la table stock."""
    row = _resolve_article(conn, ref_ou_design)
    if not row:
        return {"statut": "ERREUR", "message": f"Article '{ref_ou_design}' non trouvé"}

    stock_row = conn.execute(
        f"SELECT * FROM {T_STOCK} WHERE UPPER({C_AS_REF}) = UPPER(?)",
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


def _assurer_tiers_interne(conn: Any, code_client: str = "PROD-INT") -> None:
    """
    Garantit l'existence du tiers interne utilisé par défaut comme "client"
    des OF/BF de fabrication.
    """
    code = (code_client or "PROD-INT").upper()
    existing = conn.execute(
        f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE UPPER({C_CT_NUM}) = ?",
        (code,)
    ).fetchone()
    if existing:
        return
    conn.execute(
        f"""INSERT INTO {T_TIERS}
           ({C_CT_NUM}, {C_CT_INTITULE}, {C_CT_TYPE}, {C_CT_SOMMEIL}, {C_CT_ENCOURS})
           VALUES (?, ?, 2, 0, 0.0)""",
        (code, "Production Interne")
    )
    conn.commit()


_CHAMPS_TEXTE_OPTIONNELS_TIERS = {
    "adresse":     "C_CT_ADRESSE",
    "complement":  "C_CT_COMPLEMENT",
    "code_postal": "C_CT_CODEPOSTAL",
    "ville":       "C_CT_VILLE",
    "pays":        "C_CT_PAYS",
    "contact":     "C_CT_CONTACT",
    "telephone":   "C_CT_TELEPHONE",
    "email":       "C_CT_EMAIL",
    "site":        "C_CT_SITE",
}


def _modifier_client(conn: Any, code_client: str, **kwargs) -> dict:
    """Modifie les champs d'un client dans la table tiers (sauf code)."""
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE UPPER({C_CT_NUM}) = UPPER(?)",
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
        params.append(kwargs["intitule"])  # jamais NULL : champ obligatoire
    if "sommeil" in kwargs or "validite" in kwargs:
        val = kwargs.get("sommeil", kwargs.get("validite"))
        sommeil_val = 1 if str(val).upper() in ("1", "BLOQUE", "SOMMEIL", "TRUE") else 0
        updates.append(f"{C_CT_SOMMEIL} = ?")
        params.append(sommeil_val)
    existing_cols = _get_table_columns(conn, T_TIERS)
    existing_cols_lower = [c.lower() for c in existing_cols]
    colonnes_ignorees = []

    for kwarg_key, const_name in _CHAMPS_TEXTE_OPTIONNELS_TIERS.items():
        if kwarg_key in kwargs:
            colonne = globals()[const_name]
            if colonne.lower() not in existing_cols_lower:
                colonnes_ignorees.append(colonne)
                continue
            updates.append(f"{colonne} = ?")
            params.append(_normaliser_valeur(kwargs[kwarg_key]))  # '.' → NULL

    if not updates:
        return {"statut": "ERREUR", "message": "Aucun champ à modifier"}

    params.append(code_client)
    conn.execute(
        f"UPDATE {T_TIERS} SET {', '.join(updates)} WHERE {C_CT_NUM} = ?",
        params
    )
    conn.commit()
    msg = f"Client '{code_client}' modifié avec succès"
    if colonnes_ignorees:
        msg += f"\n⚠️ Champs non enregistrés (colonnes absentes du schéma) : {', '.join(colonnes_ignorees)}"
    return {"statut": "SUCCES", "message": msg}

def _modifier_fournisseur(conn: Any, code_fournisseur: str, **kwargs) -> dict:
    """Modifie les champs d'un fournisseur dans la table tiers (sauf code)."""
    row = conn.execute(
        f"SELECT * FROM {T_TIERS} WHERE UPPER({C_CT_NUM}) = UPPER(?)",
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
    if "sommeil" in kwargs or "validite" in kwargs:
        val = kwargs.get("sommeil", kwargs.get("validite"))
        sommeil_val = 1 if str(val).upper() in ("1", "BLOQUE", "SOMMEIL", "TRUE") else 0
        updates.append(f"{C_CT_SOMMEIL} = ?")
        params.append(sommeil_val)
    elif "encours" in kwargs:
        updates.append(f"{C_CT_ENCOURS} = ?")
        params.append(kwargs["encours"])

    existing_cols = _get_table_columns(conn, T_TIERS)
    existing_cols_lower = [c.lower() for c in existing_cols]
    colonnes_ignorees = []

    for kwarg_key, const_name in _CHAMPS_TEXTE_OPTIONNELS_TIERS.items():
        if kwarg_key in kwargs:
            colonne = globals()[const_name]
            if colonne.lower() not in existing_cols_lower:
                colonnes_ignorees.append(colonne)
                continue
            updates.append(f"{colonne} = ?")
            params.append(_normaliser_valeur(kwargs[kwarg_key]))

    if not updates:
        return {"statut": "ERREUR", "message": "Aucun champ à modifier"}

    params.append(code_fournisseur)
    conn.execute(
        f"UPDATE {T_TIERS} SET {', '.join(updates)} WHERE {C_CT_NUM} = ?",
        params
    )
    conn.commit()
    msg = f"Fournisseur '{code_fournisseur}' modifié avec succès"
    if colonnes_ignorees:
        msg += f"\n⚠️ Champs non enregistrés (colonnes absentes du schéma) : {', '.join(colonnes_ignorees)}"
    return {"statut": "SUCCES", "message": msg}

def _modifier_article(conn: Any, ref_article: str, **kwargs) -> dict:
    """Modifie les champs d'un article dans la table articles (sauf ref)."""
    row = conn.execute(
        f"SELECT * FROM {T_ARTICLE} WHERE UPPER({C_AR_REF}) = UPPER(?)",
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
        f"UPDATE {T_ARTICLE} SET {', '.join(updates)} WHERE {C_AR_REF} = ?",
        params
    )
    conn.commit()
    return {"statut": "SUCCES", "message": f"Article '{ref_article}' modifié avec succès"}


def _resolve_article(conn: Any, ref_ou_nom: str) -> Optional[dict]:
    """
    Recherche dans la table articles par référence exacte, puis par
    désignation partielle.
    """
    if not ref_ou_nom:
        return None
    row = conn.execute(
        f"SELECT * FROM {T_ARTICLE} WHERE UPPER({C_AR_REF}) = UPPER(?)",
        (ref_ou_nom,)
    ).fetchone()
    if row:
        return dict(row)
    rows = conn.execute(
        f"SELECT {_top(5)}* FROM {T_ARTICLE} WHERE UPPER({C_AR_DESIGN}) LIKE UPPER(?)",
        (f"%{ref_ou_nom}%",)
    ).fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        return None
    return dict(rows[0])


def _creer_ligne_nomenclature(conn: Any, ref_parent: str, ref_composant: str, qte: float, commentaire: str = "") -> dict:
    """Ajoute une ligne de composant à la nomenclature d'un produit."""

    # ── Pré-requis trigger TG_INS_F_NOMENCLAT : l'article parent DOIT
    #    avoir AR_Nomencl = 1 (ou 4) pour que le trigger accepte l'ajout
    #    de composants. Sans ça, il lève systématiquement CB_Error 82123.
    existing_cols_art = {c.lower(): c for c in _get_table_columns(conn, T_ARTICLE)}
    if "ar_nomencl" in existing_cols_art:
        col_ar_nomencl = existing_cols_art["ar_nomencl"]
        row_parent = conn.execute(
            f"SELECT {col_ar_nomencl} FROM {T_ARTICLE} WHERE UPPER({C_AR_REF}) = UPPER(?)",
            (ref_parent,)
        ).fetchone()
        ar_nomencl_actuel = row_parent[0] if row_parent else None

        if ar_nomencl_actuel not in (1, 4):
            conn.execute(
                f"UPDATE {T_ARTICLE} SET {col_ar_nomencl} = 1 WHERE UPPER({C_AR_REF}) = UPPER(?)",
                (ref_parent,)
            )
            logger.info(f"[nomenclature] AR_Nomencl activé (=1) pour l'article parent '{ref_parent}'")

    # S'assurer que la colonne NO_Commentaire existe dans la db SQLite (pour mock)
    if not _is_mssql():
        cols = _get_table_columns(conn, NOMENCLAT_TABLE)
        if "NO_Commentaire" not in cols and "no_commentaire" not in [c.lower() for c in cols]:
            try:
                conn.execute(f"ALTER TABLE {NOMENCLAT_TABLE} ADD COLUMN NO_Commentaire TEXT")
                conn.commit()
            except Exception:
                pass

    identity_col = _table_identity_column(conn, NOMENCLAT_TABLE)
    identity_col_lower = (identity_col or "").lower()

    existing_cols = _get_table_columns(conn, NOMENCLAT_TABLE)
    existing_cols_lower = {c.lower(): c for c in existing_cols}

    # Calcul dynamique de NO_Ordre
    no_ordre = 1
    if "no_ordre" in existing_cols_lower:
        try:
            row = conn.execute(
                f"SELECT MAX({existing_cols_lower['no_ordre']}) FROM {NOMENCLAT_TABLE} WHERE {NOMENCLAT_REF_PF} = ?", 
                (ref_parent,)
            ).fetchone()
            if row and row[0] is not None:
                no_ordre = int(row[0]) + 1
        except Exception:
            pass

    valeurs = {
        NOMENCLAT_REF_PF: ref_parent,
        NOMENCLAT_REF_MP: ref_composant,
        NOMENCLAT_QTE: qte,
        "NO_Commentaire": (commentaire or "")[:69],
        "NO_Type": 1,
        "NO_Ordre": no_ordre,
        "DE_No": 1,
        "NO_Repartition": 0.0,
        "NO_SousTraitance": 0,
        "AG_No1": 0,
        "AG_No2": 0,
        "AG_No1Comp": 0,
        "AG_No2Comp": 0
    }
    
    if not identity_col:
        cbmarq = _generer_cbmarq(conn, NOMENCLAT_TABLE, "cbMarq")
        valeurs["cbMarq"] = cbmarq

    cols_insert, vals_insert = [], []
    for col_name, val in valeurs.items():
        if val is None or col_name.lower() == identity_col_lower:
            continue
        if col_name.lower().startswith("cb") and col_name.lower() != "cbmarq":
            continue
        if val == "":
            continue
        match = next((c for c in existing_cols if c.lower() == col_name.lower()), None)
        if match:
            cols_insert.append(match)
            vals_insert.append(val)
            
    placeholders = ", ".join(["?"] * len(cols_insert))
    col_names_str = ", ".join(cols_insert)
    sql = f"INSERT INTO {NOMENCLAT_TABLE} ({col_names_str}) VALUES ({placeholders})"
    
    conn.execute(sql, vals_insert)
    conn.commit()
    return {"statut": "SUCCES", "message": f"Composant {ref_composant} ajouté à la nomenclature de {ref_parent}."}


def _lire_nomenclature(conn: Any, ref_parent: str) -> list[dict]:
    """Retourne la liste des composants de la nomenclature d'un article sans faire de N+1."""
    cols = _get_table_columns(conn, NOMENCLAT_TABLE)
    has_comment = "NO_Commentaire" in cols or "no_commentaire" in cols or "NO_COMMENTAIRE" in cols
    
    select_commentaire = "N.NO_Commentaire" if has_comment else "'' AS NO_Commentaire"
    
    query = f"""
        SELECT N.{NOMENCLAT_REF_MP} AS ref_composant, 
               N.{NOMENCLAT_QTE} AS qte, 
               A.{C_AR_DESIGN} AS design_composant,
               {select_commentaire}
        FROM {NOMENCLAT_TABLE} N
        LEFT JOIN {T_ARTICLE} A ON UPPER(A.{C_AR_REF}) = UPPER(N.{NOMENCLAT_REF_MP})
        WHERE UPPER(N.{NOMENCLAT_REF_PF}) = UPPER(?)
        ORDER BY N.NO_Ordre ASC
    """
    try:
        rows = conn.execute(query, (ref_parent,)).fetchall()
        res = []
        for r in rows:
            d = dict(r)
            d["commentaire"] = d.get("NO_Commentaire") or ""
            res.append(d)
        return res
    except Exception as e:
        logger.error(f"[_lire_nomenclature] Erreur: {e}")
        return []


def _modifier_ligne_nomenclature(conn: Any, ref_parent: str, ref_composant: str, qte: float) -> dict:
    """Modifie la quantité d'un composant existant."""
    conn.execute(
        f"UPDATE {NOMENCLAT_TABLE} SET {NOMENCLAT_QTE} = ? WHERE UPPER({NOMENCLAT_REF_PF}) = UPPER(?) AND UPPER({NOMENCLAT_REF_MP}) = UPPER(?)",
        (qte, ref_parent, ref_composant)
    )
    conn.commit()
    return {"statut": "SUCCES", "message": f"Quantité mise à jour pour {ref_composant}."}


def _supprimer_ligne_nomenclature(conn: Any, ref_parent: str, ref_composant: str) -> dict:
    """Supprime un composant de la nomenclature."""
    conn.execute(
        f"DELETE FROM {NOMENCLAT_TABLE} WHERE UPPER({NOMENCLAT_REF_PF}) = UPPER(?) AND UPPER({NOMENCLAT_REF_MP}) = UPPER(?)",
        (ref_parent, ref_composant)
    )
    conn.commit()
    return {"statut": "SUCCES", "message": f"Composant {ref_composant} retiré de la nomenclature."}


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_text(value: object) -> str:
    return f"{_to_decimal(value):.2f} {CURRENCY_SYMBOL}"


def _decimal_sum(values) -> Decimal:
    return sum((_to_decimal(value) for value in values), Decimal("0.00"))


def _get_stock(conn: Any, ref_article: str, depot: int = None) -> float:
    """Lit la quantité en stock pour un article et un dépôt donnés."""
    depot = depot if depot is not None else DEPOT_DEFAUT
    row = conn.execute(
        f"SELECT {C_AS_QTESTO} FROM {T_STOCK} "
        f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
        (ref_article, depot)
    ).fetchone()
    return float(row[C_AS_QTESTO]) if row else 0.0


def _get_montant_stock(conn: Any, ref_article: str, depot: int = None) -> float:
    """Lit le montant valorisé en stock (AS_MontSto) pour un article et dépôt donnés."""
    depot = depot if depot is not None else DEPOT_DEFAUT
    existing_cols = _get_table_columns(conn, T_STOCK)
    if C_AS_MONTSTO not in existing_cols:
        return 0.0
    row = conn.execute(
        f"SELECT {C_AS_MONTSTO} FROM {T_STOCK} "
        f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
        (ref_article, depot)
    ).fetchone()
    return float(row[C_AS_MONTSTO]) if row and row[C_AS_MONTSTO] is not None else 0.0


def _lire_cmup(conn: Any, ref_article: str, depot: int = None) -> float:
    """CMUP dérivé : AS_MontSto / AS_QteSto (mécanisme natif Sage, pas de colonne dédiée)."""
    stock = _get_stock(conn, ref_article, depot)
    if stock <= 0:
        return 0.0
    montant = _get_montant_stock(conn, ref_article, depot)
    return montant / stock


def _resoudre_depot_principal(conn, ref_article: str) -> int:
    """Résout le dépôt principal d'un article via AS_Principal = 1, ou retourne DEPOT_DEFAUT.
    Si la colonne AS_Principal n'existe pas (DB mock), retourne DEPOT_DEFAUT."""
    try:
        row = conn.execute(
            f"SELECT {C_AS_DENO} FROM {T_STOCK} "
            f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_PRINCIPAL} = 1",
            (ref_article,)
        ).fetchone()
        return int(row[C_AS_DENO]) if row else DEPOT_DEFAUT
    except Exception:
        return DEPOT_DEFAUT
def _article_a_des_lots(conn, ref_article: str) -> bool:
    """
    Détection par présence réelle en base, pas par AR_SuiviStock : la
    correspondance documentée (0/1/2/3/4) ne concorde pas avec les données
    observées (SuiviStock=1 alimente F_LOTSERIE, pas 2 ; SuiviStock=5 aussi).
    """
    row = conn.execute(
        f"SELECT {_top(1)}1 FROM {T_LOT_SERIE} WHERE UPPER({C_LS_REF})=UPPER(?)",
        (ref_article,) 
    ).fetchone()
    return row is not None
def _necessite_creation_lot_au_bf(article: dict) -> bool:
    """
    Détermine si un BF doit créer un nouveau lot pour le produit fini.

    Se base sur AR_SuiviStock choisi explicitement à la création de
    l'article (valeurs 1 et 5, les deux seules confirmées par observation
    sur cette instance comme liées à une présence réelle dans F_LOTSERIE) :
      - AR_SuiviStock=1 : confirmé sur 46 lots (montres, appareils)
      - AR_SuiviStock=5 : confirmé sur 2 lots (LINGOR18)

    Contrairement à _article_a_des_lots (qui vérifie une présence déjà
    existante en base et échoue donc pour le tout premier mouvement d'un
    article neuf), cette fonction permet de créer le lot dès la première
    fabrication.

    ⚠️ Reste une déduction empirique basée sur les données observées, pas
    une documentation officielle Sage confirmée pour AR_SuiviStock.
    """
    return int(article.get(C_AR_SUIVISTOCK) or 0) in (1, 5)
def _lister_lots_disponibles(conn, ref_article: str, depot: int = None) -> list[dict]:
    """
    F_LOTSERIE est append-only (plusieurs lignes par lot : une par
    mouvement). On ne garde que la DERNIÈRE ligne par (AR_Ref, LS_NoSerie),
    déterminée par cbMarq, pour connaître l'état actuel du lot.
    """
    depot = depot if depot is not None else DEPOT_DEFAUT
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {C_LS_REF}, {C_LS_NUMERO}
                       ORDER BY {C_LS_CBMARQ} DESC
                   ) AS rn
            FROM {T_LOT_SERIE}
            WHERE UPPER({C_LS_REF}) = UPPER(?)
        )
        SELECT {C_LS_NUMERO} AS numero, {C_LS_FABRICATION} AS fabrication,
               {C_LS_PEREMPTION} AS peremption, {C_LS_QTE_RESTE} AS qte_restante,
               {C_LS_DEPOT} AS depot
        FROM DerniereLigne
        WHERE rn = 1
          AND {C_LS_DEPOT} = ?
          AND {C_LS_EPUISE} = 0
          AND {C_LS_QTE_RESTE} > 0
        ORDER BY {C_LS_FABRICATION}
    """
    rows = conn.execute(sql, (ref_article, depot)).fetchall()
    return [dict(r) for r in rows]


def _decrementer_lot(conn, numero_lot: str, ref_article: str, qte: float, num_ligne_doc: int) -> dict:
    if qte < 0:
        return {"statut": "ERREUR", "message": "Quantité négative interdite."}

    # 1. UPDATE atomique avec garde : on met à jour toutes les lignes historiques du lot
    # Cela garantit l'atomicité et empêche deux threads de consommer le même stock.
    conn.execute(
        f"""UPDATE {T_LOT_SERIE}
            SET {C_LS_QTE_RESTE} = {C_LS_QTE_RESTE} - ?
            WHERE UPPER({C_LS_REF}) = UPPER(?) AND {C_LS_NUMERO} = ?
            AND {C_LS_QTE_RESTE} >= ?""",
        (qte, ref_article, numero_lot, qte)
    )
    rowcount = conn.execute("SELECT @@ROWCOUNT").fetchone()[0]
    if rowcount == 0:
        return {"statut": "ERREUR", "message": f"Stock insuffisant ou lot '{numero_lot}' introuvable au moment du verrouillage atomique."}

    # 2. Lecture des informations pour créer la ligne de mouvement (append-only)
    limit_clause = ""
    row = conn.execute(
        f"""SELECT {_top(1)}{C_LS_QTE_INIT}, {C_LS_QTE_RESTE}, {C_LS_FABRICATION},
                    {C_LS_PEREMPTION}, {C_LS_DEPOT}, {C_LS_DL_IN}
           FROM {T_LOT_SERIE}
           WHERE UPPER({C_LS_REF})=UPPER(?) AND {C_LS_NUMERO}=?
           ORDER BY {C_LS_CBMARQ} DESC {limit_clause}""",
        (ref_article, numero_lot)
    ).fetchone()
    
    if not row:
        return {"statut": "ERREUR", "message": f"Anomalie: Lot '{numero_lot}' introuvable après UPDATE."}

    nouvelle_qte = float(row[C_LS_QTE_RESTE]) # Déjà décrémentée par l'UPDATE précédent

    epuise = 1 if nouvelle_qte <= 1e-9 else 0
    cbmarq = _generer_cbmarq(conn, T_LOT_SERIE, "cbMarq")

    identity_col = _table_identity_column(conn, T_LOT_SERIE)

    valeurs = {
        C_LS_REF: ref_article,
        C_LS_NUMERO: numero_lot,
        C_LS_FABRICATION: row[C_LS_FABRICATION],
        C_LS_PEREMPTION: row[C_LS_PEREMPTION],
        C_LS_QTE_INIT: row[C_LS_QTE_INIT],
        C_LS_QTE_RESTE: nouvelle_qte,
        C_LS_EPUISE: epuise,
        C_LS_DEPOT: row[C_LS_DEPOT],
        C_LS_DL_IN: row[C_LS_DL_IN],
        C_LS_DL_OUT: num_ligne_doc,
        C_LS_MVT: 3,
        "DE_No": 1,
        C_LS_CBMARQ: cbmarq,
    }

    existing_cols = _get_table_columns(conn, T_LOT_SERIE)

    cols_insert, vals_insert = [], []
    for col_name, val in valeurs.items():
        if val is None:
            continue
        match = next((c for c in existing_cols if c.lower() == col_name.lower()), None)
        if match:
            cols_insert.append(match)
            vals_insert.append(val)

    if identity_col:
        conn.execute(f"SET IDENTITY_INSERT {T_LOT_SERIE} ON")
    try:
        ph = ", ".join(["?"] * len(cols_insert))
        conn.execute(
            f"INSERT INTO {T_LOT_SERIE} ({', '.join(cols_insert)}) VALUES ({ph})",
            tuple(vals_insert)
        )
    finally:
        if identity_col:
            conn.execute(f"SET IDENTITY_INSERT {T_LOT_SERIE} OFF")

    return {"statut": "OK", "qte_restante": nouvelle_qte, "epuise": bool(epuise)}
def _creer_lot(conn, ref_article: str, quantite: float, depot: int, date_expiration=None, dl_no_entree: int = None) -> str:
    """Crée un nouveau lot (entrée initiale). Retourne le numéro généré."""
    numero_lot = f"LOT-{datetime.now():%Y}-{_generer_cbmarq(conn, T_LOT_SERIE, 'cbMarq'):05d}"
    cbmarq = _generer_cbmarq(conn, T_LOT_SERIE, "cbMarq")

    # cbMarq est très probablement IDENTITY en MSSQL, comme sur les autres
    # tables Sage — mais contrairement aux tables où on OMET la colonne
    # identity en MSSQL, ici on doit la FOURNIR explicitement quand
    # IDENTITY_INSERT est ON (sinon SQL Server exige une valeur).
    identity_col = _table_identity_column(conn, T_LOT_SERIE)

    valeurs = {
        C_LS_REF: ref_article,
        C_LS_NUMERO: numero_lot,
        C_LS_FABRICATION: datetime.now(),
        C_LS_PEREMPTION: date_expiration,
        C_LS_QTE_INIT: quantite,
        C_LS_QTE_RESTE: quantite,
        C_LS_EPUISE: 0,
        C_LS_DEPOT: depot,
        C_LS_DL_IN: dl_no_entree,
        C_LS_MVT: 1,
        "DE_No": 1,
        C_LS_CBMARQ: cbmarq,   # toujours inclus, IDENTITY ou pas
    }

    existing_cols = _get_table_columns(conn, T_LOT_SERIE)

    cols_insert, vals_insert = [], []
    for col_name, val in valeurs.items():
        if val is None:
            continue
        match = next((c for c in existing_cols if c.lower() == col_name.lower()), None)
        if match:
            cols_insert.append(match)
            vals_insert.append(val)

    if identity_col:
        conn.execute(f"SET IDENTITY_INSERT {T_LOT_SERIE} ON")
    try:
        ph = ", ".join(["?"] * len(cols_insert))
        conn.execute(
            f"INSERT INTO {T_LOT_SERIE} ({', '.join(cols_insert)}) VALUES ({ph})",
            tuple(vals_insert)
        )
    finally:
        if identity_col:
            conn.execute(f"SET IDENTITY_INSERT {T_LOT_SERIE} OFF")

    return numero_lot

def _ajuster_stock_db(
    conn,
    ref_article: str,
    qte: float,
    type_mouvement: str,
    motif: str = "",
    depot: int = None,
    cout_unitaire: float = None,
) -> dict:
    """
    Met à jour AS_QteSto ET AS_MontSto (valorisation), par dépôt.

    P0-3 — Verrouillage atomique :
      Pour les SORTIES, l'UPDATE inclut la condition `AS_QteSto >= qte`
      directement dans le WHERE, et on vérifie @@ROWCOUNT. Cela évite
      toute race condition entre threads sans SELECT préalable non protégé.

    cout_unitaire :
      - SORTIE : ignoré, on sort valorisé au CMUP courant
      - ENTREE : obligatoire, coût unitaire du produit qui entre
    """
    if qte < 0:
        raise ValueError("La quantité de mouvement ne peut pas être négative")
    depot = depot if depot is not None else DEPOT_DEFAUT
    type_mouvement = type_mouvement.upper()

    existing_cols = _get_table_columns(conn, T_STOCK)
    has_mont = C_AS_MONTSTO in existing_cols

    if type_mouvement == "SORTIE":
        # ── SORTIE : UPDATE atomique — le WHERE inclut la garde de stock ──
        # On lit d'abord le CMUP pour valoriser, puis on fait l'UPDATE atomique.
        # La lecture du CMUP n'est pas une condition critique : si le stock baisse
        # entre la lecture du CMUP et l'UPDATE, l'UPDATE échoue (@@ROWCOUNT = 0).
        if has_mont:
            row = conn.execute(
                f"SELECT {C_AS_QTESTO}, {C_AS_MONTSTO} FROM {T_STOCK} "
                f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
                (ref_article, depot)
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {C_AS_QTESTO} FROM {T_STOCK} "
                f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
                (ref_article, depot)
            ).fetchone()

        if row is None:
            raise ValueError(f"Article inconnu au dépôt {depot}: {ref_article}")

        stock_avant   = float(row[C_AS_QTESTO] or 0.0)
        montant_avant = float(row[C_AS_MONTSTO] or 0.0) if has_mont else 0.0
        cmup_avant    = (montant_avant / stock_avant) if stock_avant > 0 else 0.0
        nouveau_montant = montant_avant - (qte * cmup_avant)

        if has_mont:
            conn.execute(
                f"UPDATE {T_STOCK} "
                f"SET {C_AS_QTESTO} = {C_AS_QTESTO} - ?, {C_AS_MONTSTO} = ? "
                f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ? "
                f"AND {C_AS_QTESTO} >= ?",
                (qte, nouveau_montant, ref_article, depot, qte)
            )
        else:
            conn.execute(
                f"UPDATE {T_STOCK} "
                f"SET {C_AS_QTESTO} = {C_AS_QTESTO} - ? "
                f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ? "
                f"AND {C_AS_QTESTO} >= ?",
                (qte, ref_article, depot, qte)
            )

        # Vérification atomique : @@ROWCOUNT = 0 signifie stock insuffisant au moment de l'UPDATE
        rowcount = conn.execute("SELECT @@ROWCOUNT").fetchone()[0]
        if rowcount == 0:
            raise ValueError(
                f"Stock insuffisant (race condition ou stock épuisé) pour {ref_article} au dépôt {depot}. "
                f"Stock estimé avant : {stock_avant}, quantité demandée : {qte}."
            )

        nouveau_stock = stock_avant - qte
        cout_ligne = cmup_avant
        nouveau_cmup = (nouveau_montant / nouveau_stock) if (has_mont and nouveau_stock > 0) else 0.0

    else:  # ENTREE
        if cout_unitaire is None:
            raise ValueError("cout_unitaire requis pour une ENTREE")

        row = conn.execute(
            f"SELECT {C_AS_QTESTO}{', ' + C_AS_MONTSTO if has_mont else ''} FROM {T_STOCK} "
            f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
            (ref_article, depot)
        ).fetchone()

        if row is None:
            raise ValueError(f"Article inconnu au dépôt {depot}: {ref_article}")

        stock_avant   = float(row[C_AS_QTESTO] or 0.0)
        montant_avant = float(row[C_AS_MONTSTO] or 0.0) if has_mont else 0.0
        nouveau_stock   = stock_avant + qte
        nouveau_montant = montant_avant + (qte * cout_unitaire)
        cout_ligne = cout_unitaire

        if has_mont:
            conn.execute(
                f"UPDATE {T_STOCK} SET {C_AS_QTESTO} = ?, {C_AS_MONTSTO} = ? "
                f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
                (nouveau_stock, nouveau_montant, ref_article, depot)
            )
        else:
            conn.execute(
                f"UPDATE {T_STOCK} SET {C_AS_QTESTO} = ? "
                f"WHERE UPPER({C_AS_REF}) = UPPER(?) AND {C_AS_DENO} = ?",
                (nouveau_stock, ref_article, depot)
            )

        nouveau_cmup = (nouveau_montant / nouveau_stock) if (has_mont and nouveau_stock > 0) else 0.0

    return {
        "ok":          True,
        "stock_avant": stock_avant,
        "stock_apres": nouveau_stock,
        "type":        type_mouvement,
        "qte":         qte,
        "cout_ligne":  cout_ligne,
        "cmup_apres":  nouveau_cmup,
    }

def _calculer_encours_client(conn: Any, code_client: str) -> Decimal:
    return _to_decimal(conn.execute(
        f"""
        SELECT 
            (SELECT COALESCE(SUM(l.{C_DL_QTE} * l.{C_DL_PRIX}), 0.0)
             FROM {T_DOC_ENTETE} e
             JOIN {T_DOC_LIGNE} l ON l.{C_DL_PIECE} = e.{C_DO_PIECE}
             WHERE e.{C_DO_TIERS} = ? AND e.{C_DO_TYPE} = 6 AND e.{C_DO_DOMAINE} = 0)
            -
            (SELECT COALESCE(SUM(r.{C_REGL_MONTANT}), 0.0)
             FROM {T_DOC_ENTETE} e
             JOIN {T_REGLEMENTS} r ON r.{C_REGL_PIECE} = e.{C_DO_PIECE}
             WHERE e.{C_DO_TIERS} = ? AND e.{C_DO_TYPE} = 6 AND e.{C_DO_DOMAINE} = 0)
        """,
        (code_client, code_client)
    ).fetchone()[0])
def _generer_prochain_code(conn: Any, prefixe: str) -> str:
    """Génère le prochain code séquentiel disponible pour un préfixe (CLI, FOUR...).

    Filtre désormais sur le préfixe via LIKE (au lieu de fetch toute la table),
    pour éviter le scan complet de F_COMPTET à chaque appel.
    Note : cette fonction suppose un schéma préfixe+chiffres (ex CLI0001).
    Sur une base Sage réelle où les codes sont dérivés du nom (ex "BAGUES"),
    préférez _generer_code_tiers_unique().
    """
    prefixe = (prefixe or "CLI").strip().upper()
    pattern = f"{prefixe}%"

    rows = conn.execute(
        f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE UPPER({C_CT_NUM}) LIKE ?",
        (pattern,)
    ).fetchall()

    candidates = []
    for (code,) in rows:
        if not code or not isinstance(code, str):
            continue
        code_clean = code.strip()
        m = re.match(r"^([A-Za-z]+)[\s\-_]*0*(\d+)$", code_clean)
        if not m:
            continue
        pfx, digits = m.group(1), m.group(2)
        if pfx.upper() != prefixe:
            continue
        candidates.append((pfx, len(digits), int(digits), code_clean))

    logger.debug(f"[_generer_prochain_code] {len(candidates)} codes exploitables sur {len(rows)} lignes filtrées")

    if not candidates:
        width = 3
        return f"{prefixe}{1:0{width}d}"

    best = max(candidates, key=lambda c: c[2])
    next_num = best[2] + 1
    width = max(best[1], 3)
    code_final = f"{best[0]}{next_num:0{width}d}"
    logger.debug(f"[_generer_prochain_code] dernier={best[3]} → nouveau={code_final}")
    return code_final


def _generer_code_depuis_nom(nom: str, longueur: int = 8) -> str:
    """Dérive un code tiers à partir de la raison sociale, à la manière de Sage :
    majuscules, sans accents/ponctuation, tronqué."""
    nom_norm = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode("ascii")
    nom_clean = re.sub(r"[^A-Za-z0-9]", "", nom_norm).upper()
    if not nom_clean:
        nom_clean = "TIERS"
    return nom_clean[:longueur]


def _generer_code_tiers_unique(conn: Any, nom: str, longueur: int = 8) -> str:
    """Génère un code tiers unique dérivé du nom, avec suffixe numérique en cas de collision."""
    base = _generer_code_depuis_nom(nom, longueur)

    existing = conn.execute(
        f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?", (base,)
    ).fetchone()
    if not existing:
        return base

    base_courte = base[:longueur - 2] if len(base) > longueur - 2 else base
    n = 1
    while n <= 99:
        candidat = f"{base_courte}{n:02d}"
        if not conn.execute(
            f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?", (candidat,)
        ).fetchone():
            return candidat
        n += 1

    raise RuntimeError(f"Impossible de générer un code tiers unique pour '{nom}' (base '{base}' épuisée)")



def _generer_code_tiers(prefixe: str) -> dict:
    conn = _get_conn()
    try:
        return {"statut": "OK", "code": _generer_prochain_code(conn, prefixe)}
    finally:
        conn.close()
def _get_nomenclature(conn: Any, ref_article: str) -> list[dict]:
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
                  ON UPPER(a.{C_AR_REF}) = UPPER(n.{NOMENCLAT_REF_MP})
           WHERE UPPER(n.{NOMENCLAT_REF_PF}) = UPPER(?)""",
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


def _verifier_nomenclature(conn: Any, ref_article: str) -> bool:
    """Vérifie qu'une nomenclature existe pour l'article donné."""
    row = conn.execute(
        f"SELECT COUNT(*) FROM {NOMENCLAT_TABLE} WHERE UPPER({NOMENCLAT_REF_PF}) = UPPER(?)",
        (ref_article,)
    ).fetchone()
    return bool(row and row[0])


def _est_article_stocke(conn: Any, article: dict) -> bool:
    """
    True si l'article doit générer un mouvement de stock physique.
    AR_Type > 1 : service/prestation → jamais de mouvement.
    AR_Type <= 1 : article physique, SAUF s'il possède une nomenclature
    (kit/ensemble dont les composants sortent individuellement, cf. ENSHF).
    """
    ar_type = int(article.get(C_AR_TYPE) or 0)
    if ar_type > 1:
        return False
    if _verifier_nomenclature(conn, article[C_AR_REF]):
        return False
    return True


def _table_identity_column(conn: Any, table: str) -> str | None:
    """Retourne le nom de la colonne IDENTITY de table."""
    row = conn.execute(
        "SELECT c.name FROM sys.columns c "
        "WHERE c.object_id = OBJECT_ID(?) AND c.is_identity = 1", (table,)
    ).fetchone()
    return row[0] if row else None


def _inserer_document(
    conn: Any,
    type_doc: str,
    num_piece: str,
    code_client: str,
    ref_article: str = "",
    qte: float = 0.0,
    prix_unit: float = 0.0,
    montant: float = 0.0,
    num_piece_of: str = "",
    lignes: Optional[list[dict]] = None,
    date_doc: Optional[datetime | str] = None,
) -> str:
    """
    Insère dans l'entête document et les lignes document.
    DO_Domaine : 0 = vente, 1 = achat, 2 = fabrication
    DO_Type    : 3 = BL, 6 = FA, 1 = BC, 25 = OF, 26 = BF, 5 = AV
      
    num_piece_of est stocké dans la colonne référence (champ libre).
    """
    domaine = DOC_DOMAINE.get(type_doc.upper(), 0)
    do_type = DOC_TYPE.get(type_doc.upper(), 0)
    if date_doc is None:
        date_doc = datetime.now()
    elif isinstance(date_doc, str):
        try:
            from dateutil import parser as dt_parser
            date_doc = dt_parser.parse(date_doc, dayfirst=True)
        except Exception:
            try:
                date_doc = datetime.strptime(date_doc, "%d/%m/%Y")
            except Exception:
                date_doc = datetime.now()
    elif hasattr(date_doc, "date"):
        date_doc = date_doc
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
            "qte":         float(ligne.get("qte") or ligne.get("DL_Qte") or 0.0),
            "prix_unit":   float(_to_decimal(ligne.get("prix_unit") or ligne.get("DL_PrixUnitaire") or 0.0)),
            "mvt_stock":   ligne.get("mvt_stock"),
            "depot":       ligne.get("depot", DEPOT_DEFAUT),
            "prix_ru":     ligne.get("prix_ru"),
            "cmup":        ligne.get("cmup"),
            "ref_compose": ligne.get("ref_compose"),
            "ttc":         ligne.get("ttc", 0),
            "valorise":    ligne.get("valorise", 0),
            "non_livre":   ligne.get("non_livre", 0),
            "piece_bl":    ligne.get("piece_bl"),
            "qte_bl":      ligne.get("qte_bl"),
        })

    if not normalized_lignes:
        normalized_lignes = [{"ref_article": ref_article, "qte": qte, "prix_unit": float(_to_decimal(prix_unit))}]

    piece_utilisee = num_piece or _generer_num_piece(type_doc, conn)
    for _ in range(10):
        try:
            cbmarq_entete = _generer_cbmarq(conn, T_DOC_ENTETE, C_DE_CBMARQ)
            identity_col = _table_identity_column(conn, T_DOC_ENTETE)
            if identity_col:
                conn.execute(f"SET IDENTITY_INSERT {T_DOC_ENTETE} ON")
            try:
                total_ht = sum(l["qte"] * l["prix_unit"] for l in normalized_lignes)
                total_ttc = sum(l["qte"] * l["prix_unit"] * (1 + TVA_TAUX) for l in normalized_lignes)

                entete_valeurs = {
                    C_DO_PIECE: piece_utilisee,
                    C_DO_DOMAINE: domaine,
                    C_DO_TYPE: do_type,
                    C_DO_DATE: date_doc,
                    C_DO_REF: num_piece_of or None,
                    C_DO_TIERS: code_client,
                    C_DE_CBMARQ: cbmarq_entete,
                    "CT_Num": code_client,
                }

                for col_name, default_val in _DEFAULTS_DOCENTETE_CONSTANTES.items():
                    entete_valeurs[col_name] = default_val

                entete_valeurs.update({
                    "CT_NumPayeur": code_client,
                    "DO_DocType": do_type,
                    "CG_Num": _cg_num_pour_domaine(domaine),
                    "DO_TotalHT": total_ht,
                    "DO_TotalHTNet": total_ht,
                    "DO_TotalTTC": total_ttc,
                    "DO_NetAPayer": total_ttc,
                })

                existing_entete_cols = _get_table_columns(conn, T_DOC_ENTETE)
                entete_cols_insert = []
                entete_vals_insert = []
                for col_name, val in entete_valeurs.items():
                    if col_name.lower().startswith("cb") and col_name.lower() != "cbmarq":
                        continue
                    col_match = next((c for c in existing_entete_cols if c.lower() == col_name.lower()), None)
                    if col_match and val is not None:
                        entete_cols_insert.append(col_match)
                        entete_vals_insert.append(val)

                if not entete_cols_insert:
                    entete_cols_insert = [C_DO_PIECE, C_DO_DOMAINE, C_DO_TYPE, C_DO_DATE, C_DO_REF, C_DO_TIERS, C_DE_CBMARQ]
                    entete_vals_insert = [
                        piece_utilisee, domaine, do_type,
                        date_doc,
                        num_piece_of or None, code_client,
                        cbmarq_entete
                    ]

                ph_entete = ", ".join(["?"] * len(entete_cols_insert))
                sql_entete = f"INSERT INTO {T_DOC_ENTETE} ({', '.join(entete_cols_insert)}) VALUES ({ph_entete})"
                conn.execute(sql_entete, tuple(entete_vals_insert))
            finally:
                if identity_col:
                    conn.execute(f"SET IDENTITY_INSERT {T_DOC_ENTETE} OFF")
            
            identity_col_ligne = _table_identity_column(conn, T_DOC_LIGNE)
            if identity_col_ligne:
                conn.execute(f"SET IDENTITY_INSERT {T_DOC_LIGNE} ON")
            date_ligne =date_doc
            try:
                existing_ligne_cols = _get_table_columns(conn, T_DOC_LIGNE)
                for i, ligne in enumerate(normalized_lignes):
                    cbmarq_ligne = _generer_cbmarq(conn, T_DOC_LIGNE, C_DL_CBMARQ)
                    ligne_valeurs = {
                        C_DL_PIECE: piece_utilisee,
                        C_DO_DOMAINE: domaine,
                        C_DO_TYPE: do_type,
                        C_DO_DATE: date_ligne,
                        C_DL_LIGNE: (i + 1) * 1000,
                        C_DL_REF: ligne["ref_article"],
                        C_DL_QTE: ligne["qte"],
                        C_DL_PRIX: ligne["prix_unit"],
                        C_DL_PFNUM: 0,
                        C_DL_CBMARQ: cbmarq_ligne,
                        C_DL_MVTSTOCK: ligne.get("mvt_stock"),
                        C_DL_DENO: ligne.get("depot", DEPOT_DEFAUT),
                        C_DL_PRIXRU: ligne.get("prix_ru"),
                        C_DL_CMUP: ligne.get("cmup"),
                        C_DL_ARCOMPOSE: ligne.get("ref_compose"),
                        C_DL_TTC: ligne.get("ttc", 0),
                        C_DL_VALORISE: ligne.get("valorise", 0),
                        C_DL_NONLIVRE: ligne.get("non_livre", 0),
                        C_DL_PIECEBL: ligne.get("piece_bl"),
                        C_DL_QTEBL: ligne.get("qte_bl"),
                    }

                    ligne_cols_insert = []
                    ligne_vals_insert = []
                    for col_name, val in ligne_valeurs.items():
                        if col_name.lower().startswith("cb") and col_name.lower() != "cbmarq":
                            continue
                        col_match = next((c for c in existing_ligne_cols if c.lower() == col_name.lower()), None)
                        if col_match and val is not None:
                            ligne_cols_insert.append(col_match)
                            ligne_vals_insert.append(val)

                    if not ligne_cols_insert:
                        ligne_cols_insert = [C_DL_PIECE, C_DO_DOMAINE, C_DO_TYPE, C_DO_DATE, C_DL_LIGNE, C_DL_REF, C_DL_QTE, C_DL_PRIX, C_DL_PFNUM, C_DL_CBMARQ]
                        ligne_vals_insert = [
                            piece_utilisee, domaine, do_type, date_ligne,
                            (i + 1) * 1000,
                            ligne["ref_article"], ligne["qte"], ligne["prix_unit"],
                            0, cbmarq_ligne
                        ]

                    ph_ligne = ", ".join(["?"] * len(ligne_cols_insert))
                    sql_ligne = f"INSERT INTO {T_DOC_LIGNE} ({', '.join(ligne_cols_insert)}) VALUES ({ph_ligne})"
                    conn.execute(sql_ligne, tuple(ligne_vals_insert))
            finally:
                if identity_col_ligne:
                    conn.execute(f"SET IDENTITY_INSERT {T_DOC_LIGNE} OFF")
            return piece_utilisee
        except Exception:
            piece_utilisee = _generer_num_piece(type_doc, conn)
            continue
    raise Exception("Unable to allocate a unique document number")



def _formater_bloc(titre: str, champs: dict) -> str:
    lines = [f"{titre}", "──────────────────────────────────────────────────"]
    for k, v in champs.items():
        lines.append(f"  • {k:<18} : **{v}**")
    return "\n".join(lines)


def _suggestions_clients(conn: Any, terme: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_top(5)}{C_CT_NUM} AS CT_Num, {C_CT_INTITULE} AS CT_Intitule
           FROM {T_TIERS}
           WHERE UPPER({C_CT_INTITULE}) LIKE UPPER(?)""",
        (f"%{terme}%",)
    ).fetchall()
    return [{"CT_Num": r["CT_Num"], "CT_Intitule": r["CT_Intitule"]} for r in rows]


def _suggestions_articles(conn: Any, terme: str) -> list[dict]:
    rows = conn.execute(
        f"""SELECT {_top(5)}{C_AR_REF} AS AR_Ref, {C_AR_DESIGN} AS AR_Design
           FROM {T_ARTICLE}
           WHERE UPPER({C_AR_DESIGN}) LIKE UPPER(?)
              OR UPPER({C_AR_REF})    LIKE UPPER(?)""",
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
    date_doc: Optional[str] = None,
) -> dict:
    conn = _get_conn()
    try:
        dt_doc = None
        if date_doc:
            try:
                from dateutil import parser as dt_parser
                dt_doc = dt_parser.parse(date_doc, dayfirst=True)
            except Exception:
                pass

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

        # CT_Sommeil : 1 = en sommeil / bloqué, 0 = actif / valide
        sommeil_cl = int(client.get(C_CT_SOMMEIL) or 0)

        if sommeil_cl != 0:
            return {
                "statut": "CLIENT_BLOQUE",
                "message": (
                    f"🚫 Impossible de créer le BL.\n\n"
                    f"   Client '{nom_client}' ({code_reel}) est EN SOMMEIL / BLOQUÉ.\n"
                    f"   Contactez le service comptabilité.\n\n"
                    f"   ➡️  Commande : 'modifier statut client {code_reel}'"
                ),
            }

        alerte_suspect = ""

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

        # ── Contrôle stock ────────────────────────────────────────────
        if _est_article_stocke(conn, article) and stock_dispo < quantite:
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
        # ══════════ NOUVEAU : bifurcation gestion par lot ══════════
        if _article_a_des_lots(conn, ref_reelle):
            from lot_engine import allouer, Lot, _date_expiration_valide

            rows = _lister_lots_disponibles(conn, ref_reelle, DEPOT_DEFAUT)
            lots_obj = [
        Lot(numero=r["numero"], qte_disponible=float(r["qte_restante"]),
            date_expiration=_date_expiration_valide(r["peremption"]),
            date_fabrication=r["fabrication"])
                for r in rows
    ]
            strategie = "FEFO" if any(l.date_expiration for l in lots_obj) else "FIFO"
            resultat = allouer(quantite, lots_obj, strategie)

            if not resultat.ok:
                lignes_lots = "\n".join(
            f"   • {l.numero} : {l.qte_disponible} u"
            + (f" (péremption {l.date_expiration})" if l.date_expiration else "")
            for l in resultat.lots_disponibles
        )
                return {
            "statut": "STOCK_INSUFFISANT",
            "message": (
                f"📦 Stock insuffisant pour '{desig}' ({ref_reelle}) — gestion par lot.\n\n"
                f"   Demandé : {quantite} u | Disponible : {resultat.qte_allouee} u | "
                f"Manque : {resultat.manque} u\n\n   Lots disponibles :\n{lignes_lots}"
            ),
            "stock_dispo": resultat.qte_allouee, "qte_demandee": quantite,
            "manque": resultat.manque, "ref_article": ref_reelle, "code_client": code_reel,
        }

            num_bl = _generer_num_piece("BL", conn)
            lignes_bl = []
            for alloc in resultat.allocations:
                mvt = _ajuster_stock_db(conn, ref_reelle, alloc.qte, "SORTIE",
                                 motif=f"BL {num_bl} / lot {alloc.lot}")
                lignes_bl.append({
            "ref_article": ref_reelle, "qte": alloc.qte, "prix_unit": float(prix_final),
            "mvt_stock": 3, "depot": DEPOT_DEFAUT,
            "prix_ru": mvt["cout_ligne"], "cmup": mvt["cout_ligne"],
        })

            num_bl = _inserer_document(conn, "BL", num_bl, code_reel, lignes=lignes_bl)

    # Récupérer les vrais DL_No pour tracer précisément quel lot a servi quelle ligne
            lignes_inserees = conn.execute(
        f"SELECT DL_No FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE}=? ORDER BY DL_No",
        (num_bl,)
    ).fetchall()
            for alloc, ligne_doc in zip(resultat.allocations, lignes_inserees):
                _decrementer_lot(conn, alloc.lot, ref_reelle, alloc.qte,
                          num_ligne_doc=ligne_doc["DL_No"])

            conn.commit()

            lots_txt = ", ".join(f"{a.lot} ({a.qte:.0f} u)" for a in resultat.allocations)
            dispos_txt = "\n".join(f"      - {r['numero']} ({r['qte_restante']:.0f} u) exp: {r.get('peremption') or 'N/A'}" for r in rows)
            if not dispos_txt:
                dispos_txt = "      - Aucun lot disponible"
            
            message = (
        f"✅ Bon de Livraison créé (gestion par lot)\n\n"
        f"  • Numéro BL   : {num_bl}\n"
        f"  • Client      : {nom_client} ({code_reel})\n"
        f"  • Article     : {desig} ({ref_reelle})\n"
        f"  • Quantité    : {quantite} u\n"
        f"  • Lots dispos :\n{dispos_txt}\n"
        f"  • Lots choisis ({strategie}) : {lots_txt}\n"
    )
            return {
        "statut": "GENERE", "DO_Piece": num_bl, "DO_Tiers": code_reel,
        "AR_Ref": ref_reelle, "montant": montant, "message": message,
        "suggestion_facture": {
            "code_client": code_reel, "nom_client": nom_client,
            "ref_article": ref_reelle, "quantite": quantite,
            "prix_unitaire": prix_final, "montant": montant, "num_bl": num_bl,
        },
    }
# ══════════ FIN bifurcation lot — sinon comportement existant ══════════
        # ── Création BL ───────────────────────────────────────────────
        if _est_article_stocke(conn, article):
            num_bl = _generer_num_piece("BL", conn)
            mvt = _ajuster_stock_db(
                conn, ref_reelle, quantite, "SORTIE", motif=f"BL {num_bl}"
            )
            mvt_stock_val, cout_ligne = 3, mvt["cout_ligne"]
            stock_apres_aff = mvt["stock_apres"]
        else:
            num_bl = _generer_num_piece("BL", conn)
            mvt_stock_val, cout_ligne, stock_apres_aff = 0, 0.0, None

        num_bl = _inserer_document(
            conn, "BL", num_bl, code_reel,
            lignes=[{
                "ref_article": ref_reelle, "qte": quantite,
                "prix_unit": float(prix_final),
                "mvt_stock": mvt_stock_val, "depot": DEPOT_DEFAUT,
                "prix_ru": cout_ligne, "cmup": cout_ligne,
            }],
            date_doc=dt_doc
        )
        conn.commit()

        stock_apres_msg = f"{stock_apres_aff} u" if stock_apres_aff is not None else "N/A (Non stocké)"

        champs = {
            "Numéro BL": num_bl,
            "Client": f"{nom_client} ({code_reel})",
            "Article": f"{desig} ({ref_reelle})",
            "Quantité": f"{quantite} u",
            "Prix unit.": _money_text(prix_final),
            "Montant": _money_text(montant),
            "Stock après": stock_apres_msg,
        }
        message = _formater_bloc("✅ Bon de Livraison créé !", champs)
        if alerte_suspect:
            message += f"\n\n⚠️ {alerte_suspect}\n"

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_bl,
            "DO_Tiers":    code_reel,
            "AR_Ref":      ref_reelle,
            "montant":     montant,
            "stock_apres": stock_apres_aff,
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
    except Exception:
        conn.rollback()
        raise
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

        if not _verifier_nomenclature(conn, ref_reelle):
            return {
                "statut": "ERREUR",
                "message": (
                    f"🚫 Impossible de créer l'OF : l'article '{ref_reelle}' n'a pas de "
                    f"nomenclature définie dans Sage. Créez d'abord sa nomenclature "
                    f"(composants nécessaires à la fabrication) avant de lancer un ordre de fabrication."
                ),
            }

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
                
                depot_comp = _resoudre_depot_principal(conn, ref_comp)
                
                # P1: Soft Reservation check
                from reservation_engine import calculer_disponible
                stock_comp = calculer_disponible(conn, ref_comp, depot_comp)
                
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
                        "depot": depot_comp,
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

        composants_ok = list(composants_ok)
        cout_total = sum(comp["total"] for comp in composants_ok)

        lignes_of = [{
            "ref_article": ref_reelle, "qte": quantite, "prix_unit": 0.0,
            "mvt_stock": 0, "depot": DEPOT_DEFAUT,
            "ttc": 1, "valorise": 1, "non_livre": 0,
            "prix_ru": 0.0,
        }]
        for comp in composants_ok:
            lignes_of.append({
                "ref_article": comp["ref"], "qte": comp["qte"],
                "prix_unit": comp["prix"],
                "mvt_stock": 3,
                "depot": comp["depot"],
                "prix_ru": comp["prix"],
                "ref_compose": ref_reelle,
                "ttc": 1, "valorise": 1, "non_livre": 0,
            })

        num_of = _inserer_document(
            conn, "OF", "", code_client or "PROD-INT",
            lignes=lignes_of,
        )

        # P1: Enregistrer les réservations soft
        from reservation_engine import reserver_stock
        for comp in composants_ok:
            reserver_stock(conn, comp["ref"], comp["qte"], comp["depot"], num_of)

        conn.commit()

        msg_compo = (
            "\n📋 Nomenclature :\n" + "\n".join(rapport_compo) + "\n"
            if rapport_compo else ""
        )
        msg_cout = (
            f"\n💰 Coût matières estimé : {cout_total:.3f} TND\n"
            if cout_total > 0 else ""
        )


        message = (
            f"✅ Ordre de Fabrication créé !\n"
            + msg_compo + msg_cout
            + f"\n   • Numéro OF  : {num_of}\n"
            f"   • Article    : {desig} ({ref_reelle})\n"
            f"   • Quantité   : {quantite} u\n"
            f"   • Composants : "
            + ("Listés (sorties réelles à la création du BF)" if composants_ok else "N/A")
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
    except Exception:
        conn.rollback()
        raise
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
        lignes_bf = []
        
        # P1: Libération de la réservation soft OF AVANT consommation réelle
        if num_of:
            from reservation_engine import liberer_reservation
            # On libère pour le num_of (l'OF) car le stock va être décrémenté physiquement en dur
            for comp in composants:
                depot_comp = _resoudre_depot_principal(conn, comp["ref_composant"])
                liberer_reservation(conn, comp["ref_composant"], depot_comp, num_of)

        # ── 1. Sorties Composants ──
        # Liste temporaire pour retenir les allocations et les lier aux lignes de document insérées
        compo_allocations = []  # format: [(index_ligne_bf, lot, qte), ...]

        for comp in composants:
            qte = comp["qte_necessaire"] * quantite
            depot_comp = _resoudre_depot_principal(conn, comp["ref_composant"])
            ref_c = comp["ref_composant"]

            if _article_a_des_lots(conn, ref_c):
                from api.lot_engine import allouer, Lot, _date_expiration_valide
                
                rows = _lister_lots_disponibles(conn, ref_c, depot_comp)
                lots_obj = [
                    Lot(numero=r["numero"], qte_disponible=float(r["qte_restante"]),
                        date_expiration=_date_expiration_valide(r["peremption"]),
                        date_fabrication=r["fabrication"])
                    for r in rows
                ]
                strategie = "FEFO" if any(l.date_expiration for l in lots_obj) else "FIFO"
                resultat = allouer(qte, lots_obj, strategie)

                if not resultat.ok:
                    lignes_lots = "\n".join(
                        f"   • {l.numero} : {l.qte_disponible} u"
                        + (f" (péremption {l.date_expiration})" if l.date_expiration else "")
                        for l in resultat.lots_disponibles
                    )
                    return {
                        "statut": "STOCK_INSUFFISANT",
                        "message": (
                            f"📦 Stock insuffisant pour le composant '{comp['designation']}' ({ref_c}) — gestion par lot.\n\n"
                            f"   Demandé : {qte} u | Disponible : {resultat.qte_allouee} u | "
                            f"Manque : {resultat.manque} u\n\n   Lots disponibles :\n{lignes_lots}"
                        ),
                    }

                for alloc in resultat.allocations:
                    mvt = _ajuster_stock_db(
                        conn, ref_c, alloc.qte, "SORTIE",
                        motif=f"Consommation BF (OF {num_of}) / lot {alloc.lot}", depot=depot_comp
                    )
                    cout_ligne = mvt["cout_ligne"]
                    total_ligne = alloc.qte * cout_ligne
                    cout_total += total_ligne
                    
                    rapport_compo.append(
                        f"   • {comp['designation']} ({ref_c}) [Lot: {alloc.lot}]: "
                        f"-{alloc.qte:.3f} u @ {cout_ligne:.3f} TND = {total_ligne:.3f} TND"
                    )
                    
                    lignes_bf.append({
                        "ref_article": ref_c,
                        "qte": alloc.qte,
                        "prix_unit": cout_ligne,
                        "mvt_stock": 1,
                        "depot": depot_comp,
                        "prix_ru": cout_ligne,
                        "cmup": mvt["cmup_apres"]
                    })
                    # index de la ligne qu'on vient d'ajouter (+1 pour le PF qui sera inséré en index 0)
                    # wait, on insère le PF à la fin avec insert(0, ...). Donc tous les indices actuels seront décalés de +1.
                    compo_allocations.append((len(lignes_bf), alloc.lot, alloc.qte, ref_c))

            else:
                # Composant SANS lot
                mvt = _ajuster_stock_db(
                    conn, ref_c, qte, "SORTIE",
                    motif=f"Consommation BF (OF {num_of})", depot=depot_comp
                )
                
                cout_ligne = mvt["cout_ligne"]
                total_ligne = qte * cout_ligne
                cout_total += total_ligne
                
                rapport_compo.append(
                    f"   • {comp['designation']} ({ref_c}): "
                    f"-{qte:.3f} u @ {cout_ligne:.3f} TND = {total_ligne:.3f} TND"
                )
                
                lignes_bf.append({
                    "ref_article": ref_c,
                    "qte": qte,
                    "prix_unit": cout_ligne,
                    "mvt_stock": 1,
                    "depot": depot_comp,
                    "prix_ru": cout_ligne,
                    "cmup": mvt["cmup_apres"]
                })

        # ── 2. Entrée Produit Fini ──
        cout_unitaire_pf = (cout_total / quantite) if quantite > 0 else 0.0
        depot_pf = _resoudre_depot_principal(conn, ref_reelle)
        
        mvt_pf = _ajuster_stock_db(
            conn, ref_reelle, quantite, "ENTREE",
            cout_unitaire=cout_unitaire_pf,
            motif=f"Production BF (OF {num_of})", depot=depot_pf
        )

        lignes_bf.insert(0, {
            "ref_article": ref_reelle,
            "qte": quantite,
            "prix_unit": cout_unitaire_pf,
            "mvt_stock": 1,          # ← 2 → 1 : aligné avec ce qu'exige le trigger
            "depot": depot_pf,
            "prix_ru": cout_unitaire_pf,
            "cmup": mvt_pf["cmup_apres"]
        })

        # ── 3. Création Document (AVANT la création du lot !) ──
        num_bf = _inserer_document(
            conn, "BF", "", code_client or "PROD-INT",
            lignes=lignes_bf, num_piece_of=num_of
        )

        # ── 4. Récupérer les DL_No réels des lignes insérées ──
        lignes_inserees = conn.execute(
            f"SELECT DL_No FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE}=? ORDER BY DL_No",
            (num_bf,)
        ).fetchall()
        
        # Le produit fini a été inséré en index 0
        dl_no_entree_pf = lignes_inserees[0]["DL_No"] if lignes_inserees else None

        # ── 4b. Décrémentation des lots composants (P0-4) ──
        for idx, lot_num, qte_alloc, ref_c in compo_allocations:
            # idx correspond exactement à l'index final (0-based) car on a utilisé len(lignes_bf)
            # juste après avoir ajouté le composant, ce qui anticipe le décalage de l'insert(0) du PF.
            if idx < len(lignes_inserees):
                dl_no_compo = lignes_inserees[idx]["DL_No"]
                _decrementer_lot(conn, lot_num, ref_c, qte_alloc, num_ligne_doc=dl_no_compo)

        # ── 5. Création du lot (une seule fois), avec DL_NoIn correctement renseigné ──
        numero_lot_cree = None
        if _necessite_creation_lot_au_bf(article):
            numero_lot_cree = _creer_lot(
                conn, ref_reelle, quantite, depot_pf,
                dl_no_entree=dl_no_entree_pf,
            )

        conn.commit()

        message = (
            f"✅ Bon de Fabrication créé !\n\n"
            f"   • Numéro BF      : {num_bf}\n"
            + (f"   • Lié à OF       : {num_of}\n" if num_of else "")
            + f"   • Article        : {desig} ({ref_reelle})\n"
            f"   • Qté fabriquée  : {quantite} u\n"
        )
        if numero_lot_cree:
            message += f"\n\n🏷️ Lot créé : **{numero_lot_cree}** ({quantite} u)"
        if rapport_compo:
            message += (
                "\n📋 Nomenclature (sorties effectuées au CMUP) :\n"
                + "\n".join(rapport_compo)
                + (f"\n\n💰 Coût matières total : {cout_total:.3f} TND" if cout_total > 0 else "")
            )
        message += (
            "\n\n📦 Stock produit fini mis à jour :\n"
            f"   • Stock avant    : {stock_avant} u\n"
            f"   • Entrée         : +{quantite} u\n"
            f"   • Stock actuel   : {mvt_pf['stock_apres']} u"
        )

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_bf,
            "AR_Ref":      ref_reelle,
            "num_of":      num_of,
            "stock_apres": mvt_pf["stock_apres"],
            "numero_lot":  numero_lot_cree,
            "message":     message,
            "alertes":     [],
            "nomenclature": [
                {
                    "ref": comp["ref_composant"],
                    "designation": comp["designation"],
                    "qte": comp["qte_necessaire"] * quantite,
                    "prix_unitaire": comp["prix_utilise"],
                    "total": (comp["qte_necessaire"] * quantite) * comp["prix_utilise"],
                }
                for comp in composants
            ],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _generer_facture_directe(
    code_client: str,
    ref_article: str,
    qte: float,
    prix_unitaire: float,
    date_doc: Optional[str] = None,
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

        sommeil_cl = int(client.get(C_CT_SOMMEIL) or 0)
        if sommeil_cl != 0:
            return {
                "statut": "CLIENT_BLOQUE",
                "message": f"🚫 Impossible de créer la facture : client {client[C_CT_NUM]} est en sommeil / bloqué.",
            }

        prix_final = _to_decimal(prix_unitaire if prix_unitaire > 0 else float(article[C_AR_PRIXVEN] or 0.0))
        montant    = float((prix_final * Decimal(str(qte))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        stock_dispo = _get_stock(conn, article[C_AR_REF])
        if _est_article_stocke(conn, article) and stock_dispo < qte:
            return {
                "statut": "STOCK_INSUFFISANT",
                "message": f"📦 Stock insuffisant pour {article[C_AR_REF]} : dispo {stock_dispo}, demandé {qte}",
                "stock_dispo": stock_dispo,
                "qte_demandee": qte,
            }

        encours_actuel = _calculer_encours_client(conn, client[C_CT_NUM])
        

        if _est_article_stocke(conn, article):
            num_fa = _generer_num_piece("FACTURE", conn)
            mvt = _ajuster_stock_db(conn, article[C_AR_REF], qte, "SORTIE", motif=f"FACTURE {num_fa}")
            mvt_stock_val, cout_ligne = 3, mvt["cout_ligne"]
        else:
            num_fa = _generer_num_piece("FACTURE", conn)
            mvt_stock_val, cout_ligne = 0, 0.0

        num_fa = _inserer_document(
            conn, "FACTURE", num_fa,
            client[C_CT_NUM],
            lignes=[{
                "ref_article": article[C_AR_REF], "qte": qte,
                "prix_unit": float(prix_final),
                "mvt_stock": mvt_stock_val, "depot": DEPOT_DEFAUT,
                "prix_ru": cout_ligne, "cmup": cout_ligne,
            }],
            date_doc=date_doc,
        )
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
    except Exception:
        conn.rollback()
        raise
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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _workflow_bl_achat(
    code_fournisseur: str,
    ref_article: str,
    quantite: float,
    prix_unitaire: float = 0.0,
) -> dict:
    """
    Workflow Bon de Livraison fournisseur (BL Achat).
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

        if type_tiers != 1:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": (
                    f"⚠️  '{nom_four}' ({code_reel}) n'est pas un fournisseur (type {type_tiers}).\n"
                    f"   Veuillez utiliser un tiers de type fournisseur (type 1)."
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

        num_bl = _inserer_document(
            conn, "BL_ACHAT", "", code_reel,
            ref_reelle, quantite, prix_final, montant
        )
        mvt = _ajuster_stock_db(
            conn, ref_reelle, quantite, "ENTREE",
            cout_unitaire=float(prix_final),
            motif=f"Livraison BL {num_bl} / {nom_four}"
        )
        if prix_unitaire > 0:
            conn.execute(
                f"UPDATE {T_ARTICLE} SET {C_AR_PRIXACH} = ? WHERE {C_AR_REF} = ?",
                (prix_unitaire, ref_reelle)
            )
        conn.commit()

        message = (
            f"✅ Bon de Livraison Achat créé !\n\n"
            f"   • Numéro BL       : {num_bl}\n"
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
            "DO_Piece":    num_bl,
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
                "num_br":           num_bl,
            },
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _workflow_fa_achat(
    code_fournisseur: str,
    ref_article: str,
    quantite: float,
    prix_unitaire: float = 0.0,
) -> dict:
    """
    Workflow Facture d'Achat fournisseur (FA Achat).
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

        if type_tiers != 1:
            return {
                "statut": "CLIENT_NON_TROUVE",
                "message": (
                    f"⚠️  '{nom_four}' ({code_reel}) n'est pas un fournisseur (type {type_tiers}).\n"
                    f"   Veuillez utiliser un tiers de type fournisseur (type 1)."
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

        num_fa = _generer_num_piece("FA_ACHAT", conn)
        
        mvt_stock_val, cout_ligne = 0, 0.0
        if _est_article_stocke(conn, article):
            mvt = _ajuster_stock_db(
                conn, ref_reelle, quantite, "ENTREE",
                cout_unitaire=float(prix_final),
                motif=f"Achat direct FA {num_fa} / {nom_four}"
            )
            mvt_stock_val, cout_ligne = 3, mvt["cout_ligne"]

        _inserer_document(
            conn, "FA_ACHAT", num_fa, code_reel,
            lignes=[{
                "ref_article": ref_reelle, "qte": quantite,
                "prix_unit": float(prix_final),
                "mvt_stock": mvt_stock_val, "depot": DEPOT_DEFAUT,
                "prix_ru": cout_ligne, "cmup": cout_ligne,
            }]
        )
        if prix_unitaire > 0:
            conn.execute(
                f"UPDATE {T_ARTICLE} SET {C_AR_PRIXACH} = ? WHERE {C_AR_REF} = ?",
                (prix_unitaire, ref_reelle)
            )
        conn.commit()

        message = (
            f"✅ Facture d'Achat créée !\n\n"
            f"   • Numéro FA       : {num_fa}\n"
            f"   • Fournisseur     : {nom_four} ({code_reel})\n"
            f"   • Article         : {desig} ({ref_reelle})\n"
            f"   • Quantité        : {quantite} u\n"
            f"   • Prix unit.      : {prix_final:.2f} €\n"
            f"   • Montant HT      : {_money_text(montant)}\n"
            f"   • Stock avant     : {stock_avant} u\n"
            f"   • Stock après     : {stock_avant + quantite if _est_article_stocke(conn, article) else stock_avant} u\n"
        )

        return {
            "statut":      "GENERE",
            "DO_Piece":    num_fa,
            "DO_Tiers":    code_reel,
            "AR_Ref":      ref_reelle,
            "montant":     montant,
            "message":     message,
            "alertes":     [],
        }
    except Exception:
        conn.rollback()
        raise
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
        ),types.Tool(
            name="resoudre_tiers",
            description=(
                "Résout un tiers (client, fournisseur, ou tiers interne type PROD-INT) "
                "par code exact ou nom partiel, SANS contrainte de type (contrairement à "
                "lire_client/lire_fournisseur). Usage interne : valider l'existence et le "
                "statut (bloqué/sommeil) d'un tiers avant création de document."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_ou_nom": {"type": "string", "description": "Code ou nom du tiers"},
                },
                "required": ["code_ou_nom"],
            },
        ),

        types.Tool(
            name="assurer_tiers_interne",
            description=(
                "Garantit l'existence du tiers technique interne (ex: PROD-INT) utilisé "
                "comme client par défaut pour les OF/BF de fabrication. Ne fait rien si "
                "le tiers existe déjà."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client": {"type": "string",
                                    "description": "Code du tiers interne (défaut PROD-INT)",
                                    "default": "PROD-INT"},
                },
                "required": [],
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
        # ═════ NOUVEAU (v4.2) : détection de doublon PRÉCOCE, exposée en outil MCP ═════
        # Permet à l'orchestrateur d'appeler ce contrôle dès que l'intitulé est
        # connu — AVANT de lancer la collecte des champs complémentaires
        # (adresse, ville, CP, contact, tél, email...). Réutilise la même
        # fonction interne _verifier_nom_tiers_existe() que celle appelée en
        # fin de flux dans creer_nouveau_client / creer_nouveau_fournisseur.
        types.Tool(
            name="verifier_nom_tiers_existe",
            description=(
                "Vérifie si un tiers (client ou fournisseur) portant cet intitulé exact "
                "existe déjà. À appeler dès que l'intitulé est connu, avant de démarrer "
                "la collecte des champs complémentaires (adresse, contact, etc.), pour "
                "éviter de faire répondre l'utilisateur à 10+ questions pour rien."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "intitule":   {"type": "string", "description": "Nom / raison sociale à vérifier"},
                    "type_tiers": {"type": "integer", "description": "0 = client, 1 = fournisseur"},
                },
                "required": ["intitule", "type_tiers"],
            },
        ),
        # ═════ FIN NOUVEAU (v4.2) ═════
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
                    "adresse":         {"type": "string", "default": "", "description": "Adresse postale"},
                    "complement":      {"type": "string", "default": "", "description": "Complément d'adresse"},
                    "code_postal":     {"type": "string", "default": "", "description": "Code postal"},
                    "ville":           {"type": "string", "default": "", "description": "Ville"},
                    "pays":            {"type": "string", "default": "", "description": "Pays"},
                    "contact":         {"type": "string", "default": "", "description": "Contact principal"},
                    "telephone":       {"type": "string", "default": "", "description": "Téléphone"},
                    "email":           {"type": "string", "default": "", "description": "Adresse e-mail"},
                    "site":            {"type": "string", "default": "", "description": "Site web"},
                    "cg_num_princ":    {"type": "string", "default": "", "description": "Compte comptable principal (override)"},
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
                    "adresse":         {"type": "string", "default": "", "description": "Adresse postale"},
                    "complement":      {"type": "string", "default": "", "description": "Complément d'adresse"},
                    "code_postal":     {"type": "string", "default": "", "description": "Code postal"},
                    "ville":           {"type": "string", "default": "", "description": "Ville"},
                    "pays":            {"type": "string", "default": "", "description": "Pays"},
                    "contact":         {"type": "string", "default": "", "description": "Contact principal"},
                    "telephone":       {"type": "string", "default": "", "description": "Téléphone"},
                    "email":           {"type": "string", "default": "", "description": "Adresse e-mail"},
                    "site":            {"type": "string", "default": "", "description": "Site web"},
                    "cg_num_princ":    {"type": "string", "default": "", "description": "Compte comptable principal (override)"},
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
            name="lire_encours_client",
            description=(
                "Retourne l'encours actuel (CT_Encours) d'un client depuis la table F_COMPTET. "
                "CT_Encours représente l'encours en cours du client, pas un plafond. "
                "Utiliser cette action quand l'utilisateur demande : "
                "'quel est l'encours de X', 'donne-moi l'encours du client Y', etc."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client": {"type": "string",
                                   "description": "Code CT_Num ou nom du client"},
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
            description="Modifie les champs d'un client (sauf code). Astuce : envoyer '.' comme valeur pour un champ met explicitement ce champ à NULL en base.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_client":    {"type": "string", "description": "Code du client (non modifiable)"},
                    "intitule":       {"type": "string", "description": "Nouveau nom du client"},
                    "validite":       {"type": "string", "description": "Validité (VALIDE, BLOQUE, SUSPECT)"},
                    "adresse":        {"type": "string", "description": "Adresse postale ('.' = NULL)"},
                    "complement":     {"type": "string", "description": "Complément d'adresse ('.' = NULL)"},
                    "code_postal":    {"type": "string", "description": "Code postal ('.' = NULL)"},
                    "ville":          {"type": "string", "description": "Ville ('.' = NULL)"},
                    "pays":           {"type": "string", "description": "Pays ('.' = NULL)"},
                    "contact":        {"type": "string", "description": "Contact principal ('.' = NULL)"},
                    "telephone":      {"type": "string", "description": "Téléphone ('.' = NULL)"},
                    "email":          {"type": "string", "description": "E-mail ('.' = NULL)"},
                    "site":           {"type": "string", "description": "Site web ('.' = NULL)"},
                },
                "required": ["code_client"],
            },
        ),

        types.Tool(
            name="modifier_fournisseur",
            description="Modifie les champs d'un fournisseur (sauf code). Astuce : envoyer '.' comme valeur pour un champ met explicitement ce champ à NULL en base.",
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string", "description": "Code du fournisseur (non modifiable)"},
                    "intitule":         {"type": "string", "description": "Nouveau nom du fournisseur"},
                    "validite":         {"type": "string", "description": "Validité (VALIDE, BLOQUE, SUSPECT)"},
                    "adresse":          {"type": "string", "description": "Adresse postale ('.' = NULL)"},
                    "complement":       {"type": "string", "description": "Complément d'adresse ('.' = NULL)"},
                    "code_postal":      {"type": "string", "description": "Code postal ('.' = NULL)"},
                    "ville":            {"type": "string", "description": "Ville ('.' = NULL)"},
                    "pays":             {"type": "string", "description": "Pays ('.' = NULL)"},
                    "contact":          {"type": "string", "description": "Contact principal ('.' = NULL)"},
                    "telephone":        {"type": "string", "description": "Téléphone ('.' = NULL)"},
                    "email":            {"type": "string", "description": "E-mail ('.' = NULL)"},
                    "site":             {"type": "string", "description": "Site web ('.' = NULL)"},
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
    name="lister_lots_disponibles",
    description="Liste les lots disponibles (non épuisés) pour un article, triés par date de fabrication.",
    inputSchema={
        "type": "object",
        "properties": {
            "ref_article": {"type": "string"},
            "depot": {"type": "integer", "default": 1},
        },
        "required": ["ref_article"],
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
        types.Tool(
            name="workflow_fa_achat",
            description=(
                "Workflow Facture d'Achat fournisseur (achat) : "
                "vérifie le fournisseur, crée la facture d'achat, "
                "incrémente le stock s'il s'agit d'un achat direct."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "code_fournisseur": {"type": "string",
                                         "description": "Code ou nom du fournisseur"},
                    "ref_article":      {"type": "string",
                                         "description": "Référence ou désignation de l'article"},
                    "quantite":         {"type": "number",
                                         "description": "Quantité achetée"},
                    "prix_unitaire":    {"type": "number",
                                         "description": "Prix unitaire d'achat (0 = prix catalogue fournisseur)",
                                         "default": 0},
                },
                "required": ["code_fournisseur", "ref_article", "quantite"],
            },
        ),

        types.Tool(
            name="creer_article",
            description=(
                "Crée un nouvel article dans Sage 100. "
                "AR_Nature: 0=Marchandise, 1=Nomenclature, 2=Gamme, 3=Gamme+Nomenclature. "
                "AR_SuiviStock: 0=Aucun, 1=CMUP, 2=Sérialisé, 3=Lot, 4=CMUP+Lot."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_article":   {"type": "string", "description": "Référence unique de l'article"},
                    "designation":   {"type": "string", "description": "Désignation de l'article", "default": ""},
                    "prix_achat":    {"type": "number", "description": "Prix d'achat", "default": 0},
                    "prix_vente":    {"type": "number", "description": "Prix de vente", "default": 0},
                    "nature":        {"type": "integer", "description": "AR_Nature (0-3)", "default": 0},
                    "code_famille":  {"type": "string", "description": "Code famille (FA_CodeFamille)", "default": ""},
                    "unite_vente":   {"type": "integer", "description": "AR_UniteVen", "default": 1},
                    "suivi_stock":   {"type": "integer", "description": "AR_SuiviStock (0-4)", "default": 0},
                },
                "required": ["ref_article"],
            },
        ),
        types.Tool(
            name="lister_familles_articles",
            description="Liste les familles d'articles disponibles (FA_CodeFamille, FA_Intitule).",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="creer_ligne_nomenclature",
            description="Ajoute un composant à la nomenclature d'un produit parent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_parent": {"type": "string", "description": "Référence de l'article parent (NO_RefPF)"},
                    "ref_composant": {"type": "string", "description": "Référence du composant (NO_RefDet)"},
                    "qte": {"type": "number", "description": "Quantité du composant"},
                    "commentaire": {"type": "string", "description": "Commentaire de la ligne (NO_Commentaire)", "default": ""},
                },
                "required": ["ref_parent", "ref_composant", "qte"],
            },
        ),
        types.Tool(
            name="lire_nomenclature",
            description="Retourne la liste des composants de la nomenclature d'un article parent.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_parent": {"type": "string", "description": "Référence de l'article parent"},
                },
                "required": ["ref_parent"],
            },
        ),
        types.Tool(
            name="modifier_ligne_nomenclature",
            description="Modifie la quantité d'un composant existant dans la nomenclature.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_parent": {"type": "string", "description": "Référence de l'article parent"},
                    "ref_composant": {"type": "string", "description": "Référence du composant"},
                    "qte": {"type": "number", "description": "Nouvelle quantité"},
                },
                "required": ["ref_parent", "ref_composant", "qte"],
            },
        ),
        types.Tool(
            name="supprimer_ligne_nomenclature",
            description="Supprime un composant de la nomenclature.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ref_parent": {"type": "string", "description": "Référence de l'article parent"},
                    "ref_composant": {"type": "string", "description": "Référence du composant"},
                },
                "required": ["ref_parent", "ref_composant"],
            },
        ),
    ]


# ═════════════════════════════════════════════════════════════════════
# HANDLER DES APPELS D'OUTILS
# ═════════════════════════════════════════════════════════════════════

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    logger.info(f"→ [call_tool] début {name} args={arguments}")
    t0 = time.perf_counter()
    async with _db_call_lock:
        logger.info(f"  [call_tool] lock acquis pour {name} (attente: {time.perf_counter() - t0:.2f}s)")
        try:
            result = await _call_tool_impl(name, arguments)
            logger.info(f"← [call_tool] fin {name} en {time.perf_counter() - t0:.2f}s")
            return result
        except Exception as e:
            logger.exception(f"✖ [call_tool] exception dans {name} après {time.perf_counter() - t0:.2f}s")
            raise

from api.idempotency import lock_operation, complete_operation, ConcurrencyError

# Liste des outils qui écrivent des données (les lectures sont exclues de l'idempotence)
_WRITE_TOOLS = {
    "generer_document_sage", "workflow_bl", "workflow_of", "workflow_bf",
    "workflow_bl_achat", "workflow_fa_achat", "transformer_document",
    "creer_nouveau_client", "creer_nouveau_fournisseur", "creer_nouvel_article",
    "enregistrer_reglement_facture", "ajuster_stock", "creer_ligne_nomenclature",
    "modifier_client", "modifier_fournisseur", "modifier_article",
}

async def _call_tool_impl(name: str, arguments: dict) -> list[types.TextContent]:
    # ── Idempotence : pour les outils d'écriture uniquement ──
    operation_id = arguments.pop("operation_id", None)
    if name in _WRITE_TOOLS and operation_id:
        idm_conn = _get_conn()
        try:
            cached = lock_operation(idm_conn, operation_id, name, arguments)
        except ConcurrencyError as e:
            idm_conn.close()
            return _to_text({"statut": "ERREUR", "message": f"🔒 {e}"})
        except Exception:
            idm_conn.close()
            cached = None  # Table absente ou autre erreur → continuer sans idempotence

        if cached is not None:
            idm_conn.close()
            return _to_text(cached)

        # ── INTERCEPTION DU COMMIT ──
        class TxHook:
            def __init__(self, c): self.c = c
            def commit(self): pass  # L'outil métier ne peut pas commit, on le fera à la fin
            def close(self): pass   # L'outil métier ne peut pas fermer la connexion
            def cursor(self): return self.c.cursor()
            def execute(self, *a, **k): return self.c.execute(*a, **k)
            def __getattr__(self, attr): return getattr(self.c, attr)

        token = _conn_override.set(TxHook(idm_conn))
        try:
            text_result = await _call_tool_impl_inner(name, arguments)
            
            # Extraction du résultat brut pour le cache
            raw_text = text_result[0].text if text_result else "{}"
            try:
                result_dict = json.loads(raw_text)
            except Exception:
                result_dict = {"response": raw_text}
                
            # Écriture du statut COMPLETED dans la MÊME transaction
            complete_operation(idm_conn, operation_id, result_dict, status="COMPLETED")
            idm_conn.commit()  # VRAI COMMIT (Métier + Idempotence)
            
        except Exception as exc:
            try:
                # En cas d'erreur métier, la transaction est en suspens, on la rollback pour annuler l'écriture partielle
                idm_conn.rollback()
                # On marque l'opération comme FAILED dans une nouvelle mini-transaction
                complete_operation(idm_conn, operation_id, {"statut": "ERREUR", "message": str(exc)}, status="FAILED")
                idm_conn.commit()
            except Exception:
                pass
            raise
        finally:
            _conn_override.reset(token)
            idm_conn.close()

        return text_result

    # ── Pour les lectures (ou écritures sans operation_id), on applique un retry (1 max) ──
    # Si la connexion tombe pendant l'exécution d'un SELECT, il est sûr de relancer.
    max_retries = 1 if name not in _WRITE_TOOLS else 0
    for attempt in range(max_retries + 1):
        try:
            return await _call_tool_impl_inner(name, arguments)
        except Exception as e:
            err_str = str(e).lower()
            if attempt < max_retries and ("closed connection" in err_str or "communication link failure" in err_str or "08S01" in err_str):
                import logging
                logging.getLogger("sage.erp.actions").warning(f"   🔄 [Retry] Reconnexion après erreur sur {name} : {e}")
                import asyncio
                await asyncio.sleep(0.5)
                continue
            raise


async def _call_tool_impl_inner(name: str, arguments: dict) -> list[types.TextContent]:


    if name == "generer_document_sage":
        type_d        = arguments["type_doc"].upper().strip()
        code_client   = arguments.get("code_client", "")
        ref_article   = arguments["ref_article"]
        qte           = float(arguments["qte"])
        prix_unitaire = float(arguments.get("prix_unitaire", 0.0))
        num_of        = arguments.get("num_of", "")

        if type_d == "BL":
            result = _workflow_bl(code_client, ref_article, qte, prix_unitaire)
        elif type_d == "BL_ACHAT":
            result = _workflow_bl_achat(code_client, ref_article, qte, prix_unitaire)
        elif type_d == "OF":
            result = _workflow_of(ref_article, qte, code_client)
        elif type_d == "BF":
            result = _workflow_bf(ref_article, qte, num_of, code_client)
        elif type_d in ("FACTURE", "FA", "FC"):
            result = _generer_facture_directe(code_client, ref_article, qte, prix_unitaire)
        elif type_d == "FA_ACHAT":
            result = _workflow_fa_achat(code_client, ref_article, qte, prix_unitaire)
        elif type_d == "BC":
            result = _generer_bc_direct(code_client, ref_article, qte, prix_unitaire)
        else:
            result = {
                "statut":  "ERREUR",
                "message": f"❌ Type de document inconnu : '{arguments['type_doc']}'"
            }
        return _to_text(result)
    elif name == "lister_lots_disponibles":
        conn = _get_conn()
        try:
            rows = _lister_lots_disponibles(conn, arguments["ref_article"], arguments.get("depot", DEPOT_DEFAUT))
            return _to_text({"statut": "SUCCES", "lots": rows})
        finally:
            conn.close()
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

    # ═════ NOUVEAU (v4.2) ═════
    elif name == "verifier_nom_tiers_existe":
        conn = _get_conn()
        try:
            existe = _verifier_nom_tiers_existe(
                conn,
                arguments["intitule"],
                int(arguments["type_tiers"]),
            )
            result = {"statut": "OK", "existe": existe}
        finally:
            conn.close()
        return _to_text(result)
    # ═════ FIN NOUVEAU (v4.2) ═════

    elif name == "creer_nouveau_client":
        conn = _get_conn()
        try:
            code_client    = arguments["code_client"]
            intitule       = arguments["intitule"]
            ct_validite    = (arguments.get("ct_validite") or "VALIDE").upper()
            if ct_validite not in ("VALIDE", "BLOQUE", "SUSPECT"):
                ct_validite = "VALIDE"

            if _verifier_nom_tiers_existe(conn, intitule, 0):
                return _to_text({
                    "statut": "ERREUR",
                    "message": f"❌ Client '{intitule}' existe déjà. Impossible de le recréer."
                })

            existing = conn.execute(
                f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?",
                (code_client,)
            ).fetchone()
            if existing:
                original_code = code_client
                code_client = _generer_code_tiers_unique(conn, intitule)
                logger.debug(f"[creer_nouveau_client] Duplicate code '{original_code}' trouvé, régénéré → '{code_client}'")
            ct_sommeil = 1 if str(arguments.get("ct_sommeil", ct_validite)).upper() in ("1", "BLOQUE", "SOMMEIL", "TRUE") else 0
            cbmarq = _generer_cbmarq(conn, T_TIERS, C_CT_CBMARQ)

            valeurs = {
    C_CT_NUM: code_client,
    C_CT_INTITULE: intitule,
    C_CT_TYPE: 0,
    C_CT_SOMMEIL: ct_sommeil,
    C_CT_ENCOURS: 0.0,
    C_CT_CGNUMPRINC: _normaliser_valeur(arguments.get("cg_num_princ") or _CG_NUM_PAR_TYPE.get(0)),
    C_CT_ADRESSE: _normaliser_valeur(arguments.get("adresse", "")),
    C_CT_COMPLEMENT: _normaliser_valeur(arguments.get("complement", "")),
    C_CT_CODEPOSTAL: _normaliser_valeur(arguments.get("code_postal", "")),
    C_CT_VILLE: _normaliser_valeur(arguments.get("ville", "")),
    C_CT_PAYS: _normaliser_valeur(arguments.get("pays", "")),
    C_CT_CONTACT: _normaliser_valeur(arguments.get("contact", "")),
    C_CT_TELEPHONE: _normaliser_valeur(arguments.get("telephone", "")),
    C_CT_EMAIL: _normaliser_valeur(arguments.get("email", "")),
    C_CT_SITE: _normaliser_valeur(arguments.get("site", "")),
    "cbMarq": cbmarq,
}

            # Apply constant defaults
            valeurs.update(_DEFAULTS_TIERS_CONSTANTES)

            existing_cols = _get_table_columns(conn, T_TIERS)
            cols_insert, vals_insert = [], []
            colonnes_ignorees = []
            for col, val in valeurs.items():
                if isinstance(col, str) and col.lower().startswith("cb") and col.lower() != "cbmarq":
                    continue
                if val == "":
                    continue  # champ optionnel non fourni → colonne omise
                match = next((c for c in existing_cols if c.lower() == col.lower()), None)
                if not match:
                    logger.warning(
            f"[creer_nouveau_client] Colonne '{col}' absente du schéma physique "
            f"de {T_TIERS} — valeur '{val}' NON insérée."
        )
                    colonnes_ignorees.append(col)
                    continue
                cols_insert.append(match)
                vals_insert.append(val)  # None ici = NULL explicite en base (cas '.')
                   

            if not cols_insert:
                # fallback minimal insert
                cols_insert = [C_CT_NUM, C_CT_INTITULE, C_CT_TYPE, C_CT_SOMMEIL, C_CT_ENCOURS]
                vals_insert = [code_client, intitule, 0, ct_sommeil, ct_encours]

            identity_col = _table_identity_column(conn, T_TIERS)
            if identity_col:
                conn.execute(f"SET IDENTITY_INSERT {T_TIERS} ON")
            try:
                ph = ", ".join(["?"] * len(cols_insert))
                conn.execute(f"INSERT INTO {T_TIERS} ({', '.join(cols_insert)}) VALUES ({ph})", tuple(vals_insert))
            finally:
                if identity_col:
                    conn.execute(f"SET IDENTITY_INSERT {T_TIERS} OFF")

            conn.commit()
            champs_cli = {
                "Code Client": code_client,
                "Intitulé": intitule,
            }
            if arguments.get("adresse"): champs_cli["Adresse"] = arguments["adresse"]
            if arguments.get("ville"): champs_cli["Ville"] = arguments["ville"]
            if arguments.get("email"): champs_cli["Email"] = arguments["email"]
            msg = _formater_bloc("✅ Client créé avec succès !", champs_cli)
            result = {"statut": "CREE", "CT_Num": code_client, "message": msg}
            if colonnes_ignorees:
                result["message"] += f"\n\n⚠️ Champs non enregistrés (colonnes absentes du schéma) : {', '.join(colonnes_ignorees)}"
        finally:
            conn.close()
        return _to_text(result)

    elif name == "lister_familles_articles":
        conn = _get_conn()
        try:
            rows = conn.execute(f"SELECT {C_FA_CODE} AS code, {C_FA_INTITULE} AS intitule FROM {T_FAMILLE}").fetchall()
            return [types.TextContent(type="text", text=_json_dumps_safe([dict(r) for r in rows]))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"❌ Erreur: {e}")]

    elif name == "creer_article":
        conn = _get_conn()
        try:
            ref_article = arguments["ref_article"]
            designation = arguments.get("designation", "")

            # Vérifier si l'article existe déjà
            existing = conn.execute(
                f"SELECT {C_AR_REF} FROM {T_ARTICLE} WHERE UPPER({C_AR_REF}) = UPPER(?)",
                (ref_article,)
            ).fetchone()
            if existing:
                return [types.TextContent(type="text", text=f"❌ Erreur : L'article {ref_article} existe déjà.")]

            # En MSSQL, cbMarq est IDENTITY → on ne l'insère pas
            identity_col = _table_identity_column(conn, T_ARTICLE)

            def _to_int_or_none(v):
                if v is None:
                    return None
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return None

            valeurs = {
                C_AR_REF:       ref_article,
                C_AR_DESIGN:    designation,
                C_AR_PRIXACH:   float(arguments.get("prix_achat") or 0.0),
                C_AR_PRIXVEN:   float(arguments.get("prix_vente") or 0.0),
                C_AR_NATURE:    _to_int_or_none(arguments.get("nature")),
                C_AR_FAMILLE:   (arguments.get("code_famille") or ""),
                C_AR_UNITEVEN:  _to_int_or_none(arguments.get("unite_vente")),
                C_AR_SUIVISTOCK: _to_int_or_none(arguments.get("suivi_stock")),
                C_AR_TYPE:      0,   # default MARCHANDISE (SMALLINT)
            }
            if not identity_col:
                # SQLite : on peut insérer cbMarq manuellement
                cbmarq = _generer_cbmarq(conn, T_ARTICLE, "cbMarq")
                valeurs["cbMarq"] = cbmarq

            existing_cols = _get_table_columns(conn, T_ARTICLE)
            existing_cols_lower = {c.lower(): c for c in existing_cols}
            identity_col_lower = (identity_col or "").lower()

            cols_insert, vals_insert = [], []
            for col_name, val in valeurs.items():
                if val is None or col_name.lower() == identity_col_lower:
                    continue
                if val == "":
                    continue
                match = next((c for c in existing_cols if c.lower() == col_name.lower()), None)
                if match:
                    cols_insert.append(match)
                    vals_insert.append(val)
            
            placeholders = ", ".join(["?"] * len(cols_insert))
            col_names_str = ", ".join(cols_insert)
            sql = f"INSERT INTO {T_ARTICLE} ({col_names_str}) VALUES ({placeholders})"
            
            conn.execute(sql, vals_insert)
            conn.commit()
            return _to_text({
                "statut": "CREE",
                "AR_Ref": ref_article,
                "message": f"✅ Article {ref_article} créé avec succès."
            })
        except Exception as e:
            logger.error(f"[creer_article] {e}")
            return _to_text({
                "statut": "ERREUR",
                "message": f"❌ Erreur de création : {e}"
            })

    elif name == "creer_ligne_nomenclature":
        conn = _get_conn()
        try:
            result = _creer_ligne_nomenclature(
                conn, 
                arguments["ref_parent"], 
                arguments["ref_composant"], 
                arguments["qte"], 
                arguments.get("commentaire", "")
            )
            return _to_text(result)
        except Exception as e:
            logger.error(f"[creer_ligne_nomenclature] {e}")
            return _to_text({"statut": "ERREUR", "message": str(e)})

    elif name == "lire_nomenclature":
        conn = _get_conn()
        try:
            result = _lire_nomenclature(conn, arguments["ref_parent"])
            return _to_text(result)
        except Exception as e:
            logger.error(f"[lire_nomenclature] {e}")
            return _to_text({"statut": "ERREUR", "message": str(e)})

    elif name == "modifier_ligne_nomenclature":
        conn = _get_conn()
        try:
            result = _modifier_ligne_nomenclature(
                conn, 
                arguments["ref_parent"], 
                arguments["ref_composant"], 
                arguments["qte"]
            )
            return _to_text(result)
        except Exception as e:
            logger.error(f"[modifier_ligne_nomenclature] {e}")
            return _to_text({"statut": "ERREUR", "message": str(e)})

    elif name == "supprimer_ligne_nomenclature":
        conn = _get_conn()
        try:
            result = _supprimer_ligne_nomenclature(
                conn, 
                arguments["ref_parent"], 
                arguments["ref_composant"]
            )
            return _to_text(result)
        except Exception as e:
            logger.error(f"[supprimer_ligne_nomenclature] {e}")
            return _to_text({"statut": "ERREUR", "message": str(e)})

    elif name == "creer_nouveau_fournisseur":
        conn = _get_conn()
        try:
            code_fourn = arguments["code_fournisseur"]
            intitule   = arguments["intitule"]
            if _verifier_nom_tiers_existe(conn, intitule, 1):
                return _to_text({
                    "statut": "ERREUR",
                    "message": f"❌ Fournisseur '{intitule}' existe déjà. Impossible de le recréer."
                })

            existing = conn.execute(
                f"SELECT {C_CT_NUM} FROM {T_TIERS} WHERE {C_CT_NUM} = ?",
                (code_fourn,)
            ).fetchone()
            if existing:
                original_code = code_fourn
                code_fourn = _generer_code_tiers_unique(conn, intitule)
                logger.debug(f"[creer_nouveau_fournisseur] Duplicate code '{original_code}' trouvé, régénéré → '{code_fourn}'")
            cbmarq = _generer_cbmarq(conn, T_TIERS, C_CT_CBMARQ)

            valeurs = {
                C_CT_NUM: code_fourn,
                C_CT_INTITULE: intitule,
                C_CT_TYPE: 1,
                C_CT_SOMMEIL: 0,
                C_CT_ENCOURS: 0.0,
                C_CT_CGNUMPRINC: _normaliser_valeur(arguments.get("cg_num_princ") or _CG_NUM_PAR_TYPE.get(1)),
                C_CT_ADRESSE: _normaliser_valeur(arguments.get("adresse", "")),
                C_CT_COMPLEMENT: _normaliser_valeur(arguments.get("complement", "")),
                C_CT_CODEPOSTAL: _normaliser_valeur(arguments.get("code_postal", "")),
                C_CT_VILLE: _normaliser_valeur(arguments.get("ville", "")),
                C_CT_PAYS: _normaliser_valeur(arguments.get("pays", "")),
                C_CT_CONTACT: _normaliser_valeur(arguments.get("contact", "")),
                C_CT_TELEPHONE: _normaliser_valeur(arguments.get("telephone", "")),
                C_CT_EMAIL: _normaliser_valeur(arguments.get("email", "")),
                C_CT_SITE: _normaliser_valeur(arguments.get("site", "")),
                "cbMarq": cbmarq,
            }
            valeurs.update(_DEFAULTS_TIERS_CONSTANTES)

            existing_cols = _get_table_columns(conn, T_TIERS)
            cols_insert, vals_insert = [], []
            colonnes_ignorees = []
            for col, val in valeurs.items():
                if isinstance(col, str) and col.lower().startswith("cb") and col.lower() != "cbmarq":
                    continue
                if val == "":
                    continue
                match = next((c for c in existing_cols if c.lower() == col.lower()), None)
                if not match:
                    logger.warning(
                        f"[creer_nouveau_fournisseur] Colonne '{col}' absente du schéma physique "
                        f"de {T_TIERS} — valeur '{val}' NON insérée."
                    )
                    colonnes_ignorees.append(col)
                    continue
                cols_insert.append(match)
                vals_insert.append(val)

            if not cols_insert:
                cols_insert = [C_CT_NUM, C_CT_INTITULE, C_CT_TYPE, C_CT_SOMMEIL, C_CT_ENCOURS]
                vals_insert = [code_fourn, intitule, 1, 0, 0.0]

            identity_col = _table_identity_column(conn, T_TIERS)
            if identity_col:
                conn.execute(f"SET IDENTITY_INSERT {T_TIERS} ON")
            try:
                ph = ", ".join(["?"] * len(cols_insert))
                conn.execute(f"INSERT INTO {T_TIERS} ({', '.join(cols_insert)}) VALUES ({ph})", tuple(vals_insert))
            finally:
                if identity_col:
                    conn.execute(f"SET IDENTITY_INSERT {T_TIERS} OFF")

            conn.commit()
            champs_fourn = {
                "Code Fournisseur": code_fourn,
                "Intitulé": intitule,
            }
            if arguments.get("adresse"): champs_fourn["Adresse"] = arguments["adresse"]
            if arguments.get("ville"): champs_fourn["Ville"] = arguments["ville"]
            if arguments.get("email"): champs_fourn["Email"] = arguments["email"]
            msg = _formater_bloc("✅ Fournisseur créé avec succès !", champs_fourn)
            result = {"statut": "CREE", "CT_Num": code_fourn, "message": msg}
            if colonnes_ignorees:
                result["message"] += f"\n\n⚠️ Champs non enregistrés (colonnes absentes du schéma) : {', '.join(colonnes_ignorees)}"
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
                valeur_sommeil = (
                    1
                    if nouveau_statut in ("BLOQUE", "SOMMEIL", "1", "TRUE")
                    else 0
                )
                conn.execute(
                    f"UPDATE {T_TIERS} SET {C_CT_SOMMEIL} = ? WHERE {C_CT_NUM} = ?",
                    (valeur_sommeil, client[C_CT_NUM])
                )
                conn.commit()
                statut_lbl = "EN SOMMEIL / BLOQUÉ" if valeur_sommeil == 1 else "ACTIF / VALIDE"
                result = {
                    "statut":  "MODIFIE",
                    "message": (
                        f"✅ Statut de '{client[C_CT_INTITULE]}' "
                        f"({client[C_CT_NUM]}) → {statut_lbl}."
                    ),
                }
        finally:
            conn.close()
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
                valeur_sommeil = (
                    1
                    if nouveau_statut in ("BLOQUE", "SOMMEIL", "1", "TRUE")
                    else 0
                )
                conn.execute(
                    f"UPDATE {T_TIERS} SET {C_CT_SOMMEIL} = ? WHERE {C_CT_NUM} = ?",
                    (valeur_sommeil, fournisseur[C_CT_NUM])
                )
                conn.commit()
                statut_lbl = "EN SOMMEIL / BLOQUÉ" if valeur_sommeil == 1 else "ACTIF / VALIDE"
                result = {
                    "statut":  "MODIFIE",
                    "message": (
                        f"✅ Statut de '{fournisseur[C_CT_INTITULE]}' "
                        f"({fournisseur[C_CT_NUM]}) → {statut_lbl}."
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

            _TRANSFORMATIONS_AUTORISEES = {
                ("BC", "BL"):        {"mouvement_stock": True},
                ("BL", "FACTURE"):   {"mouvement_stock": False},
                ("OF", "BF"):        {"mouvement_stock": True},
                ("FACTURE", "AVOIR"): {"mouvement_stock": False},
            }

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
                # Retrouver le type_source sous forme de chaîne (ex: 'BC')
                type_source = "INCONNU"
                for code_str, (do_type, do_domaine) in DOC_CODES.items():
                    if entete[C_DO_TYPE] == do_type and entete[C_DO_DOMAINE] == do_domaine:
                        type_source = code_str
                        break
                
                # Normalisation des alias
                if type_source in ("FA", "FC"): type_source = "FACTURE"
                if type_destination in ("FA", "FC"): type_destination = "FACTURE"
                if type_source == "AV": type_source = "AVOIR"
                if type_destination == "AV": type_destination = "AVOIR"

                transition = (type_source, type_destination)
                if transition not in _TRANSFORMATIONS_AUTORISEES:
                    result = {
                        "statut": "TRANSITION_INTERDITE",
                        "message": f"❌ Transformation de {type_source} vers {type_destination} non supportée par cette action.",
                    }
                    conn.close()
                    return _to_text(result)

                existing = conn.execute(
                    f"SELECT {C_DO_PIECE} FROM {T_DOC_ENTETE} WHERE {C_DO_REF} = ? AND {C_DO_TYPE} = ? AND {C_DO_DOMAINE} = ?",
                    (num_piece_source, DOC_TYPE.get(type_destination, 0), DOC_DOMAINE.get(type_destination, 0)),
                ).fetchone()
                if existing:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": f"⚠️  Le document source '{num_piece_source}' a déjà été transformé en {type_destination} ({existing[C_DO_PIECE]}).",
                    }
                    conn.close()
                    return _to_text(result)

                doit_mouvementer = _TRANSFORMATIONS_AUTORISEES[transition]["mouvement_stock"]
                mvt_val = None
                if doit_mouvementer:
                    if type_destination in DOC_DESTOCKANTS:
                        mvt_val = 3
                    elif type_destination in DOC_STOCKANTS:
                        mvt_val = 1

                lignes_source = conn.execute(
                    f"SELECT * FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE} = ? ORDER BY {C_DL_LIGNE}",
                    (num_piece_source,)
                ).fetchall()
                lignes_dest = [{
                    "ref_article": ligne[C_DL_REF],
                    "qte": float(ligne[C_DL_QTE]),
                    "prix_unit": float(ligne[C_DL_PRIX]),
                    "mvt_stock": mvt_val,
                    "depot": ligne.get(C_DL_DENO, DEPOT_DEFAUT),
                    "piece_bl": num_piece_source if type_destination in {"FACTURE", "FA_ACHAT"} else None,
                    "qte_bl":   float(ligne[C_DL_QTE]) if type_destination in {"FACTURE", "FA_ACHAT"} else None,
                } for ligne in lignes_source]

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
                if entete[C_DO_TYPE] not in (6, 7) or entete[C_DO_DOMAINE] != 0:
                    result = {
                        "statut": "TYPE_INVALIDE",
                        "message": f"❌ Le document '{num_facture_origine}' n'est pas une facture de vente (DO_Type={entete.get(C_DO_TYPE)}, DO_Domaine={entete.get(C_DO_DOMAINE)})."
                    }
                    conn.close()
                    return _to_text(result)

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
                    f"SELECT {C_DO_PIECE} FROM {T_DOC_ENTETE} WHERE {C_DO_REF} = ? AND {C_DO_TYPE} = 5 AND {C_DO_DOMAINE} = 0",
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
            num_piece             = arguments["num_piece"]
            mode_paiement         = arguments.get("mode_paiement", "Virement")
            numero_piece_paiement = arguments.get("numero_piece_paiement", "") or ""
            montant_arg           = arguments.get("montant")  # None = tout le solde

            _MODE_REGLEMENT_MAP = {
                "especes":  0, "espèces": 0, "espece": 0, "espèce": 0, "cash": 0,
                "cheque":   1, "chèque":   1, "cheques": 1, "chèques": 1,
                "virement": 2, "virements": 2,
                "cb":       3, "carte":     3, "cartebancaire": 3,
                "traite":   4, "traites":   4, "effet": 4,
                "lc":       5, "lcr":       5,
                "prelevement": 6, "prélèvement": 6,
            }
            _mode_key    = mode_paiement.lower().replace(" ", "").replace("_", "")
            dr_type_regl = _MODE_REGLEMENT_MAP.get(_mode_key, 2)  # défaut: Virement

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
                # Vérification : on ne règle que des factures de vente (DO_Type=6 ou 7, DO_Domaine=0)
                if entete[C_DO_TYPE] not in (6, 7) or entete[C_DO_DOMAINE] != 0:
                    result = {
                        "statut": "TYPE_INVALIDE",
                        "message": (
                            f"❌ '{num_piece}' n'est pas une facture de vente "
                            f"(DO_Type={entete.get(C_DO_TYPE)}, DO_Domaine={entete.get(C_DO_DOMAINE)})."
                        ),
                    }
                    conn.close()
                    return _to_text(result)

                # Calcul du montant total de la facture
                lignes = conn.execute(
                    f"SELECT {C_DL_QTE}, {C_DL_PRIX} FROM {T_DOC_LIGNE} WHERE {C_DL_PIECE} = ?",
                    (num_piece,)
                ).fetchall()
                montant_total = float(_decimal_sum(
                    (Decimal(str(l[C_DL_QTE])) * Decimal(str(l[C_DL_PRIX])) for l in lignes)
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                # Montant déjà réglé (somme de tous les règlements existants)
                deja_regle_row = conn.execute(
                    f"SELECT COALESCE(SUM({C_REGL_MONTANT}), 0) FROM {T_REGLEMENTS} WHERE {C_REGL_PIECE} = ?",
                    (num_piece,)
                ).fetchone()
                deja_regle = float(deja_regle_row[0]) if deja_regle_row else 0.0
                solde_restant = round(montant_total - deja_regle, 2)

                if solde_restant <= 0:
                    result = {
                        "statut":  "EXISTE_DEJA",
                        "message": (
                            f"⚠️  La facture '{num_piece}' est déjà soldée "
                            f"(Total: {_money_text(montant_total)}, Réglé: {_money_text(deja_regle)})."
                        ),
                    }
                    conn.close()
                    return _to_text(result)

                # Montant à régler maintenant
                if montant_arg is not None:
                    montant_reglement = round(float(_to_decimal(montant_arg)), 2)
                    if montant_reglement <= 0:
                        result = {"statut": "ERREUR", "message": "❌ Le montant du règlement doit être positif."}
                        conn.close()
                        return _to_text(result)
                    if montant_reglement > solde_restant + 0.005:
                        result = {
                            "statut": "MONTANT_DEPASSE",
                            "message": (
                                f"❌ Montant demandé ({_money_text(montant_reglement)}) "
                                f"dépasse le solde restant ({_money_text(solde_restant)})."
                            ),
                        }
                        conn.close()
                        return _to_text(result)
                else:
                    montant_reglement = solde_restant

                est_solde = abs(montant_reglement - solde_restant) < 0.005

                # Mise à jour de la référence de l'entête si paiement intégral
                if est_solde:
                    conn.execute(
        f"UPDATE {T_DOC_ENTETE} SET DO_Cloture = 1 WHERE {C_DO_PIECE} = ?",
        (num_piece,)
    )

                _do_domaine  = entete[C_DO_DOMAINE]
                _do_type     = entete[C_DO_TYPE]
                _regl_cbmarq = _generer_cbmarq(conn, T_REGLEMENTS, C_REGL_CBMARQ)
                identity_col_regl = _table_identity_column(conn, T_REGLEMENTS)
                if identity_col_regl:
                    conn.execute(f"SET IDENTITY_INSERT {T_REGLEMENTS} ON")
                try:
                    # Construire l'INSERT dynamiquement pour gérer DR_Reference optionnel
                    regl_cols = [
                        C_REGL_PIECE, C_REGL_DOMAINE, C_REGL_TYPE_DOC,
                        C_REGL_CBMARQ, C_REGL_MODE_PAI, C_REGL_MONTANT, C_REGL_DATE,
                    ]
                    regl_vals = [
                        num_piece, _do_domaine, _do_type,
                        _regl_cbmarq, dr_type_regl, montant_reglement, datetime.now(),
                    ]
                    if numero_piece_paiement:
                        existing_regl_cols = _get_table_columns(conn, T_REGLEMENTS)
                        if C_REGL_REFERENCE and any(c.lower() == C_REGL_REFERENCE.lower() for c in existing_regl_cols):
                            regl_cols.append(C_REGL_REFERENCE)
                            regl_vals.append(str(numero_piece_paiement)[:20])
                        else:
                            # Colonne DR_Reference absente du schéma : on log un WARNING
                            # explicite plutôt que de perdre la référence silencieusement.
                            logger.warning(
                                "[P0-6] Colonne '%s' (DR_Reference) absente de %s — "
                                "n° pièce paiement '%s' NON ENREGISTRÉ. "
                                "Migration manquante ? (ALTER TABLE %s ADD %s NVARCHAR(20))",
                                C_REGL_REFERENCE, T_REGLEMENTS, numero_piece_paiement,
                                T_REGLEMENTS, C_REGL_REFERENCE,
                            )
                            # On stocke l'avertissement pour le renvoyer à l'utilisateur
                            _ref_non_stockee = numero_piece_paiement
                    else:
                        _ref_non_stockee = None
                    ph = ", ".join(["?"] * len(regl_cols))
                    conn.execute(
                        f"INSERT INTO {T_REGLEMENTS} ({', '.join(regl_cols)}) VALUES ({ph})",
                        regl_vals
                    )
                finally:
                    if identity_col_regl:
                        conn.execute(f"SET IDENTITY_INSERT {T_REGLEMENTS} OFF")

                conn.commit()
                statut_paiement = "SOLDE" if est_solde else "PARTIEL"
                _solde_apres = round(solde_restant - montant_reglement, 2)
                result = {
    "statut":                statut_paiement,
    "DO_Piece":               num_piece,
    "montant_regle":          montant_reglement,
    "solde_restant":          _solde_apres,
    "montant_total_facture":  montant_total,
    "mode_paiement":          mode_paiement,
    "numero_piece_paiement":  numero_piece_paiement or "",
    "facture_soldee":         est_solde,
    "DO_Cloture":             1 if est_solde else 0,
    "message": (
        f"✅ Règlement enregistré !\n"
        f"   • Document        : {num_piece}\n"
        f"   • Montant réglé   : {_money_text(montant_reglement)}\n"
        f"   • Solde restant   : {_money_text(_solde_apres)}\n"
        f"   • Mode            : {mode_paiement}"
        + (f"\n   • N° pièce        : {numero_piece_paiement}" if numero_piece_paiement else "")
        + (f"\n   ✅ Facture entièrement soldée." if est_solde else "")
        + (
            f"\n\n   ⚠️  ATTENTION : le n° de pièce paiement '{_ref_non_stockee}' "
            f"n'a PAS pu être enregistré (colonne {C_REGL_REFERENCE} absente "
            f"de {T_REGLEMENTS}). Migration requise."
            if _ref_non_stockee else ""
        )
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

    elif name == "workflow_fa_achat":
        result = _workflow_fa_achat(
            arguments["code_fournisseur"],
            arguments["ref_article"],
            float(arguments["quantite"]),
            float(arguments.get("prix_unitaire", 0.0)),
        )
        return _to_text(result)

    elif name == "lire_client":
        conn = _get_conn()
        try:
            result = _lire_client(conn, arguments["code_client"])
        finally:
            conn.close()
        return _to_text(result)

    elif name == "lire_encours_client":
        conn = _get_conn()
        try:
            code_ou_nom = arguments["code_client"]
            row, candidats = _resolve_client_with_suggestions(conn, code_ou_nom)
            if not row:
                if candidats:
                    result = {
                        "statut": "AMBIGU",
                        "message": (
                            f"⚠️ '{code_ou_nom}' correspond à {len(candidats)} clients. "
                            f"Précisez le code exact (CT_Num)."
                        ),
                        "suggestions": candidats,
                    }
                else:
                    result = {"statut": "ERREUR", "message": f"❌ Client '{code_ou_nom}' non trouvé."}
            elif row.get(C_CT_TYPE) != 0:
                result = {"statut": "ERREUR", "message": f"'{code_ou_nom}' n'est pas un client."}
            else:
                encours = row.get(C_CT_ENCOURS, 0.0) or 0.0
                result = {
                    "statut": "SUCCES",
                    "CT_Num": row[C_CT_NUM],
                    "CT_Intitule": row[C_CT_INTITULE],
                    "CT_Encours": encours,
                    "message": (
                        f"📊 Encours actuel de {row[C_CT_INTITULE]} ({row[C_CT_NUM]}) : "
                        f"{encours:,.2f} DT"
                    ),
                }
        finally:
            conn.close()
        return _to_text(result)

    elif name == "lire_fournisseur":
        conn = _get_conn()
        try:
            result = _lire_fournisseur(conn, arguments["code_fournisseur"])
        finally:
            conn.close()
        return _to_text(result)

    elif name == "lire_article":
        conn = _get_conn()
        try:
            result = _lire_article(conn, arguments["ref_article"])
        finally:
            conn.close()
        return _to_text(result)

    elif name == "modifier_client":
        conn = _get_conn()
        try:
            kwargs = {}
            for champ in ("intitule", "validite",
                          
                          "adresse", "complement", "code_postal", "ville",
                          "pays", "contact", "telephone", "email", "site"):
                if champ in arguments:
                    kwargs[champ] = arguments[champ]
            result = _modifier_client(conn, arguments["code_client"], **kwargs)
        finally:
            conn.close()
        return _to_text(result)

    elif name == "modifier_fournisseur":
        conn = _get_conn()
        try:
            kwargs = {}
            for champ in ("intitule", "validite",
                          "adresse", "complement", "code_postal", "ville",
                          "pays", "contact", "telephone", "email", "site"):
                if champ in arguments:
                    kwargs[champ] = arguments[champ]
            result = _modifier_fournisseur(conn, arguments["code_fournisseur"], **kwargs)
        finally:
            conn.close()
        return _to_text(result)

    elif name == "modifier_article":
        conn = _get_conn()
        try:
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
        finally:
            conn.close()
        return _to_text(result)
    elif name == "resoudre_tiers":
        conn = _get_conn()
        try:
            row, candidats = _resolve_client_with_suggestions(conn, arguments["code_ou_nom"])
            if not row:
                if candidats:
                    result = {
                        "statut": "AMBIGU",
                        "message": (
                            f"⚠️ '{arguments['code_ou_nom']}' correspond à {len(candidats)} tiers différents. "
                            f"Précisez le code exact (CT_Num)."
                        ),
                        "suggestions": candidats,
                    }
                else:
                    result = {
                        "statut": "ERREUR",
                        "message": f"Tiers '{arguments['code_ou_nom']}' non trouvé",
                    }
            else:
                result = {
                    "statut":      "SUCCES",
                    "CT_Num":      row[C_CT_NUM],
                    "CT_Intitule": row[C_CT_INTITULE],
                    "CT_Type":     row.get(C_CT_TYPE, 0),
                    "CT_Sommeil":  row.get(C_CT_SOMMEIL, 0),
                    "CT_Encours":  row.get(C_CT_ENCOURS, 0),
                }
        finally:
            conn.close()
        return _to_text(result)

    elif name == "assurer_tiers_interne":
        conn = _get_conn()
        try:
            code = (arguments.get("code_client") or "PROD-INT").upper()
            _assurer_tiers_interne(conn, code)
            result = {"statut": "OK", "code_client": code}
        except Exception as e:
            import traceback
            tb_text = traceback.format_exc()
            try:
                with open("mcp_actions_debug.log", "a", encoding="utf-8") as f:
                    f.write(f"\n--- assurer_tiers_interne ({code}) ---\n{tb_text}\n")
            except Exception:
                pass
            result = {"statut": "ERREUR", "message": f"Échec création tiers interne : {e}"}
        finally:
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