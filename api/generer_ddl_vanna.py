"""Module to generate real DDL statements for Sage 100 tables from a MSSQL INFORMATION_SCHEMA.
It provides utilities to retrieve column metadata, format SQL types, and build CREATE TABLE
statements for a set of tables. The script can be executed directly to output DDL to stdout
or imported to obtain DDL strings for further processing (e.g., Vanna training)."""

from __future__ import annotations
import sys

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
    """Reconstruct the exact SQL type definition for a column based on its metadata."""
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

    return dtype

_COLONNES_EXCLUES_VANNA = {
    "F_DOCENTETE": {"DO_TotalHT", "DO_TotalTTC", "DO_MontantHT", "DO_MontantTTC", "DO_NetAPayer"},
}

def _get_colonnes_utilisees() -> set[str]:
    """Load the set of column names used by the application from the db_config.json file."""
    import json
    from pathlib import Path
    _DB_CONFIG_PATH = Path(__file__).parent.parent / "adaptation" / "db_config.json"
    with open(_DB_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    colonnes = set()
    for table_cols in cfg["columns"].values():
        for col_name in table_cols.values():
            colonnes.add(col_name)
    return colonnes

_COLONNES_UTILISEES = _get_colonnes_utilisees()

def generer_ddl_table(connexion, nom_table: str) -> str | None:
    """Generate a CREATE TABLE statement that mirrors the real schema of *nom_table*.
    Returns ``None`` if the table is not found in INFORMATION_SCHEMA."""
    cur = connexion.cursor()
    cur.execute(_REQUETE_COLONNES, nom_table)
    colonnes = cur.fetchall()
    if not colonnes:
        print(f"⚠️  Table '{nom_table}' introuvable dans INFORMATION_SCHEMA — ignorée.", file=sys.stderr)
        return None

    cur.execute(_REQUETE_PK, nom_table)
    pk_cols = [r.COLUMN_NAME for r in cur.fetchall()]

    exclues = _COLONNES_EXCLUES_VANNA.get(nom_table, set())

    lignes_ddl = []
    for row in colonnes:
        if row.COLUMN_NAME in exclues:
            continue
        if row.COLUMN_NAME not in _COLONNES_UTILISEES and row.COLUMN_NAME not in pk_cols:
            continue
        type_sql = _formater_type_colonne(row)
        nullable = "" if row.IS_NULLABLE == "YES" else " NOT NULL"
        lignes_ddl.append(f"    {row.COLUMN_NAME} {type_sql}{nullable}")

    if pk_cols:
        lignes_ddl.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")

    ddl = f"CREATE TABLE {nom_table} (\n" + ",\n".join(lignes_ddl) + "\n)"
    return ddl

def generer_ddl_tables(connexion, tables: list[str] | None = None) -> list[str]:
    """Generate DDL statements for all tables in *tables* (or the default list) and return them."""
    tables = tables or TABLES_UTILISEES
    resultats = []
    for nom_table in tables:
        ddl = generer_ddl_table(connexion, nom_table)
        if ddl:
            resultats.append(ddl)
    return resultats

def _connexion_depuis_env():
    """Create a pyodbc connection using environment variables compatible with the application."""
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