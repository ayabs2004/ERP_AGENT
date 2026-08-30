"""
Crée les tables de base pour une base de données relationnelle, en particulier pour une application de gestion de comptabilité.
"""
from datetime import date, timedelta

def _default_db_path() -> str:
    """
Fonction qui retourne le chemin du fichier de base de données par défaut en cas de None.
"""
    from adaptation.db_adapter import get_sqlite_path
    p = get_sqlite_path()
    return str(p) if p is not None else 'entreprise_mock.db'

def init_database_complete(db_path: str | None=None):
    """
Crée une base de données SQLite complète avec deux tables : F_COMPTET et F_ARTICLE.
"""
    conn = sqlite3.connect(db_path or _default_db_path())
    cursor = conn.cursor()
    print("Creation de l'architecture complete type Sage...")
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_COMPTET (\n        CT_Num        TEXT PRIMARY KEY,\n        CT_Intitule   TEXT,\n        CT_Type       INTEGER,\n        CT_Validite   TEXT,\n        CT_EncoursMax REAL,\n        CT_Encours    REAL DEFAULT 0.0,\n        CT_Sommeil    INTEGER DEFAULT 0,\n        cbMarq        INTEGER DEFAULT 0\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_ARTICLE (\n        AR_Ref          TEXT PRIMARY KEY,\n        AR_Design       TEXT,\n        AR_PrixAch      REAL,\n        AR_PrixVen      REAL,\n        AR_Type         INTEGER,\n        FA_CodeFamille  TEXT,\n        cbMarq          INTEGER DEFAULT 0\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_ARTSTOCK (\n        AR_Ref        TEXT PRIMARY KEY,\n        AS_QteSto     REAL,\n        AS_QteCom     REAL,\n        AS_QteAchaCom REAL,\n        DE_No         INTEGER DEFAULT 0,\n        cbMarq        INTEGER DEFAULT 0,\n        FOREIGN KEY(AR_Ref) REFERENCES F_ARTICLE(AR_Ref)\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_NOMENCLAT (\n        AR_Ref   TEXT,\n        NO_RefDet TEXT,\n        NO_Qte   REAL,\n        cbMarq   INTEGER DEFAULT 0,\n        PRIMARY KEY (AR_Ref, NO_RefDet),\n        FOREIGN KEY(AR_Ref)    REFERENCES F_ARTICLE(AR_Ref),\n        FOREIGN KEY(NO_RefDet) REFERENCES F_ARTICLE(AR_Ref)\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_DOCENTETE (\n        DO_Piece   TEXT PRIMARY KEY,\n        DO_Domaine INTEGER,\n        DO_Type    INTEGER,\n        DO_Date    TEXT,\n        DO_Ref     TEXT,\n        CT_Num     TEXT,\n        DO_Tiers   TEXT,\n        cbMarq     INTEGER DEFAULT 0,\n        FOREIGN KEY(CT_Num) REFERENCES F_COMPTET(CT_Num)\n    )')
    for col_def in ['DO_Tiers TEXT', 'cbMarq INTEGER DEFAULT 0']:
        try:
            cursor.execute(f'ALTER TABLE F_DOCENTETE ADD COLUMN {col_def}')
        except Exception:
            pass
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_DOCLIGNE (\n        DL_Ligne        INTEGER PRIMARY KEY AUTOINCREMENT,\n        DO_Piece        TEXT,\n        AR_Ref          TEXT,\n        DL_Qte          REAL,\n        DL_PrixUnitaire REAL,\n        PF_Num          TEXT,\n        cbMarq          INTEGER DEFAULT 0,\n        FOREIGN KEY(DO_Piece) REFERENCES F_DOCENTETE(DO_Piece),\n        FOREIGN KEY(AR_Ref)   REFERENCES F_ARTICLE(AR_Ref)\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS F_DOCREGL (\n        id             INTEGER PRIMARY KEY AUTOINCREMENT,\n        DO_Piece       TEXT,\n        DR_TypeRegl    TEXT,\n        mode_paiement  TEXT,\n        DR_Montant     REAL,\n        montant        REAL,\n        DR_Date        TEXT,\n        date_reglement TEXT,\n        cbMarq         INTEGER DEFAULT 0\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS reglements (\n        id             INTEGER PRIMARY KEY AUTOINCREMENT,\n        DO_Piece       TEXT,\n        mode_paiement  TEXT,\n        montant        REAL,\n        date_reglement TEXT\n    )')
    cursor.execute('\n    CREATE TABLE IF NOT EXISTS mouvements_stock (\n        id             INTEGER PRIMARY KEY AUTOINCREMENT,\n        AR_Ref         TEXT,\n        type_mouvement TEXT,\n        qte            REAL,\n        motif          TEXT,\n        date_mouvement TEXT\n    )')
    print('Insertion du jeu de donnees...')
    tiers = [('CLI001', 'Societe Alpha', 0, 'VALIDE', 5000.0, 0.0), ('CLI002', 'Boutique Beta', 0, 'BLOQUE', 0.0, 0.0), ('CLI003', 'Entreprise Gamma', 0, 'VALIDE', 8000.0, 0.0), ('CLI004', 'Commerce Delta', 0, 'VALIDE', 3000.0, 0.0), ('CLI005', 'Societe Epsilon', 0, 'SUSPECT', 2000.0, 0.0), ('FOUR01', 'Fournisseur Composants', 1, 'VALIDE', 0.0, 0.0), ('FOUR02', 'Grossiste Electronique', 1, 'VALIDE', 0.0, 0.0), ('PROD-INT', 'Production Interne', 2, 'VALIDE', 0.0, 0.0)]
    cursor.executemany('INSERT OR REPLACE INTO F_COMPTET (CT_Num, CT_Intitule, CT_Type, CT_Validite, CT_EncoursMax, CT_Encours) VALUES (?,?,?,?,?,?)', tiers)
    articles_pf = [('ECRAN4K', 'Ecran 4K 27 pouces', 80.0, 250.0, 0), ('LAPTOP', 'Laptop Professionnel', 400.0, 1200.0, 0), ('IMPRIMANTE', 'Imprimante Laser Pro', 120.0, 350.0, 0)]
    composants_ecran4k = [('DALLE-LED-4K', 'Dalle LED 4K 27 pouces', 60.0, 80.0, 1), ('CARTE-VIDEO', 'Carte video HDMI/DP', 35.0, 45.0, 1), ('CHASSIS-ALU', 'Chassis aluminium', 18.0, 25.0, 1), ('ALIMENTATION', 'Bloc alimentation 65W', 15.0, 20.0, 1), ('CABLE-HDMI', 'Cable HDMI 1.5m', 3.0, 5.0, 1)]
    composants_laptop = [('CPU-I7', 'Processeur Intel i7', 150.0, 200.0, 1), ('RAM-16GB', 'Barrette RAM 8GB DDR4', 35.0, 45.0, 1), ('SSD-512', 'SSD NVMe 512GB', 60.0, 80.0, 1), ('ECRAN-FHD', 'Ecran FHD 15.6 pouces', 90.0, 120.0, 1), ('BATTERIE', 'Batterie 72Wh', 45.0, 60.0, 1)]
    autres_articles = [('DALLE01', 'Dalle LCD 27 pouces', 50.0, 60.0, 1), ('CHASSIS', 'Chassis Plastique', 10.0, 15.0, 1)]
    tous_articles = articles_pf + composants_ecran4k + composants_laptop + autres_articles
    cursor.executemany('INSERT OR REPLACE INTO F_ARTICLE (AR_Ref, AR_Design, AR_PrixAch, AR_PrixVen, AR_Type) VALUES (?,?,?,?,?)', tous_articles)
    stocks = [('ECRAN4K', 2.0, 0.0, 0.0), ('LAPTOP', 3.0, 0.0, 0.0), ('IMPRIMANTE', 5.0, 0.0, 0.0), ('DALLE-LED-4K', 20.0, 0.0, 0.0), ('CARTE-VIDEO', 15.0, 0.0, 0.0), ('CHASSIS-ALU', 25.0, 0.0, 0.0), ('ALIMENTATION', 30.0, 0.0, 0.0), ('CABLE-HDMI', 50.0, 0.0, 0.0), ('CPU-I7', 10.0, 0.0, 0.0), ('RAM-16GB', 20.0, 0.0, 0.0), ('SSD-512', 12.0, 0.0, 0.0), ('ECRAN-FHD', 8.0, 0.0, 0.0), ('BATTERIE', 15.0, 0.0, 0.0), ('DALLE01', 10.0, 0.0, 0.0), ('CHASSIS', 15.0, 0.0, 0.0)]
    cursor.executemany('INSERT OR REPLACE INTO F_ARTSTOCK (AR_Ref, AS_QteSto, AS_QteCom, AS_QteAchaCom) VALUES (?,?,?,?)', stocks)
    nomenclature = [('ECRAN4K', 'DALLE-LED-4K', 1.0), ('ECRAN4K', 'CARTE-VIDEO', 1.0), ('ECRAN4K', 'CHASSIS-ALU', 1.0), ('ECRAN4K', 'ALIMENTATION', 1.0), ('ECRAN4K', 'CABLE-HDMI', 2.0), ('LAPTOP', 'CPU-I7', 1.0), ('LAPTOP', 'RAM-16GB', 2.0), ('LAPTOP', 'SSD-512', 1.0), ('LAPTOP', 'ECRAN-FHD', 1.0), ('LAPTOP', 'BATTERIE', 1.0)]
    cursor.executemany('INSERT OR REPLACE INTO F_NOMENCLAT (AR_Ref, NO_RefDet, NO_Qte) VALUES (?,?,?)', nomenclature)
    today = date.today()
    docs = [('BL00001', 0, 3, (today - timedelta(days=60)).isoformat(), None, 'CLI001', 'CLI001'), ('BL00002', 0, 3, (today - timedelta(days=45)).isoformat(), None, 'CLI003', 'CLI003'), ('FA00001', 0, 6, (today - timedelta(days=55)).isoformat(), None, 'CLI001', 'CLI001'), ('FA00002', 0, 6, (today - timedelta(days=40)).isoformat(), None, 'CLI001', 'CLI001'), ('FA00003', 0, 6, (today - timedelta(days=25)).isoformat(), None, 'CLI003', 'CLI003'), ('FA00004', 0, 6, (today - timedelta(days=20)).isoformat(), None, 'CLI004', 'CLI004'), ('FA00005', 0, 6, (today - timedelta(days=10)).isoformat(), None, 'CLI005', 'CLI005'), ('FA00006', 0, 6, (today - timedelta(days=90)).isoformat(), 'SOLDEE / REGLEE - Virement', 'CLI001', 'CLI001'), ('FA00007', 0, 6, (today - timedelta(days=80)).isoformat(), 'SOLDEE / REGLEE - Cheque', 'CLI003', 'CLI003'), ('OF00001', 2, 25, (today - timedelta(days=15)).isoformat(), None, 'PROD-INT', 'PROD-INT'), ('BC00001', 1, 11, (today - timedelta(days=50)).isoformat(), None, 'FOUR01', 'FOUR01'), ('BC00002', 1, 11, (today - timedelta(days=30)).isoformat(), None, 'FOUR02', 'FOUR02'), ('BC00003', 1, 11, (today - timedelta(days=10)).isoformat(), None, 'FOUR01', 'FOUR01'), ('BR00001', 1, 13, (today - timedelta(days=45)).isoformat(), None, 'FOUR01', 'FOUR01'), ('BR00002', 1, 13, (today - timedelta(days=25)).isoformat(), None, 'FOUR02', 'FOUR02'), ('FF00001', 1, 16, (today - timedelta(days=44)).isoformat(), None, 'FOUR01', 'FOUR01'), ('FF00002', 1, 16, (today - timedelta(days=24)).isoformat(), None, 'FOUR02', 'FOUR02'), ('FF00003', 1, 16, (today - timedelta(days=8)).isoformat(), None, 'FOUR01', 'FOUR01')]
    cursor.executemany('INSERT OR REPLACE INTO F_DOCENTETE (DO_Piece, DO_Domaine, DO_Type, DO_Date, DO_Ref, CT_Num, DO_Tiers) VALUES (?,?,?,?,?,?,?)', docs)
    lignes = [('BL00001', 'ECRAN4K', 3.0, 250.0), ('BL00002', 'LAPTOP', 2.0, 1200.0), ('FA00001', 'ECRAN4K', 3.0, 250.0), ('FA00002', 'LAPTOP', 1.0, 1200.0), ('FA00003', 'ECRAN4K', 4.0, 250.0), ('FA00003', 'IMPRIMANTE', 1.0, 350.0), ('FA00004', 'LAPTOP', 2.0, 1200.0), ('FA00005', 'ECRAN4K', 2.0, 250.0), ('FA00006', 'ECRAN4K', 5.0, 250.0), ('FA00007', 'LAPTOP', 3.0, 1200.0), ('OF00001', 'ECRAN4K', 5.0, 250.0), ('BC00001', 'DALLE-LED-4K', 10.0, 60.0), ('BC00001', 'CARTE-VIDEO', 8.0, 35.0), ('BC00001', 'CHASSIS-ALU', 12.0, 18.0), ('BC00002', 'CPU-I7', 5.0, 150.0), ('BC00002', 'RAM-16GB', 10.0, 35.0), ('BC00002', 'SSD-512', 6.0, 60.0), ('BC00003', 'DALLE-LED-4K', 15.0, 60.0), ('BC00003', 'ALIMENTATION', 20.0, 15.0), ('BR00001', 'DALLE-LED-4K', 10.0, 60.0), ('BR00001', 'CARTE-VIDEO', 8.0, 35.0), ('BR00002', 'CPU-I7', 5.0, 150.0), ('BR00002', 'RAM-16GB', 10.0, 35.0), ('FF00001', 'DALLE-LED-4K', 10.0, 60.0), ('FF00001', 'CARTE-VIDEO', 8.0, 35.0), ('FF00002', 'CPU-I7', 5.0, 150.0), ('FF00002', 'RAM-16GB', 10.0, 35.0), ('FF00003', 'DALLE-LED-4K', 15.0, 60.0), ('FF00003', 'ALIMENTATION', 20.0, 15.0)]
    cursor.executemany('INSERT OR REPLACE INTO F_DOCLIGNE (DO_Piece, AR_Ref, DL_Qte, DL_PrixUnitaire) VALUES (?,?,?,?)', lignes)
    reglements_fdocregl = [('FA00006', 'Virement', 'Virement', 1250.0, 1250.0, (today - timedelta(days=85)).isoformat(), (today - timedelta(days=85)).isoformat()), ('FA00007', 'Cheque', 'Cheque', 3600.0, 3600.0, (today - timedelta(days=75)).isoformat(), (today - timedelta(days=75)).isoformat())]
    cursor.executemany('INSERT OR REPLACE INTO F_DOCREGL (DO_Piece, DR_TypeRegl, mode_paiement, DR_Montant, montant, DR_Date, date_reglement) VALUES (?,?,?,?,?,?,?)', reglements_fdocregl)
    reglements = [('FA00006', 'Virement', 1250.0, (today - timedelta(days=85)).isoformat()), ('FA00007', 'Cheque', 3600.0, (today - timedelta(days=75)).isoformat())]
    cursor.executemany('INSERT OR REPLACE INTO reglements (DO_Piece, mode_paiement, montant, date_reglement) VALUES (?,?,?,?)', reglements)
    conn.commit()
    conn.close()
    print('Base de donnees Sage operationnelle.')
    print('  Clients          : CLI001 (Valide), CLI002 (Bloque), CLI003-004 (Valide), CLI005 (Suspect)')
    print('  Fournisseurs     : FOUR01 (Fournisseur Composants), FOUR02 (Grossiste Electronique)')
    print('  Tiers interne    : PROD-INT')
    print('  Composants ECRAN4K : DALLE-LED-4K, CARTE-VIDEO, CHASSIS-ALU, ALIMENTATION, CABLE-HDMI')
    print('  Composants LAPTOP  : CPU-I7, RAM-16GB, SSD-512, ECRAN-FHD, BATTERIE')
    print('  Docs vente       : BL00001-2, FA00001-7')
    print('  Docs fabrication : OF00001')
    print('  Docs achat       : BC00001-3 (commandes), BR00001-2 (receptions), FF00001-3 (factures)')
    print('  Reglements       : FA00006 (Virement), FA00007 (Cheque)')
if __name__ == '__main__':
    init_database_complete()

def init_database_mssql():
    """
Initialise une base de données MSSQL en appliquant les migrations.
"""
    import os
    from pathlib import Path
    from adaptation.db_adapter import get_connection
    import sys
    mig_file = Path(__file__).parent / 'migrations' / 'mssql_fix_82086.sql'
    if not mig_file.exists():
        print(f'⚠️  Script de migration introuvable : {mig_file}', file=sys.stderr)
        return
    print('🗄️  [MSSQL] Vérification et application des migrations...', file=sys.stderr)
    try:
        with open(mig_file, 'r', encoding='utf-8') as f:
            sql = f.read()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        conn.close()
        print('✅ [MSSQL] Migrations appliquées avec succès (message 82086).', file=sys.stderr)
    except Exception as e:
        print(f"⚠️  [MSSQL] Échec de l'application des migrations : {e}", file=sys.stderr)