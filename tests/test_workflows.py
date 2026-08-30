
import pytest
from datetime import datetime

# Adjust Python path to import from the parent directory if needed
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from api.mcp_actions_sage import _ajuster_stock_db


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # Create required tables for testing
    conn.executescript("""
        CREATE TABLE F_ARTSTOCK (
            AR_Ref TEXT PRIMARY KEY,
            AS_QteSto REAL NOT NULL
        );
        CREATE TABLE mouvements_stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            AR_Ref TEXT,
            type_mouvement TEXT,
            qte REAL,
            motif TEXT,
            date_mouvement TEXT
        );
    """)
    
    # Insert test data
    conn.executescript("""
        INSERT INTO F_ARTSTOCK (AR_Ref, AS_QteSto) VALUES ('ART001', 10.0);
        INSERT INTO F_ARTSTOCK (AR_Ref, AS_QteSto) VALUES ('ART002', 0.0);
    """)
    
    yield conn
    conn.close()

def test_ajuster_stock_entree(memory_db):
    res = _ajuster_stock_db(memory_db, 'ART001', 5.0, 'ENTREE', 'Test Entrée')
    assert res['ok'] is True
    assert res['stock_avant'] == 10.0
    assert res['stock_apres'] == 15.0
    assert res['type'] == 'ENTREE'
    assert res['qte'] == 5.0
    
    row = memory_db.execute("SELECT AS_QteSto FROM F_ARTSTOCK WHERE AR_Ref='ART001'").fetchone()
    assert float(row['AS_QteSto']) == 15.0

def test_ajuster_stock_sortie_succes(memory_db):
    res = _ajuster_stock_db(memory_db, 'ART001', 4.0, 'SORTIE', 'Test Sortie')
    assert res['ok'] is True
    assert res['stock_avant'] == 10.0
    assert res['stock_apres'] == 6.0
    
    row = memory_db.execute("SELECT AS_QteSto FROM F_ARTSTOCK WHERE AR_Ref='ART001'").fetchone()
    assert float(row['AS_QteSto']) == 6.0

def test_ajuster_stock_sortie_insuffisant(memory_db):
    with pytest.raises(ValueError, match="Stock insuffisant pour ART001: 10.0 < 15.0"):
        _ajuster_stock_db(memory_db, 'ART001', 15.0, 'SORTIE', 'Test Sortie Insuffisante')
        
    # Stock should remain unchanged
    row = memory_db.execute("SELECT AS_QteSto FROM F_ARTSTOCK WHERE AR_Ref='ART001'").fetchone()
    assert float(row['AS_QteSto']) == 10.0

def test_ajuster_stock_article_inconnu(memory_db):
    with pytest.raises(ValueError, match="Article inconnu: INCONNU"):
        _ajuster_stock_db(memory_db, 'INCONNU', 1.0, 'SORTIE', 'Test')
