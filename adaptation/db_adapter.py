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
from dotenv import load_dotenv
load_dotenv()
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
            if pyodbc.pooling:
                pyodbc.pooling = False
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
                f"UID={user};PWD={password};TrustServerCertificate=yes;MARS_Connection=yes;"
            )
        else:
            # Authentification Windows intégrée
            conn_str = (
                f"DRIVER={odbc_driver};SERVER={server};DATABASE={database};"
                f"Trusted_Connection=yes;TrustServerCertificate=yes;MARS_Connection=yes;"
            )
        conn = pyodbc.connect(conn_str)
        return PyODBCConnectionWrapper(conn)

    else:
        raise ValueError(f"Driver DB inconnu: '{driver}' (attendu: 'sqlite' ou 'mssql')")


class DictRow(dict):
    """Permet l'accès aux colonnes par nom (r['col']), attribut (r.col), index (r[0]) ou insensible à la casse."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        if key in self:
            return super().__getitem__(key)
        key_lower = str(key).lower()
        for k, v in self.items():
            if str(k).lower() == key_lower:
                return v
        return super().__getitem__(key)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(e)

    def get(self, key, default=None):
        try:
            val = self[key]
            return default if val is None else val
        except (KeyError, IndexError):
            return default


class PyODBCCursorWrapper:
    def __init__(self, cursor):
        self._cursor = cursor

    @property
    def row_factory(self):
        return None

    @row_factory.setter
    def row_factory(self, val):
        pass

    def fetchone(self):
        if self._cursor is None: return None
        try:
            row = self._cursor.fetchone()
            if row is None:
                return None
            cols = [c[0] for c in self._cursor.description]
            return DictRow(zip(cols, row))
        except Exception:
            raise

    def fetchall(self):
        if self._cursor is None: return []
        try:
            rows = self._cursor.fetchall()
            if not rows or not self._cursor.description:
                return []
            cols = [c[0] for c in self._cursor.description]
            return [DictRow(zip(cols, r)) for r in rows]
        except Exception:
            raise

    def fetchmany(self, size=None):
        if self._cursor is None: return []
        try:
            rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
            if not rows or not self._cursor.description:
                return []
            cols = [c[0] for c in self._cursor.description]
            return [DictRow(zip(cols, r)) for r in rows]
        except Exception:
            raise

    def execute(self, sql, *args):
        if self._cursor: self._cursor.execute(sql, *args)
        return self

    def __iter__(self):
        if self._cursor is None: return
        try:
            rows = self._cursor.fetchall()
            if not rows or not self._cursor.description:
                return
            cols = [c[0] for c in self._cursor.description]
            for r in rows:
                yield DictRow(zip(cols, r))
        except Exception:
            raise

    def __getattr__(self, name):
        if self._cursor is None: return None
        return getattr(self._cursor, name)

    def __del__(self):
        if self._cursor is not None:
            try:
                self._cursor.close()
            except:
                pass


class PyODBCConnectionWrapper:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._row_factory = None

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, val):
        self._row_factory = val

    def cursor(self):
        return PyODBCCursorWrapper(self._conn.cursor())

    def execute(self, sql, *args):
        cur = self._conn.cursor()
        sql_upper = sql.lstrip().upper()
        is_dml = not (
            sql_upper.startswith("SELECT")
            or sql_upper.startswith("WITH")
            or sql_upper.startswith("PRAGMA")
        )
        try:
            cur.execute(sql, *args)
        except Exception:
            # Trigger a échoué (ex: erreur Sage 82084) — le curseur peut
            # avoir des result sets "fantômes" en attente côté MARS.
            # On les consomme AVANT de fermer pour éviter que la prochaine
            # opération (même un rollback) échoue avec l'erreur 3988.
            try:
                while cur.nextset():
                    pass
            except Exception:
                pass
            try:
                cur.close()
            except Exception:
                pass
            raise  # re-lever l'erreur originale vers l'appelant

        if is_dml:
            # Consume any pending result sets (e.g., from Sage triggers)
            try:
                while cur.nextset():
                    pass
            except Exception:
                pass
            cur.close()
            return PyODBCCursorWrapper(None)

        return PyODBCCursorWrapper(cur)

    def commit(self):
        # Ne PAS avaler l'exception ici : un commit qui échoue après une
        # insertion réussie doit remonter l'erreur à l'appelant, sinon le
        # code peut annoncer un succès alors que rien n'a été persisté.
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._conn.rollback()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


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

T_REGLEMENTS  = table("reglements")
T_NOMENCLAT   = table("nomenclature")
T_FAMILLE     = table("familles")
T_LOT_SERIE = table('lot_serie')
C_CT_NUM          = col("clients_fournisseurs", "code")
C_CT_INTITULE     = col("clients_fournisseurs", "nom")
C_CT_TYPE         = col("clients_fournisseurs", "type_tiers")
C_CT_SOMMEIL      = col("clients_fournisseurs", "sommeil")
C_CT_ENCOURS      = col("clients_fournisseurs", "encours")
C_CT_ADRESSE     = col("clients_fournisseurs", "adresse")
C_CT_COMPLEMENT  = col("clients_fournisseurs", "complement")
C_CT_CODEPOSTAL  = col("clients_fournisseurs", "code_postal")
C_CT_VILLE       = col("clients_fournisseurs", "ville")
C_CT_PAYS        = col("clients_fournisseurs", "pays")
C_CT_CONTACT     = col("clients_fournisseurs", "contact")
C_CT_TELEPHONE   = col("clients_fournisseurs", "telephone")
C_CT_TELECOPIE   = col("clients_fournisseurs", "telecopie")
C_CT_EMAIL       = col("clients_fournisseurs", "email")
C_CT_SITE        = col("clients_fournisseurs", "site")
C_CT_CGNUMPRINC  = col("clients_fournisseurs", "cg_num_princ")
C_LS_REF         = col('lot_serie', 'ref')
C_LS_NUMERO      = col('lot_serie', 'numero')
C_LS_FABRICATION = col('lot_serie', 'fabrication')
C_LS_PEREMPTION  = col('lot_serie', 'peremption')
C_LS_QTE_INIT    = col('lot_serie', 'qte_initiale')
C_LS_QTE_RESTE   = col('lot_serie', 'qte_restante')
C_LS_QTE_RES     = col('lot_serie', 'qte_reservee')
C_LS_EPUISE      = col('lot_serie', 'epuise')
C_LS_DEPOT       = col('lot_serie', 'depot')
C_LS_DL_IN       = col('lot_serie', 'ligne_entree')
C_LS_DL_OUT      = col('lot_serie', 'ligne_sortie')
C_LS_MVT         = col('lot_serie', 'type_mouvement')
C_LS_CBMARQ      = col('lot_serie', 'cbmarq')

# "validite" n'existe pas comme colonne native dans Sage : un tiers est
# valide s'il n'est PAS en sommeil (CT_Sommeil). Ce n'est donc pas un
# simple renommage de colonne mais une INVERSION de logique — on expose
# une expression SQL et un helper Python plutôt qu'une fausse constante
# de nom de colonne, pour éviter que quelqu'un écrive par erreur
# "WHERE C_CT_VALIDITE = 1" en pensant tester la validité directement.

def sql_est_valide(logical_table: str = "clients_fournisseurs") -> str:
    """
    Expression SQL booléenne (1 = valide, 0 = invalide) à utiliser dans un
    WHERE, dérivée de la colonne 'sommeil' inversée.
    Exemple: f"SELECT * FROM {T_TIERS} WHERE {sql_est_valide()} = 1"
    """
    sommeil_col = col(logical_table, "sommeil")
    return f"(CASE WHEN {sommeil_col} = 0 THEN 1 ELSE 0 END)"


def est_valide(row, logical_table: str = "clients_fournisseurs") -> bool:
    """
    Teste en Python si un enregistrement déjà chargé (dict/Row) est valide,
    càd non en sommeil. Utiliser après un SELECT * plutôt que de refaire une
    requête.
    """
    sommeil_col = col(logical_table, "sommeil")
    return not bool(row[sommeil_col])
C_AR_REF      = col("articles", "ref")
C_AR_DESIGN   = col("articles", "designation")
C_AR_PRIXACH  = col("articles", "prix_achat")
C_AR_PRIXVEN  = col("articles", "prix_vente")
C_AR_TYPE     = col("articles", "type_article")
C_AR_FAMILLE  = col("articles", "code_famille")
C_AR_NATURE   = col("articles", "nature")
C_AR_UNITEVEN = col("articles", "unite_vente")
C_AR_SUIVISTOCK = col("articles", "suivi_stock")

C_FA_CODE     = col("familles", "code")
C_FA_INTITULE = col("familles", "intitule")

C_AS_REF      = col("stock", "ref")
C_AS_DENO     = col("stock", "depot")
C_AS_QTESTO   = col("stock", "qte_stock")
C_AS_QTECOM   = col("stock", "qte_commande")
C_AS_MONTSTO  = col("stock", "montant_stock")
C_AS_PRINCIPAL = col("stock", "principal")

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
C_DL_ID       = col("doc_ligne", "id")
C_DL_MVTSTOCK = col("doc_ligne", "mvt_stock")
C_DL_DENO     = col("doc_ligne", "depot")
C_DL_PRIXRU   = col("doc_ligne", "prix_ru")
C_DL_CMUP     = col("doc_ligne", "cmup")
C_DL_ARCOMPOSE = col("doc_ligne", "ref_compose")
C_DL_TTC      = col("doc_ligne", "ttc")
C_DL_VALORISE = col("doc_ligne", "valorise")
C_DL_NONLIVRE = col("doc_ligne", "non_livre")
C_DL_PIECEBL  = col("doc_ligne", "piecebl")
C_DL_QTEBL    = col("doc_ligne", "qtebl")

C_NO_REF_PF   = col("nomenclature", "ref_pf")
C_NO_REF_MP   = col("nomenclature", "ref_mp")
C_NO_QTE      = col("nomenclature", "qte")
C_CT_CBMARQ   = col("clients_fournisseurs", "cb_marq")   # adaptez au nom réel de la fonction lookup
C_DE_ENTETE_CBMARQ = col("doc_entete", "cb_marq")
C_DL_CBMARQ   = col("doc_ligne", "cb_marq")
C_DL_PFNUM    = col("doc_ligne", "pf_num")
C_REGL_CBMARQ      = col("reglements", "cb_marq")
C_REGL_PIECE       = col("reglements", "piece")
C_REGL_DOMAINE     = col("reglements", "domaine")
C_REGL_TYPE_DOC    = col("reglements", "type")
C_REGL_CB_PIECE    = col("reglements", "cb_piece")
C_REGL_TYPE        = col("reglements", "type_reglement")
C_REGL_MONTANT     = col("reglements", "montant")
C_REGL_DATE        = col("reglements", "date_reglement")
C_REGL_MODE_PAI    = col("reglements", "mode_paiement")