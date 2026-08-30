"""
Rôle de ce fichier Python : Charger et gérer les configurations de bases de données, notamment la connexion à une base de données Microsoft SQL Server via ODBC.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional
from dotenv import load_dotenv
load_dotenv()
_CONFIG_PATH = Path(__file__).parent / 'db_config.json'
_config_cache: Optional[dict] = None

def _load_config() -> dict:
    """
Charge les configurations du fichier JSON en mémoire.
"""
    global _config_cache
    if _config_cache is None:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            _config_cache = json.load(f)
    return _config_cache

def reload_config() -> None:
    """
Réinitialise la cache de configuration pour forcer la recharge de la configuration.
"""
    global _config_cache
    _config_cache = None
    _load_config()

def table(logical_name: str) -> str:
    """
Récupère la configuration d'une table logique dans le fichier db_config.json.
"""
    cfg = _load_config()
    try:
        return cfg['tables'][logical_name]
    except KeyError:
        raise KeyError(f"Table logique inconnue: '{logical_name}'. Ajoute-la dans adaptation/db_config.json > tables.")

def col(logical_table: str, logical_column: str) -> str:
    """
Renvoie la valeur de colonne logique correspondante dans la configuration.
"""
    cfg = _load_config()
    try:
        return cfg['columns'][logical_table][logical_column]
    except KeyError:
        raise KeyError(f"Colonne logique inconnue: table='{logical_table}' colonne='{logical_column}'. Ajoute-la dans adaptation/db_config.json > columns.{logical_table}.")

def cols(logical_table: str, logical_columns: Iterable[str]) -> list[str]:
    """
Récupère les valeurs des colonnes d'une table logique.
"""
    return [col(logical_table, c) for c in logical_columns]

def get_connection():
    """
Crée et renvoie une connexion à une base de données SQL Server.
"""
    try:
        import pyodbc
        if pyodbc.pooling:
            pyodbc.pooling = False
    except ImportError as e:
        raise RuntimeError("driver='mssql' nécessite pyodbc. Installe-le avec: pip install pyodbc") from e
    server = os.environ['DB_SERVER']
    database = os.environ['DB_NAME']
    user = os.environ.get('DB_USER')
    password = os.environ.get('DB_PASS')
    odbc_driver = os.getenv('DB_ODBC_DRIVER', '{ODBC Driver 17 for SQL Server}')
    if user:
        conn_str = f'DRIVER={odbc_driver};SERVER={server};DATABASE={database};UID={user};PWD={password};TrustServerCertificate=yes;MARS_Connection=yes;'
    else:
        conn_str = f'DRIVER={odbc_driver};SERVER={server};DATABASE={database};Trusted_Connection=yes;TrustServerCertificate=yes;MARS_Connection=yes;'
    return _pool.get_connection(conn_str)
import queue
import threading

class _ConnectionPool:
    """
Crée et gère un pool de connexions avec le système de bases de données.
"""

    def __init__(self, max_size=10):
        """
Gestionnaire de pool de tâches, permettant d'ajouter et de récupérer des tâches de manière concurrentielle.
"""
        self.pool = queue.Queue(maxsize=max_size)
        self.lock = threading.Lock()
        self.size = 0
        self.max_size = max_size
        self.local = threading.local()

    def _ping(self, conn):
        """
Teste la connectivité à une base de données en effectuant une requête SQL simple.
"""
        try:
            cur = conn.cursor()
            cur.execute('SELECT 1')
            cur.fetchone()
            cur.close()
            return True
        except Exception:
            return False

    def get_connection(self, conn_str):
        """
Rétourne une connexion à une base de données en la prenant dans une pool si possible, sinon en la créant en temps réel.
"""
        if getattr(self.local, 'conn', None) is not None:
            wrapper = self.local.conn
            if self._ping(wrapper._conn):
                return wrapper
            else:
                self.local.conn = None
        import pyodbc
        while True:
            try:
                conn = self.pool.get_nowait()
                if self._ping(conn):
                    break
                else:
                    try:
                        conn.close()
                    except Exception:
                        pass
            except queue.Empty:
                conn = pyodbc.connect(conn_str)
                break
        wrapper = PyODBCConnectionWrapper(conn, pool=self)
        self.local.conn = wrapper
        return wrapper

    def release(self, conn_wrapper):
        """
Gère la mise en attente d'une connexion de base de données dans une file de requêtes.
"""
        if hasattr(self.local, 'conn'):
            self.local.conn = None
        try:
            self.pool.put_nowait(conn_wrapper._conn)
        except queue.Full:
            conn_wrapper._conn.close()
_pool = _ConnectionPool(max_size=int(os.environ.get('DB_POOL_SIZE', '15')))

class DictRow(dict):
    """
Réalise le traitement des clés de type int, en les remplaçant par les valeurs correspondantes de la liste des valeurs.
"""

    def __getitem__(self, key):
        """
Cette méthode permet de récupérer un élément d'un objet héritant de la classe dict, en utilisant soit un indice entier, soit une clé en chaîne de caractères.
"""
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
        """
Permet de traiter les attributs non définis de la classe comme des clés d'un dictionnaire.
"""
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(e)

    def get(self, key, default=None):
        """
Renvoie la valeur associée à la clé spécifiée, ou le valeur par défaut si la clé n'existe pas dans le tableau.
"""
        try:
            val = self[key]
            return default if val is None else val
        except (KeyError, IndexError):
            return default

class PyODBCCursorWrapper:
    """
Facilitateur de gestion d'un curseur ODBC, permettant d'utiliser des enregistrements sous forme de dictionnaires.
"""

    def __init__(self, cursor):
        """
Création et initialisation d'un objet avec un curseur de base de données.
"""
        self._cursor = cursor

    @property
    def row_factory(self):
        """
Crée une propriété permettant de fabriquer des lignes.
"""
        return None

    @row_factory.setter
    def row_factory(self, val):
        """
Fonction qui définit le type de données retourné par la base de données pour les enregistrements.
"""
        pass

    def fetchone(self):
        """
Retourne une ligne de résultats sous forme de dictionnaire, si disponibles.
"""
        if self._cursor is None:
            return None
        try:
            row = self._cursor.fetchone()
            if row is None:
                return None
            cols = [c[0] for c in self._cursor.description]
            return DictRow(zip(cols, row))
        except Exception:
            raise

    def fetchall(self):
        """
Retourne la liste de toutes les lignes d'un résultat de requête SQL.
"""
        if self._cursor is None:
            return []
        try:
            rows = self._cursor.fetchall()
            if not rows or not self._cursor.description:
                return []
            cols = [c[0] for c in self._cursor.description]
            return [DictRow(zip(cols, r)) for r in rows]
        except Exception:
            raise

    def fetchmany(self, size=None):
        """
Récupère les données d'une requête SQL en utilisant le mécanisme de curseur.
"""
        if self._cursor is None:
            return []
        try:
            rows = self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()
            if not rows or not self._cursor.description:
                return []
            cols = [c[0] for c in self._cursor.description]
            return [DictRow(zip(cols, r)) for r in rows]
        except Exception:
            raise

    def execute(self, sql, *args):
        """
Exécute une requête SQL sur la base de données.
"""
        if self._cursor:
            self._cursor.execute(sql, *args)
        return self

    def __iter__(self):
        """
Fournit un itérateur permettant de parcourir les résultats d'une requête SQL.
"""
        if self._cursor is None:
            return
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
        """
Rétourne la valeur d'un attribut d'un objet interne.
"""
        if self._cursor is None:
            return None
        return getattr(self._cursor, name)

    def __del__(self):
        """
Ferme la connexion à la base de données si elle est ouverte.
"""
        if self._cursor is not None:
            try:
                self._cursor.close()
            except:
                pass

class PyODBCConnectionWrapper:
    """
Crée et gère une connexion ODBC vers une base de données.
"""

    def __init__(self, raw_conn, pool=None):
        """
Crée une connexion à une base de données.
"""
        self._conn = raw_conn
        self._pool = pool
        self._row_factory = None

    def close(self):
        """
Rôle de la fonction close : fermer la connexion à la base de données et libérer les ressources associées.
"""
        if self._pool:
            try:
                self._conn.rollback()
            except Exception:
                pass
            self._pool.release(self)
        else:
            self._conn.close()

    @property
    def row_factory(self):
        """
Crée une instance de la fabrique de lignes si elle n'est pas déjà initialisée.
"""
        return self._row_factory

    @row_factory.setter
    def row_factory(self, val):
        """
Définit ou modifie la fonction fabrique de lignes du résultat de requête.
"""
        self._row_factory = val

    def cursor(self):
        """
Renvoie un objet de type PyODBCCursorWrapper permettant l'exécution de requêtes sur la base de données.
"""
        return PyODBCCursorWrapper(self._conn.cursor())

    def execute(self, sql, *args):
        """
Cette fonction exécute des requêtes SQL sur une base de données et gère les résultats.
"""
        cur = self._conn.cursor()
        sql_upper = sql.lstrip().upper()
        is_dml = not (sql_upper.startswith('SELECT') or sql_upper.startswith('WITH') or sql_upper.startswith('PRAGMA'))
        try:
            cur.execute(sql, *args)
        except Exception:
            try:
                while cur.nextset():
                    pass
            except Exception:
                pass
            try:
                cur.close()
            except Exception:
                pass
            raise
        if is_dml:
            try:
                while cur.nextset():
                    pass
            except Exception:
                pass
            cur.close()
            return PyODBCCursorWrapper(None)
        return PyODBCCursorWrapper(cur)

    def commit(self):
        """
Cette fonction permet d'exécuter les modifications non encore validées dans la base de données.
"""
        self._conn.commit()

    def rollback(self):
        """
Fonction de réversion d'une transaction en base de données.
"""
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        """
Cette fonction permet de fermer la connexion à la base de données et d'annuler les opérations non validées.
"""
        try:
            self._conn.rollback()
        except Exception:
            pass
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self):
        """
Fournit un contexte pour l'utilisation avec les opérateurs de bloc avec.
"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
Cette fonction est une méthode spécifique des exceptions utilisées avec le mot clé `with`, elle permet de gérer la fermeture des ressources utilisées.
"""
        if exc_type:
            self.rollback()
        else:
            self.commit()

    def __getattr__(self, name):
        """
Rôle de cette fonction : permet de faire appel aux attributs d'une instance de base de données comme s'ils étaient propres à l'objet.

Cette fonction permet de faire appel aux attributs d'une instance de base de données comme s'ils étaient propres à l'objet.
"""
        return getattr(self._conn, name)

def placeholder() -> str:
    """
Renvoie un caractère de remplacement standard dans les requêtes SQL, '?'
"""
    return '?'

def build_select(logical_table: str, logical_columns: Optional[list[str]]=None) -> str:
    """
Rôle de la fonction : Construire une chaîne SQL pour une requête SELECT.
"""
    real_table = table(logical_table)
    if logical_columns:
        real_cols = ', '.join(cols(logical_table, logical_columns))
    else:
        real_cols = '*'
    return f'SELECT {real_cols} FROM {real_table}'

def build_insert(logical_table: str, values: dict[str, Any]) -> tuple[str, list]:
    """
Génère une requête SQL d'insertion dans une table physique en fonction d'une table logique et de valeurs associées.
"""
    real_table = table(logical_table)
    real_cols = cols(logical_table, values.keys())
    ph = placeholder()
    placeholders = ', '.join([ph] * len(real_cols))
    sql = f"INSERT INTO {real_table} ({', '.join(real_cols)}) VALUES ({placeholders})"
    return (sql, list(values.values()))

def build_update(logical_table: str, values: dict[str, Any], where_logical_col: str, where_value: Any) -> tuple[str, list]:
    """
Génération de requête SQL d'update et de paramètres associés pour mise à jour d'une table physique à partir d'une table logique.
"""
    real_table = table(logical_table)
    real_cols = cols(logical_table, values.keys())
    ph = placeholder()
    set_clause = ', '.join((f'{c} = {ph}' for c in real_cols))
    where_col = col(logical_table, where_logical_col)
    sql = f'UPDATE {real_table} SET {set_clause} WHERE {where_col} = {ph}'
    params = list(values.values()) + [where_value]
    return (sql, params)
T_TIERS = table('clients_fournisseurs')
T_ARTICLE = table('articles')
T_STOCK = table('stock')
T_DOC_ENTETE = table('doc_entete')
T_DOC_LIGNE = table('doc_ligne')
T_REGLEMENTS = table('reglements')
T_NOMENCLAT = table('nomenclature')
T_FAMILLE = table('familles')
T_LOT_SERIE = table('lot_serie')
C_CT_NUM = col('clients_fournisseurs', 'code')
C_CT_INTITULE = col('clients_fournisseurs', 'nom')
C_CT_TYPE = col('clients_fournisseurs', 'type_tiers')
C_CT_SOMMEIL = col('clients_fournisseurs', 'sommeil')
C_CT_ENCOURS = col('clients_fournisseurs', 'encours')
C_CT_ADRESSE = col('clients_fournisseurs', 'adresse')
C_CT_COMPLEMENT = col('clients_fournisseurs', 'complement')
C_CT_CODEPOSTAL = col('clients_fournisseurs', 'code_postal')
C_CT_VILLE = col('clients_fournisseurs', 'ville')
C_CT_PAYS = col('clients_fournisseurs', 'pays')
C_CT_CONTACT = col('clients_fournisseurs', 'contact')
C_CT_TELEPHONE = col('clients_fournisseurs', 'telephone')
C_CT_TELECOPIE = col('clients_fournisseurs', 'telecopie')
C_CT_EMAIL = col('clients_fournisseurs', 'email')
C_CT_SITE = col('clients_fournisseurs', 'site')
C_CT_CGNUMPRINC = col('clients_fournisseurs', 'cg_num_princ')
C_LS_REF = col('lot_serie', 'ref')
C_LS_NUMERO = col('lot_serie', 'numero')
C_LS_FABRICATION = col('lot_serie', 'fabrication')
C_LS_PEREMPTION = col('lot_serie', 'peremption')
C_LS_QTE_INIT = col('lot_serie', 'qte_initiale')
C_LS_QTE_RESTE = col('lot_serie', 'qte_restante')
C_LS_QTE_RES = col('lot_serie', 'qte_reservee')
C_LS_EPUISE = col('lot_serie', 'epuise')
C_LS_DEPOT = col('lot_serie', 'depot')
C_LS_DL_IN = col('lot_serie', 'ligne_entree')
C_LS_DL_OUT = col('lot_serie', 'ligne_sortie')
C_LS_MVT = col('lot_serie', 'type_mouvement')
C_LS_CBMARQ = col('lot_serie', 'cbmarq')

def sql_est_valide(logical_table: str='clients_fournisseurs') -> str:
    """
Cette fonction génère une expression SQL valide pour un tableau logique, remplissant un champ spécifique avec 0 ou 1 en fonction de la valeur d'une colonne spécifique.
"""
    sommeil_col = col(logical_table, 'sommeil')
    return f'(CASE WHEN {sommeil_col} = 0 THEN 1 ELSE 0 END)'

def est_valide(row, logical_table: str='clients_fournisseurs') -> bool:
    """
Cette fonction vérifie si un élément est valide en fonction de la table logique spécifiée. Elle retourne True si le champ 'sommeil' est vide pour la ligne donnée.
"""
    sommeil_col = col(logical_table, 'sommeil')
    return not bool(row[sommeil_col])
C_AR_REF = col('articles', 'ref')
C_AR_DESIGN = col('articles', 'designation')
C_AR_PRIXACH = col('articles', 'prix_achat')
C_AR_PRIXVEN = col('articles', 'prix_vente')
C_AR_TYPE = col('articles', 'type_article')
C_AR_FAMILLE = col('articles', 'code_famille')
C_AR_NATURE = col('articles', 'nature')
C_AR_UNITEVEN = col('articles', 'unite_vente')
C_AR_SUIVISTOCK = col('articles', 'suivi_stock')
C_FA_CODE = col('familles', 'code')
C_FA_INTITULE = col('familles', 'intitule')
C_AS_REF = col('stock', 'ref')
C_AS_DENO = col('stock', 'depot')
C_AS_QTESTO = col('stock', 'qte_stock')
C_AS_QTECOM = col('stock', 'qte_commande')
C_AS_MONTSTO = col('stock', 'montant_stock')
C_AS_PRINCIPAL = col('stock', 'principal')
C_DO_PIECE = col('doc_entete', 'piece')
C_DO_DOMAINE = col('doc_entete', 'domaine')
C_DO_TYPE = col('doc_entete', 'type')
C_DO_DATE = col('doc_entete', 'date')
C_DO_REF = col('doc_entete', 'reference')
C_DO_TIERS = col('doc_entete', 'code_tiers')
C_DL_PIECE = col('doc_ligne', 'piece')
C_DL_REF = col('doc_ligne', 'ref_article')
C_DL_QTE = col('doc_ligne', 'qte')
C_DL_PRIX = col('doc_ligne', 'prix_unitaire')
C_DL_LIGNE = col('doc_ligne', 'ligne')
C_DL_ID = col('doc_ligne', 'id')
C_DL_MVTSTOCK = col('doc_ligne', 'mvt_stock')
C_DL_DENO = col('doc_ligne', 'depot')
C_DL_PRIXRU = col('doc_ligne', 'prix_ru')
C_DL_CMUP = col('doc_ligne', 'cmup')
C_DL_ARCOMPOSE = col('doc_ligne', 'ref_compose')
C_DL_TTC = col('doc_ligne', 'ttc')
C_DL_VALORISE = col('doc_ligne', 'valorise')
C_DL_NONLIVRE = col('doc_ligne', 'non_livre')
C_DL_PIECEBL = col('doc_ligne', 'piecebl')
C_DL_QTEBL = col('doc_ligne', 'qtebl')
C_NO_REF_PF = col('nomenclature', 'ref_pf')
C_NO_REF_MP = col('nomenclature', 'ref_mp')
C_NO_QTE = col('nomenclature', 'qte')
C_CT_CBMARQ = col('clients_fournisseurs', 'cb_marq')
C_DE_ENTETE_CBMARQ = col('doc_entete', 'cb_marq')
C_DL_CBMARQ = col('doc_ligne', 'cb_marq')
C_DL_PFNUM = col('doc_ligne', 'pf_num')
C_REGL_CBMARQ = col('reglements', 'cb_marq')
C_REGL_PIECE = col('reglements', 'piece')
C_REGL_DOMAINE = col('reglements', 'domaine')
C_REGL_TYPE_DOC = col('reglements', 'type')
C_REGL_CB_PIECE = col('reglements', 'cb_piece')
C_REGL_TYPE = col('reglements', 'type_reglement')
C_REGL_MONTANT = col('reglements', 'montant')
C_REGL_DATE = col('reglements', 'date_reglement')
C_REGL_REFERENCE = col('reglements', 'reference_paiement')
C_REGL_MODE_PAI = col('reglements', 'mode_paiement')