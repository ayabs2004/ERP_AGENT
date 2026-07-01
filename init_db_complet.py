import sqlite3
from datetime import date, timedelta

def init_database_complete(db_path: str | None = None):
    conn = sqlite3.connect(db_path or "entreprise_mock.db")
    cursor = conn.cursor()

    print("Creation de l'architecture complete type Sage...")

    # ══════════════════════════════════════════════════════════════════
    # 1. TIERS
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS F_COMPTET (
        CT_Num        TEXT PRIMARY KEY,
        CT_Intitule   TEXT,
        CT_Type       INTEGER,
        CT_Validite   TEXT,
        CT_EncoursMax REAL,
        CT_Encours    REAL DEFAULT 0.0
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 2. ARTICLES
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS F_ARTICLE (
        AR_Ref     TEXT PRIMARY KEY,
        AR_Design  TEXT,
        AR_PrixAch REAL,
        AR_PrixVen REAL,
        AR_Type    INTEGER
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 3. STOCKS
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS F_ARTSTOCK (
        AR_Ref        TEXT PRIMARY KEY,
        AS_QteSto     REAL,
        AS_QteCom     REAL,
        AS_QteAchaCom REAL,
        FOREIGN KEY(AR_Ref) REFERENCES F_ARTICLE(AR_Ref)
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 4. NOMENCLATURES
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS F_NOMENCLAT (
        NO_RefPF TEXT,
        NO_RefMP TEXT,
        NO_Qte   REAL,
        PRIMARY KEY (NO_RefPF, NO_RefMP),
        FOREIGN KEY(NO_RefPF) REFERENCES F_ARTICLE(AR_Ref),
        FOREIGN KEY(NO_RefMP) REFERENCES F_ARTICLE(AR_Ref)
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 5. ENTETES DOCUMENTS
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS F_DOCENTETE (
        DO_Piece   TEXT PRIMARY KEY,
        DO_Domaine INTEGER,
        DO_Type    INTEGER,
        DO_Date    TEXT,
        DO_Ref     TEXT,
        CT_Num     TEXT,
        FOREIGN KEY(CT_Num) REFERENCES F_COMPTET(CT_Num)
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 6. LIGNES DOCUMENTS
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS F_DOCLIGNE (
        DL_Ligne        INTEGER PRIMARY KEY AUTOINCREMENT,
        DO_Piece        TEXT,
        AR_Ref          TEXT,
        DL_Qte          REAL,
        DL_PrixUnitaire REAL,
        FOREIGN KEY(DO_Piece) REFERENCES F_DOCENTETE(DO_Piece),
        FOREIGN KEY(AR_Ref)   REFERENCES F_ARTICLE(AR_Ref)
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 7. REGLEMENTS
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reglements (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        DO_Piece       TEXT,
        mode_paiement  TEXT,
        montant        REAL,
        date_reglement TEXT
    )""")

    # ══════════════════════════════════════════════════════════════════
    # 8. MOUVEMENTS STOCK
    # ══════════════════════════════════════════════════════════════════
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS mouvements_stock (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        AR_Ref         TEXT,
        type_mouvement TEXT,
        qte            REAL,
        motif          TEXT,
        date_mouvement TEXT
    )""")

    print("Insertion du jeu de donnees...")

    # ── Tiers ─────────────────────────────────────────────────────────
    tiers = [
        ('CLI001',   'Societe Alpha',           0, 'VALIDE',  5000.0, 0.0),
        ('CLI002',   'Boutique Beta',            0, 'BLOQUE',     0.0, 0.0),
        ('CLI003',   'Entreprise Gamma',         0, 'VALIDE',  8000.0, 0.0),
        ('CLI004',   'Commerce Delta',           0, 'VALIDE',  3000.0, 0.0),
        ('CLI005',   'Societe Epsilon',          0, 'SUSPECT', 2000.0, 0.0),
        ('FOUR01',   'Fournisseur Composants',   1, 'VALIDE',     0.0, 0.0),
        ('FOUR02',   'Grossiste Electronique',   1, 'VALIDE',     0.0, 0.0),
        # Tiers interne fabrication — CRITIQUE pour OF/BF
        ('PROD-INT', 'Production Interne',       2, 'VALIDE',     0.0, 0.0),
    ]
    cursor.executemany(
        "INSERT OR REPLACE INTO F_COMPTET VALUES (?,?,?,?,?,?)", tiers
    )

    # ── Articles produits finis (AR_Type=0) ───────────────────────────
    articles_pf = [
        ('ECRAN4K',    'Ecran 4K 27 pouces',      80.0,  250.0, 0),
        ('LAPTOP',     'Laptop Professionnel',    400.0, 1200.0, 0),
        ('IMPRIMANTE', 'Imprimante Laser Pro',    120.0,  350.0, 0),
    ]

    # ── Composants ECRAN4K (AR_Type=1) ───────────────────────────────
    composants_ecran4k = [
        ('DALLE-LED-4K', 'Dalle LED 4K 27 pouces',  60.0,  80.0, 1),
        ('CARTE-VIDEO',  'Carte video HDMI/DP',      35.0,  45.0, 1),
        ('CHASSIS-ALU',  'Chassis aluminium',         18.0,  25.0, 1),
        ('ALIMENTATION', 'Bloc alimentation 65W',    15.0,  20.0, 1),
        ('CABLE-HDMI',   'Cable HDMI 1.5m',           3.0,   5.0, 1),
    ]

    # ── Composants LAPTOP (AR_Type=1) ─────────────────────────────────
    composants_laptop = [
        ('CPU-I7',    'Processeur Intel i7',    150.0, 200.0, 1),
        ('RAM-16GB',  'Barrette RAM 8GB DDR4',   35.0,  45.0, 1),
        ('SSD-512',   'SSD NVMe 512GB',           60.0,  80.0, 1),
        ('ECRAN-FHD', 'Ecran FHD 15.6 pouces',   90.0, 120.0, 1),
        ('BATTERIE',  'Batterie 72Wh',            45.0,  60.0, 1),
    ]

    # ── Autres composants generiques ──────────────────────────────────
    autres_articles = [
        ('DALLE01', 'Dalle LCD 27 pouces', 50.0, 60.0, 1),
        ('CHASSIS', 'Chassis Plastique',   10.0, 15.0, 1),
    ]

    tous_articles = (
        articles_pf
        + composants_ecran4k
        + composants_laptop
        + autres_articles
    )
    cursor.executemany(
        "INSERT OR REPLACE INTO F_ARTICLE VALUES (?,?,?,?,?)", tous_articles
    )

    # ── Stocks initiaux ───────────────────────────────────────────────
    stocks = [
        # Produits finis
        ('ECRAN4K',    2.0,  0.0, 0.0),
        ('LAPTOP',     3.0,  0.0, 0.0),
        ('IMPRIMANTE', 5.0,  0.0, 0.0),
        # Composants ECRAN4K
        ('DALLE-LED-4K', 20.0, 0.0, 0.0),
        ('CARTE-VIDEO',  15.0, 0.0, 0.0),
        ('CHASSIS-ALU',  25.0, 0.0, 0.0),
        ('ALIMENTATION', 30.0, 0.0, 0.0),
        ('CABLE-HDMI',   50.0, 0.0, 0.0),
        # Composants LAPTOP
        ('CPU-I7',    10.0, 0.0, 0.0),
        ('RAM-16GB',  20.0, 0.0, 0.0),
        ('SSD-512',   12.0, 0.0, 0.0),
        ('ECRAN-FHD',  8.0, 0.0, 0.0),
        ('BATTERIE',  15.0, 0.0, 0.0),
        # Autres
        ('DALLE01', 10.0, 0.0, 0.0),
        ('CHASSIS', 15.0, 0.0, 0.0),
    ]
    cursor.executemany(
        "INSERT OR REPLACE INTO F_ARTSTOCK VALUES (?,?,?,?)", stocks
    )

    # ── Nomenclatures ─────────────────────────────────────────────────
    nomenclature = [
        # ECRAN4K = 1 DALLE + 1 CARTE + 1 CHASSIS + 1 ALIM + 2 CABLES
        ('ECRAN4K', 'DALLE-LED-4K', 1.0),
        ('ECRAN4K', 'CARTE-VIDEO',  1.0),
        ('ECRAN4K', 'CHASSIS-ALU',  1.0),
        ('ECRAN4K', 'ALIMENTATION', 1.0),
        ('ECRAN4K', 'CABLE-HDMI',   2.0),
        # LAPTOP = 1 CPU + 2 RAM + 1 SSD + 1 ECRAN + 1 BATTERIE
        ('LAPTOP', 'CPU-I7',    1.0),
        ('LAPTOP', 'RAM-16GB',  2.0),
        ('LAPTOP', 'SSD-512',   1.0),
        ('LAPTOP', 'ECRAN-FHD', 1.0),
        ('LAPTOP', 'BATTERIE',  1.0),
    ]
    cursor.executemany(
        "INSERT OR REPLACE INTO F_NOMENCLAT VALUES (?,?,?)", nomenclature
    )

    # ── Documents ─────────────────────────────────────────────────────
    today = date.today()
    docs = [
        # ── Vente (DO_Domaine=0) ──────────────────────────────────
        # BL (DO_Type=2)
        ('BL00001', 0, 2, (today - timedelta(days=60)).isoformat(), None,                        'CLI001'),
        ('BL00002', 0, 2, (today - timedelta(days=45)).isoformat(), None,                        'CLI003'),
        # Factures vente (DO_Type=3)
        ('FA00001', 0, 3, (today - timedelta(days=55)).isoformat(), None,                        'CLI001'),
        ('FA00002', 0, 3, (today - timedelta(days=40)).isoformat(), None,                        'CLI001'),
        ('FA00003', 0, 3, (today - timedelta(days=25)).isoformat(), None,                        'CLI003'),
        ('FA00004', 0, 3, (today - timedelta(days=20)).isoformat(), None,                        'CLI004'),
        ('FA00005', 0, 3, (today - timedelta(days=10)).isoformat(), None,                        'CLI005'),
        ('FA00006', 0, 3, (today - timedelta(days=90)).isoformat(), 'SOLDEE / REGLEE - Virement','CLI001'),
        ('FA00007', 0, 3, (today - timedelta(days=80)).isoformat(), 'SOLDEE / REGLEE - Cheque',  'CLI003'),
        # Fabrication (DO_Domaine=2)
        # OF (DO_Type=1)
        ('OF00001', 2, 1, (today - timedelta(days=15)).isoformat(), None,                        'PROD-INT'),
        # ── Achat fournisseurs (DO_Domaine=1) ────────────────────
        # Bons de commande fournisseur (DO_Type=6)
        ('BC00001', 1, 6, (today - timedelta(days=50)).isoformat(), None, 'FOUR01'),
        ('BC00002', 1, 6, (today - timedelta(days=30)).isoformat(), None, 'FOUR02'),
        ('BC00003', 1, 6, (today - timedelta(days=10)).isoformat(), None, 'FOUR01'),
        # Bons de reception fournisseur (DO_Type=2, DO_Domaine=1)
        ('BR00001', 1, 2, (today - timedelta(days=45)).isoformat(), None, 'FOUR01'),
        ('BR00002', 1, 2, (today - timedelta(days=25)).isoformat(), None, 'FOUR02'),
        # Factures fournisseur (DO_Type=3, DO_Domaine=1)
        ('FF00001', 1, 3, (today - timedelta(days=44)).isoformat(), None, 'FOUR01'),
        ('FF00002', 1, 3, (today - timedelta(days=24)).isoformat(), None, 'FOUR02'),
        ('FF00003', 1, 3, (today - timedelta(days=8)).isoformat(),  None, 'FOUR01'),
    ]
    cursor.executemany(
        "INSERT OR REPLACE INTO F_DOCENTETE "
        "(DO_Piece, DO_Domaine, DO_Type, DO_Date, DO_Ref, CT_Num) "
        "VALUES (?,?,?,?,?,?)",
        docs
    )

    # ── Lignes de documents ───────────────────────────────────────────
    lignes = [
        # ── Vente ────────────────────────────────────────────────
        ('BL00001', 'ECRAN4K',    3.0,  250.0),
        ('BL00002', 'LAPTOP',     2.0, 1200.0),
        ('FA00001', 'ECRAN4K',    3.0,  250.0),
        ('FA00002', 'LAPTOP',     1.0, 1200.0),
        ('FA00003', 'ECRAN4K',    4.0,  250.0),
        ('FA00003', 'IMPRIMANTE', 1.0,  350.0),
        ('FA00004', 'LAPTOP',     2.0, 1200.0),
        ('FA00005', 'ECRAN4K',    2.0,  250.0),
        ('FA00006', 'ECRAN4K',    5.0,  250.0),
        ('FA00007', 'LAPTOP',     3.0, 1200.0),
        # ── Fabrication ──────────────────────────────────────────
        ('OF00001', 'ECRAN4K',    5.0,  250.0),
        # ── Achat fournisseurs ────────────────────────────────────
        # BC FOUR01 : commande composants ECRAN4K
        ('BC00001', 'DALLE-LED-4K', 10.0,  60.0),
        ('BC00001', 'CARTE-VIDEO',   8.0,  35.0),
        ('BC00001', 'CHASSIS-ALU',  12.0,  18.0),
        # BC FOUR02 : commande composants LAPTOP
        ('BC00002', 'CPU-I7',    5.0, 150.0),
        ('BC00002', 'RAM-16GB', 10.0,  35.0),
        ('BC00002', 'SSD-512',   6.0,  60.0),
        # BC FOUR01 : nouvelle commande
        ('BC00003', 'DALLE-LED-4K', 15.0,  60.0),
        ('BC00003', 'ALIMENTATION', 20.0,  15.0),
        # BR FOUR01 : reception composants ECRAN4K
        ('BR00001', 'DALLE-LED-4K', 10.0,  60.0),
        ('BR00001', 'CARTE-VIDEO',   8.0,  35.0),
        # BR FOUR02 : reception composants LAPTOP
        ('BR00002', 'CPU-I7',    5.0, 150.0),
        ('BR00002', 'RAM-16GB', 10.0,  35.0),
        # FF FOUR01 : factures fournisseur composants ECRAN4K
        ('FF00001', 'DALLE-LED-4K', 10.0,  60.0),
        ('FF00001', 'CARTE-VIDEO',   8.0,  35.0),
        # FF FOUR02 : facture fournisseur composants LAPTOP
        ('FF00002', 'CPU-I7',    5.0, 150.0),
        ('FF00002', 'RAM-16GB', 10.0,  35.0),
        # FF FOUR01 : 2eme facture
        ('FF00003', 'DALLE-LED-4K', 15.0,  60.0),
        ('FF00003', 'ALIMENTATION', 20.0,  15.0),
    ]
    cursor.executemany(
        "INSERT OR REPLACE INTO F_DOCLIGNE "
        "(DO_Piece, AR_Ref, DL_Qte, DL_PrixUnitaire) VALUES (?,?,?,?)",
        lignes
    )

    # ── Reglements (factures vente déjà réglées) ──────────────────────
    reglements = [
        ('FA00006', 'Virement', 1250.0, (today - timedelta(days=85)).isoformat()),
        ('FA00007', 'Cheque',   3600.0, (today - timedelta(days=75)).isoformat()),
    ]
    cursor.executemany(
        "INSERT OR REPLACE INTO reglements "
        "(DO_Piece, mode_paiement, montant, date_reglement) VALUES (?,?,?,?)",
        reglements
    )

    conn.commit()
    conn.close()

    print("Base de donnees Sage operationnelle.")
    print("  Clients          : CLI001 (Valide), CLI002 (Bloque), CLI003-004 (Valide), CLI005 (Suspect)")
    print("  Fournisseurs     : FOUR01 (Fournisseur Composants), FOUR02 (Grossiste Electronique)")
    print("  Tiers interne    : PROD-INT")
    print("  Composants ECRAN4K : DALLE-LED-4K, CARTE-VIDEO, CHASSIS-ALU, ALIMENTATION, CABLE-HDMI")
    print("  Composants LAPTOP  : CPU-I7, RAM-16GB, SSD-512, ECRAN-FHD, BATTERIE")
    print("  Docs vente       : BL00001-2, FA00001-7")
    print("  Docs fabrication : OF00001")
    print("  Docs achat       : BC00001-3 (commandes), BR00001-2 (receptions), FF00001-3 (factures)")
    print("  Reglements       : FA00006 (Virement), FA00007 (Cheque)")


if __name__ == "__main__":
    init_database_complete()