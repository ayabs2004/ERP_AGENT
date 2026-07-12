"""
db_adapter.py — Couche d'abstraction base de données
=====================================================
BUT : neutraliser le projet vis-à-vis du schéma réel de la base cible.

Tant que tu n'as pas accès à la vraie base Sage :
- driver = "sqlite" dans db_config.json → tout continue de marcher
  exactement comme avant, sur entreprise_mock.db.

Le jour où tu obtiens l'accès Sage :
1. Lance introspect_schema.py sur la vraie base pour découvrir les
   vrais noms de tables/colonnes.
2. Mets à jour adaptation/db_config.json avec les vraies valeurs
   (uniquement les valeurs à droite — les clés logiques à gauche
   ne bougent JAMAIS, c'est elles que le code métier utilise).
3. Passe "driver": "mssql" dans db_config.json et renseigne les
   variables d'environnement DB_SERVER / DB_NAME / DB_USER / DB_PASS.
4. Aucune autre modification de code n'est nécessaire.

RÈGLE D'OR pour tout le reste du projet :
    Ne JAMAIS écrire "F_COMPTET" ou "CT_Num" en dur dans une requête SQL.
    Toujours passer par table("clients_fournisseurs") et
    col("clients_fournisseurs", "code").
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

_CONFIG_PATH = Path(__file__).parent / "db_config.json"
_config_cache: Optional[dict] = None


def _load_config() -> dict:
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
    return _config_cache


def reload_config() -> None:
    """Force le rechargement de db_config.json (utile en tests)."""
    global _config_cache
    _config_cache = None
    _load_config()


def table(logical_name: str) -> str:
    """Traduit un nom logique de table ('clients_fournisseurs') en nom réel ('F_COMPTET')."""
    cfg = _load_config()
    try:
        return cfg["tables"][logical_name]
    except KeyError:
        raise KeyError(
            f"Table logique inconnue: '{logical_name}'. "
            f"Ajoute-la dans adaptation/db_config.json > tables."
        )


def col(logical_table: str, logical_column: str) -> str:
    """Traduit une colonne logique ('code') pour une table logique donnée en colonne réelle ('CT_Num')."""
    cfg = _load_config()
    try:
        return cfg["columns"][logical_table][logical_column]
    except KeyError:
        raise KeyError(
            f"Colonne logique inconnue: table='{logical_table}' colonne='{logical_column}'. "
            f"Ajoute-la dans adaptation/db_config.json > columns.{logical_table}."
        )


def cols(logical_table: str, logical_columns: Iterable[str]) -> list[str]:
    """Traduit plusieurs colonnes logiques d'un coup, dans l'ordre fourni."""
    return [col(logical_table, c) for c in logical_columns]


# ─────────────────────────────────────────────────────────────────────
# CONNEXION — factory selon le driver déclaré dans db_config.json
# ─────────────────────────────────────────────────────────────────────

def get_sqlite_path() -> Optional[Path]:
    """
    Retourne le chemin du fichier sqlite qui serait utilisé par get_connection(),
    ou None si le driver actif n'est pas 'sqlite' (ex: 'mssql' — pas de fichier local).

    À utiliser partout où du code a besoin de vérifier/initialiser le fichier DB
    (scripts d'init, checks de démarrage) au lieu de recalculer un chemin en dur :
    ça garantit qu'on parle toujours du même fichier que get_connection().
    """
    cfg = _load_config()
    driver = os.getenv("DB_DRIVER", cfg.get("driver", "sqlite")).lower()
    if driver != "sqlite":
        return None
    return Path(os.getenv(
        "DB_PATH",
        str(Path(__file__).parent.parent / "entreprise_mock.db")
    ))


def get_connection():
    """
    Retourne une connexion DB-API 2.0 (sqlite3.Connection ou pyodbc.Connection).
    Le code appelant peut continuer à faire conn.execute(...) / conn.commit()
    de la même façon dans les deux cas.

    Variables d'environnement prioritaires :
      DB_DRIVER  — "sqlite" | "mssql"  (écrase db_config.json)
      DB_PATH    — chemin vers le fichier .db en mode sqlite
      DB_SERVER  — adresse du serveur SQL Server
      DB_NAME    — nom de la base SQL Server
      DB_USER    — utilisateur SQL (optionnel → auth Windows sinon)
      DB_PASS    — mot de passe
      DB_ODBC_DRIVER — driver ODBC (défaut: {ODBC Driver 17 for SQL Server})
    """
    cfg = _load_config()
    driver = os.getenv("DB_DRIVER", cfg.get("driver", "sqlite")).lower()

    if driver == "sqlite":
        db_path = get_sqlite_path()
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    elif driver == "mssql":
        try:
            import pyodbc
        except ImportError as e:
            raise RuntimeError(
                "driver='mssql' nécessite pyodbc. Installe-le avec: pip install pyodbc"
            ) from e

        server      = os.environ["DB_SERVER"]        # ex: "192.168.1.10" ou "SRV-SAGE\\SAGE100"
        database    = os.environ["DB_NAME"]           # ex: "SAGE100_MACOMPTA"
        user        = os.environ.get("DB_USER")
        password    = os.environ.get("DB_PASS")
        odbc_driver = os.getenv("DB_ODBC_DRIVER", "{ODBC Driver 17 for SQL Server}")

        if user:
            conn_str = (
                f"DRIVER={odbc_driver};SERVER={server};DATABASE={database};"
                f"UID={user};PWD={password};TrustServerCertificate=yes;"
            )
        else:
            # Authentification Windows intégrée
            conn_str = (
                f"DRIVER={odbc_driver};SERVER={server};DATABASE={database};"
                f"Trusted_Connection=yes;TrustServerCertificate=yes;"
            )
        conn = pyodbc.connect(conn_str)
        return conn

    else:
        raise ValueError(f"Driver DB inconnu: '{driver}' (attendu: 'sqlite' ou 'mssql')")


def placeholder() -> str:
    """
    Retourne le symbole de paramètre SQL selon le driver ('?' pour sqlite ET pyodbc,
    donc en pratique identique — utile si un jour tu ajoutes psycopg2 qui utilise '%s').
    """
    cfg = _load_config()
    driver = os.getenv("DB_DRIVER", cfg.get("driver", "sqlite")).lower()
    return "?"  # sqlite et pyodbc utilisent tous les deux '?'


# ─────────────────────────────────────────────────────────────────────
# HELPERS DE CONSTRUCTION SQL — utilisent le mapping automatiquement
# ─────────────────────────────────────────────────────────────────────

def build_select(logical_table: str, logical_columns: Optional[list[str]] = None) -> str:
    """SELECT col1, col2 FROM vraie_table"""
    real_table = table(logical_table)
    if logical_columns:
        real_cols = ", ".join(cols(logical_table, logical_columns))
    else:
        real_cols = "*"
    return f"SELECT {real_cols} FROM {real_table}"


def build_insert(logical_table: str, values: dict[str, Any]) -> tuple[str, list]:
    """
    values = {"code": "C001", "nom": "ACME"}  (clés = colonnes LOGIQUES)
    Retourne (sql, params) avec les vrais noms de colonnes.
    """
    real_table = table(logical_table)
    real_cols  = cols(logical_table, values.keys())
    ph         = placeholder()
    placeholders = ", ".join([ph] * len(real_cols))
    sql = f"INSERT INTO {real_table} ({', '.join(real_cols)}) VALUES ({placeholders})"
    return sql, list(values.values())


def build_update(
    logical_table: str,
    values: dict[str, Any],
    where_logical_col: str,
    where_value: Any,
) -> tuple[str, list]:
    """
    Construit un UPDATE ... SET ... WHERE ... à partir de clés logiques.
    """
    real_table = table(logical_table)
    real_cols  = cols(logical_table, values.keys())
    ph         = placeholder()
    set_clause = ", ".join(f"{c} = {ph}" for c in real_cols)
    where_col  = col(logical_table, where_logical_col)
    sql        = f"UPDATE {real_table} SET {set_clause} WHERE {where_col} = {ph}"
    params     = list(values.values()) + [where_value]
    return sql, params


# ─────────────────────────────────────────────────────────────────────
# CONSTANTES T_*/C_* — pour le code qui préfère `sch.T_TIERS` à
# `table('clients_fournisseurs')`. Générées à l'import depuis
# db_config.json : aucune valeur en dur ici, tout vient du JSON.
# Si tu ajoutes une table/colonne logique, ajoute-la aussi ci-dessous
# (avec le même nom logique que dans db_config.json).
# ─────────────────────────────────────────────────────────────────────

T_TIERS       = table("clients_fournisseurs")
T_ARTICLE     = table("articles")
T_STOCK       = table("stock")
T_DOC_ENTETE  = table("doc_entete")
T_DOC_LIGNE   = table("doc_ligne")
T_MVT_STOCK   = table("mouvements_stock")
T_REGLEMENTS  = table("reglements")
T_NOMENCLAT   = table("nomenclature")

C_CT_NUM          = col("clients_fournisseurs", "code")
C_CT_INTITULE     = col("clients_fournisseurs", "nom")
C_CT_TYPE         = col("clients_fournisseurs", "type_tiers")
C_CT_VALIDITE     = col("clients_fournisseurs", "validite")
C_CT_ENCOURS_MAX  = col("clients_fournisseurs", "encours_max")
C_CT_ENCOURS      = col("clients_fournisseurs", "encours")

C_AR_REF      = col("articles", "ref")
C_AR_DESIGN   = col("articles", "designation")
C_AR_PRIXACH  = col("articles", "prix_achat")
C_AR_PRIXVEN  = col("articles", "prix_vente")
C_AR_TYPE     = col("articles", "type_article")

C_AS_REF      = col("stock", "ref")
C_AS_QTESTO   = col("stock", "qte_stock")
C_AS_QTECOM   = col("stock", "qte_commande")

C_DO_PIECE    = col("doc_entete", "piece")
C_DO_DOMAINE  = col("doc_entete", "domaine")
C_DO_TYPE     = col("doc_entete", "type")
C_DO_DATE     = col("doc_entete", "date")
C_DO_REF      = col("doc_entete", "reference")
C_DO_TIERS    = col("doc_entete", "code_tiers")

C_DL_PIECE    = col("doc_ligne", "piece")
C_DL_REF      = col("doc_ligne", "ref_article")
C_DL_QTE      = col("doc_ligne", "qte")
C_DL_PRIX     = col("doc_ligne", "prix_unitaire")
C_DL_LIGNE    = col("doc_ligne", "ligne")

C_NO_REF_PF   = col("nomenclature", "ref_pf")
C_NO_REF_MP   = col("nomenclature", "ref_mp")
C_NO_QTE      = col("nomenclature", "qte")