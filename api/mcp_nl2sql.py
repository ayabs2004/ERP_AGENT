#!/usr/bin/env python3
"""
nl2sql_server.py — Serveur MCP NL2SQL Sage 100 v4.3
====================================================
v4.1 : CORRECTIF DE NEUTRALITÉ DB.
       v4.0 n'avait neutralisé (via adaptation/db_adapter.py + db_config.json)
       QUE le moteur de patterns NL->SQL (_NL_PATTERNS, _sql_*, _generer_sql_generique).
       Tous les "OUTILS MÉTIER" (lister_*, analyser_*, calculer_*, generer_*,
       verifier_*, rechercher_*, exporter_*, detecter_*) utilisaient encore des
       noms de tables/colonnes Sage 100 codés en dur (F_DOCENTETE, CT_Num,
       DO_Piece, DL_Qte, AR_Ref, ...). Ce fichier remplace TOUTES ces occurrences
       par des appels table()/col(), y compris pour l'accès aux lignes de résultat
       (via des alias AS <nom_logique> pour ne plus dépendre du nom physique réel).

v4.2 : CORRECTIF BUGS SILENCIEUX — f-string + .format() différé.
       Plusieurs patterns de _NL_PATTERNS construisaient leur SQL avec un
       f-string contenant un placeholder (ex: `{seuil}`, `{n}`, `{mois}`,
       `{code}`, `{an}`, `{filtre}`) censé être rempli par un `.format(...)`
       appliqué APRÈS coup sur le résultat déjà évalué du f-string.
       Or un f-string évalue tous ses `{...}` IMMÉDIATEMENT, donc `{seuil}`
       (ou `{n}`, `{mois}`, `{code}`, `{an}`, `{filtre}`) levait une
       `NameError` avant même que `.format()` ne s'exécute. Cette exception
       était avalée silencieusement par le `try/except: continue` de
       `interpreter_et_analyser_via_sql`, faisant retomber le moteur sur le
       pattern suivant ou sur le fallback générique — d'où des filtres
       ignorés (prix, quantité, mois, fournisseur, année...) sans aucune
       erreur visible.
       Fix : toutes les valeurs sont maintenant calculées AVANT et injectées
       directement dans le f-string (soit inline, soit via une petite
       fonction génératrice dédiée pour les cas les plus complexes),
       sans jamais passer par un `.format()` différé.
       Un pattern "marge brute sur un article précis" a aussi été ajouté
       (le pattern générique existant ignorait la référence article demandée).

v4.3 : CORRECTIF CONFUSION type=3 / type=6 (factures de vente vs BL) — AUDIT COMPLET.
       Schéma réel confirmé par le docstring Vanna (orchestrateur_general.py) :
           type=6  domaine=0 → factures de vente
           type=3  domaine=0 → bons de livraison (BL)
           type=1  domaine=0 → bons de commande client (BC)
           type=25 domaine=2 → ordres de fabrication (OF)
           type=26 domaine=2 → bons de fabrication (BF)
           type=5  domaine=0 → avoirs de vente
       Une grande partie du moteur analytique (CA, DSO, top clients, RFM,
       palmarès/rentabilité articles, saisonnalité, KPI, balance âgée,
       clients actifs/bloqués/inactifs, détection de baisse de CA,
       déclaration fiscale...) utilisait encore `type = 3` pour désigner
       des "factures", alors que `lister_toutes_factures` /
       `lister_toutes_factures_client` (déjà correctes) utilisent bien
       `type = 6`. Résultat : ces analyses calculaient silencieusement sur
       des BONS DE LIVRAISON au lieu des factures — chiffres plausibles en
       apparence mais faux (ou vides si peu de BL existent).
       Fix : remplacement de `type = 3 AND domaine = 0` par
       `type = 6 AND domaine = 0` dans toutes les fonctions/patterns qui
       calculent réellement sur des factures de vente. Les patterns
       explicitement dédiés aux BL ("Liste des bons de livraison", "BL non
       facturés") restent inchangés (type = 3), de même que les patterns
       BC (type=1), OF (type=25) et BF (type=26) qui étaient déjà corrects.
       `selectionner_documents_par_periode` utilisait en plus un
       `_TYPE_MAP` totalement erroné (FACTURE→3, BC→6, BL→2 inexistant,
       AV→9 inexistant, OF→1, BF→4) sans filtre de domaine. Remplacé par
       `_TYPE_DOMAINE_MAP` aligné sur le schéma réel, avec filtre domaine.
       ⚠️ Côté FOURNISSEUR (domaine=1), plusieurs fonctions utilisent encore
       des codes divergents pour "facture fournisseur" (16, 6, 3 selon
       l'endroit) — ceci n'a PAS été corrigé ici faute de confirmation sur
       le bon code, et fait l'objet d'un audit séparé (voir commentaires
       "AUDIT FOURNISSEUR" ci-dessous). Ne pas supposer que ces valeurs
       sont correctes.
"""
import json
import os
import re
import sqlite3
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from datetime import datetime, timedelta

try:
    import sqlglot
    from sqlglot import exp
except Exception:
    sqlglot = None
    exp = None

# ── sys.path: permet d'importer adaptation.db_adapter et api.declaration
# que le script soit lancé depuis la racine ou depuis api/ (subprocess MCP)
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_API_DIR = Path(__file__).parent
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

# Patch json.dumps pour Decimal (valeurs NUMERIC de SQL Server)
_json_dumps_orig = json.dumps
def _json_dumps_safe(obj, **kwargs):
    kwargs.setdefault("default", lambda o: float(o) if isinstance(o, Decimal) else str(o))
    return _json_dumps_orig(obj, **kwargs)
json.dumps = _json_dumps_safe

def _f(v) -> float:
    """Coerce pyodbc Decimal (or None) to plain float safely.
    Prevents 'Decimal * float' TypeError when SQL Server returns NUMERIC columns."""
    return float(v) if v is not None else 0.0

from declaration import generer_declaration_mensuelle_excel as _gen_decla
from mcp.server.fastmcp import FastMCP
from adaptation.db_adapter import table, col, cols, get_connection, placeholder

mcp = FastMCP("nl2sql")


class _ConnexionCorrigee:
    """Wrapper transparent pour corriger automatiquement les GROUP BY MSSQL.
    Il délègue tous les attributs au vrai objet conn, mais corrige chaque
    `execute()` avant exécution.
    """

    def __init__(self, conn_reelle):
        self._conn = conn_reelle

    def execute(self, sql, params=()):
        sql_corrige = _corriger_group_by_mssql(sql)
        try:
            return self._conn.execute(sql_corrige, params)
        except Exception as e:
            m = _RX_ERREUR_GROUPBY_MSSQL.search(str(e)) if _RX_ERREUR_GROUPBY_MSSQL else None
            if m and _is_mssql():
                col_manquante = m.group(1)
                sql_retry = re.sub(
                    r'(GROUP BY .+?)(\s+ORDER BY|\s+HAVING|$)',
                    rf'\1, {col_manquante}\2',
                    sql_corrige,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                return self._conn.execute(sql_retry, params)
            raise

    def __getattr__(self, name):
        return getattr(self._conn, name)


# ─────────────────────────────────────────────────────────────────────
# CONNEXION DB — utilise db_adapter.get_connection()
# ─────────────────────────────────────────────────────────────────────
def _connect():
    """Connexion DB via db_adapter (sqlite ou mssql selon config)."""
    conn = get_connection()

    # SQLite uniquement
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass  # SQL Server ne supporte pas ces PRAGMA

    # Tables optionnelles
    try:
        mouvements_table = table("mouvements_stock")
        mouvements_ref_article = col("mouvements_stock", "ref_article")
    except KeyError:
        mouvements_table = None
        mouvements_ref_article = None

    try:
        reglements_table = table("reglements")
        reglements_piece = col("reglements", "piece")
    except KeyError:
        reglements_table = None
        reglements_piece = None

    if _is_mssql():

        # ==========================
        # mouvements_stock
        # ==========================
        if mouvements_table:
            conn.execute(f"""
                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.tables
                    WHERE name = '{mouvements_table}'
                )
                CREATE TABLE {mouvements_table} (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    {mouvements_ref_article} NVARCHAR(50),
                    type_mouvement NVARCHAR(50),
                    qte DECIMAL(18,4),
                    motif NVARCHAR(255),
                    date_mouvement NVARCHAR(50)
                )
            """)

        # ==========================
        # reglements
        # ==========================
        if reglements_table:
            conn.execute(f"""
                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.tables
                    WHERE name = '{reglements_table}'
                )
                CREATE TABLE {reglements_table} (
                    id INT IDENTITY(1,1) PRIMARY KEY,
                    {reglements_piece} NVARCHAR(50),
                    mode_paiement NVARCHAR(50),
                    montant DECIMAL(18,2),
                    date_reglement NVARCHAR(50),
                    numero_piece_paiement NVARCHAR(50)
                )
            """)

            conn.execute(f"""
                IF NOT EXISTS (
                    SELECT 1
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = '{reglements_table}'
                      AND COLUMN_NAME = 'numero_piece_paiement'
                )
                ALTER TABLE {reglements_table}
                ADD numero_piece_paiement NVARCHAR(50)
            """)

        conn.commit()

    else:
        # ==========================
        # SQLite
        # ==========================

        if mouvements_table:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {mouvements_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    {mouvements_ref_article} TEXT,
                    type_mouvement TEXT,
                    qte REAL,
                    motif TEXT,
                    date_mouvement TEXT
                )
            """)

        if reglements_table:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {reglements_table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    {reglements_piece} TEXT,
                    mode_paiement TEXT,
                    montant REAL,
                    date_reglement TEXT,
                    numero_piece_paiement TEXT
                )
            """)

            try:
                conn.execute(
                    f"ALTER TABLE {reglements_table} "
                    "ADD COLUMN numero_piece_paiement TEXT"
                )
            except Exception:
                pass

        conn.commit()

    return _ConnexionCorrigee(conn) if _is_mssql() else conn

# ─────────────────────────────────────────────────────────────────────
# SCHÉMA RÉEL F_DOCENTETE (pour référence)
# ─────────────────────────────────────────────────────────────────────
# F_DOCENTETE : DO_Piece, DO_Domaine, DO_Type, DO_Date, DO_Ref, DO_Tiers
# F_DOCLIGNE  : DL_Ligne, DO_Piece, AR_Ref, DL_Qte, DL_PrixUnitaire
# F_COMPTET   : CT_Num, CT_Intitule, CT_Type, CT_Validite,
#               CT_EncoursMax, CT_Encours
# F_ARTICLE   : AR_Ref, AR_Design, AR_PrixAch, AR_PrixVen, AR_Type
# F_ARTSTOCK  : AR_Ref, AS_QteSto, AS_QteCom, AS_QteAchaCom
# F_NOMENCLAT : NO_RefPF, NO_RefMP, NO_Qte
# reglements  : id, DO_Piece, mode_paiement, montant, date_reglement
#
# CODES type/domaine CONFIRMÉS (côté VENTE, domaine=0 sauf mention contraire) :
#   type=6  domaine=0 → factures de vente
#   type=3  domaine=0 → bons de livraison (BL)
#   type=1  domaine=0 → bons de commande client (BC)
#   type=25 domaine=2 → ordres de fabrication (OF)
#   type=26 domaine=2 → bons de fabrication (BF)
#   type=5  domaine=0 → avoirs de vente
#   Côté FOURNISSEUR (domaine=1) : codes NON confirmés, audit séparé requis
#   (le fichier utilise encore 16, 6 et 3 à différents endroits pour désigner
#   une "facture fournisseur" — voir commentaires "AUDIT FOURNISSEUR").
#
# NOTE : ce bloc est purement documentaire. Le code ne doit JAMAIS utiliser
# ces noms en dur — toujours passer par table()/col().


# ─────────────────────────────────────────────────────────────────────
# HELPER : COMPATIBILITÉ SQL SERVER (LIMIT → TOP / FETCH FIRST)
# ─────────────────────────────────────────────────────────────────────
def _executer_sql(
    conn,
    sql: str,
    params: tuple = (),
    limite: int = 100,
) -> list[dict]:
    sql_clean = sql.strip()
    sql_clean = sql_clean.split(";")[0].strip()
    if not re.match(r"^\s*(SELECT|WITH)\b", sql_clean, re.IGNORECASE):
        return [{"erreur": "Seules les requêtes SELECT sont autorisées."}]

    has_limit = re.search(r"\bLIMIT\b|\bFETCH\s+FIRST\b|\bTOP\s+\d+\b", sql_clean, re.IGNORECASE)
    if not has_limit:
        if _is_mssql():
            sql_clean = re.sub(
                r'^(\s*SELECT\s+)(DISTINCT\s+)?',
                lambda mo: f"{mo.group(1)}{mo.group(2) or ''}TOP {limite} ",
                sql_clean,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            sql_clean = f"{sql_clean} LIMIT {limite}"
    else:
        sql_clean = _convert_limit_for_mssql(sql_clean)

    try:
        cursor = conn.execute(sql_clean, params)
        # ── FIX pyodbc : récupérer les noms de colonnes explicitement ──
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = []
        for row in cursor.fetchall():
            if hasattr(row, "keys"):
                d = dict(row)
            else:
                d = {columns[i]: row[i] for i in range(len(columns))}
            # ── FIX Decimal → float (MSSQL-safe) ──
            for k, v in d.items():
                if isinstance(v, Decimal):
                    d[k] = float(v)
            rows.append(d)
        return rows
    except Exception as e:
        return [{"erreur": str(e)}]
def _is_mssql() -> bool:
    import os
    cfg = os.getenv("DB_DRIVER", "sqlite").lower()
    return cfg == "mssql"

def _fmt_mois(col_expr: str) -> str:
    if _is_mssql():
        return f"FORMAT({col_expr}, 'yyyy-MM')"
    return f"STRFTIME('%Y-%m', {col_expr})"

def _fmt_annee(col_expr: str) -> str:
    if _is_mssql():
        return f"FORMAT({col_expr}, 'yyyy')"
    return f"STRFTIME('%Y', {col_expr})"

def _fmt_mois_num(col_expr: str) -> str:
    if _is_mssql():
        return f"MONTH({col_expr})"
    return f"CAST(STRFTIME('%m', {col_expr}) AS INTEGER)"

def _diff_jours(date_fin: str, date_debut: str) -> str:
    if _is_mssql():
        return f"DATEDIFF(day, {date_debut}, {date_fin})"
    return f"(JULIANDAY({date_fin}) - JULIANDAY({date_debut}))"

def _now_date() -> str:
    return "GETDATE()" if _is_mssql() else "'now'"

def _date_sub_days(n_days: int) -> str:
    if _is_mssql():
        return f"DATEADD(day, -{n_days}, GETDATE())"
    return f"DATE('now', '-{n_days} days')"


def _limit(n: int) -> str:
    """Trailing clause — empty for MSSQL (use _top() prefix instead). SQLite: LIMIT N."""
    return "" if _is_mssql() else f"LIMIT {n}"

def _top(n: int) -> str:
    """Préfixe SELECT pour limitation. MSSQL: 'TOP N ', SQLite: '' (vide, utiliser _limit)."""
    if _is_mssql():
        return f"TOP {n} "
    return ""

def _convert_limit_for_mssql(sql: str) -> str:
    """Convertit 'LIMIT N' en 'TOP N' dans le SELECT si driver=mssql.
    Also handles 'LIMIT ?' (paramétré) si présent."""
    if not _is_mssql():
        return sql
    # Capture LIMIT N, remove it, then inject TOP N after SELECT (or SELECT DISTINCT)
    m = re.search(r'\bLIMIT\s+(\d+)\b', sql, flags=re.IGNORECASE)
    if m:
        n = m.group(1)
        sql = re.sub(r'\bLIMIT\s+\d+\b', '', sql, flags=re.IGNORECASE)
        sql = re.sub(r'\bSELECT(?:\s+DISTINCT)?\b',
                     lambda mo: f'{mo.group(0)} TOP {n}', sql, count=1, flags=re.IGNORECASE)
        return sql
    # LIMIT ? (paramétré) — ne peut pas être converti proprement, on retire juste le LIMIT
    sql = re.sub(r'\bLIMIT\s+\?', '', sql, flags=re.IGNORECASE)
    return sql


# ─────────────────────────────────────────────────────────────────────
# HELPER : RÉSOLUTION CLIENT (code OU nom)
# ─────────────────────────────────────────────────────────────────────
def _resoudre_client(conn, code_ou_nom: str):
    if not code_ou_nom:
        return None
    clients_table = table('clients_fournisseurs')
    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')

    row = conn.execute(
        f"SELECT * FROM {clients_table} WHERE UPPER({code_col}) = UPPER(?)",
        (code_ou_nom.strip(),),
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        f"SELECT * FROM {clients_table} "
        f"WHERE UPPER({nom_col}) LIKE UPPER(?) "
        f"ORDER BY {code_col}",
        (f"%{code_ou_nom.strip()}%",),
    ).fetchone()
    return row


def _resoudre_fournisseur(conn, code_ou_nom: str):
    """Résolution fournisseur (code OU nom), symétrique de _resoudre_client
    mais filtrée sur type_tiers = 1 (fournisseur)."""
    if not code_ou_nom:
        return None
    clients_table = table('clients_fournisseurs')
    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')
    type_col = col('clients_fournisseurs', 'type_tiers')

    row = conn.execute(
        f"SELECT * FROM {clients_table} WHERE UPPER({code_col}) = UPPER(?) AND {type_col} = 1",
        (code_ou_nom.strip(),),
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        f"SELECT * FROM {clients_table} "
        f"WHERE UPPER({nom_col}) LIKE UPPER(?) AND {type_col} = 1 "
        f"ORDER BY {code_col}",
        (f"%{code_ou_nom.strip()}%",),
    ).fetchone()
    return row
def _article_existe(conn, ref: str) -> bool:
    """Vérifie qu'une référence article existe dans le catalogue."""
    if not ref:
        return False
    return conn.execute(
        f"SELECT 1 FROM {table('articles')} WHERE UPPER({col('articles','ref')})=UPPER(?)",
        (ref.strip(),)
    ).fetchone() is not None


def _lot_existe(conn, numero: str) -> bool:
    """Vérifie qu'un numéro de lot existe dans F_LOTSERIE."""
    if not numero:
        return False
    return conn.execute(
        f"SELECT 1 FROM {table('lot_serie')} WHERE UPPER({col('lot_serie','numero')})=UPPER(?)",
        (numero.strip(),)
    ).fetchone() is not None
@mcp.tool()
def lister_references_articles() -> str:
    """Liste toutes les références d'articles (fuzzy-matching côté orchestrateur)."""
    try:
        conn = _connect()
        rows = conn.execute(
            f"SELECT {col('articles', 'ref')} AS ref FROM {table('articles')}"
        ).fetchall()
        conn.close()
        return json.dumps({
            "statut": "OK",
            "references": [r["ref"] for r in rows if r["ref"]],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)
@mcp.tool()
def generer_declaration_mensuelle_excel(periode: str) -> str:
    """Génère un Excel avec 2 tableaux côte à côte (Achat / Vente) des factures du mois demandé.
    'periode' est le texte libre de la demande (ex: 'juin 2026')."""
    try:
        return _gen_decla(periode)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────
# HELPER : MONTANT TOTAL D'UN DOCUMENT (depuis doc_ligne)
# ─────────────────────────────────────────────────────────────────────
def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _montant_doc(conn, do_piece: str) -> Decimal:
    """Calcule le montant HT depuis doc_ligne."""
    doc_ligne_table = table('doc_ligne')
    piece_col = col('doc_ligne', 'piece')
    qte_col = col('doc_ligne', 'qte')
    prix_col = col('doc_ligne', 'prix_unitaire')

    row = conn.execute(
        f"""SELECT COALESCE(SUM({qte_col} * {prix_col}), 0) AS total
           FROM {doc_ligne_table} WHERE {piece_col} = ?""",
        (do_piece,),
    ).fetchone()
    return _to_decimal(row["total"]) if row else Decimal("0.00")


_RX_ERREUR_GROUPBY_MSSQL = re.compile(
    r"La colonne '([^']+)' n'est pas valide dans la liste de sélection",
    re.IGNORECASE,
)


def _corriger_group_by_mssql(sql: str) -> str:
    """Complète le GROUP BY sous MSSQL pour éviter l'erreur 8120.
    SQLite tolère ces colonnes manquantes ; SQL Server ne le tolère pas.
    """
    if not _is_mssql() or not sql or not sql.strip():
        return sql
    if sqlglot is None or exp is None:
        return sql
    try:
        tree = sqlglot.parse_one(sql, dialect="tsql")
        group = tree.args.get("group")
        if group is None:
            has_agg = any(isinstance(n, exp.AggFunc) for n in tree.walk())
            if not has_agg:
                return sql
            return sql

        group_cols = {str(g) for g in group.expressions}
        select_exprs = tree.args.get("expressions", [])
        missing = []
        for e in select_exprs:
            inner = e.this if isinstance(e, exp.Alias) else e
            if isinstance(inner, exp.Column):
                col_str = str(inner)
                if col_str not in group_cols and str(e) not in group_cols:
                    missing.append(inner)

        if missing:
            for col in missing:
                group.append("expressions", col.copy())
            print(f"   🔧 [MSSQL-Fix] GROUP BY complété : +{[str(c) for c in missing]}")
        return tree.sql(dialect="tsql")
    except Exception as e:
        print(f"   ⚠️  [MSSQL-Fix] Correction GROUP BY échouée, SQL original conservé : {e}")
        return sql


def _executer_sql(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
    limite: int = 100,
) -> list[dict]:
    sql_clean = sql.strip()
    sql_clean = sql_clean.split(";")[0].strip()
    if not re.match(r"^\s*(SELECT|WITH)\b", sql_clean, re.IGNORECASE):
        return [{"erreur": "Seules les requêtes SELECT sont autorisées."}]

    sql_clean = _corriger_group_by_mssql(sql_clean)

    has_limit = re.search(r"\bLIMIT\b|\bFETCH\s+FIRST\b|\bTOP\s+\d+\b", sql_clean, re.IGNORECASE)
    if not has_limit:
        if _is_mssql():
            sql_clean = re.sub(
                r'^(\s*SELECT\s+)(DISTINCT\s+)?',
                lambda mo: f"{mo.group(1)}{mo.group(2) or ''}TOP {limite} ",
                sql_clean,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            sql_clean = f"{sql_clean} LIMIT {limite}"
    else:
        sql_clean = _convert_limit_for_mssql(sql_clean)

    try:
        cursor = conn.execute(sql_clean, params)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = []
        for row in cursor.fetchall():
            if hasattr(row, "keys"):
                d = dict(row)
            else:
                d = {columns[i]: row[i] for i in range(len(columns))}
            for k, v in d.items():
                if isinstance(v, Decimal):
                    d[k] = float(v)
            rows.append(d)
        return rows
    except Exception as e:
        err_str = str(e)
        if _is_mssql():
            m = _RX_ERREUR_GROUPBY_MSSQL.search(err_str)
            if m:
                col_manquante = m.group(1)
                print(f"   🔁 [MSSQL-Retry] Ajout de '{col_manquante}' au GROUP BY et nouvel essai")
                sql_retry = re.sub(
                    r'(GROUP BY .+?)(\s+ORDER BY|\s+HAVING|$)',
                    rf'\1, {col_manquante}\2',
                    sql_clean,
                    count=1,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                try:
                    cursor = conn.execute(sql_retry, params)
                    columns = [d[0] for d in cursor.description] if cursor.description else []
                    rows = []
                    for row in cursor.fetchall():
                        if hasattr(row, "keys"):
                            d = dict(row)
                        else:
                            d = {columns[i]: row[i] for i in range(len(columns))}
                        for k, v in d.items():
                            if isinstance(v, Decimal):
                                d[k] = float(v)
                        rows.append(d)
                    return rows
                except Exception as e2:
                    return [{"erreur": str(e2)}]
        return [{"erreur": err_str}]

# ─────────────────────────────────────────────────────────────────────
# HELPER : FORMATAGE RÉSULTATS
# ─────────────────────────────────────────────────────────────────────
def _formater_resultats(rows: list[dict], description: str) -> str:
    """Retourne du JSON structuré (jamais de texte pré-formaté) afin que
    formatters.py (côté frontend) produise un vrai tableau Markdown via
    _table_md() / _formater_resultats_generiques(), au lieu d'un bloc
    texte figé. Le préfixe "📊" et les tirets Unicode ont été retirés
    volontairement : ce sont eux qui faisaient passer la réponse dans
    _PREFIXES_DEJA_FORMATES côté formatters.py et court-circuitaient le
    JSON.parse (donc le rendu tableau).

    La troncature n'est plus faite ici (rows[:20] en dur) : elle est
    déléguée à formatters.py::_tronquer (MAX_LIGNES_TABLE, actuellement
    60), pour rester cohérente avec tous les autres formatteurs JSON.
    """
    if not rows:
        return json.dumps(
            {"statut": "OK", "description": description, "resultats": []},
            ensure_ascii=False,
        )
    if "erreur" in rows[0]:
        return json.dumps({"erreur": rows[0]["erreur"]}, ensure_ascii=False)

    # Résultat scalaire unique (ex: "SELECT COUNT(*) AS nb_clients ...")
    if len(rows) == 1 and len(rows[0]) == 1:
        key, val = next(iter(rows[0].items()))
        return json.dumps(
            {"statut": "OK", "description": description, "message": f"{description} : {val}"},
            ensure_ascii=False,
        )

    return json.dumps(
        {"statut": "OK", "description": description, "resultats": rows},
        ensure_ascii=False,
    )

# ─────────────────────────────────────────────────────────────────────
# HELPERS DATE
# ─────────────────────────────────────────────────────────────────────
def _filtre_date(periode: str | None) -> str:
    if not periode:
        return ""
    periode = periode.strip().lower()
    date_col = col('doc_entete', 'date')
    if re.match(r"^\d{4}$", periode):
        return f"AND {_fmt_annee(f'e.{date_col}')} = '{periode}'"
    _MOIS = {
        "jan": "01", "fév": "02", "feb": "02", "mar": "03",
        "avr": "04", "apr": "04", "mai": "05", "may": "05",
        "jun": "06", "jui": "07", "jul": "07", "aoû": "08",
        "aug": "08", "sep": "09", "oct": "10", "nov": "11",
        "déc": "12", "dec": "12",
        "janvier": "01", "février": "02", "mars": "03",
        "avril": "04", "mai": "05", "juin": "06",
        "juillet": "07", "août": "08", "septembre": "09",
        "octobre": "10", "novembre": "11", "décembre": "12",
    }
    m = re.match(r"(\w+)\s+(\d{4})", periode)
    if m:
        mois_num = _MOIS.get(m.group(1)[:3].lower())
        if mois_num:
            return f"AND {_fmt_mois(f'e.{date_col}')} = '{m.group(2)}-{mois_num}'"
    if re.match(r"^\d{4}-\d{2}$", periode):
        return f"AND {_fmt_mois(f'e.{date_col}')} = '{periode}'"
    return ""


def _jours_depuis(valeur: str | None, unite: str) -> int:
    n = int(valeur) if valeur and valeur.isdigit() else 6
    return n * 365 if "an" in str(unite).lower() else n * 30


# ─────────────────────────────────────────────────────────────────────
# HELPERS SQL COMPLEXES — neutres (table()/col())
# ─────────────────────────────────────────────────────────────────────

def _sql_client_encours(code_ou_nom: str, conn) -> str:
    row = _resoudre_client(conn, code_ou_nom)
    code_col = col('clients_fournisseurs', 'code')
    code = row[code_col] if row else code_ou_nom

    clients_table = table('clients_fournisseurs')
    doc_entete_table = table('doc_entete')
    doc_ligne_table = table('doc_ligne')
    reglements_table = table('reglements')

    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')
    encours_max_col = col('clients_fournisseurs', 'encours_max')

    piece_col_entete = col('doc_entete', 'piece')
    type_col_entete = col('doc_entete', 'type')
    # FIX v4.2 : cette colonne manquait, provoquant une NameError avalée
    # silencieusement par le try/except du moteur de patterns, qui faisait
    # retomber "encours du client X" sur le fallback générique (→ CA total
    # au lieu du solde impayé).
    domaine_col_entete = col('doc_entete', 'domaine')
    code_tiers_col_entete = col('doc_entete', 'code_tiers')

    piece_col_ligne = col('doc_ligne', 'piece')
    qte_col = col('doc_ligne', 'qte')
    prix_col = col('doc_ligne', 'prix_unitaire')

    piece_col_reglements = col('reglements', 'piece')

    return f"""
        SELECT c.{code_col}, c.{nom_col},
               COALESCE(c.{encours_max_col}, 0) AS encours_autorise,
               COALESCE(SUM(l.{qte_col} * l.{prix_col}), 0) AS encours_utilise
        FROM {clients_table} c
        LEFT JOIN {doc_entete_table} e ON c.{code_col} = e.{code_tiers_col_entete}
            AND e.{type_col_entete} = 6
            AND e.{domaine_col_entete} = 0
            AND e.{piece_col_entete} NOT IN (SELECT {piece_col_reglements} FROM {reglements_table})
        LEFT JOIN {doc_ligne_table} l ON e.{piece_col_entete} = l.{piece_col_ligne}
        WHERE c.{code_col} = '{code}'
        GROUP BY c.{code_col}, c.{nom_col}, c.{encours_max_col}
    """


def _sql_factures_non_reglees(code_ou_nom: str, conn) -> str:
    """
    Factures non réglées = type=6 (factures de vente) dont piece absent de reglements.
    """
    clients_table = table('clients_fournisseurs')
    doc_entete_table = table('doc_entete')
    doc_ligne_table = table('doc_ligne')
    reglements_table = table('reglements')

    piece_col_entete = col('doc_entete', 'piece')
    type_col_entete = col('doc_entete', 'type')
    domaine_col_entete = col('doc_entete', 'domaine')
    date_col_entete = col('doc_entete', 'date')
    code_tiers_col_entete = col('doc_entete', 'code_tiers')

    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')

    piece_col_ligne = col('doc_ligne', 'piece')
    qte_col = col('doc_ligne', 'qte')
    prix_col = col('doc_ligne', 'prix_unitaire')

    piece_col_reglements = col('reglements', 'piece')

    filtre = ""
    if code_ou_nom:
        row = _resoudre_client(conn, code_ou_nom)
        if row:
            filtre = f"AND e.{code_tiers_col_entete} = '{row[code_col]}'"
    days_diff_sql = f"CAST({_diff_jours(_now_date(), f'e.{date_col_entete}')} AS INTEGER)"

    return f"""
        SELECT e.{piece_col_entete},
               e.{code_tiers_col_entete} AS DO_Tiers,
               c.{nom_col},
               e.{date_col_entete},
               COALESCE(SUM(l.{qte_col} * l.{prix_col}), 0) AS montant_ht,
               {days_diff_sql} AS jours_retard
        FROM {doc_entete_table} e
        LEFT JOIN {clients_table}  c ON e.{code_tiers_col_entete} = c.{code_col}
        LEFT JOIN {doc_ligne_table} l ON e.{piece_col_entete} = l.{piece_col_ligne}
        WHERE e.{type_col_entete} = 6 AND e.{domaine_col_entete} = 0
          AND e.{piece_col_entete} NOT IN (SELECT {piece_col_reglements} FROM {reglements_table})
          {filtre}
        GROUP BY e.{piece_col_entete}, e.{code_tiers_col_entete}, c.{nom_col}, e.{date_col_entete}
        ORDER BY jours_retard DESC
    """


def _sql_ca_client(
    code_ou_nom: str,
    conn,
    annee: str | None = None,
) -> str:
    clients_table = table('clients_fournisseurs')
    doc_entete_table = table('doc_entete')
    doc_ligne_table = table('doc_ligne')

    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')

    piece_col_entete = col('doc_entete', 'piece')
    type_col_entete = col('doc_entete', 'type')
    domaine_col_entete = col('doc_entete', 'domaine')
    date_col_entete = col('doc_entete', 'date')
    code_tiers_col_entete = col('doc_entete', 'code_tiers')

    piece_col_ligne = col('doc_ligne', 'piece')
    qte_col = col('doc_ligne', 'qte')
    prix_col = col('doc_ligne', 'prix_unitaire')

    row = _resoudre_client(conn, code_ou_nom)
    code = row[code_col] if row else code_ou_nom
    filtre_an = f"AND {_fmt_annee(f'e.{date_col_entete}')} = '{annee}'" if annee else ""
    # FIX v4.3 : type=3 = BL, pas facture. Le CA client doit être calculé
    # sur les factures de vente (type=6).
    return f"""
        SELECT e.{code_col},
               c.{nom_col},
               COUNT(DISTINCT e.{piece_col_entete})             AS nb_factures,
               SUM(l.{qte_col} * l.{prix_col})     AS ca_total,
               MIN(e.{date_col_entete})                          AS premiere_facture,
               MAX(e.{date_col_entete})                          AS derniere_facture
        FROM {doc_entete_table} e
        JOIN {doc_ligne_table}  l ON e.{piece_col_entete} = l.{piece_col_ligne}
        LEFT JOIN {clients_table} c ON e.{code_tiers_col_entete} = c.{code_col}
        WHERE e.{type_col_entete} = 6 AND e.{domaine_col_entete} = 0
          AND e.{code_tiers_col_entete} = '{code}'
          {filtre_an}
        GROUP BY e.{code_col}
    """


def _sql_docs_periode(
    code_ou_nom: str,
    date_debut: str,
    date_fin: str,
    conn,
) -> str:
    clients_table = table('clients_fournisseurs')
    doc_entete_table = table('doc_entete')
    doc_ligne_table = table('doc_ligne')

    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')

    piece_col_entete = col('doc_entete', 'piece')
    type_col_entete = col('doc_entete', 'type')
    date_col_entete = col('doc_entete', 'date')
    code_tiers_col_entete = col('doc_entete', 'code_tiers')

    piece_col_ligne = col('doc_ligne', 'piece')
    qte_col = col('doc_ligne', 'qte')
    prix_col = col('doc_ligne', 'prix_unitaire')

    filtre = ""
    if code_ou_nom:
        row = _resoudre_client(conn, code_ou_nom)
        if row:
            filtre = f"AND e.{code_tiers_col_entete} = '{row[code_col]}'"
    if _is_mssql():
        date_filtre = (
            f"TRY_CAST(e.{date_col_entete} AS DATE) >= TRY_CAST('{date_debut}' AS DATE) "
            f"AND TRY_CAST(e.{date_col_entete} AS DATE) <= TRY_CAST('{date_fin}' AS DATE)"
        )
    else:
        date_filtre = f"e.{date_col_entete} >= '{date_debut}' AND e.{date_col_entete} <= '{date_fin}'"

    return f"""
        SELECT e.{piece_col_entete}, e.{type_col_entete}, e.{date_col_entete},
               e.{code_tiers_col_entete}, c.{nom_col},
               COALESCE(SUM(l.{qte_col} * l.{prix_col}), 0) AS montant_ht
        FROM {doc_entete_table} e
        LEFT JOIN {clients_table}  c ON e.{code_tiers_col_entete} = c.{code_col}
        LEFT JOIN {doc_ligne_table} l ON e.{piece_col_entete} = l.{piece_col_ligne}
        WHERE {date_filtre}
          {filtre}
        GROUP BY e.{piece_col_entete}, e.{type_col_entete}, e.{date_col_entete},
                 e.{code_tiers_col_entete}, c.{nom_col}
        ORDER BY e.{date_col_entete} DESC
    """
def _sql_dso(code_ou_nom: str, conn) -> str:
    """
    DSO = délai moyen entre date et date_reglement (table reglements).
    Pour les non réglées, on utilise la date d'aujourd'hui.
    """
    clients_table = table('clients_fournisseurs')
    doc_entete_table = table('doc_entete')
    doc_ligne_table = table('doc_ligne')
    reglements_table = table('reglements')

    code_col = col('clients_fournisseurs', 'code')
    nom_col = col('clients_fournisseurs', 'nom')

    piece_col_entete = col('doc_entete', 'piece')
    type_col_entete = col('doc_entete', 'type')
    domaine_col_entete = col('doc_entete', 'domaine')
    date_col_entete = col('doc_entete', 'date')
    code_tiers_col_entete = col('doc_entete', 'code_tiers')

    piece_col_ligne = col('doc_ligne', 'piece')
    qte_col = col('doc_ligne', 'qte')
    prix_col = col('doc_ligne', 'prix_unitaire')

    piece_col_reglements = col('reglements', 'piece')
    date_reglement_col = col('reglements', 'date_reglement')

    filtre = ""
    if code_ou_nom:
        row = _resoudre_client(conn, code_ou_nom)
        if row:
            filtre = f"AND e.{code_tiers_col_entete} = '{row[code_col]}'"
    # FIX v4.3 : type=3 = BL, pas facture. Le DSO doit être calculé sur
    # les factures de vente (type=6).
    return f"""
        SELECT e.{code_col},
               c.{nom_col},
               ROUND(AVG(
                 CASE WHEN r.{date_reglement_col} IS NOT NULL
                      THEN {_diff_jours(f'r.{date_reglement_col}', f'e.{date_col_entete}')}
                      ELSE {_diff_jours(_now_date(), f'e.{date_col_entete}')}
                 END
               ), 1) AS dso_jours,
               COUNT(DISTINCT e.{piece_col_entete}) AS nb_factures,
               SUM(l.{qte_col} * l.{prix_col}) AS montant_total
        FROM {doc_entete_table} e
        LEFT JOIN {clients_table}  c ON e.{code_tiers_col_entete} = c.{code_col}
        LEFT JOIN {doc_ligne_table} l ON e.{piece_col_entete} = l.{piece_col_ligne}
        LEFT JOIN {reglements_table} r ON e.{piece_col_entete} = r.{piece_col_reglements}
        WHERE e.{type_col_entete} = 6 AND e.{domaine_col_entete} = 0
          {filtre}
        GROUP BY e.{code_col}, c.{nom_col}
        ORDER BY dso_jours DESC
    """


# ─────────────────────────────────────────────────────────────────────
# GÉNÉRATEURS DE PATTERNS COMPLEXES (v4.2)
# Extraits en fonctions dédiées pour éviter le piège du f-string évalué
# AVANT le .format() qui devait le compléter (cause du bug silencieux).
# Toute valeur dynamique est calculée en Python normal AVANT d'être
# injectée dans le f-string du SQL.
# ─────────────────────────────────────────────────────────────────────

def _gen_factures_mois_numero(m, conn):
    mois = int(next((g for g in m.groups() if g), 1))
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    sql = f"""
        SELECT e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
               e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
               COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
               CASE WHEN r.{col('reglements', 'piece')} IS NOT NULL THEN 'RÉGLÉE' ELSE 'EN ATTENTE' END AS statut
        FROM {table('doc_entete')} e
        LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
        LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
        LEFT JOIN {table('reglements')} r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
        WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
          AND {_fmt_mois_num(f"e.{col('doc_entete', 'date')}")} = {mois}
        GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                 e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                 r.{col('reglements', 'piece')}
        ORDER BY e.{col('doc_entete', 'date')} DESC
    """
    return {"sql": sql, "description": f"Factures du mois {mois}"}


_MOIS_NOMS_FR = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}


def _gen_factures_mois_nom(m, conn):
    mois = _MOIS_NOMS_FR.get(m.group(1).lower(), 1)
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    sql = f"""
        SELECT e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
               e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
               COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
               CASE WHEN r.{col('reglements', 'piece')} IS NOT NULL THEN 'RÉGLÉE' ELSE 'EN ATTENTE' END AS statut
        FROM {table('doc_entete')} e
        LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
        LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
        LEFT JOIN {table('reglements')} r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
        WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
          AND {_fmt_mois_num(f"e.{col('doc_entete', 'date')}")} = {mois}
        GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                 e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                 r.{col('reglements', 'piece')}
        ORDER BY e.{col('doc_entete', 'date')} DESC
    """
    return {"sql": sql, "description": f"Factures du mois de {m.group(1)}"}


def _gen_articles_plus_vendus(m, conn):
    date_col = f"e.{col('doc_entete', 'date')}"
    if m.group(1) and "mois" in m.group(1):
        filtre = f"AND {_fmt_mois(date_col)} = {_fmt_mois(_now_date())}"
    elif m.group(1) and "semaine" in m.group(1):
        filtre = f"AND {date_col} >= {_date_sub_days(7)}"
    elif m.group(2):
        filtre = f"AND {_fmt_annee(date_col)} = '{m.group(2)}'"
    else:
        filtre = ""
    # FIX v4.3 : type=3 = BL, pas facture. Les articles "vendus" sont
    # comptabilisés sur les factures de vente (type=6), cohérent avec
    # analyser_palmares_articles.
    sql = f"""
        SELECT {_top(10)}l.{col('doc_ligne', 'ref_article')}, a.{col('articles', 'designation')},
               SUM(l.{col('doc_ligne', 'qte')})                      AS qte_vendue,
               SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})  AS ca
        FROM {table('doc_ligne')}  l
        JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
        LEFT JOIN {table('articles')} a ON l.{col('doc_ligne', 'ref_article')} = a.{col('articles', 'ref')}
        WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
          {filtre}
        GROUP BY l.{col('doc_ligne', 'ref_article')}
        ORDER BY qte_vendue DESC
    """
    return {
        "sql": sql,
        "description": "Articles les plus vendus" + (f" ({m.group(1)})" if m.group(1) else ""),
    }


def _gen_factures_fournisseur_specifique(m, conn):
    # AUDIT FOURNISSEUR (non corrigé — code type=16/domaine=1 non confirmé,
    # voir découverte annexe : incohérence 16/6/3 selon les fonctions).
    code = next((m.group(i) for i in range(1, (m.lastindex or 0) + 1) if m.group(i)), "").upper()
    sql = f"""
        SELECT e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
               e.{col('doc_entete', 'code_tiers')} AS code_fournisseur,
               c.{col('clients_fournisseurs', 'nom')} AS nom_fournisseur,
               COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
               CASE WHEN r.{col('reglements', 'piece')} IS NOT NULL
                    THEN 'RÉGLÉE' ELSE 'EN ATTENTE'
               END AS statut
        FROM {table('doc_entete')} e
        LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
        LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
        LEFT JOIN {table('reglements')} r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
        WHERE e.{col('doc_entete', 'type')} = 16 AND e.{col('doc_entete', 'domaine')} = 1
          AND e.{col('doc_entete', 'code_tiers')} = '{code}'
        GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                 e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                 r.{col('reglements', 'piece')}
        ORDER BY e.{col('doc_entete', 'date')} DESC
    """
    return {"sql": sql, "description": f"Factures fournisseur {code}"}


def _gen_marge_article_specifique(m, conn):
    ref = m.group(1).strip()
    existe = conn.execute(
        f"SELECT 1 FROM {table('articles')} WHERE UPPER({col('articles','ref')})=UPPER(?)",
        (ref,)
    ).fetchone()
    if not existe:
        return {"sql": "", "description": f"Article '{ref}' introuvable dans le catalogue"}
    sql = f"""
        SELECT l.{col('doc_ligne', 'ref_article')}, a.{col('articles', 'designation')},
               SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}) AS ca_vente,
               SUM(l.{col('doc_ligne', 'qte')} * a.{col('articles', 'prix_achat')}) AS cout_achat,
               SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})
                 - SUM(l.{col('doc_ligne', 'qte')} * a.{col('articles', 'prix_achat')}) AS marge_brute,
               ROUND(
                 (SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})
                  - SUM(l.{col('doc_ligne', 'qte')} * a.{col('articles', 'prix_achat')}))
                 / NULLIF(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) * 100,
               1) AS taux_marge_pct
        FROM {table('doc_ligne')} l
        JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
        LEFT JOIN {table('articles')} a ON l.{col('doc_ligne', 'ref_article')} = a.{col('articles', 'ref')}
        WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
          AND UPPER(l.{col('doc_ligne', 'ref_article')}) = UPPER('{ref}')
        GROUP BY l.{col('doc_ligne', 'ref_article')}
    """
    return {"sql": sql, "description": f"Marge brute sur l'article '{ref}' (aucune vente si vide)"}
# ─────────────────────────────────────────────────────────────────────
# CATALOGUE DE PATTERNS NL→SQL — neutre (table()/col())
# ─────────────────────────────────────────────────────────────────────
def _gen_lots_disponibles(m, conn):
    ref = next(g for g in m.groups() if g)
    if not _article_existe(conn, ref):
        return {"sql": "", "description": f"Article '{ref}' introuvable dans le catalogue"}
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                       ORDER BY {col('lot_serie','cbmarq')} DESC
                   ) AS rn
            FROM {table('lot_serie')}
            WHERE UPPER({col('lot_serie','ref')}) = UPPER('{ref}')
        )
        SELECT {col('lot_serie','numero')} AS numero,
               {col('lot_serie','qte_restante')} AS qte_restante,
               {col('lot_serie','qte_reservee')} AS qte_reservee,
               {col('lot_serie','fabrication')} AS date_fabrication,
               {col('lot_serie','peremption')} AS date_peremption
        FROM DerniereLigne
        WHERE rn = 1
          AND COALESCE({col('lot_serie','epuise')}, 0) = 0
          AND COALESCE({col('lot_serie','qte_restante')}, 0) > 0
        ORDER BY {col('lot_serie','fabrication')} ASC
    """
    return {"sql": sql, "description": f"Lots disponibles pour l'article {ref}"}
def _gen_quantite_restante_lot(m, conn):
    numero = m.group(1)
    if not _lot_existe(conn, numero):
        return {"sql": "", "description": f"Lot '{numero}' introuvable dans la base"}
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                       ORDER BY {col('lot_serie','cbmarq')} DESC
                   ) AS rn
            FROM {table('lot_serie')}
            WHERE UPPER({col('lot_serie','numero')}) = UPPER('{numero}')
        )
        SELECT {col('lot_serie','ref')} AS ref,
               {col('lot_serie','numero')} AS numero,
               {col('lot_serie','qte_initiale')} AS qte_initiale,
               {col('lot_serie','qte_restante')} AS qte_restante,
               {col('lot_serie','qte_reservee')} AS qte_reservee
        FROM DerniereLigne
        WHERE rn = 1
    """
    return {"sql": sql, "description": f"Quantité restante du lot {numero}"}
def _gen_lot_disponibilite(m, conn):
    numero = m.group(1)
    if not _lot_existe(conn, numero):
        return {"sql": "", "description": f"Lot '{numero}' introuvable dans la base"}
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                       ORDER BY {col('lot_serie','cbmarq')} DESC
                   ) AS rn
            FROM {table('lot_serie')}
            WHERE UPPER({col('lot_serie','numero')}) = UPPER('{numero}')
        )
        SELECT {col('lot_serie','ref')} AS ref,
               {col('lot_serie','numero')} AS numero,
               {col('lot_serie','qte_restante')} AS qte_restante,
               {col('lot_serie','epuise')} AS epuise,
               {col('lot_serie','peremption')} AS date_peremption
        FROM DerniereLigne
        WHERE rn = 1
    """
    return {"sql": sql, "description": f"Disponibilité du lot {numero}"}
def _gen_lot_origine(m, conn):
    numero = next(g for g in m.groups() if g)
    if not _lot_existe(conn, numero):
        return {"sql": "", "description": f"Lot '{numero}' introuvable dans la base"}
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                       ORDER BY {col('lot_serie','cbmarq')} DESC
                   ) AS rn
            FROM {table('lot_serie')}
            WHERE UPPER({col('lot_serie','numero')}) = UPPER('{numero}')
        )
        SELECT ls.{col('lot_serie','numero')} AS numero,
               e.{col('doc_entete','piece')} AS piece_origine,
               e.{col('doc_entete','type')} AS type_doc,
               e.{col('doc_entete','domaine')} AS domaine,
               e.{col('doc_entete','date')} AS date_doc,
               CASE
                   WHEN e.{col('doc_entete','type')}=25 AND e.{col('doc_entete','domaine')}=2 THEN 'Ordre de fabrication (OF)'
                   WHEN e.{col('doc_entete','type')}=26 AND e.{col('doc_entete','domaine')}=2 THEN 'Bon de fabrication (BF)'
                   WHEN e.{col('doc_entete','type')}=13 AND e.{col('doc_entete','domaine')}=1 THEN 'Bon de réception fournisseur'
                   WHEN e.{col('doc_entete','type')}=16 AND e.{col('doc_entete','domaine')}=1 THEN 'Facture fournisseur (achat)'
                   ELSE 'Origine inconnue'
               END AS origine_libelle
        FROM DerniereLigne ls
        LEFT JOIN {table('doc_ligne')} l ON ls.{col('lot_serie','ligne_entree')} = l.{col('doc_ligne','id')}
        LEFT JOIN {table('doc_entete')} e ON l.{col('doc_ligne','piece')} = e.{col('doc_entete','piece')}
        WHERE ls.rn = 1
    """
    return {"sql": sql, "description": f"Origine (document d'entrée) du lot {numero}"}
def _gen_lot_destination(m, conn):
    numero = next(g for g in m.groups() if g)
    if not _lot_existe(conn, numero):
        return {"sql": "", "description": f"Lot '{numero}' introuvable dans la base"}
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                       ORDER BY {col('lot_serie','cbmarq')} DESC
                   ) AS rn
            FROM {table('lot_serie')}
            WHERE UPPER({col('lot_serie','numero')}) = UPPER('{numero}')
        )
        SELECT ls.{col('lot_serie','numero')} AS numero,
               e.{col('doc_entete','piece')} AS piece_sortie,
               e.{col('doc_entete','type')} AS type_doc,
               e.{col('doc_entete','domaine')} AS domaine,
               e.{col('doc_entete','date')} AS date_doc,
               e.{col('doc_entete','code_tiers')} AS code_client
        FROM DerniereLigne ls
        LEFT JOIN {table('doc_ligne')} l ON ls.{col('lot_serie','ligne_sortie')} = l.{col('doc_ligne','id')}
        LEFT JOIN {table('doc_entete')} e ON l.{col('doc_ligne','piece')} = e.{col('doc_entete','piece')}
        WHERE ls.rn = 1
    """
    return {"sql": sql, "description": f"Document de sortie du lot {numero}"}
def _date_add_days(n_days: int) -> str:
    if _is_mssql():
        return f"DATEADD(day, {n_days}, GETDATE())"
    return f"DATE('now', '+{n_days} days')"
def _gen_lots_epuises(m, conn):
    ref = next(g for g in m.groups() if g)
    if not _article_existe(conn, ref):
        return {"sql": "", "description": f"Article '{ref}' introuvable dans le catalogue"}
    sql = f"""
        WITH DerniereLigne AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                       ORDER BY {col('lot_serie','cbmarq')} DESC
                   ) AS rn
            FROM {table('lot_serie')}
            WHERE UPPER({col('lot_serie','ref')}) = UPPER('{ref}')
        )
        SELECT {col('lot_serie','numero')} AS numero,
               {col('lot_serie','qte_initiale')} AS qte_initiale,
               {col('lot_serie','fabrication')} AS date_fabrication
        FROM DerniereLigne
        WHERE rn = 1
          AND COALESCE({col('lot_serie','epuise')}, 0) = 1
        ORDER BY {col('lot_serie','fabrication')} ASC
    """
    return {"sql": sql, "description": f"Lots épuisés pour l'article {ref}"}
_FOURNISSEUR_RX = r"fou?r?n+i?s+e?u?r"

_NL_PATTERNS: list[tuple] = [


    # LOTS / SÉRIES (F_LOTSERIE) — 7 formulations
    # F_LOTSERIE est append-only : on déduplique via ROW_NUMBER() OVER
    # (PARTITION BY ref, numero ORDER BY cbmarq DESC) pour ne garder
    # que la dernière ligne par lot.
    # Les jointures origine/destination utilisent DL_No (id), pas DL_Ligne.
    # ══════════════════════════════════════════════════════════════════

    # ── 1. Lots disponibles pour un article ───────────────────────────
    (
        r"quels?\s+(?:sont\s+les\s+)?lots?\s+(?:encore\s+)?disponibles?\s+(?:pour|de|du|d['\u2019])\s+([A-Za-z0-9\-]+)"
        r"|lots?\s+disponibles?\s+(?:pour|de|du|d['\u2019])\s+([A-Za-z0-9\-]+)",
                _gen_lots_disponibles,
    ),

    # ── 2. Quantité restante d'un lot précis ──────────────────────────
    
            (
        r"quantit[eé]\s+restante\s+(?:du\s+|de\s+)?lot\s+([A-Za-z0-9\-]+)",
        _gen_quantite_restante_lot,
    ),
    

    # ── 3. Un lot est-il encore disponible ? ──────────────────────────
        (
        r"lot\s+([A-Za-z0-9\-]+)\s+est[\s-]il\s+(?:encore\s+)?disponible",
        _gen_lot_disponibilite,
    ),
    # ── 4. Origine d'un lot (document d'entrée : OF/BF/achat) ────────
        (
        r"d['\u2019]o[u\u00f9]\s+vient\s+(?:le\s+)?lot\s+([A-Za-z0-9\-]+)"
        r"|origine\s+(?:du\s+)?lot\s+([A-Za-z0-9\-]+)"
        r"|quel\s+document\s+d['\u2019]entr[eé]e\s+.{0,20}lot\s+([A-Za-z0-9\-]+)",
        _gen_lot_origine,
    ),
    # ── 5. Sur quel BL/document le lot est-il sorti ───────────────────
        (
        r"sur\s+quel\s+bl\s+.{0,20}lot\s+([A-Za-z0-9\-]+)"
        r"|lot\s+([A-Za-z0-9\-]+)\s+.{0,20}(?:est\s+)?parti(?:e)?"
        r"|destination\s+(?:du\s+)?lot\s+([A-Za-z0-9\-]+)",
        _gen_lot_destination,
    ),

    # ── 6. Lots dont la péremption approche (30 jours) ────────────────
    (
        r"lots?\s+.{0,20}(?:expirent?|p[eé]rim[eé]s?|p[eé]remption)\s+.{0,20}(?:bient[o\u00f4]t|proche|prochaine)"
        r"|p[eé]remption\s+proche",
        lambda m, conn: {
            "sql": f"""
                WITH DerniereLigne AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY {col('lot_serie','ref')}, {col('lot_serie','numero')}
                               ORDER BY {col('lot_serie','cbmarq')} DESC
                           ) AS rn
                    FROM {table('lot_serie')}
                )
                SELECT {col('lot_serie','ref')} AS ref,
                       {col('lot_serie','numero')} AS numero,
                       {col('lot_serie','peremption')} AS date_peremption,
                       {col('lot_serie','qte_restante')} AS qte_restante
                FROM DerniereLigne
                WHERE rn = 1
                  AND {col('lot_serie','peremption')} IS NOT NULL
                  AND {col('lot_serie','peremption')} <= {_date_add_days(30)}
                  AND {col('lot_serie','peremption')} >= {_now_date()}
                  AND COALESCE({col('lot_serie','epuise')}, 0) = 0
                ORDER BY {col('lot_serie','peremption')} ASC
            """,
            "description": "Lots dont la péremption approche (30 jours)",
        }
    ),

    # ── 7. Lots épuisés d'un article ──────────────────────────────────
        (
        r"lots?\s+(?:de\s+)?([A-Za-z0-9\-]+)\s+.{0,20}(?:sont\s+)?[eé]puis[eé]s?"
        r"|lots?\s+[eé]puis[eé]s?\s+(?:de|pour)\s+([A-Za-z0-9\-]+)",
        _gen_lots_epuises,
    ),

    # ── Clients classés par nombre de commandes ───────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"clas(?:se|sement|s[eé])\s+.{0,30}clients?.{0,30}(?:nombre|nb)\s+(?:de\s+)?commandes?"
        r"|clients?.{0,30}par\s+(?:nombre|nb)\s+(?:de\s+)?commandes?"
        r"|clients?.{0,30}(?:tri[eé]s?|class[eé]s?|ordonn[eé]s?|rang[eé]s?).{0,30}(?:nombre|nb).{0,20}commandes?"
        r"|(?:nombre|nb)\s+(?:de\s+)?commandes?\s+(?:par\s+)?client"
        r"|qui\s+(?:commande|achète|a\s+achet[eé])\s+le\s+plus",
        lambda m, conn: {
            "sql": f"""
                SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_commandes,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS ca_total
                FROM {table('clients_fournisseurs')} c
                LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                    AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
                GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
                ORDER BY nb_commandes DESC
            """,
            "description": "Clients classés par nombre de commandes (décroissant)",
        }
    ),
    # ── Alias : "moyenne des factures par client" → panier moyen ──
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"moyenne\s+des?\s+factures?\s+par\s+client",
        lambda m, conn: {
            "sql": f"""
                SELECT e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures,
                       ROUND(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})
                             / COUNT(DISTINCT e.{col('doc_entete', 'piece')}), 2) AS moyenne_facture,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}) AS ca_total
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY e.{col('doc_entete', 'code_tiers')}
                ORDER BY moyenne_facture DESC
            """,
            "description": "Moyenne des factures par client",
        }
    ),
    # ── Alias : détail complet de tous les clients ──
    (
        r"(?:tous\s+les\s+)?d[eé]tails?\s+des?\s+clients?"
        r"|informations?\s+de\s+contact\s+des?\s+clients?"
        r"|coordonn[eé]es?\s+des?\s+clients?",
        lambda m, conn: {
            "sql": f"""
                SELECT {col('clients_fournisseurs', 'code')}, {col('clients_fournisseurs', 'nom')}, {col('clients_fournisseurs', 'type_tiers')}, {col('clients_fournisseurs', 'validite')},
                       {col('clients_fournisseurs', 'encours')}, {col('clients_fournisseurs', 'encours_max')}
                FROM {table('clients_fournisseurs')}
                WHERE {col('clients_fournisseurs', 'type_tiers')} = 0
                ORDER BY {col('clients_fournisseurs', 'nom')}
            """,
            "description": "Détail de tous les clients",
        }
    ),
    # ── Articles sous seuil de stock ET commandés ce mois ────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6 (cohérence avec le
    # reste des analyses ; NB : "commandé" ici est calculé via les
    # factures, pas via les BC — à confirmer métier si besoin).
    (
        r"articles?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique).{0,40}command[eé]s?"
        r"|articles?.{0,40}command[eé]s?.{0,40}stock.{0,20}(?:inf[eé]r|seuil|insuffisant|critique)"
        r"|articles?.{0,30}(?:stock\s+(?:faible|bas|insuffisant|inf[eé]r|critique)|sous.{0,10}seuil).{0,40}(?:command[eé]|achet[eé])"
        r"|rupture.{0,20}command[eé]|command[eé].{0,20}rupture",
        lambda m, conn: {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock_dispo,
                       COALESCE(s.{col('stock', 'qte_commande')}, 0) AS en_commande,
                       COUNT(DISTINCT l.{col('doc_ligne', 'piece')}) AS nb_commandes_mois,
                       SUM(l.{col('doc_ligne', 'qte')}) AS qte_commandee_mois
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                JOIN {table('doc_ligne')} l ON a.{col('articles', 'ref')} = l.{col('doc_ligne', 'ref_article')}
                JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                  AND {_fmt_mois(f"e.{col('doc_entete', 'date')}")} = {_fmt_mois(_now_date())}
                  AND COALESCE(s.{col('stock', 'qte_stock')}, 0) < COALESCE(s.{col('stock', 'qte_commande')}, 5)
                GROUP BY a.{col('articles', 'ref')}, a.{col('articles', 'designation')}, s.{col('stock', 'qte_stock')}, s.{col('stock', 'qte_commande')}
                ORDER BY stock_dispo ASC
            """,
            "description": "Articles dont le stock est insuffisant ET qui ont été commandés ce mois",
        }
    ),(
        r"articles?.{0,30}stock.{0,20}inf[eé]r(?:ieur)?\s*(?:à|a)?\s*(\d+)",
        lambda m, conn: {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE COALESCE(s.{col('stock', 'qte_stock')}, 0) < {int(m.group(1))}
                ORDER BY stock ASC
            """,
            "description": f"Articles avec stock inférieur à {m.group(1)}",
        }
    ),
    (
        r"articles?.{0,30}stock.{0,20}sup[eé]rieur\s*(?:à|a)?\s*(\d+)",
        lambda m, conn: {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE COALESCE(s.{col('stock', 'qte_stock')}, 0) > {int(m.group(1))}
                ORDER BY stock DESC
            """,
            "description": f"Articles avec stock supérieur à {m.group(1)}",
        }
    ),

    # ── Évolution factures impayées mois par mois ─────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"[eé]volution.{0,30}(?:factures?\s+)?impay[eé]es?\s+mois\s+par\s+mois"
        r"|impay[eé]es?\s+mois\s+par\s+mois"
        r"|factures?\s+impay[eé]es?\s+par\s+mois",
        lambda m, conn: {
            "sql": f"""
                SELECT {_fmt_mois(f"e.{col('doc_entete', 'date')}")} AS mois,
                       COUNT(*)                      AS nb_factures,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}) AS montant_impaye
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                AND e.{col('doc_entete', 'piece')} NOT IN (SELECT {col('reglements', 'piece')} FROM {table('reglements')})
                GROUP BY {_fmt_mois(f"e.{col('doc_entete', 'date')}")}
                ORDER BY mois ASC
            """,
            "description": "Évolution des factures impayées mois par mois",
        }
    ),

    # ── Articles vendus à un seul client ─────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"articles?.{0,30}vendu[se]?.{0,20}(?:un\s+seul|1\s+seul|unique)\s+client"
        r"|articles?.{0,20}(?:un\s+seul|unique)\s+client",
        lambda m, conn: {
            "sql": f"""
                SELECT l.{col('doc_ligne', 'ref_article')}, a.{col('articles', 'designation')},
                       COUNT(DISTINCT e.{col('doc_entete', 'code_tiers')}) AS nb_clients
                FROM {table('doc_ligne')} l
                JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
                JOIN {table('articles')} a ON l.{col('doc_ligne', 'ref_article')} = a.{col('articles', 'ref')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY l.{col('doc_ligne', 'ref_article')}
                HAVING nb_clients = 1
                ORDER BY l.{col('doc_ligne', 'ref_article')}
            """,
            "description": "Articles vendus à un seul client",
        }
    ),

    # ── Fournisseurs livrant des articles des meilleurs clients ───
    # FIX v4.3 : le classement "meilleurs clients" (sous-requête) doit
    # être basé sur les factures (type=6), pas les BL (type=3). Le
    # domaine=1 côté fournisseur reste hors périmètre (AUDIT FOURNISSEUR).
    (
       r"fournisseurs?.{0,80}(?:meilleurs?|top)\s+clients?"
    r"|fournisseurs?.{0,80}articles?.{0,60}(?:meilleurs?|top)\s+clients?",
        lambda m, conn: {
            "sql": f"""
                SELECT DISTINCT f.{col('clients_fournisseurs', 'code')}, f.{col('clients_fournisseurs', 'nom')}
                FROM {table('clients_fournisseurs')} f
                JOIN {table('doc_entete')} ea ON ea.{col('doc_entete', 'code_tiers')} = f.{col('clients_fournisseurs', 'code')} AND ea.{col('doc_entete', 'domaine')} = 1
                JOIN {table('doc_ligne')} la ON la.{col('doc_ligne', 'piece')} = ea.{col('doc_entete', 'piece')}
                WHERE f.{col('clients_fournisseurs', 'type_tiers')} = 1
                AND la.{col('doc_ligne', 'ref_article')} IN (
                    SELECT l.{col('doc_ligne', 'ref_article')}
                    FROM {table('doc_ligne')} l
                    JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
                    WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                    AND e.{col('doc_entete', 'code_tiers')} IN (
                        SELECT {_top(5)}e2.{col('doc_entete', 'code_tiers')}
                        FROM {table('doc_entete')} e2
                        JOIN {table('doc_ligne')} l2 ON l2.{col('doc_ligne', 'piece')} = e2.{col('doc_entete', 'piece')}
                        WHERE e2.{col('doc_entete', 'type')} = 6 AND e2.{col('doc_entete', 'domaine')} = 0
                        GROUP BY e2.{col('doc_entete', 'code_tiers')}
                        ORDER BY SUM(l2.{col('doc_ligne', 'qte')} * l2.{col('doc_ligne', 'prix_unitaire')}) DESC
                    )
                )
                ORDER BY f.{col('clients_fournisseurs', 'nom')}
            """,
            "description": "Fournisseurs des articles vendus aux 5 meilleurs clients",
        }
    ),

    # ── Panier moyen par client ───────────────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"panier\s+moyen(?:\s+par\s+client)?",
        lambda m, conn: {
            "sql": f"""
                SELECT e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures,
                       ROUND(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})
                             / COUNT(DISTINCT e.{col('doc_entete', 'piece')}), 2) AS panier_moyen,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}) AS ca_total
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY e.{col('doc_entete', 'code_tiers')}
                ORDER BY panier_moyen DESC
            """,
            "description": "Panier moyen par client",
        }
    ),

    # ── Clients sans commande depuis N mois (version générique) ──
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"clients?.{0,30}(?:pas\s+command[eé]|sans\s+commande|inactifs?)\s+depuis\s+(?:plus\s+de\s+)?(\d+)\s+mois",
        lambda m, conn: {
            "sql": f"""
                SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                       MAX(e.{col('doc_entete', 'date')}) AS derniere_facture,
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures_total
                FROM {table('clients_fournisseurs')} c
                LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                    AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
                GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
                HAVING MAX(e.{col('doc_entete', 'date')}) IS NULL
                    OR MAX(e.{col('doc_entete', 'date')}) < {_date_sub_days(int(m.group(1)) * 30)}
                ORDER BY derniere_facture ASC
            """,
            "description": f"Clients sans commande depuis {m.group(1)} mois",
        }
    ),

    # ── Liste globale de tous les BL de vente ─────────────────────────
    # NE PAS MODIFIER : pattern explicitement dédié aux BL (type=3 correct).
    (
        r"(?:liste|affiche|montre|donne|toutes?)\s+(?:toutes?\s+|des\s+|les\s+)?bls?$",
        lambda m, conn: {
            "sql": f"""
                SELECT e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht
                FROM {table('doc_entete')} e
                LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 3 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Liste des bons de livraison",
        }
    ),

    # ── BL non facturés ──────────────────────────────────────────────
    # NE PAS MODIFIER : pattern explicitement dédié aux BL (type=3 correct,
    # les deux occurrences ci-dessous incluses).
    (
        r"bl\s+non\s+factur[eé]s?|bons?\s+de\s+livraison\s+non\s+factur[eé]s?|liste\s+(?:des?\s+)?bl\s+(?:non|en\s+attente)",
        lambda m, conn: {
            "sql": f"""
                SELECT e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht
                FROM {table('doc_entete')} e
                LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 3 AND e.{col('doc_entete', 'domaine')} = 0
                  AND e.{col('doc_entete', 'piece')} NOT IN (
                      SELECT DISTINCT {col('doc_entete', 'reference')}
                      FROM {table('doc_entete')}
                      WHERE {col('doc_entete', 'type')} = 3 AND {col('doc_entete', 'domaine')} = 0 AND {col('doc_entete', 'reference')} IS NOT NULL AND {col('doc_entete', 'reference')} != ''
                  )
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "BL non facturés",
        }
    ),

    # ── Articles en rupture de stock (liste des articles EN RUPTURE) ──
    (
        r"articles?\s+en\s+rupture|rupture\s+de\s+stock|articles?\s+(?:avec\s+)?stock\s+(?:nul|[zé]ro|négatif|faible|bas)|liste\s+(?:des?\s+)?ruptures?",
        lambda m, conn: {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock_disponible,
                       COALESCE(s.{col('stock', 'qte_commande')}, 0) AS en_commande,
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) - COALESCE(s.{col('stock', 'qte_commande')}, 0) AS stock_net,
                       a.{col('articles', 'prix_vente')} AS prix_vente
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE COALESCE(s.{col('stock', 'qte_stock')}, 0) <= 0
                ORDER BY a.{col('articles', 'ref')}
            """,
            "description": "Articles en rupture de stock",
        }
    ),

    # ── Clients inactifs depuis N mois ───────────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"clients?\s+(?:qui\s+)?n['\u2019]ont\s+pas\s+command[eé]\s+depuis\s+(\d+)\s+mois"
        r"|clients?\s+inactifs?\s+depuis\s+(\d+)\s+mois"
        r"|quels?\s+clients?.{0,30}command[eé].{0,20}depuis\s+(\d+)\s+mois",
        lambda m, conn: {
            "sql": f"""
                SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                       MAX(e.{col('doc_entete', 'date')}) AS derniere_facture,
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures
                FROM {table('clients_fournisseurs')} c
                LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                    AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
                GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
                HAVING MAX(e.{col('doc_entete', 'date')}) IS NULL
                    OR MAX(e.{col('doc_entete', 'date')}) < {_date_sub_days(int(next((g for g in m.groups() if g), 6)) * 30)}
                ORDER BY MAX(e.{col('doc_entete', 'date')}) ASC
            """,
            "description": f"Clients inactifs depuis {next((g for g in m.groups() if g), '6')} mois",
        }
    ),

    # ── Factures par mois (nom ou numéro) ────────────────────────────
    (
        r"factures?.*?(?:du\s+mois\s+(?:de\s+)?|mois\s+)(\d{1,2})\b"
        r"|factures?.*?mis\s+(\d{1,2})\b",
        _gen_factures_mois_numero,
    ),

    # ── Factures par mois (nom littéral) ─────────────────────────────
    (
        r"factures?.*?(?:du\s+mois\s+(?:de\s+)?|en\s+|de\s+)?(janvier|février|fevrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)",
        _gen_factures_mois_nom,
    ),

    # ── Articles filtrés par prix de vente ────────────────────────────
    (
        r"articles?.{0,40}prix.{0,20}(?:vente|ven)?.{0,20}(?:sup[eé]r|d[eé]passe|plus\s+(?:de|que)|>\s*)\s*(\d+(?:[.,]\d+)?)",
        lambda m, conn: {
            "sql": f"""
                SELECT {col('articles', 'ref')}, {col('articles', 'designation')},
                       {col('articles', 'prix_vente')} AS prix_vente,
                       {col('articles', 'prix_achat')} AS prix_achat,
                       ROUND({col('articles', 'prix_vente')} - {col('articles', 'prix_achat')}, 2) AS marge
                FROM {table('articles')}
                WHERE {col('articles', 'prix_vente')} > {float((next((g for g in m.groups() if g), "0")).replace(",", "."))}
                ORDER BY {col('articles', 'prix_vente')} DESC
            """,
            "description": f"Articles avec prix de vente > {next((g for g in m.groups() if g), '0')} €",
        }
    ),

    # ── Articles filtrés par prix inférieur ───────────────────────────
    (
        r"articles?.{0,40}prix.{0,20}(?:vente|ven)?.{0,20}(?:inf[eé]r|moins\s+(?:de|que)|<\s*)\s*(\d+(?:[.,]\d+)?)",
        lambda m, conn: {
            "sql": f"""
                SELECT {col('articles', 'ref')}, {col('articles', 'designation')},
                       {col('articles', 'prix_vente')} AS prix_vente,
                       {col('articles', 'prix_achat')} AS prix_achat
                FROM {table('articles')}
                WHERE {col('articles', 'prix_vente')} < {float((next((g for g in m.groups() if g), "0")).replace(",", "."))} AND {col('articles', 'prix_vente')} > 0
                ORDER BY {col('articles', 'prix_vente')} ASC
            """,
            "description": f"Articles avec prix de vente < {next((g for g in m.groups() if g), '0')} €",
        }
    ),

    # ── Clients avec plus/moins de N commandes ────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"clients?.{0,50}(?:plus\s+de|au\s+moins|plus\s+qu[e'\u2019])\s+(\d+)\s+(?:commandes?|factures?|achats?)",
        lambda m, conn: {
            "sql": f"""
                SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS ca_total
                FROM {table('clients_fournisseurs')} c
                JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')} AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
                GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
                HAVING COUNT(DISTINCT e.{col('doc_entete', 'piece')}) > {int(m.group(1))}
                ORDER BY nb_factures DESC
            """,
            "description": f"Clients avec plus de {m.group(1)} commandes/factures",
        }
    ),

    # ── Clients avec moins de N commandes ────────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"clients?.{0,50}(?:moins\s+de|moins\s+qu[e'\u2019])\s+(\d+)\s+(?:commandes?|factures?|achats?)",
        lambda m, conn: {
            "sql": f"""
                SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures
                FROM {table('clients_fournisseurs')} c
                LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')} AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
                GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
                HAVING COUNT(DISTINCT e.{col('doc_entete', 'piece')}) < {int(m.group(1))}
                ORDER BY nb_factures ASC
            """,
            "description": f"Clients avec moins de {m.group(1)} commandes/factures",
        }
    ),

    # ── Liste globale de toutes les factures ──────────────────────────
    # FIX v4.3 : la description annonçait déjà "factures de vente" mais le
    # SQL tapait sur type=3 (BL) → corrigé en type=6.
    (
        r"(?:liste|affiche|montre|donne|toutes?)\s+(?:toutes?\s+|des\s+|les\s+)?factures?$",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')},
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
                       CASE WHEN r.{col('reglements', 'piece')} IS NOT NULL THEN 'RÉGLÉE' ELSE 'EN ATTENTE' END AS statut
                FROM {table('doc_entete')} e
                LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                LEFT JOIN {table('reglements')} r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}, r.{col('reglements', 'piece')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Liste de toutes les factures de vente",
        }
    ),

    # ── PRIORITÉ 1 : Factures d'un fournisseur spécifique ────────────
    # AUDIT FOURNISSEUR (non corrigé — voir _gen_factures_fournisseur_specifique).
    (
        rf"factures?\s+(?:du\s+|de\s+|d[eu]\s+)?{_FOURNISSEUR_RX}\s+([A-Z0-9]+)"
        rf"|factures?\s+{_FOURNISSEUR_RX}.*?([A-Z]{{1,}}\d{{1,}}[A-Z0-9]*)"
        rf"|liste.*factures?.*{_FOURNISSEUR_RX}.*?([A-Z]{{1,}}\d{{1,}}[A-Z0-9]*)",
        _gen_factures_fournisseur_specifique,
    ),

    # ── PRIORITÉ 2 : Factures fournisseur globales (sans code) ───────
    # AUDIT FOURNISSEUR (non corrigé — type=16/domaine=1 non confirmé).
    (
        rf"factures?\s+{_FOURNISSEUR_RX}|factures?\s+(?:d.achat|achat)|achats?\s+factur",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')} AS code_fournisseur,
                       c.{col('clients_fournisseurs', 'nom')} AS nom_fournisseur,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht
                FROM {table('doc_entete')} e
                LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 16 AND e.{col('doc_entete', 'domaine')} = 1
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                         e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Factures fournisseur (achats)",
        }
    ),

    # ── PRIORITÉ 3 : Liste fournisseurs (générique) ───────────────────
    # /!\ DOIT ÊTRE APRÈS les patterns "factures fournisseur"
    (
        rf"^(?:liste\s+)?(?:les\s+)?{_FOURNISSEUR_RX}s?$"
        rf"|^(?:tous\s+)?(?:les\s+)?{_FOURNISSEUR_RX}s?$",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(100)}{col('clients_fournisseurs', 'code')}, {col('clients_fournisseurs', 'nom')},
                       COALESCE({col('clients_fournisseurs', 'encours')}, 0)    AS encours,
                       COALESCE({col('clients_fournisseurs', 'encours_max')}, 0) AS encours_max,
                       COALESCE({col('clients_fournisseurs', 'validite')}, 'VALIDE') AS statut
                FROM {table('clients_fournisseurs')}
                WHERE {col('clients_fournisseurs', 'type_tiers')} = 1
                ORDER BY {col('clients_fournisseurs', 'nom')}
            """,
            "description": "Liste des fournisseurs",
        }
    ),

    # ── Bons de réception fournisseur ───────────────────────────────
    # AUDIT FOURNISSEUR (non corrigé — type=13/domaine=1 non confirmé,
    # à recouper avec les codes 16/6/3 utilisés ailleurs pour fournisseur).
    (
        rf"(?:bons?\s+de\s+)?r[eé]ceptions?(?:\s+{_FOURNISSEUR_RX})?|bl\s+achat|livraison\s+{_FOURNISSEUR_RX}",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')} AS code_fournisseur,
                       c.{col('clients_fournisseurs', 'nom')} AS nom_fournisseur,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht
                FROM {table('doc_entete')} e
                LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 13 AND e.{col('doc_entete', 'domaine')} = 1
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                         e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Bons de livraison achat (BL fournisseur)",
        }
    ),

    # ── Produits finis définis en nomenclature ─────────────────────────
    (
        r"produits?\s+finis?|articles?\s+finis?",
        lambda m, conn: {
            "sql": f"""
                SELECT DISTINCT a.{col('articles', 'ref')} AS ref,
                                a.{col('articles', 'designation')} AS designation
                FROM {table('articles')} a
                JOIN {table('nomenclature')} n
                  ON UPPER(a.{col('articles', 'ref')}) = UPPER(n.{col('nomenclature', 'ref_pf')})
                WHERE UPPER(a.{col('articles', 'ref')}) NOT IN (
                    SELECT UPPER({col('nomenclature', 'ref_mp')}) FROM {table('nomenclature')}
                )
                ORDER BY a.{col('articles', 'ref')}
            """,
            "description": "Produits finis (non utilisés comme composant d'un autre article)",
        }
    ),

    # ── Matières premières présentes dans la nomenclature ─────────────
    (
        r"mati[èe]res?\s+premi[eè]res?|mati[èe]re\s+premi[eè]re",
        lambda m, conn: {
            "sql": f"""
                SELECT DISTINCT a.{col('articles', 'ref')} AS ref,
                                a.{col('articles', 'designation')} AS designation
                FROM {table('articles')} a
                JOIN {table('nomenclature')} n
                  ON UPPER(a.{col('articles', 'ref')}) = UPPER(n.{col('nomenclature', 'ref_mp')})
                WHERE UPPER(a.{col('articles', 'ref')}) NOT IN (
                    SELECT UPPER({col('nomenclature', 'ref_pf')}) FROM {table('nomenclature')}
                )
                ORDER BY a.{col('articles', 'ref')}
            """,
            "description": "Matières premières (composants sans nomenclature propre)",
        }
    ),

    # ── Composants semi-finis / sous-ensembles intermédiaires ─────────
    (
        r"(?:articles?|produits?|composants?)\s+semi[\s\-]finis?"
        r"|semi[\s\-]finis?"
        r"|sous[\s\-]ensembles?"
        r"|composants?\s+interm[eé]diaires?",
        lambda m, conn: {
            "sql": f"""
                SELECT DISTINCT a.{col('articles', 'ref')} AS ref,
                                a.{col('articles', 'designation')} AS designation
                FROM {table('articles')} a
                WHERE UPPER(a.{col('articles', 'ref')}) IN (
                    SELECT UPPER({col('nomenclature', 'ref_pf')}) FROM {table('nomenclature')}
                )
                AND UPPER(a.{col('articles', 'ref')}) IN (
                    SELECT UPPER({col('nomenclature', 'ref_mp')}) FROM {table('nomenclature')}
                )
                ORDER BY a.{col('articles', 'ref')}
            """,
            "description": "Composants semi-finis (ont leur propre nomenclature ET sont utilisés dans une autre)",
        }
    ),

    # ── Liste articles ────────────────────────────────────────────────
    (
        r"(?:liste|tous|toutes|affiche|montre|donne).*(?:produits?|articles?|catalogue|référence)",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       a.{col('articles', 'prix_vente')},
                       COALESCE(SUM(s.{col('stock', 'qte_stock')}), 0) AS stock
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                GROUP BY a.{col('articles', 'ref')}, a.{col('articles', 'designation')}, a.{col('articles', 'prix_vente')}
                ORDER BY a.{col('articles', 'ref')}
            """,
            "description": "Liste des articles du catalogue",
        }
    ),

    # ── Clients ayant acheté un article ──────────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"meilleurs?\s+clients?(?:\s+par\s+ca)?(?:\s+top\s*(\d+))?",
        lambda m, conn: {
            "sql": f"""
                SELECT e.{col('doc_entete', 'code_tiers')},
                       c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')})         AS nb_factures,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}) AS ca_total
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY e.{col('doc_entete', 'code_tiers')}
                ORDER BY ca_total DESC
                LIMIT {int(m.group(1)) if m.group(1) else 10}
            """,
            "description": "Top clients par CA",
        }
    ),

    # ── Clients bloqués ──────────────────────────────────────────────
    (
        r"clients?\s+bloqu[eé]s?",
        lambda m, conn: {
            "sql": f"""
                SELECT {col('clients_fournisseurs', 'code')}, {col('clients_fournisseurs', 'nom')},
                       {col('clients_fournisseurs', 'encours')}, {col('clients_fournisseurs', 'validite')}
                FROM {table('clients_fournisseurs')}
                WHERE {col('clients_fournisseurs', 'type_tiers')} = 0
                  AND UPPER({col('clients_fournisseurs', 'validite')}) = 'BLOQUE'
                ORDER BY {col('clients_fournisseurs', 'nom')}
            """,
            "description": "Clients bloqués",
        }
    ),

    # ── Clients inactifs ──────────────────────────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6 (domaine=0 ajouté par
    # cohérence avec les autres analyses de factures).
    (
        r"clients?\s+(?:sans\s+commande|inactifs?|qui\s+n.ont\s+pas\s+command[eé])"
        r"(?:\s+depuis\s+(\d+)\s+(mois|ans?))?",
        lambda m, conn: {
            "sql": f"""
                SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                       MAX(e.{col('doc_entete', 'date')}) AS derniere_commande
                FROM {table('clients_fournisseurs')} c
                LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                    AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
                GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
                HAVING derniere_commande IS NULL
                    OR derniere_commande < {_date_sub_days(_jours_depuis(m.group(1) if m.lastindex and m.lastindex >= 1 else None, m.group(2) if m.lastindex and m.lastindex >= 2 else "mois"))}
                ORDER BY derniere_commande ASC
            """,
            "description": "Clients inactifs",
        }
    ),

    # ── Encours client ────────────────────────────────────────────────
    (
        r"(?:encours|cr[eé]dit)\s+(?:du\s+)?client\s+(.+)",
        lambda m, conn: {
            "sql": _sql_client_encours(m.group(1).strip(), conn),
            "description": f"Encours client '{m.group(1)}'",
        }
    ),
    (
        r"(?:encours|cr[eé]dit)\s+(?:du\s+client\s+)?([A-Z]{2,}\d{3,})",
        lambda m, conn: {
            "sql": _sql_client_encours(m.group(1).strip(), conn),
            "description": f"Encours client '{m.group(1)}'",
        }
    ),

    # ── Rupture de stock ──────────────────────────────────────────────
    (
        r"articles?\s+(?:en\s+)?rupture(?:\s+de\s+stock)?",
        lambda m, conn: {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')},  0) AS stock,
                       COALESCE(s.{col('stock', 'qte_commande')},  0) AS en_commande
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE COALESCE(s.{col('stock', 'qte_stock')}, 0) <= 0
                ORDER BY a.{col('articles', 'ref')}
            """,
            "description": "Articles en rupture de stock",
        }
    ),

    # ── Stock d'un article ────────────────────────────────────────────
    (
        r"stock\s+(?:de\s+|du\s+)?(?:l.article\s+)?(.+)",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(5)}a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')},  0)                              AS qte_stock,
                       COALESCE(s.{col('stock', 'qte_commande')},  0)                              AS qte_commande,
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) - COALESCE(s.{col('stock', 'qte_commande')}, 0)   AS qte_nette
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE UPPER(a.{col('articles', 'ref')})    LIKE UPPER('%{m.group(1).strip()}%')
                   OR UPPER(a.{col('articles', 'designation')}) LIKE UPPER('%{m.group(1).strip()}%')
            """,
            "description": f"Stock article '{m.group(1)}'",
        }
    ),

    # ── Articles les plus vendus ──────────────────────────────────────
    (
        r"articles?\s+les?\s+plus?\s+vendu[se]?s?(?:\s+(ce\s+mois|cette\s+semaine|en\s+(\d{4})))?",
        _gen_articles_plus_vendus,
    ),

    # ── Marge brute sur un article spécifique (v4.2 — nouveau) ────────
    # /!\ DOIT ÊTRE AVANT le pattern générique "Marge brute par article",
    # sinon "marge sur l'article MONTRE01" retomberait sur la liste
    # complète non filtrée.
    (
        r"marge\s+(?:brute\s+)?(?:sur|de|pour)\s+(?:l['\u2019]article\s+)?([A-Za-z0-9\-]+)",
        _gen_marge_article_specifique,
    ),

    # ── Marge brute par article (liste complète) ───────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6.
    (
        r"marge\s+(?:brute\s+)?(?:sur\s+|de\s+|par\s+)?article",
        lambda m, conn: {
            "sql": f"""
                SELECT l.{col('doc_ligne', 'ref_article')}, a.{col('articles', 'designation')},
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})  AS ca_vente,
                       SUM(l.{col('doc_ligne', 'qte')} * a.{col('articles', 'prix_achat')})        AS cout_achat,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})
                         - SUM(l.{col('doc_ligne', 'qte')} * a.{col('articles', 'prix_achat')})    AS marge_brute,
                       ROUND(
                         (SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})
                          - SUM(l.{col('doc_ligne', 'qte')} * a.{col('articles', 'prix_achat')}))
                         / NULLIF(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) * 100,
                       1) AS taux_marge_pct
                FROM {table('doc_ligne')}  l
                JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
                LEFT JOIN {table('articles')} a ON l.{col('doc_ligne', 'ref_article')} = a.{col('articles', 'ref')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY l.{col('doc_ligne', 'ref_article')}
                ORDER BY marge_brute DESC
            """,
            "description": "Marge brute par article",
        }
    ),

    # ── Factures non réglées ──────────────────────────────────────────
    (
        r"factures?\s+(?:non\s+réglées?|impayées?|en\s+attente)"
        r"(?:\s+(?:du\s+|de\s+)?client\s+(.+))?",
        lambda m, conn: {
            "sql": _sql_factures_non_reglees(
                m.group(1).strip() if m.group(1) else "", conn
            ),
            "description": "Factures non réglées",
        }
    ),

    # ── CA d'un client ────────────────────────────────────────────────
    (
        r"(?:encours|cr[eé]dit)\s+.+"  # garde-fou : ne jamais capturer une question "encours"/"crédit" ici
        r"(?!)",  # pattern jamais vrai — placeholder retiré ci-dessous
        lambda m, conn: {"sql": "", "description": ""},
    ),
]

# ── PATCH v4.2 : retrait du garde-fou placeholder ci-dessus (il ne sert
# qu'à documenter l'intention) et ajout du VRAI pattern "CA d'un client",
# avec une négation explicite pour ne jamais intercepter une question
# contenant "encours" ou "crédit" (cause du bug #1 : "encours client X"
# tombant sur le pattern CA à cause d'un test de sous-chaîne trop large
# dans le fallback générique — corrigé aussi dans _generer_sql_generique
# ci-dessous, cette négation est une deuxième barrière de sécurité).
_NL_PATTERNS.pop()  # retire le placeholder ci-dessus

# ── FIX prob2/prob5 : Insérer les patterns SPÉCIFIQUES avant le pattern
# générique "CA client" qui est trop vorace et capture "CA par mois" ou
# "clients qui n'ont pas commandé" avant les patterns dédiés.
# ─────────────────────────────────────────────────────────────────────

# CA mensuel — DOIT être testé avant le pattern générique "CA client"
# FIX v4.3 : type=3 = BL → type=6.
_NL_PATTERNS.append((
    r"(?:ca|chiffre\s+d.affaires?)\s+(?:par\s+mois|mensuel)",
    lambda m, conn: {
        "sql": f"""
            SELECT {_top(24)}{_fmt_mois(f"e.{col('doc_entete', 'date')}")}            AS mois,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})               AS nb_factures,
                   SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})       AS ca_ht
            FROM {table('doc_entete')} e
            JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
            GROUP BY {_fmt_mois(f"e.{col('doc_entete', 'date')}")}
            ORDER BY mois DESC
        """,
        "description": "CA mensuel",
    }
))

# Clients inactifs depuis N mois — DOIT être testé avant le pattern générique "CA client"
# FIX v4.3 : type=3 = BL → type=6.
_NL_PATTERNS.append((
    r"clients?\s+(?:qui\s+)?n[''´]?ont\s+pas\s+command[eé]\s+depuis\s+(\d+)\s+mois"
    r"|clients?\s+inactifs?\s+depuis\s+(\d+)\s+mois"
    r"|quels?\s+clients?.{0,30}command[eé].{0,20}depuis\s+(\d+)\s+mois"
    r"|clients?.{0,30}(?:pas\s+command[eé]|sans\s+commande|inactifs?)\s+depuis\s+(?:plus\s+de\s+)?(\d+)\s+mois",
    lambda m, conn: {
        "sql": f"""
            SELECT c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')},
                   MAX(e.{col('doc_entete', 'date')}) AS derniere_facture,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_factures
            FROM {table('clients_fournisseurs')} c
            LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                AND e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
            WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
            GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
            HAVING MAX(e.{col('doc_entete', 'date')}) IS NULL
                OR MAX(e.{col('doc_entete', 'date')}) < {_date_sub_days(int(next((g for g in m.groups() if g), 6)) * 30)}
            ORDER BY MAX(e.{col('doc_entete', 'date')}) ASC
        """,
        "description": f"Clients sans commande depuis {next((g for g in m.groups() if g), '6')} mois",
    }
))

_NL_PATTERNS.append((
    r"(?<!encours\s)(?<!cr[eé]dit\s)(?:ca|chiffre\s+d.affaires?)\s+(?:du\s+|de\s+)?(?:client\s+)?(.+)"
    r"(?:\s+en\s+(\d{4}))?",
    lambda m, conn: {
        "sql": _sql_ca_client(
            m.group(1).strip(), conn,
            m.group(2) if m.lastindex and m.lastindex >= 2 else None
        ),
        "description": f"CA client '{m.group(1)}'",
    }
))

_NL_PATTERNS.extend([
    # CA par annee - FIX v4.3 : type=3 = BL, pas facture
    (
        r"(?:ca|chiffre\s+d.affaires?)\s+(?:par\s+)?(\d{4})",
        lambda m, conn: {
            "sql": f"""
                SELECT {_fmt_mois(f"e.{col('doc_entete', 'date')}")}        AS mois,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})  AS ca_ht,
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')})           AS nb_factures
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                  AND {_fmt_annee(f"e.{col('doc_entete', 'date')}")} = '{m.group(1)}'
                GROUP BY {_fmt_mois(f"e.{col('doc_entete', 'date')}")}
                ORDER BY mois
            """,
            "description": f"CA par mois en {m.group(1)}",
        }
    ),

    # ── Documents sur une période ─────────────────────────────────────
    (
        r"documents?\s+(?:du\s+client\s+)?(.+?)\s+(?:entre|du)\s+"
        r"(\d{4}-\d{2}-\d{2})\s+(?:et|au)\s+(\d{4}-\d{2}-\d{2})",
        lambda m, conn: {
            "sql": _sql_docs_periode(
                m.group(1).strip(), m.group(2), m.group(3), conn
            ),
            "description": f"Documents {m.group(1)} du {m.group(2)} au {m.group(3)}",
        }
    ),

    # ── DSO / délai de paiement ───────────────────────────────────────
    (
        r"(?:délai|dso|retard)\s+(?:de\s+)?paiement"
        r"(?:\s+(?:du\s+|de\s+)?client\s+(.+))?",
        lambda m, conn: {
            "sql": _sql_dso(
                m.group(1).strip() if m.group(1) else "", conn
            ),
            "description": "Délai moyen de paiement (DSO)",
        }
    ),

    # ── Ordres de fabrication (OF) ────────────────────────────────────
    # type=25 domaine=2 déjà correct (cf. docstring Vanna) — inchangé.
    (
        r"\bofs?\b|ordres?\s+de\s+fabrication",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')} AS code_client,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')}), 0) AS qte_totale
                FROM {table('doc_entete')} e
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 25 AND e.{col('doc_entete', 'domaine')} = 2
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Liste des ordres de fabrication (OF)",
        }
    ),

    # ── Bons de fabrication (BF) ──────────────────────────────────────
    # type=26 domaine=2 déjà correct (cf. docstring Vanna) — inchangé.
    (
        r"\bbfs?\b|bons?\s+de\s+fabrication",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')} AS code_client,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')}), 0) AS qte_totale
                FROM {table('doc_entete')} e
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 26 AND e.{col('doc_entete', 'domaine')} = 2
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Liste des bons de fabrication (BF)",
        }
    ),

    # ── Bons de commande client (BC) ──────────────────────────────────
    # type=1 domaine=0 déjà correct (cf. docstring Vanna) — inchangé.
    # /!\ Doit être AVANT le pattern générique résumé/KPI
    (
        r"\bbcs?\b|bons?\s+de\s+commande\s+(?:client|achat|vente|client|achat)?",
        lambda m, conn: {
            "sql": f"""
                SELECT {_top(50)}e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')},
                       e.{col('doc_entete', 'code_tiers')} AS code_client,
                       c.{col('clients_fournisseurs', 'nom')} AS nom_client,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht
                FROM {table('doc_entete')} e
                LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')} = 1 AND e.{col('doc_entete', 'domaine')} = 0
                GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "description": "Liste des bons de commande client (BC)",
        }
    ),

    # ── Stats générales ───────────────────────────────────────────────
    (
        r"(?:nombre|combien)\s+(?:de\s+)?clients?",
        lambda m, conn: {
            "sql": f"SELECT COUNT(*) AS nb_clients FROM {table('clients_fournisseurs')} WHERE {col('clients_fournisseurs', 'type_tiers')} = 0",
            "description": "Nombre de clients",
        }
    ),

    # FIX v4.3 : codes type totalement erronés (1=BC pas OF, 2 n'existe
    # pas, 3=BL pas facture, 9 n'existe pas [avoir=5]). Corrigés selon le
    # docstring Vanna, avec alias renommés en conséquence.
    (
        r"(?:nombre|combien)\s+(?:de\s+)?(?:factures?|documents?)",
        lambda m, conn: {
            "sql": f"""
                SELECT
                    COUNT(*) AS nb_total,
                    SUM(CASE WHEN {col('doc_entete', 'type')}=1 THEN 1 ELSE 0 END) AS bc,
                    SUM(CASE WHEN {col('doc_entete', 'type')}=3 THEN 1 ELSE 0 END) AS bl,
                    SUM(CASE WHEN {col('doc_entete', 'type')}=6 THEN 1 ELSE 0 END) AS factures,
                    SUM(CASE WHEN {col('doc_entete', 'type')}=5 THEN 1 ELSE 0 END) AS avoirs
                FROM {table('doc_entete')}
            """,
            "description": "Nombre de documents par type",
        }
    ),

    (
        r"(?:nombre|combien)\s+(?:de\s+)?articles?",
        lambda m, conn: {
            "sql": f"SELECT COUNT(*) AS nb_articles FROM {table('articles')}",
            "description": "Nombre d'articles",
        }
    ),

    # ── KPI / tableau de bord ─────────────────────────────────────────
    # FIX v4.3 : type=3 = BL, pas facture → type=6 (+ domaine=0 explicite).
    (
        r"r[eé]sum[eé]|vue\s+d.ensemble|tableau\s+de\s+bord|kpi",
        lambda m, conn: {
            "sql": f"""
                SELECT
                  (SELECT COUNT(*) FROM {table('clients_fournisseurs')} WHERE {col('clients_fournisseurs', 'type_tiers')}=0)
                    AS nb_clients,
                  (SELECT COUNT(*) FROM {table('articles')})
                    AS nb_articles,
                  (SELECT COUNT(*) FROM {table('doc_entete')} WHERE {col('doc_entete', 'type')}=6 AND {col('doc_entete', 'domaine')}=0)
                    AS nb_factures,
                  (SELECT COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0)
                   FROM {table('doc_ligne')} l
                   JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
                   WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0)
                    AS ca_total,
                  (SELECT COUNT(*)
                   FROM {table('doc_entete')}
                   WHERE {col('doc_entete', 'type')}=6 AND {col('doc_entete', 'domaine')}=0
                     AND {col('doc_entete', 'piece')} NOT IN (SELECT {col('reglements', 'piece')} FROM {table('reglements')}))
                    AS factures_impayees
            """,
            "description": "Résumé général KPI",
        }
    ),
])


# ─────────────────────────────────────────────────────────────────────
# SQL GÉNÉRIQUE — neutre (table()/col())
# ─────────────────────────────────────────────────────────────────────
_MOTS_QUALIFICATIFS_GENERIQUE = (
    "impayé", "impayés", "inactif", "inactifs", "bloqué", "bloqués",
    "moins de", "plus de", "supérieur", "supérieures", "inférieur",
    "encours", "gros", "commandes", "achats", "factures"
)


def _generer_sql_generique(
    q: str, code_client: str, conn: sqlite3.Connection
) -> dict | None:
    m_art = re.search(r"\b([A-Z]{2,}[0-9]{2,}|[A-Z][A-Z0-9\-]{3,})\b", q.upper())
    art_ref = m_art.group(1) if m_art else None

    if art_ref and "stock" in q:
        return {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock,
                       COALESCE(s.{col('stock', 'qte_commande')}, 0) AS en_commande,
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) - COALESCE(s.{col('stock', 'qte_commande')}, 0) AS stock_net
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE UPPER(a.{col('articles', 'ref')}) = '{art_ref}'
            """,
            "desc": f"Stock article {art_ref}",
        }

    # FIX v4.2 : "ca" in q était un test de SOUS-CHAÎNE — "carat" contient
    # "ca" ! Toute question "encours du client CARAT" qui échouait (pour
    # une autre raison) sur le pattern dédié retombait ici et matchait à
    # tort la branche CA. Remplacé par un test à frontière de mot, et
    # explicitement exclu si la question porte sur l'encours/crédit.
    # FIX v4.3 : type=3 = BL, pas facture → type=6, + ajout domaine=0
    # (absent à l'origine, ce qui pouvait mélanger vente/achat).
    if (
        code_client
        and re.search(r"\b(?:ca|chiffre|vente|facture)\b", q)
        and not re.search(r"\b(?:encours|cr[eé]dit)\b", q)
    ):
        return {
            "sql": f"""
                SELECT e.{col('doc_entete', 'code_tiers')},
                       c.{col('clients_fournisseurs', 'nom')},
                       COUNT(DISTINCT e.{col('doc_entete', 'piece')})            AS nb_factures,
                       SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})    AS ca_total
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0
                  AND e.{col('doc_entete', 'code_tiers')} = '{code_client}'
                GROUP BY e.{col('doc_entete', 'code_tiers')}
            """,
            "desc": f"CA client {code_client}",
        }

    # FIX v4.2 : "encours du client X" doit être calculé via _sql_client_encours
    # (solde impayé), jamais retomber sur un résumé générique de documents.
    if code_client and re.search(r"\b(?:encours|cr[eé]dit)\b", q):
        return {
            "sql": _sql_client_encours(code_client, conn),
            "desc": f"Encours client {code_client}",
        }

    if code_client and any(w in q for w in ("commande", "bon", "bl", "bc", "document", "livraison")):
        return {
            "sql": f"""
                SELECT e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'type')}, e.{col('doc_entete', 'date')},
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht
                FROM {table('doc_entete')} e
                LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                WHERE e.{col('doc_entete', 'code_tiers')} = '{code_client}'
                GROUP BY e.{col('doc_entete', 'piece')}
                ORDER BY e.{col('doc_entete', 'date')} DESC
            """,
            "desc": f"Documents client {code_client}",
        }

    if any(w in q for w in ("rupture", "stock faible", "stock bas", "stock nul")):
        return {
            "sql": f"""
                SELECT a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock,
                       COALESCE(s.{col('stock', 'qte_stock')}, 0) - COALESCE(s.{col('stock', 'qte_commande')}, 0) AS stock_net
                FROM {table('articles')} a
                LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
                WHERE COALESCE(s.{col('stock', 'qte_stock')}, 0) <= 0
                ORDER BY stock ASC
            """,
            "desc": "Articles en rupture de stock",
        }

    if not code_client and any(w in q for w in ("client", "clients", "tiers")):
        if any(w in q for w in _MOTS_QUALIFICATIFS_GENERIQUE):
            return None
        return {
            "sql": f"""
                SELECT {col('clients_fournisseurs', 'code')}, {col('clients_fournisseurs', 'nom')}, {col('clients_fournisseurs', 'validite')}, {col('clients_fournisseurs', 'encours')}
                FROM {table('clients_fournisseurs')}
                WHERE {col('clients_fournisseurs', 'type_tiers')} = 0
                ORDER BY {col('clients_fournisseurs', 'nom')}
            """,
            "desc": "Liste des clients",
        }

    if any(w in q for w in ("article", "articles", "produit", "produits", "catalogue")):
        # FIX v4.2 : si un filtre de prix/quantité a été formulé mais que le
        # pattern dédié n'a pas matché (échec de reconnaissance), il vaut
        # mieux le signaler que de retourner silencieusement le catalogue
        # complet non filtré (bug #2). On ajoute donc une garde ici aussi.
        if any(w in q for w in ("prix", "tarif", "coût", "cout", "dt", "€", "eur")):
            return None
        return {
            "sql": f"""
                SELECT {col('articles', 'ref')}, {col('articles', 'designation')}, {col('articles', 'prix_vente')}, {col('articles', 'prix_achat')}
                FROM {table('articles')}
                ORDER BY {col('articles', 'ref')}
            """,
            "desc": "Catalogue articles",
        }

    return None


# ─────────────────────────────────────────────────────────────────────
# OUTIL : executer_sql_vanna
# ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def executer_sql_vanna(sql: str, description: str = "", params: list | None = None) -> str:
    """Exécute un SQL généré par Vanna et retourne le résultat formaté.

    params: liste optionnelle de paramètres bindés (utiliser ? dans le SQL
    pour les valeurs injectées côté orchestrateur, ex: filtre code client).
    """
    try:
        sql = sql.strip().split(";")[0].strip()  # FIX multi-statements
        conn = _connect()
        bound_params = tuple(params) if params else ()
        rows = _executer_sql(conn, sql, bound_params)
        conn.close()
        return _formater_resultats(rows, description or "Résultat Vanna")
    except Exception as e:
        import logging as _log
        _log.getLogger("api.mcp_nl2sql").error(
            f"[executer_sql_vanna] Erreur SQL — requête: {sql[:200]} | exception: {type(e).__name__}: {e}"
        )
        err_str = str(e)
        # Point 1.3 : donner un message d'erreur non trompeur
        if "no such column" in err_str.lower() or "invalid column" in err_str.lower() or "4104" in err_str:
            return f"__ERREUR__:Colonne inconnue dans la requête générée. Détail DB: {err_str}"
        return f"__ERREUR__:{err_str}"


# ─────────────────────────────────────────────────────────────────────
# OUTIL PRINCIPAL : interpreter_et_analyser_via_sql
# ─────────────────────────────────────────────────────────────────────
@mcp.tool()
def interpreter_et_analyser_via_sql(question_metier: str) -> str:
    """
    Moteur NL→SQL neutre vis-à-vis du schéma (adaptation/db_config.json).
    """
    try:
        conn = _connect()
        q    = question_metier.lower().strip()
        code_col = col('clients_fournisseurs', 'code')
        clients_table = table('clients_fournisseurs')
        type_col = col('clients_fournisseurs', 'type_tiers')

        # Résolution client contextuelle
        code_client_ctx = ""
        for pattern in [
            r"(?:du\s+|de\s+|pour\s+)?client\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ0-9\s\-&']{2,40}?)(?:\s*[?.,]|\s*$)",
            r"\b(C[A-Z]{0,2}\d{3,})\b",
        ]:
            m = re.search(pattern, question_metier, re.IGNORECASE)
            if m:
                row_c = _resoudre_client(conn, m.group(1).strip())
                if row_c:
                    code_client_ctx = row_c[code_col]
                    break

        # Extraction du code injecté par l'orchestrateur
        m_code_injecte = re.search(r"\(code:\s*([A-Z0-9]+)\)", question_metier)
        if m_code_injecte and not code_client_ctx:
            code_injecte = m_code_injecte.group(1).upper()
            # Vérifier si c'est un fournisseur
            row_four = conn.execute(
                f"SELECT {code_col} FROM {clients_table} WHERE {code_col}=? AND {type_col}=1",
                (code_injecte,)
            ).fetchone()
            if row_four:
                code_fournisseur_ctx = code_injecte
            else:
                code_client_ctx = code_injecte

        # Matching patterns
        import traceback as _tb
        for pattern, generateur in _NL_PATTERNS:
            m = re.search(pattern, q, re.IGNORECASE)
            if m:
                try:
                    result = generateur(m, conn)
                    sql    = result.get("sql", "").strip()
                    desc   = result.get("description", "Résultat")
                    if sql:
                        print(f"   🎯 [NL2SQL Debug] Pattern matché : {pattern[:60]}")
                        rows = _executer_sql(conn, sql)
                        conn.close()
                        return _formater_resultats(rows, desc)
                    elif desc:
                        conn.close()
                        return desc  
                except Exception as e:
                    # FIX : log au lieu d'avaler silencieusement — sinon
                    # on retombe sur le fallback générique sans savoir pourquoi.
                    print(f"⚠️  [NL2SQL] Pattern '{pattern[:40]}...' a échoué : {e}")
                    
                    continue

        # SQL générique
        sql_gen = _generer_sql_generique(q, code_client_ctx, conn)
        if sql_gen:
            rows = _executer_sql(conn, sql_gen["sql"])
            conn.close()
            return _formater_resultats(rows, sql_gen["desc"])

        # Résumé général fallback
        # FIX v4.3 : type=3 = BL, pas facture → type=6, + ajout domaine=0.
        stats = conn.execute(f"""
            SELECT
              (SELECT COUNT(*) FROM {table('clients_fournisseurs')} WHERE {col('clients_fournisseurs', 'type_tiers')}=0)  AS nb_clients,
              (SELECT COUNT(*) FROM {table('articles')})                   AS nb_articles,
              (SELECT COUNT(*) FROM {table('doc_entete')} WHERE {col('doc_entete', 'type')}=6 AND {col('doc_entete', 'domaine')}=0) AS nb_factures,
              (SELECT COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0)
               FROM {table('doc_ligne')} l
               JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
               WHERE e.{col('doc_entete', 'type')} = 6 AND e.{col('doc_entete', 'domaine')} = 0)                              AS ca_total
        """).fetchone()
        conn.close()

        ctx = f"\nClient résolu : {code_client_ctx}" if code_client_ctx else ""
        return (
            f"Question : '{question_metier}'\n"
            f"(Aucun pattern SQL trouvé — résumé général){ctx}\n\n"
            f"📊 Résumé Sage 100 :\n"
            f"  Clients    : {stats['nb_clients']}\n"
            f"  Articles   : {stats['nb_articles']}\n"
            f"  Factures   : {stats['nb_factures']}\n"
            f"  CA Total   : {round(stats['ca_total'] or 0, 2):,.2f} €\n\n"
            f"💡 Essayez : 'top 5 clients', 'articles en rupture', "
            f"'CA mensuel', 'factures impayées'..."
        )
    except Exception as e:
        return f"__ERREUR__:{e}"


# ─────────────────────────────────────────────────────────────────────
# OUTILS MÉTIER — neutralisés via table()/col()
# Toute colonne utile après la requête est aliasée avec son nom LOGIQUE
# (ex: "AS code", "AS nom", "AS piece"...) pour que l'accès par clé
# (row["code"]) reste indépendant du nom physique réel de la colonne.
# ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def rechercher_fiche_client(code_client: str) -> str:
    """Recherche fiche client par code OU nom."""
    try:
        conn = _connect()
        row  = _resoudre_client(conn, code_client)
        if not row:
            conn.close()
            return json.dumps({
                "statut": "NON_TROUVE", "code": "",
                "message": f"Client '{code_client}' absent de la base.",
            }, ensure_ascii=False)

        code_reel = row[col('clients_fournisseurs', 'code')]
        # FIX v4.3 : type=3 = BL, pas facture → type=6 (stats + sous-requête
        # encours_factures).
        stats = conn.execute(f"""
            SELECT COUNT(DISTINCT e.{col('doc_entete', 'piece')})               AS nb_factures,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}), 0) AS ca_total,
                   COALESCE(
                     (SELECT SUM(l2.{col('doc_ligne', 'qte')} * l2.{col('doc_ligne', 'prix_unitaire')})
                      FROM {table('doc_entete')} e2
                      JOIN {table('doc_ligne')} l2 ON e2.{col('doc_entete', 'piece')} = l2.{col('doc_ligne', 'piece')}
                      WHERE e2.{col('doc_entete', 'type')} = 6 AND e2.{col('doc_entete', 'domaine')} = 0
                        AND e2.{col('doc_entete', 'code_tiers')} = ?
                        AND e2.{col('doc_entete', 'piece')} NOT IN (SELECT {col('reglements', 'piece')} FROM {table('reglements')})
                     ), 0) AS encours_factures
            FROM {table('doc_entete')} e
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0 AND e.{col('doc_entete', 'code_tiers')}=?
        """, (code_reel, code_reel)).fetchone()
        conn.close()

        validite = str(row[col('clients_fournisseurs', 'validite')] or "VALIDE").upper()
        statut   = "BLOQUÉ" if validite == "BLOQUE" else validite

        return json.dumps({
            "statut":           "TROUVE",
            "code":             code_reel,
            "nom":              row[col('clients_fournisseurs', 'nom')],
            "validite":         validite,
            "statut_client":    statut,
            "encours":          row[col('clients_fournisseurs', 'encours')] or 0,
            "encours_max":      row[col('clients_fournisseurs', 'encours_max')] or 0,
            "ca_total":         round(stats["ca_total"] or 0, 2),
            "nb_factures":      stats["nb_factures"] or 0,
            "encours_factures": round(stats["encours_factures"] or 0, 2),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def verifier_statut_client(code_client: str) -> str:
    """Statut client par code OU nom."""
    try:
        conn = _connect()
        row  = _resoudre_client(conn, code_client)
        if not row:
            conn.close()
            return f"STATUT : NON_TROUVE\nCode : \nClient '{code_client}' absent."
        validite = str(row[col('clients_fournisseurs', 'validite')] or "VALIDE").upper()
        statut   = "BLOQUE" if validite == "BLOQUE" else validite
        code_reel = row[col('clients_fournisseurs', 'code')]
        nom = row[col('clients_fournisseurs', 'nom')]
        encours = row[col('clients_fournisseurs', 'encours')] or 0
        encours_max = row[col('clients_fournisseurs', 'encours_max')] or 0
        conn.close()
        return (
            f"STATUT : {statut}\n"
            f"Code : {code_reel}\n"
            f"Nom : {nom}\n"
            f"Validité : {validite}\n"
            f"Encours : {encours} €\n"
            f"Encours max : {encours_max} €"
        )
    except Exception as e:
        return f"Erreur statut client : {e}"


@mcp.tool()
def lister_toutes_factures() -> str:
    """Liste globale de toutes les factures récentes. (déjà correct : type=6)"""
    try:
        conn = _connect()
        # Requête pour les 50 dernières factures
        factures = conn.execute(f"""
            SELECT e.{col('doc_entete', 'piece')} AS piece,
                   e.{col('doc_entete', 'date')} AS date_doc,
                   e.{col('doc_entete', 'reference')} AS reference,
                   c.{col('clients_fournisseurs', 'nom')} AS nom_client,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
                   CASE WHEN r.{col('reglements', 'piece')} IS NOT NULL THEN 1 ELSE 0 END AS regle
            FROM {table('doc_entete')} e
            LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            LEFT JOIN {table('reglements')} r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'reference')}, c.{col('clients_fournisseurs', 'nom')}, r.{col('reglements', 'piece')}
            ORDER BY e.{col('doc_entete', 'date')} DESC
        """).fetchmany(50)
        conn.close()

        result = []
        for f in factures:
            regle = bool(f["regle"])
            result.append({
                "piece": f["piece"],
                "date": f["date_doc"],
                "client": f["nom_client"] or "",
                "reference": f["reference"] or "",
                "montant_ht": round(float(f["montant_ht"] or 0.0), 2),
                "statut": "RÉGLÉE" if regle else "EN ATTENTE",
            })

        return json.dumps({
            "statut": "TROUVE",
            "nb_factures": len(result),
            "factures": result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)

@mcp.tool()
def lister_toutes_factures_client(code_client: str) -> str:
    """Liste factures client — montants calculés depuis doc_ligne. (déjà correct : type=6)"""
    try:
        conn       = _connect()
        row_client = _resoudre_client(conn, code_client)
        if not row_client:
            conn.close()
            return json.dumps({
                "statut": "NON_TROUVE", "code": "",
                "message": f"Client '{code_client}' introuvable.",
                "factures": [],
            }, ensure_ascii=False)

        code_reel = row_client[col('clients_fournisseurs', 'code')]
        factures = conn.execute(f"""
            SELECT e.{col('doc_entete', 'piece')} AS piece,
                   MAX(e.{col('doc_entete', 'date')}) AS date_doc,
                   MAX(e.{col('doc_entete', 'reference')}) AS reference,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
                   CASE WHEN MAX(r.{col('reglements', 'piece')}) IS NOT NULL THEN 1 ELSE 0 END AS regle,
                   MAX(r.{col('reglements', 'date_reglement')}) AS date_reglement,
                   MAX(r.{col('reglements', 'type_reglement')}) AS mode_paiement
            FROM {table('doc_entete')} e
            LEFT JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            LEFT JOIN {table('reglements')}  r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0 AND e.{col('doc_entete', 'code_tiers')}=?
            GROUP BY e.{col('doc_entete', 'piece')}
            ORDER BY MAX(e.{col('doc_entete', 'date')}) DESC
        """, (code_reel,)).fetchall()
        conn.close()

        result   = []
        total_ht = total_regle = 0.0
        for f in factures:
            mnt   = _f(f["montant_ht"])
            regle = bool(f["regle"])
            total_ht += mnt
            if regle:
                total_regle += mnt
            result.append({
                "piece":          f["piece"],
                "date":           f["date_doc"],
                "reference":      f["reference"] or "",
                "montant_ht":     round(mnt, 2),
                "regle":          regle,
                "date_reglement": f["date_reglement"] or "",
                "mode_paiement":  f["mode_paiement"] or "",
                "statut":         "RÉGLÉE" if regle else "EN ATTENTE",
            })

        return json.dumps({
            "statut":           "TROUVE",
            "code":             code_reel,
            "nom":              row_client[col('clients_fournisseurs', 'nom')],
            "nb_factures":      len(result),
            "total_ht":         round(total_ht, 2),
            "total_regle":      round(total_regle, 2),
            "total_en_attente": round(total_ht - total_regle, 2),
            "factures":         result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def lister_factures_non_reglees(code_tiers: str = "") -> str:
    """Factures non réglées (absentes de la table reglements). (déjà correct : type=6)"""
    try:
        conn      = _connect()
        code_reel = ""
        if code_tiers:
            row_c = _resoudre_client(conn, code_tiers)
            if row_c:
                code_reel = row_c[col('clients_fournisseurs', 'code')]

        base_sql = f"""
            SELECT e.{col('doc_entete', 'piece')} AS piece,
                   e.{col('doc_entete', 'code_tiers')} AS code_tiers,
                   e.{col('doc_entete', 'date')} AS date_doc,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
                   c.{col('clients_fournisseurs', 'nom')} AS nom
            FROM {table('doc_entete')} e
            LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
              AND e.{col('doc_entete', 'piece')} NOT IN (SELECT {col('reglements', 'piece')} FROM {table('reglements')})
        """
        params = (code_reel,) if code_reel else ()
        suffix = (
            f" AND e.{col('doc_entete', 'code_tiers')}=? GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'code_tiers')}, e.{col('doc_entete', 'date')}, c.{col('clients_fournisseurs', 'nom')} ORDER BY e.{col('doc_entete', 'date')} DESC"
            if code_reel
            else f" GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'code_tiers')}, e.{col('doc_entete', 'date')}, c.{col('clients_fournisseurs', 'nom')} ORDER BY e.{col('doc_entete', 'date')} DESC {_limit(50)}"
        )
        rows = conn.execute(base_sql + suffix, params).fetchall()
        conn.close()

        result   = []
        total_du = 0.0
        for f in rows:
            mnt = _f(f["montant_ht"])
            total_du += mnt
            result.append({
                "piece":     f["piece"],
                "code":      f["code_tiers"],
                "nom":       f["nom"] or f["code_tiers"],
                "date":      f["date_doc"],
                "montant_ht": round(mnt, 2),
            })

        return json.dumps({
            "statut":      "OK",
            "code":        code_reel,
            "nb_factures": len(result),
            "total_du":    round(total_du, 2),
            "factures":    result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def resoudre_article(libelle_ou_ref: str) -> str:
    """Résout un article par ref exacte OU désignation (LIKE)."""
    try:
        conn = _connect()
        base = f"""
            SELECT a.*, COALESCE(s.{col('stock', 'qte_stock')}, 0) AS stock_resolu
            FROM {table('articles')} a
            LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
        """
        row = conn.execute(
            base + f" WHERE UPPER(a.{col('articles', 'ref')})=UPPER(?)",
            (libelle_ou_ref.strip(),),
        ).fetchone()
        if not row:
            row = conn.execute(
                base + f" WHERE UPPER(a.{col('articles', 'designation')}) LIKE UPPER(?)",
                (f"%{libelle_ou_ref.strip()}%",),
            ).fetchone()
        if not row:
            candidats = conn.execute(
                f"SELECT {_top(5)}{col('articles', 'ref')} AS ref, {col('articles', 'designation')} AS designation FROM {table('articles')} "
                f"WHERE UPPER({col('articles', 'ref')}) LIKE UPPER(?) OR UPPER({col('articles', 'designation')}) LIKE UPPER(?)",
                (f"%{libelle_ou_ref}%",) * 2,
            ).fetchall()
            conn.close()
            return json.dumps({
                "trouve": False,
                "message": f"Article '{libelle_ou_ref}' introuvable.",
                "candidats": [{"ref": c["ref"], "designation": c["designation"]}
                              for c in candidats],
            }, ensure_ascii=False)
        conn.close()
        return json.dumps({
            "trouve":      True,
            "ref":         row[col('articles', 'ref')],
            "designation": row[col('articles', 'designation')],
            "prix_vente":  round(row[col('articles', 'prix_vente')] or 0.0, 4),
            "prix_achat":  round(row[col('articles', 'prix_achat')] or 0.0, 4),
            "stock":       round(row["stock_resolu"] or 0.0, 2),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"trouve": False, "message": str(e)}, ensure_ascii=False)


@mcp.tool()
def verifier_stock_article(ref_article: str) -> str:
    """Stock d'un article par ref ou désignation."""
    try:
        conn = _connect()
        base = f"""
            SELECT a.{col('articles', 'ref')} AS ref, a.{col('articles', 'designation')} AS designation,
                   a.{col('articles', 'prix_vente')} AS prix_vente,
                   a.{col('articles', 'prix_achat')} AS prix_achat,
                   COALESCE(s.{col('stock', 'qte_stock')}, 0)  AS qte_stock,
                   COALESCE(s.{col('stock', 'qte_commande')}, 0)  AS qte_commande,
                   COALESCE(s.{col('stock', 'qte_stock')}, 0) - COALESCE(s.{col('stock', 'qte_commande')}, 0) AS qte_nette
            FROM {table('articles')} a
            LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
        """
        row = conn.execute(
            base + f" WHERE UPPER(a.{col('articles', 'ref')})=UPPER(?)",
            (ref_article.strip(),),
        ).fetchone()
        if not row:
            row = conn.execute(
                base + f" WHERE UPPER(a.{col('articles', 'designation')}) LIKE UPPER(?)",
                (f"%{ref_article.strip()}%",),
            ).fetchone()
        conn.close()
        if not row:
            return f"STOCK : NON_TROUVE\nArticle '{ref_article}' introuvable."
        alerte = (
            " ⚠️ RUPTURE DE STOCK" if row["qte_nette"] <= 0
            else " ⚠️ STOCK FAIBLE" if row["qte_nette"] < 10
            else ""
        )
        return (
            f"STOCK : TROUVE\n"
            f"Ref       : {row['ref']}\n"
            f"Design    : {row['designation']}\n"
            f"Prix vente: {round(row['prix_vente'] or 0.0, 2)} €\n"
            f"Prix achat: {round(row['prix_achat'] or 0.0, 2)} €\n"
            f"stock     : {row['qte_stock']}\n"
            f"commande  : {row['qte_commande']}\n"
            f"net       : {row['qte_nette']}{alerte}"
        )
    except Exception as e:
        return f"Erreur stock : {e}"


@mcp.tool()
def obtenir_top_clients(top_n: int = 5) -> str:
    """Top N clients par CA — montants depuis doc_ligne."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT {_top(top_n)} e.{col('doc_entete', 'code_tiers')} AS code,
                   c.{col('clients_fournisseurs', 'nom')} AS nom,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})            AS nb_factures,
                   SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')})    AS ca_total
            FROM {table('doc_entete')} e
            JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            GROUP BY e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}
            ORDER BY ca_total DESC
            {_limit(top_n)}
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut": "OK", "top_n": top_n,
            "clients": [
                {
                    "rang":         i + 1,
                    "code_client":  r["code"],
                    "nom_client":   r["nom"] or r["code"],
                    "nb_factures":  r["nb_factures"],
                    "ca_total":     round(r["ca_total"] or 0, 2),
                }
                for i, r in enumerate(rows)
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def analyser_solvabilite_rfm() -> str:
    """Analyse RFM."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT c.{col('clients_fournisseurs', 'code')} AS code,
                   c.{col('clients_fournisseurs', 'nom')} AS nom,
                   COALESCE(c.{col('clients_fournisseurs', 'validite')}, 'VALIDE')  AS validite,
                   COALESCE(c.{col('clients_fournisseurs', 'encours')},   0)          AS encours,
                   COALESCE(c.{col('clients_fournisseurs', 'encours_max')},0)          AS encours_max,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})            AS nb_factures,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca_total,
                   MAX(e.{col('doc_entete', 'date')})                        AS derniere_commande
            FROM {table('clients_fournisseurs')} c
            LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                AND e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE c.{col('clients_fournisseurs', 'type_tiers')}=0
            GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}, c.{col('clients_fournisseurs', 'validite')}, c.{col('clients_fournisseurs', 'encours')}, c.{col('clients_fournisseurs', 'encours_max')}
            ORDER BY ca_total DESC
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut": "OK", "nb_clients": len(rows),
            "clients": [
                {
                    "code":              r["code"],
                    "nom":               r["nom"],
                    "statut":            r["validite"],
                    "encours":           round(r["encours"], 2),
                    "encours_max":       round(r["encours_max"], 2),
                    "nb_factures":       r["nb_factures"],
                    "ca_total":          round(r["ca_total"], 2),
                    "derniere_commande": r["derniere_commande"] or "Jamais",
                }
                for r in rows
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def lister_tous_les_articles() -> str:
    """Liste complète des articles avec stock."""
    try:
        conn = _connect()
        rows = conn.execute(f"""
            SELECT a.{col('articles', 'ref')} AS ref, a.{col('articles', 'designation')} AS designation,
                   a.{col('articles', 'prix_vente')} AS prix_vente, a.{col('articles', 'prix_achat')} AS prix_achat,
                   COALESCE(SUM(s.{col('stock', 'qte_stock')}), 0) AS stock
            FROM {table('articles')} a
            LEFT JOIN {table('stock')} s ON a.{col('articles', 'ref')} = s.{col('stock', 'ref')}
            GROUP BY a.{col('articles', 'ref')}, a.{col('articles', 'designation')},
                     a.{col('articles', 'prix_vente')}, a.{col('articles', 'prix_achat')}
            ORDER BY a.{col('articles', 'ref')}
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut":      "OK",
            "nb_articles": len(rows),
            "articles": [
                {
                    "ref":         r["ref"],
                    "designation": r["designation"],
                    "prix_vente":  round(r["prix_vente"] or 0, 2),
                    "prix_achat":  round(r["prix_achat"] or 0, 2),
                    "stock":       round(r["stock"] or 0, 2),
                }
                for r in rows
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def analyser_palmares_articles(top_n: int = 3) -> str:
    """Palmarès articles par CA — montants depuis doc_ligne."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT {_top(top_n)} l.{col('doc_ligne', 'ref_article')} AS ref, a.{col('articles', 'designation')} AS designation,
                   SUM(l.{col('doc_ligne', 'qte')})                     AS qte_vendue,
                   SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}) AS ca_article,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})         AS nb_docs
            FROM {table('doc_ligne')} l
            JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
            LEFT JOIN {table('articles')} a ON l.{col('doc_ligne', 'ref_article')} = a.{col('articles', 'ref')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            GROUP BY l.{col('doc_ligne', 'ref_article')}, a.{col('articles', 'designation')}
            ORDER BY ca_article DESC
            {_limit(top_n)}
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut": "OK", "top_n": top_n,
            "palmares": [
                {
                    "rang":       i + 1,
                    "ref":        r["ref"],
                    "designation": r["designation"] or r["ref"],
                    "qte_vendue": round(r["qte_vendue"] or 0, 2),
                    "ca_article": round(r["ca_article"] or 0, 2),
                    "nb_docs":    r["nb_docs"],
                }
                for i, r in enumerate(rows)
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def calculer_chiffre_affaires_global() -> str:
    """CA global — montants depuis doc_ligne."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        r = conn.execute(f"""
            SELECT COUNT(DISTINCT e.{col('doc_entete', 'piece')})                AS nb_factures,
                   COUNT(DISTINCT e.{col('doc_entete', 'code_tiers')})           AS nb_clients,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca_ht,
                   MIN(e.{col('doc_entete', 'date')})                             AS date_debut,
                   MAX(e.{col('doc_entete', 'date')})                             AS date_fin
            FROM {table('doc_entete')} e
            JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
        """).fetchone()
        conn.close()
        ca_ht = _f(r["ca_ht"])
        return json.dumps({
            "statut":      "OK",
            "nb_factures": r["nb_factures"],
            "nb_clients":  r["nb_clients"],
            "ca_ht":       round(ca_ht, 2),
            "tva_19":      round(ca_ht * 0.19, 2),
            "ca_ttc":      round(ca_ht * 1.19, 2),
            "date_debut":  r["date_debut"],
            "date_fin":    r["date_fin"],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def detecter_clients_en_baisse() -> str:
    """Clients dont le CA a baissé entre les 2 derniers semestres."""
    try:
        conn  = _connect()
        today = datetime.now()
        # Format "AAAAMMJJ" universel pour éviter les erreurs de conversion (242) sur SQL Server
        d_m6  = (today - timedelta(days=180)).strftime("%Y%m%d")
        d_m12 = (today - timedelta(days=360)).strftime("%Y%m%d")

        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        recents = {
            r["code"]: (r["nom"], _f(r["ca"]))
            for r in conn.execute(f"""
                SELECT e.{col('doc_entete', 'code_tiers')} AS code, c.{col('clients_fournisseurs', 'nom')} AS nom,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                LEFT JOIN {table('clients_fournisseurs')} c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
                WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0 AND e.{col('doc_entete', 'date')}>=?
                GROUP BY e.{col('doc_entete', 'code_tiers')}
            """, (d_m6,)).fetchall()
        }
        anciens = {
            r["code"]: _f(r["ca"])
            for r in conn.execute(f"""
                SELECT e.{col('doc_entete', 'code_tiers')} AS code,
                       COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca
                FROM {table('doc_entete')} e
                JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
                WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
                  AND e.{col('doc_entete', 'date')}>=? AND e.{col('doc_entete', 'date')}<?
                GROUP BY e.{col('doc_entete', 'code_tiers')}
            """, (d_m12, d_m6)).fetchall()
        }
        conn.close()

        baisse = []
        for code, (nom, ca_rec) in recents.items():
            ca_anc = anciens.get(code, 0.0)
            if ca_anc > 0 and ca_rec < ca_anc:
                variation = round((ca_rec - ca_anc) / ca_anc * 100, 1)
                baisse.append({
                    "code":          code,
                    "nom":           nom or code,
                    "ca_recent":     round(ca_rec, 2),
                    "ca_ancien":     round(ca_anc, 2),
                    "variation_pct": variation,
                })
        baisse.sort(key=lambda x: x["variation_pct"])
        return json.dumps(
            {"statut": "OK", "nb": len(baisse), "clients": baisse},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def analyser_rentabilite_articles() -> str:
    """Marge brute par article."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT l.{col('doc_ligne', 'ref_article')} AS ref, a.{col('articles', 'designation')} AS designation,
                   SUM(l.{col('doc_ligne', 'qte')})                     AS qte_vendue,
                   SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')})   AS ca_vente,
                   SUM(l.{col('doc_ligne', 'qte')}*a.{col('articles', 'prix_achat')})         AS cout_achat
            FROM {table('doc_ligne')} l
            JOIN {table('doc_entete')} e ON l.{col('doc_ligne', 'piece')} = e.{col('doc_entete', 'piece')}
            LEFT JOIN {table('articles')} a ON l.{col('doc_ligne', 'ref_article')} = a.{col('articles', 'ref')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            GROUP BY l.{col('doc_ligne', 'ref_article')}
            ORDER BY ca_vente DESC
        """).fetchall()
        conn.close()
        result = []
        for r in rows:
            ca   = _f(r["ca_vente"])
            cout = _f(r["cout_achat"])
            marge = round(ca - cout, 2)
            taux  = round(marge / ca * 100, 1) if ca > 0 else 0
            result.append({
                "ref":         r["ref"],
                "designation": r["designation"] or r["ref"],
                "qte_vendue":  round(_f(r["qte_vendue"]), 2),
                "ca_vente":    round(ca, 2),
                "cout_achat":  round(cout, 2),
                "marge_brute": marge,
                "taux_marge":  taux,
            })
        return json.dumps({"statut": "OK", "articles": result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def analyser_saisonnalite_ventes() -> str:
    """CA mensuel sur 24 mois."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT {_top(24)}{_fmt_mois(f"e.{col('doc_entete', 'date')}")}              AS mois,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})                 AS nb_factures,
                   COUNT(DISTINCT e.{col('doc_entete', 'code_tiers')})            AS nb_clients,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca_mensuel
            FROM {table('doc_entete')} e
            JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0 AND e.{col('doc_entete', 'date')} IS NOT NULL
            GROUP BY {_fmt_mois(f"e.{col('doc_entete', 'date')}")}
            ORDER BY mois DESC
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut": "OK",
            "mois": [
                {
                    "mois":        r["mois"],
                    "nb_factures": r["nb_factures"],
                    "nb_clients":  r["nb_clients"],
                    "ca_mensuel":  round(r["ca_mensuel"] or 0, 2),
                }
                for r in rows
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def calculer_delai_moyen_paiement() -> str:
    """DSO global et par client — via table reglements."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT e.{col('doc_entete', 'code_tiers')} AS code, c.{col('clients_fournisseurs', 'nom')} AS nom,
                   e.{col('doc_entete', 'date')} AS date_doc,
                   r.{col('reglements', 'date_reglement')} AS date_reglement,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS montant_ht,
                   CASE WHEN r.{col('reglements', 'date_reglement')} IS NOT NULL AND r.{col('reglements', 'date_reglement')}!=''
                        THEN {_diff_jours(f"r.{col('reglements', 'date_reglement')}", f"e.{col('doc_entete', 'date')}")}
                        ELSE {_diff_jours(_now_date(), f"e.{col('doc_entete', 'date')}")}
                   END AS delai
            FROM {table('doc_entete')} e
            LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            LEFT JOIN {table('reglements')} r ON e.{col('doc_entete', 'piece')} = r.{col('reglements', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0 AND e.{col('doc_entete', 'date')} IS NOT NULL
            GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')}, e.{col('doc_entete', 'date')}, r.{col('reglements', 'date_reglement')}
        """).fetchall()
        conn.close()

        if not rows:
            return json.dumps({"statut": "OK", "dso_global": 0}, ensure_ascii=False)

        tot_p = sum(_f(r["delai"]) * _f(r["montant_ht"]) for r in rows)
        tot_m = sum(_f(r["montant_ht"]) for r in rows)
        dso_g = round(tot_p / tot_m, 1) if tot_m > 0 else 0

        par_cli: dict[str, dict] = {}
        for r in rows:
            c = r["code"]
            if c not in par_cli:
                par_cli[c] = {"nom": r["nom"] or c, "d": [], "m": []}
            par_cli[c]["d"].append(_f(r["delai"]))
            par_cli[c]["m"].append(_f(r["montant_ht"]))

        clients = []
        for code, d in par_cli.items():
            tm = sum(d["m"])
            dso_c = round(
                sum(j * m for j, m in zip(d["d"], d["m"])) / tm, 1
            ) if tm > 0 else 0
            clients.append({
                "code":        code,
                "nom":         d["nom"],
                "dso_jours":   dso_c,
                "nb_factures": len(d["d"]),
            })
        clients.sort(key=lambda x: x["dso_jours"], reverse=True)

        return json.dumps({
            "statut":      "OK",
            "dso_global":  dso_g,
            "nb_factures": len(rows),
            "clients":     clients[:10],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


# FIX v4.3 : _TYPE_MAP remplacé par _TYPE_DOMAINE_MAP (type ET domaine),
# aligné sur le schéma réel confirmé (docstring Vanna / orchestrateur).
_TYPE_DOMAINE_MAP = {
    "BC": (1, 0), "BL": (3, 0), "FACTURE": (6, 0), "FC": (6, 0),
    "AV": (5, 0), "AVOIR": (5, 0), "OF": (25, 2), "BF": (26, 2),
}


@mcp.tool()
def selectionner_documents_par_periode(
    type_doc: str,
    date_debut: str,
    date_fin: str,
    code_tiers: str = "",
) -> str:
    """Documents par période."""
    try:
        conn = _connect()
        # FIX v4.2 : valeur par défaut sensée si type_doc absent/vide.
        # FIX v4.3 : ancien _TYPE_MAP totalement erroné (FACTURE→3=BL,
        # BC→6=FACTURE, BL→2 inexistant, AV→9 inexistant, OF→1=BC,
        # BF→4 inexistant) ET sans filtre de domaine → remplacé par
        # _TYPE_DOMAINE_MAP + filtre domaine explicite dans la requête.
        type_doc = (type_doc or "FACTURE").strip() or "FACTURE"
        do_type, do_domaine = _TYPE_DOMAINE_MAP.get(type_doc.upper(), (6, 0))
        code_reel = ""
        if code_tiers:
            row_c = _resoudre_client(conn, code_tiers)
            if row_c:
                code_reel = row_c[col('clients_fournisseurs', 'code')]
        date_col = col('doc_entete', 'date')
        if _is_mssql():
            date_cond = (f"TRY_CAST(e.{date_col} AS DATE)>=TRY_CAST(? AS DATE) "
                 f"AND TRY_CAST(e.{date_col} AS DATE)<=TRY_CAST(? AS DATE)")
        else:
            date_cond = f"e.{date_col}>=? AND e.{date_col}<=?"
        base = f"""
    SELECT e.{col('doc_entete', 'piece')} AS piece, e.{col('doc_entete', 'type')} AS type_doc,
           e.{date_col} AS date_doc, e.{col('doc_entete', 'code_tiers')} AS code_tiers,
           c.{col('clients_fournisseurs', 'nom')} AS nom,
           COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS montant_ht
    FROM {table('doc_entete')} e
    LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
    LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
    WHERE e.{col('doc_entete', 'type')}=? AND e.{col('doc_entete', 'domaine')}=?
      AND {date_cond}
        """
        if code_reel:
            rows = conn.execute(
                base + f" AND e.{col('doc_entete', 'code_tiers')}=? GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'type')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')} ORDER BY e.{col('doc_entete', 'date')} DESC",
                (do_type, do_domaine, date_debut, date_fin, code_reel),
            ).fetchall()
        else:
            rows = conn.execute(
                base + f" GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'type')}, e.{col('doc_entete', 'date')}, e.{col('doc_entete', 'code_tiers')}, c.{col('clients_fournisseurs', 'nom')} ORDER BY e.{col('doc_entete', 'date')} DESC",
                (do_type, do_domaine, date_debut, date_fin),
            ).fetchall()
        conn.close()

        result = [
            {
                "piece":      r["piece"],
                "date":       r["date_doc"],
                "code":       r["code_tiers"],
                "nom":        r["nom"] or r["code_tiers"],
                "montant_ht": round(_f(r["montant_ht"]), 2),
            }
            for r in rows
        ]
        return json.dumps({
            "statut":     "OK",
            "type_doc":   type_doc.upper(),
            "date_debut": date_debut,
            "date_fin":   date_fin,
            "code":       code_reel,
            "nb_docs":    len(result),
            "total_ht":   round(sum(r["montant_ht"] for r in result), 2),
            "documents":  result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def generer_offre_de_prix_excel(
    code_client: str, ref_article: str, qte: float
) -> str:
    """Données pour offre de prix."""
    try:
        conn  = _connect()
        row_c = _resoudre_client(conn, code_client)
        row_a = conn.execute(
            f"SELECT * FROM {table('articles')} WHERE UPPER({col('articles', 'ref')})=UPPER(?)",
            (ref_article.strip(),),
        ).fetchone()
        conn.close()

        client_nom = row_c[col('clients_fournisseurs', 'nom')] if row_c else code_client
        prix_unit  = float(row_a[col('articles', 'prix_vente')] or 0.0) if row_a else 0.0
        ar_design  = row_a[col('articles', 'designation')]                if row_a else ref_article
        montant_ht = round(qte * prix_unit, 2)

        return json.dumps({
            "statut":      "OK",
            "code":        row_c[col('clients_fournisseurs', 'code')] if row_c else code_client,
            "nom":         client_nom,
            "ref":         ref_article,
            "designation": ar_design,
            "qte":         qte,
            "prix_unit":   prix_unit,
            "montant_ht":  montant_ht,
            "tva":         round(montant_ht * 0.19, 2),
            "montant_ttc": round(montant_ht * 1.19, 2),
            "message":     f"📄 Offre {client_nom} — {ar_design} × {qte}",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def exporter_declaration_fiscale_excel() -> str:
    """Résumé fiscal — montants depuis doc_ligne."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        r = conn.execute(f"""
            SELECT COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca_ht,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')}) AS nb_fa
            FROM {table('doc_entete')} e
            JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
        """).fetchone()
        conn.close()
        ca_ht = _f(r["ca_ht"])
        return json.dumps({
            "statut": "OK",
            "ca_ht":  round(ca_ht, 2),
            "tva_19": round(ca_ht * 0.19, 2),
            "ca_ttc": round(ca_ht * 1.19, 2),
            "nb_fa":  r["nb_fa"],
            "annee":  datetime.now().strftime("%Y"),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def generer_balance_agee_excel() -> str:
    """Balance âgée — factures absentes de reglements."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT e.{col('doc_entete', 'code_tiers')} AS code, c.{col('clients_fournisseurs', 'nom')} AS nom,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS montant_ht,
                   CAST({_diff_jours(_now_date(), f"e.{col('doc_entete', 'date')}")} AS INTEGER) AS jours
            FROM {table('doc_entete')} e
            LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
              AND e.{col('doc_entete', 'piece')} NOT IN (SELECT {col('reglements', 'piece')} FROM {table('reglements')})
            GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'code_tiers')},
                     c.{col('clients_fournisseurs', 'nom')}
            ORDER BY jours DESC
        """).fetchall()
        conn.close()

        tranches: dict[str, dict] = {}
        for r in rows:
            code = r["code"]
            nom  = r["nom"] or code
            j    = int(r["jours"] or 0)
            mnt  = _f(r["montant_ht"])
            if code not in tranches:
                tranches[code] = {
                    "code": code, "nom": nom,
                    "0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0,
                }
            if j <= 30:    tranches[code]["0-30"]  += mnt
            elif j <= 60:  tranches[code]["31-60"] += mnt
            elif j <= 90:  tranches[code]["61-90"] += mnt
            else:          tranches[code]["90+"]   += mnt

        result = []
        for d in tranches.values():
            tot = d["0-30"] + d["31-60"] + d["61-90"] + d["90+"]
            result.append({
                **d,
                "0-30":  round(d["0-30"],  2),
                "31-60": round(d["31-60"], 2),
                "61-90": round(d["61-90"], 2),
                "90+":   round(d["90+"],   2),
                "total": round(tot, 2),
            })
        return json.dumps(
            {"statut": "OK", "nb": len(result), "clients": result},
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def generer_dashboard_kpi_excel() -> str:
    """KPI direction."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        r1 = conn.execute(f"""
            SELECT COUNT(DISTINCT e.{col('doc_entete', 'piece')})                AS nb_fa,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0) AS ca
            FROM {table('doc_entete')} e
            JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
        """).fetchone()
        nb_fa    = r1["nb_fa"] or 0
        total_ca = _f(r1["ca"])
        nb_cli   = conn.execute(
            f"SELECT COUNT(*) FROM {table('clients_fournisseurs')} WHERE {col('clients_fournisseurs', 'type_tiers')}=0"
        ).fetchone()[0]
        nb_docs  = conn.execute(
            f"SELECT COUNT(*) FROM {table('doc_entete')}"
        ).fetchone()[0]
        conn.close()
        return json.dumps({
            "statut":     "OK",
            "ca_total":   round(total_ca, 2),
            "marge_22":   round(total_ca * 0.22, 2),
            "panier_moy": round(total_ca / nb_fa, 2) if nb_fa > 0 else 0,
            "nb_clients":  nb_cli,
            "nb_docs":     nb_docs,
            "nb_factures": nb_fa,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def lister_clients_actifs() -> str:
    """Clients actifs — validite != 'BLOQUE'."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6 (+ domaine=0 ajouté).
        rows = conn.execute(f"""
            SELECT c.{col('clients_fournisseurs', 'code')} AS code, c.{col('clients_fournisseurs', 'nom')} AS nom,
                   c.{col('clients_fournisseurs', 'encours')} AS encours, c.{col('clients_fournisseurs', 'validite')} AS validite,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})                    AS nb_factures,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0)  AS ca_total
            FROM {table('clients_fournisseurs')} c
            LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')} AND e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            LEFT JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE c.{col('clients_fournisseurs', 'type_tiers')}=0
              AND UPPER(COALESCE(c.{col('clients_fournisseurs', 'validite')},'VALIDE')) != 'BLOQUE'
            GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}, c.{col('clients_fournisseurs', 'encours')}, c.{col('clients_fournisseurs', 'validite')}
            ORDER BY c.{col('clients_fournisseurs', 'nom')}
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut":     "OK",
            "nb_clients": len(rows),
            "clients": [
                {
                    "code":        r["code"],
                    "nom":         r["nom"],
                    "validite":    r["validite"] or "VALIDE",
                    "encours":     round(r["encours"] or 0, 2),
                    "nb_factures": r["nb_factures"],
                    "ca_total":    round(r["ca_total"] or 0, 2),
                }
                for r in rows
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def lister_clients_bloques() -> str:
    """Clients bloqués — validite = 'BLOQUE'."""
    try:
        conn = _connect()
        # FIX v4.3 : type=3 = BL, pas facture → type=6 (+ domaine=0 ajouté).
        rows = conn.execute(f"""
            SELECT c.{col('clients_fournisseurs', 'code')} AS code, c.{col('clients_fournisseurs', 'nom')} AS nom,
                   c.{col('clients_fournisseurs', 'encours')} AS encours, c.{col('clients_fournisseurs', 'validite')} AS validite,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})                    AS nb_factures,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')}*l.{col('doc_ligne', 'prix_unitaire')}),0)  AS ca_total
            FROM {table('clients_fournisseurs')} c
            LEFT JOIN {table('doc_entete')} e ON c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')} AND e.{col('doc_entete', 'type')}=6 AND e.{col('doc_entete', 'domaine')}=0
            LEFT JOIN {table('doc_ligne')}  l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE c.{col('clients_fournisseurs', 'type_tiers')}=0
              AND UPPER(COALESCE(c.{col('clients_fournisseurs', 'validite')},'')) = 'BLOQUE'
            GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}, c.{col('clients_fournisseurs', 'encours')}, c.{col('clients_fournisseurs', 'validite')}
            ORDER BY c.{col('clients_fournisseurs', 'nom')}
        """).fetchall()
        conn.close()

        return json.dumps({
            "statut": "OK",
            "nb_clients": len(rows),
            "clients": [
                {
                    "code":        r["code"],
                    "nom":         r["nom"],
                    "validite":    r["validite"] or "BLOQUE",
                    "encours":     round(r["encours"] or 0, 2),
                    "nb_factures": r["nb_factures"],
                    "ca_total":    round(r["ca_total"] or 0, 2),
                }
                for r in rows
            ],
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def lister_clients_inactifs(duree_jours: int = 180) -> str:
    """
    Liste les clients inactifs depuis duree_jours jours.
    """
    try:
        conn = _connect()
        date_limite = f"-{duree_jours} days"

        if _is_mssql():
            date_cond = f"MAX(e.{col('doc_entete', 'date')}) < DATEADD(day, ?, GETDATE())"
            param_date = -duree_jours
        else:
            date_cond = f"MAX(e.{col('doc_entete', 'date')}) < DATE('now', ?)"
            param_date = f"-{duree_jours} days"

        # FIX v4.3 : type=3 = BL, pas facture → type=6.
        rows = conn.execute(f"""
            SELECT
                c.{col('clients_fournisseurs', 'code')} AS code,
                c.{col('clients_fournisseurs', 'nom')} AS nom,
                c.{col('clients_fournisseurs', 'encours')} AS encours,
                c.{col('clients_fournisseurs', 'validite')} AS validite,
                MAX(e.{col('doc_entete', 'date')})                                    AS derniere_facture,
                COUNT(DISTINCT e.{col('doc_entete', 'piece')})                        AS nb_factures,
                COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0)  AS ca_total
            FROM {table('clients_fournisseurs')} c
            LEFT JOIN {table('doc_entete')} e
                ON  c.{col('clients_fournisseurs', 'code')}  = e.{col('doc_entete', 'code_tiers')}
                AND e.{col('doc_entete', 'type')} = 6
                AND e.{col('doc_entete', 'domaine')} = 0
            LEFT JOIN {table('doc_ligne')} l
                ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 0
            GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}, c.{col('clients_fournisseurs', 'encours')}, c.{col('clients_fournisseurs', 'validite')}
            HAVING
                MAX(e.{col('doc_entete', 'date')}) IS NULL
                OR {date_cond}
            ORDER BY derniere_facture ASC, nom
        """, (param_date,)).fetchall()

        conn.close()

        nb_mois = round(duree_jours / 30)

        return json.dumps({
            "statut":       "OK",
            "duree_jours":  duree_jours,
            "periode_mois": nb_mois,
            "nb_clients":   len(rows),
            "clients": [
                {
                    "code":             r["code"],
                    "nom":              r["nom"],
                    "validite":         r["validite"] or "VALIDE",
                    "encours":          round(r["encours"] or 0, 2),
                    "nb_factures":      r["nb_factures"],
                    "ca_total":         round(r["ca_total"] or 0, 2),
                    "derniere_facture": str(r["derniere_facture"])
                                        if r["derniere_facture"] else None,
                }
                for r in rows
            ],
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({
            "statut":  "ERREUR",
            "message": str(e)
        }, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────────
# ALIAS COMPATIBILITÉ ORCHESTRATEUR
# ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def lister_articles_catalogue() -> str:
    """[ALIAS] → lister_tous_les_articles"""
    return lister_tous_les_articles()


@mcp.tool()
def analyser_top_clients_ca(top_n: int = 5) -> str:
    """[ALIAS] → obtenir_top_clients"""
    return obtenir_top_clients(top_n)


@mcp.tool()
def detecter_clients_baisse_ca() -> str:
    """[ALIAS] → detecter_clients_en_baisse"""
    return detecter_clients_en_baisse()


@mcp.tool()
def lister_factures_impayees(code_tiers: str = "") -> str:
    """[ALIAS] → lister_factures_non_reglees"""
    return lister_factures_non_reglees(code_tiers)


@mcp.tool()
def calculer_ca_global_periode() -> str:
    """[ALIAS] → calculer_chiffre_affaires_global"""
    return calculer_chiffre_affaires_global()


@mcp.tool()
def analyser_rentabilite_clients() -> str:
    """[ALIAS] → analyser_rentabilite_articles"""
    return analyser_rentabilite_articles()


@mcp.tool()
def calculer_dso_clients(code_client: str = "") -> str:
    """[ALIAS] → calculer_delai_moyen_paiement"""
    return calculer_delai_moyen_paiement()


@mcp.tool()
def analyser_rfm_clients(code_client: str = "") -> str:
    """[ALIAS] → analyser_solvabilite_rfm"""
    return analyser_solvabilite_rfm()


@mcp.tool()
def lister_documents_par_periode(
    type_doc: str, date_debut: str, date_fin: str, code_tiers: str = ""
) -> str:
    """[ALIAS] → selectionner_documents_par_periode"""
    return selectionner_documents_par_periode(
        type_doc, date_debut, date_fin, code_tiers
    )


@mcp.tool()
def exporter_offre_prix_excel(
    code_client: str, ref_article: str = "", qte: float = 1.0
) -> str:
    """[ALIAS] → generer_offre_de_prix_excel"""
    return generer_offre_de_prix_excel(code_client, ref_article, qte)


@mcp.tool()
def exporter_balance_agee_excel() -> str:
    """[ALIAS] → generer_balance_agee_excel"""
    return generer_balance_agee_excel()


@mcp.tool()
def exporter_dashboard_kpi_excel() -> str:
    """[ALIAS] → generer_dashboard_kpi_excel"""
    return generer_dashboard_kpi_excel()


# ─────────────────────────────────────────────────────────────────────
# FACTURES FOURNISSEURS NON RÉGLÉES
# ─────────────────────────────────────────────────────────────────────
# ⚠️ AUDIT FOURNISSEUR (NON CORRIGÉ) : ce bloc utilise type=3/domaine=1
# pour "facture fournisseur", alors que _gen_factures_fournisseur_specifique
# et le pattern "Factures fournisseur globales" utilisent type=16/domaine=1,
# et analyser_top_fournisseurs utilise type=6/domaine=1. Trois codes
# différents pour le même concept — à vérifier en base (GROUP BY type,
# domaine côté domaine=1 + recoupement avec des pièces fournisseur connues)
# avant de choisir lequel corriger. Ne pas supposer que 3 est correct ici.
def lister_factures_fournisseurs_non_reglees_core(code_fourn: str = "") -> str:
    """
    Retourne les factures fournisseurs (type=3, domaine=1) absentes
    de la table reglements. Filtre optionnel sur un fournisseur précis.
    """
    try:
        conn = _connect()
        code_reel = ""
        if code_fourn:
            row = _resoudre_fournisseur(conn, code_fourn)
            if row:
                code_reel = row[col('clients_fournisseurs', 'code')]

        base_sql = f"""
            SELECT e.{col('doc_entete', 'piece')} AS piece, e.{col('doc_entete', 'code_tiers')} AS code_tiers, e.{col('doc_entete', 'date')} AS date_doc,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS montant_ht,
                   c.{col('clients_fournisseurs', 'nom')} AS nom
            FROM {table('doc_entete')} e
            LEFT JOIN {table('clients_fournisseurs')}  c ON e.{col('doc_entete', 'code_tiers')} = c.{col('clients_fournisseurs', 'code')}
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'type')}=3 AND e.{col('doc_entete', 'domaine')}=1
              AND e.{col('doc_entete', 'piece')} NOT IN (SELECT {col('reglements', 'piece')} FROM {table('reglements')})
        """
        if code_reel:
            rows = conn.execute(
                base_sql + f" AND e.{col('doc_entete', 'code_tiers')}=? GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'code_tiers')}, e.{col('doc_entete', 'date')}, c.{col('clients_fournisseurs', 'nom')} ORDER BY e.{col('doc_entete', 'date')} DESC",
                (code_reel,)
            ).fetchall()
        else:
            rows = conn.execute(
                base_sql + f" GROUP BY e.{col('doc_entete', 'piece')}, e.{col('doc_entete', 'code_tiers')}, e.{col('doc_entete', 'date')}, c.{col('clients_fournisseurs', 'nom')} ORDER BY e.{col('doc_entete', 'date')} DESC"
            ).fetchall()
        conn.close()

        result   = []
        total_du = 0.0
        for f in rows:
            mnt = _f(f["montant_ht"])
            total_du += mnt
            result.append({
                "piece": f["piece"],
                "code":  f["code_tiers"],
                "nom":   f["nom"] or f["code_tiers"],
                "date":  f["date_doc"],
                "montant_ht": round(mnt, 2),
            })

        return json.dumps({
            "statut":      "OK",
            "code":        code_reel,
            "nb_factures": len(result),
            "total_du":    round(total_du, 2),
            "factures":    result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def lister_factures_fournisseurs_non_reglees(code_fournisseur: str = "") -> str:
    """Factures fournisseurs non réglées (domaine=1, absentes de reglements)."""
    return lister_factures_fournisseurs_non_reglees_core(code_fournisseur)


@mcp.tool()
def factures_impayees_fournisseur(code_fournisseur: str = "") -> str:
    """[ALIAS] → lister_factures_fournisseurs_non_reglees"""
    return lister_factures_fournisseurs_non_reglees_core(code_fournisseur)


@mcp.tool()
def lister_fournisseurs() -> str:
    """Liste tous les fournisseurs (type_tiers=1) avec statut et encours."""
    try:
        conn = _connect()
        rows = conn.execute(f"""
            SELECT {col('clients_fournisseurs', 'code')} AS code, {col('clients_fournisseurs', 'nom')} AS nom,
                   COALESCE({col('clients_fournisseurs', 'encours')},    0) AS encours,
                   COALESCE({col('clients_fournisseurs', 'encours_max')}, 0) AS encours_max,
                   COALESCE({col('clients_fournisseurs', 'validite')}, 'VALIDE') AS statut
            FROM {table('clients_fournisseurs')}
            WHERE {col('clients_fournisseurs', 'type_tiers')} = 1
            ORDER BY {col('clients_fournisseurs', 'nom')}
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut":          "OK",
            "nb_fournisseurs": len(rows),
            "fournisseurs": [
                {
                    "code":        r["code"],
                    "nom":         r["nom"],
                    "encours":     round(r["encours"],     2),
                    "encours_max": round(r["encours_max"], 2),
                    "validite":    r["statut"],
                }
                for r in rows
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def analyser_top_fournisseurs(top_n: int = 10) -> str:
    """Top N fournisseurs par volume d'achat (BC fournisseur, domaine=1).
    AUDIT FOURNISSEUR (non corrigé) : utilise type=6/domaine=1, à recouper
    avec les 16 et 3 utilisés ailleurs pour "facture fournisseur"."""
    try:
        conn = _connect()
        # FIX prob6 : type=6 domaine=1 n'existe pas dans la DB (0 résultats).
        # On retient tous les documents côté fournisseur (domaine=1) — BC (11),
        # réceptions (13) et factures fournisseur (16) — pour calculer le
        # volume réel engagé par fournisseur.
        rows = conn.execute(f"""
            SELECT {_top(top_n)} c.{col('clients_fournisseurs', 'code')} AS code, c.{col('clients_fournisseurs', 'nom')} AS nom,
                   COUNT(DISTINCT e.{col('doc_entete', 'piece')})                    AS nb_commandes,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS volume_achat
            FROM {table('clients_fournisseurs')} c
            LEFT JOIN {table('doc_entete')} e
                ON  c.{col('clients_fournisseurs', 'code')} = e.{col('doc_entete', 'code_tiers')}
                AND e.{col('doc_entete', 'domaine')} = 1
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE c.{col('clients_fournisseurs', 'type_tiers')} = 1
            GROUP BY c.{col('clients_fournisseurs', 'code')}, c.{col('clients_fournisseurs', 'nom')}
            ORDER BY volume_achat DESC
            {_limit(top_n)}
        """).fetchall()
        conn.close()
        return json.dumps({
            "statut":          "OK",
            "top_n":           top_n,
            "nb_fournisseurs": len(rows),
            "fournisseurs": [
                {
                    "rang":         i + 1,
                    "code":         r["code"],
                    "nom":          r["nom"],
                    "nb_commandes": r["nb_commandes"],
                    "volume_achat": round(r["volume_achat"], 2),
                }
                for i, r in enumerate(rows)
            ],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)


@mcp.tool()
def rechercher_fiche_fournisseur(code_fournisseur: str) -> str:
    """Fiche complète d'un fournisseur par code exact ou nom partiel."""
    try:
        conn = _connect()
        row = _resoudre_fournisseur(conn, code_fournisseur)
        if not row:
            conn.close()
            return json.dumps({
                "statut":  "NON_TROUVE",
                "code":    "",
                "message": f"Fournisseur '{code_fournisseur}' absent de la base.",
            }, ensure_ascii=False)

        code_reel = row[col('clients_fournisseurs', 'code')]
        stats = conn.execute(f"""
            SELECT COUNT(DISTINCT e.{col('doc_entete', 'piece')})                    AS nb_commandes,
                   COALESCE(SUM(l.{col('doc_ligne', 'qte')} * l.{col('doc_ligne', 'prix_unitaire')}), 0) AS volume_total
            FROM {table('doc_entete')} e
            LEFT JOIN {table('doc_ligne')} l ON e.{col('doc_entete', 'piece')} = l.{col('doc_ligne', 'piece')}
            WHERE e.{col('doc_entete', 'code_tiers')} = ? AND e.{col('doc_entete', 'domaine')} = 1
        """, (code_reel,)).fetchone()
        conn.close()

        validite = str(row[col('clients_fournisseurs', 'validite')] or "VALIDE").upper()
        return json.dumps({
            "statut":        "TROUVE",
            "code":          code_reel,
            "nom":           row[col('clients_fournisseurs', 'nom')],
            "validite":      validite,
            "encours":       round(row[col('clients_fournisseurs', 'encours')]    or 0, 2),
            "encours_max":   round(row[col('clients_fournisseurs', 'encours_max')] or 0, 2),
            "nb_commandes":  stats["nb_commandes"]  or 0,
            "volume_total":  round(stats["volume_total"] or 0, 2),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)

@mcp.tool()
def lire_nomenclature_article(ref_article: str) -> str:
    """Affiche la nomenclature (liste des composants) d'un article/produit fini."""
    try:
        conn = _connect()
        base = f"""
            SELECT {col('articles', 'ref')} AS ref, {col('articles', 'designation')} AS design
            FROM {table('articles')}
        """
        row_pf = conn.execute(
            base + f" WHERE UPPER({col('articles', 'ref')})=UPPER(?)",
            (ref_article.strip(),),
        ).fetchone()
        if not row_pf:
            row_pf = conn.execute(
                base + f" WHERE UPPER({col('articles', 'designation')}) LIKE UPPER(?)",
                (f"%{ref_article.strip()}%",),
            ).fetchone()
        if not row_pf:
            conn.close()
            return json.dumps({
                "statut": "NON_TROUVE",
                "message": f"Article '{ref_article}' introuvable.",
            }, ensure_ascii=False)

        ref_pf = row_pf["ref"]
        rows = conn.execute(f"""
            SELECT n.{col('nomenclature', 'ref_mp')} AS ref_composant,
                   a.{col('articles', 'designation')} AS designation,
                   n.{col('nomenclature', 'qte')} AS qte
            FROM {table('nomenclature')} n
            LEFT JOIN {table('articles')} a
                ON UPPER(n.{col('nomenclature', 'ref_mp')}) = UPPER(a.{col('articles', 'ref')})
            WHERE UPPER(n.{col('nomenclature', 'ref_pf')}) = UPPER(?)
            ORDER BY n.{col('nomenclature', 'ref_mp')}
        """, (ref_pf,)).fetchall()
        conn.close()

        composants = [
            {
                "ref": r["ref_composant"],
                "designation": r["designation"] or r["ref_composant"],
                "qte": round(_f(r["qte"]), 3),
            }
            for r in rows
        ]
        return json.dumps({
            "statut": "OK",
            "ref_parent": ref_pf,
            "design_parent": row_pf["design"] or ref_pf,
            "nb_composants": len(composants),
            "composants": composants,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"erreur": str(e)}, ensure_ascii=False)
@mcp.tool()
def verifier_document_deja_transforme(
    num_piece_source: str,
    type_destination: str,
) -> str:
    """
    Vérifie si num_piece_source a déjà été transformé vers type_destination.
    Retourne {"deja_transforme": bool, "nb": int, "message": str}.
    Types supportés : FACTURE | FA | FACTURE_ACHAT | FA_ACHAT | BF
    """
    # FIX v4.3 : FACTURE/FA corrigés en (6,0) [type=3 était en réalité un
    # BL]. BF corrigé en (26,2) pour matcher le pattern BF déjà correct
    # utilisé ailleurs dans ce fichier. FACTURE_ACHAT/FA_ACHAT (domaine=1)
    # laissés en l'état — AUDIT FOURNISSEUR non résolu, ne pas supposer
    # que (3,1) est juste.
    _TYPE_MAP = {
        "FACTURE":       (6, 0),
        "FA":            (6, 0),
        "FACTURE_ACHAT": (3, 1),
        "FA_ACHAT":      (3, 1),
        "BF":            (26, 2),
    }
    _LABELS = {
        "FACTURE":       "facture de vente",
        "FA":            "facture de vente",
        "FACTURE_ACHAT": "facture fournisseur",
        "FA_ACHAT":      "facture fournisseur",
        "BF":            "bon de fabrication",
    }
    try:
        target = _TYPE_MAP.get(type_destination.upper())
        if target is None:
            return json.dumps({
                "deja_transforme": False,
                "nb":              0,
                "message":         (
                    f"Type destination '{type_destination}' non géré "
                    f"par la vérification doublon."
                ),
            }, ensure_ascii=False)

        do_type, do_domaine = target
        conn = _connect()
        nb = conn.execute(f"""
            SELECT COUNT(*) AS nb
            FROM {table('doc_entete')}
            WHERE {col('doc_entete', 'reference')} = ?
              AND {col('doc_entete', 'type')}      = ?
              AND {col('doc_entete', 'domaine')}   = ?
        """, (num_piece_source, do_type, do_domaine)).fetchone()["nb"]
        conn.close()

        if nb > 0:
            label = _LABELS.get(type_destination.upper(), type_destination)
            return json.dumps({
                "deja_transforme": True,
                "nb":              nb,
                "message": (
                    f"⚠️  Le document **{num_piece_source}** a déjà été transformé "
                    f"en {label} ({nb} document(s) existant(s) lié(s)).\n"
                    f"   Utilisez 'liste des factures' pour retrouver le document."
                ),
            }, ensure_ascii=False)

        return json.dumps({
            "deja_transforme": False,
            "nb":              0,
            "message":         "",
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "deja_transforme": False,
            "nb":              0,
            "message":         f"Erreur vérification doublon : {e}",
        }, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()