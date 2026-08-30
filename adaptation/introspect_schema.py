"""Introspects the Sage database schema (SQL Server or SQLite) to discover tables and columns of interest, then updates and backs up the db_config.json configuration file accordingly."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from adaptation.db_adapter import get_connection, _load_config

TABLES_TO_INSPECT = [
    "F_COMPTET",
    "F_ARTICLE",
    "F_ARTSTOCK",
    "F_DOCENTETE",
    "F_DOCLIGNE",
    "mouvements_stock",
    "reglements",
]

LOGICAL_COLUMNS_OF_INTEREST = {
    "F_COMPTET":    ["CT_Num", "CT_Intitule", "CT_Type", "CT_Sommeil", "CT_Encours"],
    "F_ARTICLE":    ["AR_Ref", "AR_Design", "AR_PrixAch", "AR_PrixVen", "AR_Type"],
    "F_ARTSTOCK":   ["AR_Ref", "AS_QteSto", "AS_QteCom"],
    "F_DOCENTETE":  ["DO_Piece", "DO_Domaine", "DO_Type", "DO_Date", "DO_Ref", "DO_Tiers"],
    "F_DOCLIGNE":   ["DO_Piece", "AR_Ref", "DL_Qte", "DL_PrixUnitaire", "DL_Ligne"],
}


def _get_columns_sqlite(conn, table_name: str) -> list[str]:
    """Return a list of column names for the given SQLite table using PRAGMA table_info."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [r[1] for r in rows]
    except Exception:
        return []


def _get_columns_mssql(conn, table_name: str) -> list[str]:
    """Return a list of column names for the given SQL Server table using INFORMATION_SCHEMA.COLUMNS."""
    try:
        rows = conn.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION",
            (table_name,)
        ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def _list_tables_sqlite(conn) -> list[str]:
    """Return a list of all table names present in the SQLite database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _list_tables_mssql(conn) -> list[str]:
    """Return a list of all base table names present in the SQL Server database."""
    rows = conn.execute(
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
    ).fetchall()
    return [r[0] for r in rows]


def main():
    """Entry point of the script. Determines the database driver, connects to the database, lists tables, inspects target tables for columns of interest, validates column presence, updates the db_config.json file, and creates a backup of the previous configuration."""
    driver = os.getenv("DB_DRIVER", "sqlite").lower()
    print(f"\n🔍  Introspection schéma — driver={driver}")
    print("=" * 60)

    conn = get_connection()

    if driver == "sqlite":
        get_cols = lambda t: _get_columns_sqlite(conn, t)
        list_tables = lambda: _list_tables_sqlite(conn)
    else:
        get_cols = lambda t: _get_columns_mssql(conn, t)
        list_tables = lambda: _list_tables_mssql(conn)

    all_tables = list_tables()
    print(f"\n📋  Tables trouvées dans la base ({len(all_tables)}) :")
    for t in all_tables:
        print(f"     {t}")

    print("\n📐  Colonnes des tables d'intérêt :")
    found: dict[str, list[str]] = {}
    for tbl in TABLES_TO_INSPECT:
        actual_cols = get_cols(tbl)
        found[tbl] = actual_cols
        if actual_cols:
            print(f"\n  [{tbl}]")
            for c in actual_cols:
                mark = "✅" if c in LOGICAL_COLUMNS_OF_INTEREST.get(tbl, []) else "  "
                print(f"    {mark} {c}")
        else:
            print(f"\n  [{tbl}] — ⚠️  TABLE NON TROUVÉE")

    cfg = _load_config()
    cfg["driver"] = driver

    print("\n\n🔎  Vérification des colonnes d'intérêt :")
    missing_any = False
    for tbl, expected_cols in LOGICAL_COLUMNS_OF_INTEREST.items():
        real_cols_set = set(found.get(tbl, []))
        for ec in expected_cols:
            if real_cols_set and ec not in real_cols_set:
                candidates = [c for c in real_cols_set if c.lower() == ec.lower()]
                if candidates:
                    print(f"  ℹ️  {tbl}.{ec} → trouvé sous le nom '{candidates[0]}' (casse différente)")
                else:
                    print(f"  ⚠️  {tbl}.{ec} — INTROUVABLE dans la vraie base !")
                    missing_any = True

    if missing_any:
        print(
            "\n  ⚠️  Certaines colonnes attendues sont introuvables.\n"
            "  Ouvre db_config.json et corrige manuellement les valeurs correspondantes.\n"
        )
    else:
        print("\n  ✅  Toutes les colonnes d'intérêt sont présentes.")

    output_path = Path(__file__).parent / "db_config.json"
    backup_path = output_path.with_suffix(".json.bak")
    if output_path.exists():
        backup_path.write_text(output_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n💾  Backup de l'ancien config → {backup_path}")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

    print(f"✅  db_config.json mis à jour → {output_path}")
    print("\n📌  Prochaine étape :")
    print("    Ouvre adaptation/db_config.json et corrige les noms de colonnes")
    print("    si certaines différent entre le mock et la vraie base Sage.\n")

    conn.close()


if __name__ == "__main__":
    main()