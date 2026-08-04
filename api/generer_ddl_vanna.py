"""
generer_ddl_vanna.py
================================================================================
Génère le DDL réel des tables Sage 100 depuis INFORMATION_SCHEMA de la base
MSSQL de production, pour remplacer le bloc `tables_ddl` codé en dur dans
`_vanna_entrainer_schema()` (orchestrateur_general.py).

Pourquoi : le DDL actuel est une simplification manuelle (quelques colonnes
par table, largeurs approximatives). Vanna doit être entraîné sur le VRAI
schéma pour générer du SQL qui s'exécute sans erreur de troncature/type sur
la base réelle (ex: DO_Piece NVARCHAR(20) exact, pas une supposition).

Usage :
    python generer_ddl_vanna.py > ddl_sage_reel.sql
    # ou, pour l'injecter directement dans l'entraînement Vanna :
    from generer_ddl_vanna import generer_ddl_tables
    for ddl in generer_ddl_tables(connexion, TABLES_UTILISEES):
        vn.train(ddl=ddl)

Prérequis : pyodbc + connexion valide à la base MSSQL de production
(ou une base de staging avec un schéma IDENTIQUE, données anonymisées).
"""
from __future__ import annotations
import sys

# ─────────────────────────────────────────────────────────────────────
# Tables réellement utilisées par l'application (à ajuster si vous en
# ajoutez d'autres dans les requêtes NL2SQL / mcp_actions_sage).
# ─────────────────────────────────────────────────────────────────────
TABLES_UTILISEES = [
    "F_COMPTET",
    "F_ARTICLE",
    "F_ARTSTOCK",
    "F_NOMENCLAT",
    "F_DOCENTETE",
    "F_DOCLIGNE",
    "F_DOCREGL",
]

_REQUETE_COLONNES = """
SELECT
    c.COLUMN_NAME,
    c.DATA_TYPE,
    c.CHARACTER_MAXIMUM_LENGTH,
    c.NUMERIC_PRECISION,
    c.NUMERIC_SCALE,
    c.IS_NULLABLE,
    c.COLUMN_DEFAULT,
    c.ORDINAL_POSITION
FROM INFORMATION_SCHEMA.COLUMNS c
WHERE c.TABLE_NAME = ?
ORDER BY c.ORDINAL_POSITION
"""

_REQUETE_PK = """
SELECT ku.COLUMN_NAME
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE ku
    ON tc.CONSTRAINT_NAME = ku.CONSTRAINT_NAME
    AND tc.TABLE_NAME = ku.TABLE_NAME
WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
    AND tc.TABLE_NAME = ?
ORDER BY ku.ORDINAL_POSITION
"""


def _formater_type_colonne(row) -> str:
    """Reconstruit le type SQL avec sa taille/précision exacte."""
    dtype = row.DATA_TYPE.upper()

    if dtype in ("NVARCHAR", "VARCHAR", "NCHAR", "CHAR"):
        longueur = row.CHARACTER_MAXIMUM_LENGTH
        if longueur == -1:
            return f"{dtype}(MAX)"
        return f"{dtype}({longueur})"

    if dtype in ("DECIMAL", "NUMERIC"):
        return f"{dtype}({row.NUMERIC_PRECISION},{row.NUMERIC_SCALE})"

    if dtype in ("FLOAT",) and row.NUMERIC_PRECISION:
        return f"{dtype}({row.NUMERIC_PRECISION})"

    # INT, BIGINT, SMALLINT, TINYINT, BIT, DATETIME, DATE, MONEY, etc.
    return dtype


def generer_ddl_table(connexion, nom_table: str) -> str | None:
    """
    Génère un CREATE TABLE reflétant exactement le schéma réel de `nom_table`
    dans la base connectée (colonnes, types, tailles, nullabilité, PK).
    Retourne None si la table n'existe pas dans cette base.
    """
    cur = connexion.cursor()
    cur.execute(_REQUETE_COLONNES, nom_table)
    colonnes = cur.fetchall()
    if not colonnes:
        print(f"⚠️  Table '{nom_table}' introuvable dans INFORMATION_SCHEMA — ignorée.", file=sys.stderr)
        return None

    cur.execute(_REQUETE_PK, nom_table)
    pk_cols = [r.COLUMN_NAME for r in cur.fetchall()]

    lignes_ddl = []
    for col in colonnes:
        type_sql = _formater_type_colonne(col)
        nullable = "" if col.IS_NULLABLE == "YES" else " NOT NULL"
        lignes_ddl.append(f"    {col.COLUMN_NAME} {type_sql}{nullable}")

    if pk_cols:
        lignes_ddl.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    ddl = f"CREATE TABLE {nom_table} (\n" + ",\n".join(lignes_ddl) + "\n)"
    return ddl


def generer_ddl_tables(connexion, tables: list[str] | None = None) -> list[str]:
    """Génère le DDL pour toutes les tables listées, en sautant celles absentes."""
    tables = tables or TABLES_UTILISEES
    resultats = []
    for nom_table in tables:
        ddl = generer_ddl_table(connexion, nom_table)
        if ddl:
            resultats.append(ddl)
    return resultats


def _connexion_depuis_env():
    """
    Ouvre une connexion pyodbc en réutilisant les variables d'environnement
    déjà utilisées par l'application (adaptation/db_adapter.py).
    Adaptez les noms de variables si db_adapter.py en utilise d'autres.
    """
    import os
    import pyodbc

    driver = os.getenv("MSSQL_DRIVER", "{ODBC Driver 17 for SQL Server}")
    server = os.environ["MSSQL_SERVER"]
    database = os.environ["MSSQL_DATABASE"]
    uid = os.environ.get("MSSQL_UID", "")
    pwd = os.environ.get("MSSQL_PWD", "")
    trusted = os.environ.get("MSSQL_TRUSTED_CONNECTION", "no")

    if trusted.lower() == "yes":
        conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};Trusted_Connection=yes;"
    else:
        conn_str = f"DRIVER={driver};SERVER={server};DATABASE={database};UID={uid};PWD={pwd};"

    return pyodbc.connect(conn_str)


if __name__ == "__main__":
    conn = _connexion_depuis_env()
    try:
        ddls = generer_ddl_tables(conn, TABLES_UTILISEES)
        for ddl in ddls:
            print(ddl + ";\n")
        print(f"-- {len(ddls)}/{len(TABLES_UTILISEES)} tables extraites avec succès", file=sys.stderr)
    finally:
        conn.close()